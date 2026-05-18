"""
Audio recording via ``arecord`` (ALSA).

We spawn ``arecord`` detached, write its PID to ``REC_PID_FILE``, and
``SIGINT`` it to stop. ``arecord`` finalises the WAV header on SIGINT,
so we get a valid file even from a forced stop.

Format is hard-coded to what whisper.cpp expects: 16 kHz mono 16-bit LE.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .logging import log
from .paths import LOGS_DIR, REC_PID_FILE, REC_WAV_FILE


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def is_recording() -> bool:
    """True if a live arecord PID is in REC_PID_FILE."""
    if not REC_PID_FILE.exists():
        return False
    try:
        pid = int(REC_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        REC_PID_FILE.unlink(missing_ok=True)
        return False


def start() -> None:
    """Begin recording. No-op if already recording.

    The caller (``pipeline.start_recording``) emits the user-facing
    log line; this layer stays silent so the message is not
    duplicated. We keep the guard as a defensive net in case any
    other code path calls us directly.
    """
    if is_recording():
        return
    REC_WAV_FILE.unlink(missing_ok=True)

    cmd = [
        "arecord",
        "-q",
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-t", "wav",
        str(REC_WAV_FILE),
    ]
    arecord_log = (LOGS_DIR / "arecord.log").open("ab")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=arecord_log,
            start_new_session=True,
        )
    finally:
        # Child inherits the fd; we can drop our copy.
        arecord_log.close()
    REC_PID_FILE.write_text(str(proc.pid))
    log(f"Recording started (pid={proc.pid}).")


def stop() -> Path | None:
    """
    Stop recording and return the WAV path. Returns ``None`` if
    no recording was running, or if the captured audio is too small
    (likely a tap rather than real speech).
    """
    if not is_recording():
        log("Not recording.")
        return None
    pid = int(REC_PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGINT)  # arecord finalises the WAV header
    except ProcessLookupError:
        pass
    # wait up to 2.5s for the process to exit
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            break
    REC_PID_FILE.unlink(missing_ok=True)

    if not REC_WAV_FILE.exists() or REC_WAV_FILE.stat().st_size < 4096:
        log("Recording too short or empty.")
        return None
    log(f"Recording stopped: {REC_WAV_FILE} ({REC_WAV_FILE.stat().st_size} bytes).")
    return REC_WAV_FILE
