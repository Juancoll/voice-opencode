"""
Adapter: opencode HTTP server as an :class:`~voice_opencode.llm.LLMBackend`.

Two surfaces:

* ``health()`` — module-level liveness check, kept for backward
  compatibility (used by ``cli.py`` status command).
* ``OpencodeBackend`` — implements the LLM Protocol; persists the
  session id to ``SESSION_FILE`` so it survives across CLI processes.
* ``Session`` — legacy thin wrapper kept as a compatibility shim for
  callers that still want the imperative style (``Session.current_id()``,
  ``Session(sid).abort()``). New code should prefer ``OpencodeBackend``
  via :func:`voice_opencode.llm.get_backend`.

Why a class? The session id needs to persist across CLI invocations
(``REC stop`` runs in a new process), so we read/write the id from
``SESSION_FILE`` rather than holding it in memory.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests

from .config import settings
from .logging import log
from .paths import SESSION_FILE
from .screenshot import to_data_url


def health() -> bool:
    """True if the opencode HTTP server answers 2xx within 2s."""
    try:
        r = requests.get(f"{settings.opencode_url}/global/health", timeout=2)
        return r.ok
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Backend implementation (ADR-0029)
# ---------------------------------------------------------------------------
class OpencodeBackend:
    """``opencode serve`` HTTP client, exposed as an LLMBackend."""

    name = "opencode"

    def health(self) -> bool:
        return health()

    def session_id(self) -> str | None:
        if SESSION_FILE.exists():
            return SESSION_FILE.read_text().strip() or None
        return None

    def forget(self) -> None:
        SESSION_FILE.unlink(missing_ok=True)

    def ensure_session(self) -> None:
        """Create or validate the cached session. Honours
        ``settings.keep_context`` — if disabled, drops the cached id
        and forces a fresh one."""
        if not health():
            raise RuntimeError(
                f"opencode server not reachable at {settings.opencode_url}. "
                "Start it with: systemctl --user start opencode-serve"
            )
        if not settings.keep_context:
            SESSION_FILE.unlink(missing_ok=True)

        if SESSION_FILE.exists():
            sid = SESSION_FILE.read_text().strip()
            try:
                r = requests.get(
                    f"{settings.opencode_url}/session/{sid}", timeout=5
                )
                if r.ok:
                    return
            except Exception:
                pass

        r = requests.post(
            f"{settings.opencode_url}/session",
            json={"title": "voice"},
            timeout=10,
        )
        r.raise_for_status()
        sid = r.json()["id"]
        SESSION_FILE.write_text(sid)
        log(f"Created opencode session: {sid}")

    def ask(
        self,
        prompt: str,
        screenshot: Path | None = None,
        extra_context: str = "",
    ) -> str:
        """Send a user message; return the concatenated text reply.

        Implemented on top of :meth:`ask_stream` so the non-streaming
        and streaming code paths share one HTTP / SSE pipeline. The
        screenshot, if given, is attached as a base64 data URL so we
        don't have to host a file server.
        """
        return "".join(
            self.ask_stream(prompt, screenshot=screenshot, extra_context=extra_context)
        ).strip()

    def ask_stream(
        self,
        prompt: str,
        screenshot: Path | None = None,
        extra_context: str = "",
    ) -> Iterator[str]:
        """Stream text deltas from opencode via SSE.

        Sequence:

        1. ``GET /event`` (SSE) opened *before* posting the message so
           we don't miss the first ``message.part.delta``. opencode's
           event stream is multiplexed across sessions; we filter by
           ``properties.sessionID``.
        2. ``POST /session/{id}/message`` on a background thread so
           this generator can immediately start draining SSE in the
           main thread.
        3. Yield ``properties.delta`` from every ``message.part.delta``
           event whose ``field == "text"`` and ``sessionID`` matches.
        4. Stop on ``session.idle`` for our session, or when the POST
           thread finishes — whichever comes first.
        5. On timeout/transport error, call ``self.abort()`` so the
           server stops any runaway tool loop, then re-raise.
        """
        self.ensure_session()
        sid = self.session_id()
        if sid is None:  # pragma: no cover — ensure_session guarantees this
            raise RuntimeError("opencode session missing after ensure_session()")

        parts: list[dict] = []
        # extra_context (e.g. monitor layout) goes *before* the user
        # prompt so the model has the spatial / environmental context
        # in hand by the time it reads the instruction. We label it
        # explicitly so the model doesn't confuse it with user intent.
        if extra_context:
            parts.append({
                "type": "text",
                "text": f"[Contexto del sistema]\n{extra_context}",
            })
        parts.append({"type": "text", "text": prompt})
        if screenshot is not None and screenshot.exists():
            parts.append({
                "type": "file",
                "mime": "image/png",
                "filename": "screen.png",
                "url": to_data_url(screenshot),
            })
            log(f"Attaching screenshot ({screenshot.stat().st_size} bytes).")

        log(f"SSE+POST /session/{sid}/message (streaming)")

        # 1. Open SSE stream *first* — opencode starts emitting events
        # the moment the POST hits, and we don't want to miss the
        # opening deltas. The 30s connect-timeout is generous; reads
        # are unbounded so a slow model doesn't kill the stream.
        try:
            sse = requests.get(
                f"{settings.opencode_url}/event",
                stream=True,
                timeout=(30, None),
            )
            sse.raise_for_status()
            # Force UTF-8 — opencode emits JSON with non-ASCII chars
            # (e.g. 'Sí', '¿'), but the response declares text/event-
            # stream without a charset, so requests defaults to
            # ISO-8859-1 per RFC 2616 §3.7.1. Without this override
            # iter_lines(decode_unicode=True) returns mojibake like
            # 'SÃ\xad' for 'Sí'.
            sse.encoding = "utf-8"
        except Exception:
            self.abort()
            raise

        # 2. POST on a background thread so we can consume SSE
        # synchronously below. The POST blocks until opencode finishes
        # the turn (just like the non-streaming path), which is how
        # we know when to stop reading SSE.
        post_error: list[BaseException] = []

        def _post() -> None:
            try:
                payload: dict[str, Any] = {"parts": parts}
                # Inject the voice system prompt if set. opencode
                # accepts a top-level ``system`` field that's
                # prepended for this turn. We send it on every
                # request (cheap) so the user can edit it in
                # config.json + ``voice reset`` without restarts.
                sys_prompt = settings.voice_system_prompt
                if sys_prompt:
                    payload["system"] = sys_prompt
                r = requests.post(
                    f"{settings.opencode_url}/session/{sid}/message",
                    json=payload,
                    timeout=180,
                )
                r.raise_for_status()
            except BaseException as e:  # noqa: BLE001 — re-raised below
                post_error.append(e)

        poster = threading.Thread(target=_post, daemon=True)
        poster.start()

        # 3+4. Drain SSE until session.idle for our session, or the
        # POST thread finishes (in case of error/abort).
        delta_count = 0
        event_count = 0
        last_etype: str | None = None
        error_evt: dict | None = None
        try:
            for raw in sse.iter_lines(decode_unicode=True):
                # requests' stub types raw as bytes even with
                # decode_unicode=True; normalise so static analysis
                # is happy without a per-line cast.
                line = raw.decode("utf-8") if isinstance(raw, bytes) else (raw or "")
                if not line or not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except ValueError:
                    continue
                event_count += 1
                etype = evt.get("type")
                last_etype = etype
                props = evt.get("properties") or {}
                # Hard filter: ignore events for other sessions.
                # Some events (server.connected, server.heartbeat) have no
                # sessionID — let those through so we don't break on them.
                evt_sid = props.get("sessionID")
                if evt_sid is not None and evt_sid != sid:
                    continue
                # Trace every event we accept so the log shows the full
                # SSE timeline. Truncated to keep voice.log readable.
                log(f"SSE evt#{event_count}: type={etype} "
                    f"keys={sorted(props.keys())}")
                if etype == "message.part.delta":
                    if props.get("field") == "text":
                        delta = props.get("delta") or ""
                        if delta:
                            delta_count += 1
                            if delta_count == 1:
                                log(f"SSE: first delta after {event_count} events")
                            yield delta
                elif etype == "session.idle":
                    log(f"SSE: session.idle received, closing stream "
                        f"(events={event_count}, deltas={delta_count})")
                    break
                elif etype == "session.error":
                    # Dump the entire event — we want every byte the
                    # server sent so we can diagnose later without a
                    # live repro. Slicing avoids drowning the log if
                    # the server attaches a stack trace.
                    raw_dump = json.dumps(evt)[:2000]
                    log(f"SSE: session.error received "
                        f"(events={event_count}, deltas={delta_count}); "
                        f"event={raw_dump}")
                    error_evt = evt
                    break
                # We deliberately ignore message.updated /
                # message.part.updated (cumulative snapshots) to avoid
                # double-emitting text the caller already saw via deltas.
        finally:
            log(f"SSE: stream closed (events={event_count}, "
                f"deltas={delta_count}, last_etype={last_etype})")
            try:
                sse.close()
            except Exception:  # pragma: no cover — defensive
                pass

        # If the server reported session.error and nothing was emitted,
        # propagate as a hard failure so the pipeline can show an error
        # HUD instead of feeding "" to TTS. Caller already has the dump
        # in voice.log for diagnosis.
        if error_evt is not None and delta_count == 0:
            err_props = error_evt.get("properties") or {}
            err_data = err_props.get("error") or err_props
            msg = json.dumps(err_data)[:200] if err_data else "session.error"
            self.abort()
            raise RuntimeError(f"opencode session.error: {msg}")

        # 5. Surface POST errors, if any. abort() so the server stops
        # the runaway loop (same contract as non-streaming ask).
        poster.join(timeout=2.0)
        if post_error:
            self.abort()
            raise post_error[0]

    def abort(self) -> bool:
        """Tell opencode server to stop the current run for this session.

        Best-effort: server returns 200 even when there is nothing to
        abort. Logged, never raises.
        """
        sid = self.session_id()
        if sid is None:
            return False
        try:
            r = requests.post(
                f"{settings.opencode_url}/session/{sid}/abort",
                timeout=5,
            )
            log(f"POST /session/{sid}/abort -> {r.status_code}")
            return r.ok
        except Exception as e:
            log(f"abort error: {e}")
            return False


# ---------------------------------------------------------------------------
# Legacy compatibility shim (pre-ADR-0029)
# ---------------------------------------------------------------------------
# The pipeline used to do ``Session.get_or_create().ask(...)`` and the
# cancel path called ``Session(sid).abort()``. New code should use the
# backend Protocol, but we keep this thin wrapper so external callers
# (e.g. tests, hand-written scripts) don't break.
class Session:
    """Backwards-compatible facade over :class:`OpencodeBackend`.

    Each instance carries a fixed session id; if you need the *current*
    cached id use :meth:`current_id`.
    """

    def __init__(self, sid: str) -> None:
        self.id = sid

    @classmethod
    def get_or_create(cls) -> Session:
        backend = OpencodeBackend()
        backend.ensure_session()
        sid = backend.session_id()
        if sid is None:  # pragma: no cover
            raise RuntimeError("opencode session missing after ensure_session()")
        return cls(sid)

    @staticmethod
    def forget() -> None:
        OpencodeBackend().forget()

    @staticmethod
    def current_id() -> str | None:
        return OpencodeBackend().session_id()

    def ask(self, prompt: str, screenshot: Path | None = None) -> str:
        # Delegate to the backend, but pin the session id so callers
        # holding a stale Session() don't accidentally talk to a newer
        # one.
        backend = OpencodeBackend()
        # ensure_session() may rotate the cached id if it's stale; for
        # the legacy callsites we want to honour *their* id, so we
        # temporarily restore it.
        current = backend.session_id()
        if current != self.id:
            SESSION_FILE.write_text(self.id)
        try:
            return backend.ask(prompt, screenshot=screenshot)
        finally:
            # Don't bother restoring `current` — the legacy callers
            # treat the most-recent id as authoritative anyway.
            pass

    def abort(self) -> bool:
        backend = OpencodeBackend()
        current = backend.session_id()
        if current != self.id:
            SESSION_FILE.write_text(self.id)
        try:
            return backend.abort()
        finally:
            pass
