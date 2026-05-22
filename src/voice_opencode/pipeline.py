"""
Pipeline orchestration: record → STT → opencode → TTS, with state events.

The pipeline coordinates the dumb modules (audio, stt, screenshot,
opencode_client, tts) and emits ``state`` updates so the tray icon can
react. Each phase is wrapped in try/except so a failure in one stage
doesn't leave the state machine stuck.

Concurrency: every Hyprland F9 bind dispatch spawns a fresh ``voice``
process, so ``threading.Lock`` is useless here — we need cross-process
mutual exclusion. We use a **timestamped lockfile** at
``PIPELINE_LOCK_FILE`` (inside ``$XDG_RUNTIME_DIR/voice-opencode/``):

* Acquire: ``os.open(O_CREAT | O_EXCL)`` — kernel-atomic. The file
  contains a single line ``"<pid> <unix_ms>"`` so it is both
  inspectable (``cat pipeline.lock``) and forensically useful.
* Stale recovery: if O_EXCL fails, read the file. If the timestamp is
  older than ``_LOCK_TTL_S`` (covers any sane TTS reply, and the
  ``paplay`` watchdog already caps replies at 60s) OR the PID is dead,
  we steal the lock — unlink + one retry. Worst case the user
  recovers by hand with ``rm $XDG_RUNTIME_DIR/voice-opencode/pipeline.lock``.
* Release: ``unlink()``. We don't validate the owner before unlinking
  on purpose: if someone stole the lock from us, they already won the
  race and we have nothing useful to protect.

This is preferable to ``fcntl.flock`` because the lockfile is human
inspectable and manually removable, which matters when something goes
wrong on the user's machine (it already did once in the smoke test:
``paplay timed out; piper exit code -9``).
"""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import subprocess
import time
from collections.abc import Iterator

from . import agent, audio, desktop, stt, tts
from . import platform as _plat
from . import state as state_mod
from .config import settings
from .context import build_extra_context
from .llm import get_backend
from .logging import log
from .notify import turn_end, turn_start, turn_update
from .paths import PIPELINE_LOCK_FILE, ensure_dirs
from .screenshot import capture
from .state import set_state

# A long TTS reply caps at ~60s thanks to the paplay watchdog in
# tts.speak(); 120s gives us 2x headroom plus STT + opencode latency.
_LOCK_TTL_S: float = 120.0

# Phases during which a second F9 release is interpreted as "cancel
# the running turn" instead of "ignored, you're busy". 'recording'
# is excluded because audio.stop() / stop_and_run() is the normal
# successor of a recording turn, not a cancel of one.
_CANCELLABLE_STATES: frozenset[str] = frozenset({"thinking", "speaking"})


