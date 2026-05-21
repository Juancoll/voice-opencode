"""
LLM backend abstraction.

The voice pipeline talks to *some* LLM agent that takes text + an
optional screenshot and returns text. Historically that was always
``opencode serve``, hard-coded. ADR-0029 introduces a thin Protocol so
other agents (Claude Code CLI, Hermes, Ollama, …) can be plugged in
without touching ``pipeline.py``.

Public surface:

* ``LLMBackend`` — the Protocol every backend must satisfy.
* ``get_backend()`` — factory; reads ``settings.llm_backend`` and
  returns a *module-level singleton* implementing the protocol.
* ``reset_backend_cache()`` — drop the singleton (used by tests and
  by ``config.reload()``).

Selecting the active backend is **config-only** (``VOICE_LLM_BACKEND``
env var or ``llm_backend`` in ``config.json``). There is intentionally
no CLI to swap at runtime: a turn that started against backend A and
finishes against backend B would be a debugging nightmare. To switch,
edit config and restart the tray + ``opencode-serve``.

Screenshot handling is **per-backend**: the protocol passes a
``pathlib.Path | None`` and each implementation decides whether to
inline it (opencode data URL), upload it, or ignore it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import config as _config


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
@runtime_checkable
class LLMBackend(Protocol):
    """Contract every LLM backend must satisfy.

    Implementations are stateful: they own a persistent conversation /
    session id so multi-turn context survives across CLI invocations
    (each F9 release is a *fresh process*).
    """

    name: str
    """Short identifier, e.g. ``"opencode"``. Used in logs."""

    def health(self) -> bool:
        """Liveness probe. Cheap; called by the tray's status menu."""

    def session_id(self) -> str | None:
        """Return current conversation id, or ``None`` if no session yet."""

    def ensure_session(self) -> None:
        """Create the conversation if needed. May raise ``RuntimeError``
        if the backend is unreachable; the pipeline turns that into a
        user-visible HUD error."""

    def ask(
        self,
        prompt: str,
        screenshot: Path | None = None,
        extra_context: str = "",
    ) -> str:
        """Send a user turn; block until the full reply is available.

        ``screenshot`` is best-effort — backends that don't support
        vision should silently ignore it.

        ``extra_context`` is best-effort additional system-style text
        injected alongside the prompt (e.g. monitor layout for vision
        turns). Backends should treat it as low-priority context, not
        as the user's instruction. Empty string ⇒ inject nothing.

        On HTTP/transport timeout, implementations **must** call
        ``self.abort()`` themselves before re-raising, so the
        server-side tool loop stops instead of burning credits while
        the pipeline gives up.

        Default implementations of ``ask_stream`` and ``ask`` are
        mutually expressible: a backend may override just ``ask`` (no
        streaming support) or just ``ask_stream`` (preferred — the
        non-stream ``ask`` then collapses to ``"".join(...)``).
        """

    def ask_stream(
        self,
        prompt: str,
        screenshot: Path | None = None,
        extra_context: str = "",
    ) -> Iterator[str]:
        """Yield text **deltas** (fragments) as the model produces them.

        Each yielded chunk is a *fragment* of the reply (e.g. ``"Ho"``,
        ``"la, "``, ``" ¿cómo"``), **not** cumulative — callers
        concatenate.

        Backends without true streaming may yield the full reply in
        one chunk and return: callers must work either way.

        Same error contract as ``ask``: on timeout/transport failure,
        implementations call ``self.abort()`` before raising.
        """

    def abort(self) -> bool:
        """Best-effort: stop any in-flight tool loop for this session.
        Returns True on success, False otherwise. Never raises."""

    def forget(self) -> None:
        """Drop the cached session id; next ``ensure_session()`` makes
        a fresh one. Used by ``voice session reset``."""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_BACKEND: LLMBackend | None = None


def get_backend() -> LLMBackend:
    """Return the active backend singleton, building it on first call.

    The selection is read from ``settings.llm_backend`` once and then
    cached. Call :func:`reset_backend_cache` if you mutate the config
    and want the next ``get_backend()`` to re-resolve.
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    name = _config.settings.llm_backend.strip().lower() or "opencode"
    if name == "opencode":
        from .opencode_client import OpencodeBackend

        _BACKEND = OpencodeBackend()
    else:
        # Unknown name → fail loudly. We don't silently fall back to
        # opencode because that hides config typos.
        raise RuntimeError(
            f"Unknown llm_backend {name!r}. "
            f"Known: 'opencode'. Set VOICE_LLM_BACKEND or "
            f"llm_backend in config.json."
        )
    # Silent on factory build: the CLI is invoked once per second by
    # the tray's status poller, and logging here would flood voice.log
    # with one line per second. Backends log their own activity (POST,
    # SSE deltas, abort) which is what we actually need to diagnose.
    return _BACKEND


def reset_backend_cache() -> None:
    """Drop the cached backend so the next ``get_backend()`` rebuilds.

    Called by ``config.reload()`` and by tests that switch backends.
    """
    global _BACKEND
    _BACKEND = None
