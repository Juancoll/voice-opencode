"""``arecord`` (ALSA) recorder backend.

Concrete implementation of ``RecorderBackend`` for any Linux box with
``alsa-utils`` installed. Format is hard-coded to whisper.cpp's
expectation: 16 kHz mono 16-bit LE.

We spawn ``arecord`` detached, write its PID to ``REC_PID_FILE``, and
``SIGINT`` it to stop. ``arecord`` finalises the WAV header on SIGINT
so we get a valid file even from a forced stop.

This file is the Linux-native sibling of any future Windows recorder
(WASAPI via ``sounddevice``). Both must agree on:

* same WAV format on disk (16 kHz mono S16_LE);
* same minimum-size threshold for "too short / empty";
* same identity-persistence semantics (the backend may be
  re-instantiated between ``start`` and ``stop`` because each F9
  invocation is its own process).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ...logging import log
from ...paths import LOGS_DIR, REC_PID_FILE
from ...platform.base import BackendError
from ...platform.capabilities import (
    RECORDER_IS_RECORDING,
    RECORDER_START,
    RECORDER_STOP,
)

# 4 KB ≈ 0.13 s of 16 kHz mono S16_LE — anything shorter is a tap, not speech.
_MIN_USABLE_BYTES = 4096


class ArecordRecorderBackend:
    def __init__(self) -> None:
        if not shutil.which("arecord"):
            raise BackendError("arecord not on PATH (install alsa-utils)")

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {RECORDER_START, RECORDER_STOP, RECORDER_IS_RECORDING}
        )

    # ------------------------------------------------------------------
    def is_recording(self) -> bool:
        """True if a live arecord PID is in ``REC_PID_FILE``."""
        if not REC_PID_FILE.exists():
            return False
        try:
            pid = int(REC_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            REC_PID_FILE.unlink(missing_ok=True)
            return False

    def start(self, out_path: Path) -> None:
        """Begin recording to ``out_path``. No-op if already recording.

        The caller (``pipeline.start_recording``) emits the user-facing
        log line; this layer stays silent so the message is not
        duplicated. We keep the guard as a defensive net in case any
        other code path calls us directly.
        """
        if self.is_recording():
            return
        out_path.unlink(missing_ok=True)

        # -d 120: hard cap at 2 min so a missed "stop" event (lost release
        # bind, crash, suspend) cannot leave a zombie arecord eating disk.
        # arecord finalises the WAV header cleanly on its own timeout; the
        # orphan rec.pid is reaped by is_recording() on next invocation.
        cmd = [
            "arecord",
            "-q",
            "-d", "120",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "wav",
            str(out_path),
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

    def stop(self, out_path: Path) -> Path | None:
        """Stop recording. Returns ``out_path`` on success, ``None``
        if nothing was running or the captured audio is too small."""
        if not self.is_recording():
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

        if not out_path.exists() or out_path.stat().st_size < _MIN_USABLE_BYTES:
            log("Recording too short or empty.")
            return None
        log(f"Recording stopped: {out_path} ({out_path.stat().st_size} bytes).")
        return out_path