def _cancel_active_turn(holder_pid: int) -> None:
    """Best-effort cancellation of an in-flight pipeline turn.

    Called from a *different* process than the one holding the lock,
    so we cannot just raise — we must signal across processes.

    Order:

    1. ``backend.abort()`` so the agent server stops the runaway
       tool loop. Without this, killing the local process leaves the
       model still spinning server-side and the next ``ask()`` blocks
       on its `/event` stream until the previous run times out.
    2. ``SIGINT`` (not SIGTERM) to the holder PID. SIGINT raises
       ``KeyboardInterrupt`` in pure-Python ``time.sleep`` /
       ``subprocess.wait``, which lets the holder's
       ``_pipeline_lock`` ``finally`` clause run and unlink the
       lockfile — clean exit, no stale lock. SIGTERM bypasses
       ``finally`` and would leave the lock for the TTL stealer.
    3. Best-effort ``pkill paplay`` in case the holder is mid-TTS;
       SIGINT'ing the python parent doesn't propagate to ``paplay``
       (separate process group via ``subprocess.Popen`` defaults).
    4. HUD update + ``turn_end()`` from *this* process. The HUD lives
       in the tray and is reachable via the unix socket from any
       process, so we can render "🛑 Cancelado" without waiting for
       the dying holder to update it.
    """
    try:
        backend = get_backend()
        sid = backend.session_id()
        if sid:
            backend.abort()
            log(f"Cancel: aborted {backend.name} session {sid}.")
    except Exception as e:  # pragma: no cover — defensive
        log(f"Cancel: backend.abort() failed: {e}")

    try:
        os.kill(holder_pid, signal.SIGINT)
        log(f"Cancel: sent SIGINT to pipeline holder pid={holder_pid}.")
    except ProcessLookupError:
        log(f"Cancel: holder pid={holder_pid} already gone.")
    except PermissionError as e:  # pragma: no cover — only if owned by root
        log(f"Cancel: cannot signal pid={holder_pid}: {e}")

    # paplay runs in its own subprocess that doesn't share a signal
    # mask with our python parent. pkill -x is targeted enough that
    # we won't murder unrelated audio players.
    try:
        subprocess.run(
            ["pkill", "-x", "paplay"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):  # pragma: no cover
        pass

    # State + HUD updates from the canceller. The holder's own HUD
    # code path won't run because SIGINT interrupts it mid-call.
    set_state("idle")
    turn_update("🛑 Cancelado", "")
    turn_end()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists, owned by someone else. Treat as alive.
        return True
    return True


def _read_lock_owner(path: os.PathLike[str]) -> tuple[int, float] | None:
    """Return (pid, age_seconds) of the current lockholder, or None if unreadable."""
    try:
        raw = open(path).read().strip()
        pid_s, ts_ms_s = raw.split()
        pid = int(pid_s)
        ts_ms = float(ts_ms_s)
    except (OSError, ValueError):
        return None
    age = time.time() - (ts_ms / 1000.0)
    return pid, age


def _try_create_lockfile() -> bool:
    """Attempt one O_CREAT|O_EXCL write of our pid + timestamp. Bool = won."""
    payload = f"{os.getpid()} {int(time.time() * 1000)}\n".encode()
    try:
        fd = os.open(
            PIPELINE_LOCK_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError:
        return False
    except OSError as e:  # pragma: no cover — surfaces real fs problems
        if e.errno == errno.EEXIST:
            return False
        raise
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return True


@contextlib.contextmanager
def _pipeline_lock(action: str) -> Iterator[bool]:
    """Yield True if the cross-process pipeline lock was acquired.

    On contention with a *live, fresh* holder we log and yield False
    silently. The HUD already shows the running turn's state, so
    libnotify toasts are NOT emitted here — that was the whole point
    of ADR-0026 and ADR-0027.

    On contention with a stale holder (dead PID or older than
    ``_LOCK_TTL_S``) we steal the lock and proceed. The log line on
    contention always includes pid + age so the user can diagnose
    runaway turns by tailing ``logs/voice.log``.
    """
    ensure_dirs()

    if not _try_create_lockfile():
        owner = _read_lock_owner(PIPELINE_LOCK_FILE)
        stale = (
            owner is None
            or owner[1] > _LOCK_TTL_S
            or not _pid_alive(owner[0])
        )
        if not stale:
            pid, age = owner  # type: ignore[misc]
            log(f"Pipeline busy — ignoring {action} (held by pid={pid}, age={age:.1f}s).")
            yield False
            return
        # Stale: steal and retry once.
        why = "dead pid" if owner and not _pid_alive(owner[0]) else "expired ttl"
        log(f"Pipeline lock looks stale ({why}); stealing.")
        try:
            os.unlink(PIPELINE_LOCK_FILE)
        except FileNotFoundError:
            pass
        if not _try_create_lockfile():
            # Lost the steal race to a concurrent press; treat as busy.
            log(f"Pipeline busy after steal attempt — ignoring {action}.")
            yield False
            return

    try:
        yield True
    finally:
        try:
            os.unlink(PIPELINE_LOCK_FILE)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
def start_recording() -> None:
    """Begin recording unless paused or already running.

    A second F9 press while we're already recording (Hyprland
    key-repeat, accidental double-tap, fast retry) is dropped
    silently — no toast, no overlapping ``arecord``. We still write
    an explicit "F9 descartado" line to ``logs/voice.log`` so the
    trail is obvious when reviewing what F9 did. The same line is
    emitted whether the duplicate was caught by ``audio.is_recording``
    (fast path: most key-repeats) or by the cross-process lockfile
    (true race between two processes that both passed the audio
    check). Unifying the message simplifies grep/tail diagnostics.
    """
    if agent.is_blocking():
        why = "agent activo" if agent.is_active() else "en pausa"
        log(f"Blocked — ignoring start ({why}).")
        return
    if audio.is_recording():
        log("F9 descartado: ya hay un turno en curso (grabación o respuesta).")
        return
    with _pipeline_lock("start_recording") as acquired:
        if not acquired:
            log("F9 descartado: ya hay un turno en curso (grabación o respuesta).")
            return
        audio.start()
        set_state("recording")
        turn_start("🎙 Grabando…", "Suelta F9 para enviar")


def stop_and_run() -> None:
    """
    Stop recording and run the full pipeline. The pipeline is linear
    (no early return on partial success) but each step has its own
    error path so the state always lands on ``idle`` or ``error``.

    Holds the cross-process pipeline lock for the whole STT → opencode
    → TTS run so a second F9 release while we're speaking is either
    dropped or interpreted as a cancel (see below).

    **F9-while-busy = cancel** (ADR-0027): if the lock is held by a
    live, fresh holder AND the pipeline is currently in a cancellable
    phase (``thinking`` or ``speaking``), this F9 release means
    "stop, I don't want this turn anymore". We abort the opencode
    session, SIGINT the holder, kill any in-flight ``paplay``, and
    close the HUD. The holder process exits cleanly via its
    ``_pipeline_lock`` ``finally`` clause.

    Per-turn HUD (ADR-0026): opened in ``start_recording`` and kept
    alive across each phase so the user sees "🎙 Grabando" →
    "🧠 Pensando" → "⚙️ <tool>" (updated by the MCP server as it
    runs tools) → "🔊 Respondiendo" → closed. Error paths update the
    HUD before closing it; libnotify toasts are NOT used here because
    they would stack on top of the HUD (the bug ADR-0026 was meant
    to fix in the first place).
    """
    # Cancel-on-busy branch — runs before we try to acquire the lock.
    if PIPELINE_LOCK_FILE.exists():
        owner = _read_lock_owner(PIPELINE_LOCK_FILE)
        if (
            owner is not None
            and owner[1] <= _LOCK_TTL_S
            and _pid_alive(owner[0])
            and state_mod.get_state() in _CANCELLABLE_STATES
        ):
            holder_pid, age = owner
            log(
                f"F9 mid-turn: cancelling holder pid={holder_pid} "
                f"age={age:.1f}s state={state_mod.get_state()}."
            )
            _cancel_active_turn(holder_pid)
            return

    with _pipeline_lock("stop_and_run") as acquired:
        if not acquired:
            return

        t0 = time.monotonic()
        log("=== turn start: stop_and_run ===")
        wav = audio.stop()
        if wav is None:
            log(f"turn end: no audio (t={time.monotonic()-t0:.2f}s)")
            set_state("idle")
            turn_update("🤷 Sin audio", "")
            turn_end()
            return

        set_state("thinking")
        turn_update("🧠 Transcribiendo…", "")

        # 1. Transcribe
        t_stt0 = time.monotonic()
        try:
            text = stt.transcribe(wav)
        except Exception as e:
            log(f"STT error: {e}")
            set_state("error")
            turn_update("❌ Error STT", str(e)[:120])
            turn_end()
            return
        log(f"turn: STT done in {time.monotonic()-t_stt0:.2f}s "
            f"-> {text!r}")
        if not text:
            set_state("idle")
            turn_update("🤷 Nada que transcribir", "")
            turn_end()
            return

        turn_update("🧠 Pensando…", text[:80])

        # 2. Ask the LLM backend (with optional screenshot), streaming
        # the reply into the HUD subtitle so the user sees progress
        # instead of waiting on a static "Pensando…" for 30+ seconds.
        # The subtitle is throttled to once per ~80ms and only shows
        # the trailing window of the reply (keeps the HUD readable
        # even on long answers).
        t_shot0 = time.monotonic()
        shot = capture()
        log(f"turn: screenshot in {time.monotonic()-t_shot0:.2f}s "
            f"(shot={shot is not None})")
        # Build per-turn context. Combines monitor layout + system
        # info (OS, kernel, binary versions) + audio devices. Each
        # section is cached so the cost is one subprocess fan-out at
        # process start and zero afterwards.
        extra_ctx = ""
        if settings.attach_monitor_layout:
            extra_ctx = build_extra_context()
            if extra_ctx:
                log(f"turn: attaching context "
                    f"({extra_ctx.count(chr(10))+1} lines, {len(extra_ctx)} chars)")
        backend = get_backend()
        reply_parts: list[str] = []
        last_hud_ts = 0.0
        delta_count = 0
        hud_update_count = 0
        t_llm0 = time.monotonic()
        try:
            for delta in backend.ask_stream(text, screenshot=shot, extra_context=extra_ctx):
                if not delta:
                    continue
                reply_parts.append(delta)
                delta_count += 1
                now = time.monotonic()
                if now - last_hud_ts >= 0.08:
                    # Show last ~80 chars; full reply lives in
                    # reply_parts and is the only thing TTS sees.
                    tail = "".join(reply_parts)[-80:]
                    turn_update("🧠 Pensando…", tail)
                    last_hud_ts = now
                    hud_update_count += 1
            log(f"turn: LLM stream done in {time.monotonic()-t_llm0:.2f}s "
                f"({delta_count} deltas, {hud_update_count} HUD updates, "
                f"reply={len(''.join(reply_parts))} chars)")
        except Exception as e:
            log(f"turn: LLM error after {time.monotonic()-t_llm0:.2f}s: "
                f"{type(e).__name__}: {e}")
            # Make sure the server stops the runaway tool loop even if
            # ask_stream() already called abort() on Timeout — extra
            # POST is cheap and idempotent.
            backend.abort()
            set_state("error")
            turn_update(f"❌ {backend.name}", str(e)[:120])
            turn_end()
            return
        reply = "".join(reply_parts).strip()
        if not reply:
            log("turn: LLM produced empty reply; nothing to speak")
            set_state("idle")
            turn_update("🤐 Sin respuesta", "")
            turn_end()
            return

        turn_update("🔊 Respondiendo", reply[:80])

        # 3. Speak
        set_state("speaking")
        t_tts0 = time.monotonic()
        try:
            tts.speak(reply)
        except Exception as e:
            log(f"TTS error after {time.monotonic()-t_tts0:.2f}s: {e}")
            set_state("error")
            turn_update("❌ Error TTS", str(e)[:120])
            turn_end()
            return
        log(f"turn: TTS done in {time.monotonic()-t_tts0:.2f}s")

        set_state("idle")
        turn_end()
        log(f"=== turn end: total {time.monotonic()-t0:.2f}s ===")


def toggle() -> None:
    """Push-to-talk single-binding helper: stop+run if recording, else start.

    Delegates to ``stop_and_run`` / ``start_recording``; the lock is
    acquired inside those, so toggle() itself is a thin dispatcher.
    """
    if audio.is_recording():
        stop_and_run()
    else:
        start_recording()


# ---------------------------------------------------------------------------
# Dictation flow (Ctrl+F9 by default)
#
# A second, simpler pipeline that shares the recorder + STT with the
# assistant flow but DOES NOT touch the LLM, the opencode session, or
# the TTS. The transcribed text is injected at the cursor of whatever
# window is focused at stop time. Use case: filling forms, writing chat
# messages, code comments — anything where you want speech-to-text
# without an assistant turn in the middle.
#
# Locking strategy: dictation uses the SAME ``PIPELINE_LOCK_FILE`` as
# the assistant flow on purpose. The two can't run concurrently — they
# both need the microphone — and reusing the lock means F9-during-
# dictation behaves like the existing "busy" path (silently dropped)
# instead of stepping on each other's audio device.
#
# Cancel semantics: there is no mid-turn cancel. Dictation phases are
# short (record + transcribe + inject, never more than a few seconds
# after the user releases the key) and the inject step is atomic from
# the user's point of view. If a release races with a still-running
# transcription, the in-flight one finishes; the new "release" is
# dropped by the lock.
# ---------------------------------------------------------------------------
def start_dictation() -> None:
    """Begin a dictation recording. Mirror of ``start_recording`` but
    with a distinct HUD label so the user knows no LLM is in play.

    The state machine reuses ``recording`` — the tray only cares about
    "mic is hot", not which flow owns it.
    """
    if agent.is_blocking():
        why = "agent activo" if agent.is_active() else "en pausa"
        log(f"Blocked — ignoring dictation start ({why}).")
        return
    if audio.is_recording():
        log("Ctrl+F9 descartado: ya hay un turno en curso.")
        return
    with _pipeline_lock("start_dictation") as acquired:
        if not acquired:
            log("Ctrl+F9 descartado: ya hay un turno en curso.")
            return
        audio.start()
        set_state("recording")
        turn_start("✍️ Dictando…", "Suelta Ctrl+F9 para insertar")


def stop_dictation_and_inject() -> None:
    """Stop the dictation recording, transcribe with whisper, inject
    the text at the focused window's cursor. No LLM, no TTS, no
    session bump.

    Injection method is governed by ``settings.dictation_inject_method``:

    * ``"paste"``  — write to the clipboard, send ``ctrl+v``. Fast,
                     clobbers the clipboard.
    * everything else (default ``"type"``) — synthesise key events.
                     Slower but preserves clipboard contents.

    The state always ends on ``idle`` or ``error``. The HUD shows the
    first ~80 chars of what we injected, so the user can verify before
    it auto-closes.
    """
    with _pipeline_lock("stop_dictation_and_inject") as acquired:
        if not acquired:
            return

        t0 = time.monotonic()
        log("=== turn start: stop_dictation_and_inject ===")
        wav = audio.stop()
        if wav is None:
            log(f"dictation: no audio (t={time.monotonic()-t0:.2f}s)")
            set_state("idle")
            turn_update("🤷 Sin audio", "")
            turn_end()
            return

        set_state("thinking")
        turn_update("✍️ Transcribiendo…", "")

        t_stt0 = time.monotonic()
        try:
            text = stt.transcribe(wav)
        except Exception as e:
            log(f"dictation STT error: {e}")
            set_state("error")
            turn_update("❌ Error STT", str(e)[:120])
            turn_end()
            return
        log(f"dictation: STT done in {time.monotonic()-t_stt0:.2f}s "
            f"-> {text!r}")
        if not text:
            set_state("idle")
            turn_update("🤷 Nada que transcribir", "")
            turn_end()
            return

        method = (settings.dictation_inject_method or "type").lower()
        try:
            _inject_text(text, method)
        except Exception as e:
            log(f"dictation inject error ({method}): {e}")
            set_state("error")
            turn_update("❌ Error inyección", str(e)[:120])
            turn_end()
            return

        log(f"dictation: injected via {method} "
            f"(t_total={time.monotonic()-t0:.2f}s, len={len(text)})")
        set_state("idle")
        turn_update("✅ Insertado", text[:80])
        turn_end()


def toggle_dictation() -> None:
    """Single-binding helper symmetric to ``toggle()``."""
    if audio.is_recording():
        stop_dictation_and_inject()
    else:
        start_dictation()


def _inject_text(text: str, method: str) -> None:
    """Dispatch text injection to the chosen backend.

    ``paste`` requires both a clipboard backend that can write and an
    input backend that can send ``ctrl+v``. If clipboard write fails,
    we fall back to ``type`` rather than crashing the turn — the user
    cares about getting their text in, not about which mechanism we
    used.
    """
    if method == "paste":
        try:
            _plat.clipboard.write(text)
            desktop.press_key("ctrl+v")
            return
        except Exception as e:
            log(f"dictation: paste failed ({e}); falling back to type.")
            # Fall through to type below.
    desktop.type_text(text)

