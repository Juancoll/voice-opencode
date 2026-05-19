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

from . import agent, audio, stt, tts
from . import state as state_mod
from .logging import log
from .notify import turn_end, turn_start, turn_update
from .opencode_client import Session
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

    1. ``Session.abort()`` so the opencode server stops the runaway
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
        sid = Session.current_id()
        if sid:
            Session(sid).abort()
            log(f"Cancel: aborted opencode session {sid}.")
    except Exception as e:  # pragma: no cover — defensive
        log(f"Cancel: session.abort() failed: {e}")

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

        wav = audio.stop()
        if wav is None:
            set_state("idle")
            turn_update("🤷 Sin audio", "")
            turn_end()
            return

        set_state("thinking")
        turn_update("🧠 Transcribiendo…", "")

        # 1. Transcribe
        try:
            text = stt.transcribe(wav)
        except Exception as e:
            log(f"STT error: {e}")
            set_state("error")
            turn_update("❌ Error STT", str(e)[:120])
            turn_end()
            return
        if not text:
            set_state("idle")
            turn_update("🤷 Nada que transcribir", "")
            turn_end()
            return

        turn_update("🧠 Pensando…", text[:80])

        # 2. Ask opencode (with optional screenshot)
        shot = capture()
        session = Session.get_or_create()
        try:
            reply = session.ask(text, screenshot=shot)
        except Exception as e:
            log(f"opencode error: {e}")
            # Make sure the server stops the runaway tool loop even if
            # ask() already called abort() on Timeout — extra POST is
            # cheap and idempotent.
            session.abort()
            set_state("error")
            turn_update("❌ opencode", str(e)[:120])
            turn_end()
            return
        if not reply:
            set_state("idle")
            turn_update("🤐 Sin respuesta", "")
            turn_end()
            return

        turn_update("🔊 Respondiendo", reply[:80])

        # 3. Speak
        set_state("speaking")
        try:
            tts.speak(reply)
        except Exception as e:
            log(f"TTS error: {e}")
            set_state("error")
            turn_update("❌ Error TTS", str(e)[:120])
            turn_end()
            return

        set_state("idle")
        turn_end()


def toggle() -> None:
    """Push-to-talk single-binding helper: stop+run if recording, else start.

    Delegates to ``stop_and_run`` / ``start_recording``; the lock is
    acquired inside those, so toggle() itself is a thin dispatcher.
    """
    if audio.is_recording():
        stop_and_run()
    else:
        start_recording()

