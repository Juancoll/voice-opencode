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
import time
from collections.abc import Iterator

from . import agent, audio, stt, tts
from .logging import log
from .notify import notify
from .opencode_client import Session
from .paths import PIPELINE_LOCK_FILE, ensure_dirs
from .screenshot import capture
from .state import set_state

# A long TTS reply caps at ~60s thanks to the paplay watchdog in
# tts.speak(); 120s gives us 2x headroom plus STT + opencode latency.
_LOCK_TTL_S: float = 120.0


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
def _pipeline_lock(action: str, *, notify_on_busy: bool = True) -> Iterator[bool]:
    """Yield True if the cross-process pipeline lock was acquired.

    On contention with a *live, fresh* holder we log and (unless
    ``notify_on_busy`` is False) emit a low-urgency notify, then yield
    False. ``start_recording`` passes ``notify_on_busy=False`` so a
    held F9 (Hyprland key-repeat) or an accidental double-tap during
    recording is dropped silently — popping a toast on every keypress
    is worse than the bug it fixes. ``stop_and_run`` keeps the toast
    so the user gets feedback if releasing F9 mid-reply does nothing.

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
            if notify_on_busy:
                notify(
                    "⏳ Ocupado",
                    "Esperá a que termine el turno actual",
                    urgency="low",
                )
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
        notify(f"⏸  {why.capitalize()}", "F9 ignorado", urgency="low")
        return
    if audio.is_recording():
        log("F9 descartado: ya hay un turno en curso (grabación o respuesta).")
        return
    with _pipeline_lock("start_recording", notify_on_busy=False) as acquired:
        if not acquired:
            log("F9 descartado: ya hay un turno en curso (grabación o respuesta).")
            return
        audio.start()
        set_state("recording")
        notify("🎙 Grabando…", "Suelta F9 para enviar")


def stop_and_run() -> None:
    """
    Stop recording and run the full pipeline. The pipeline is linear
    (no early return on partial success) but each step has its own
    error path so the state always lands on ``idle`` or ``error``.

    Holds the cross-process pipeline lock for the whole STT → opencode
    → TTS run so a second F9 release while we're speaking is dropped
    instead of racing a second TTS on top of the first.
    """
    with _pipeline_lock("stop_and_run") as acquired:
        if not acquired:
            return

        wav = audio.stop()
        if wav is None:
            set_state("idle")
            return

        set_state("thinking")

        # 1. Transcribe
        try:
            text = stt.transcribe(wav)
        except Exception as e:
            log(f"STT error: {e}")
            notify("❌ Error STT", str(e), urgency="critical")
            set_state("error")
            return
        if not text:
            notify("🤷 Nada que transcribir", "")
            set_state("idle")
            return

        notify("🧠 Pensando…", text[:80])

        # 2. Ask opencode (with optional screenshot)
        shot = capture()
        try:
            reply = Session.get_or_create().ask(text, screenshot=shot)
        except Exception as e:
            log(f"opencode error: {e}")
            notify("❌ opencode", str(e), urgency="critical")
            set_state("error")
            return
        if not reply:
            notify("🤐 Sin respuesta", "")
            set_state("idle")
            return

        notify("🔊 Respondiendo", reply[:80])

        # 3. Speak
        set_state("speaking")
        try:
            tts.speak(reply)
        except Exception as e:
            log(f"TTS error: {e}")
            notify("❌ Error TTS", str(e), urgency="critical")
            set_state("error")
            return

        set_state("idle")


def toggle() -> None:
    """Push-to-talk single-binding helper: stop+run if recording, else start.

    Delegates to ``stop_and_run`` / ``start_recording``; the lock is
    acquired inside those, so toggle() itself is a thin dispatcher.
    """
    if audio.is_recording():
        stop_and_run()
    else:
        start_recording()

