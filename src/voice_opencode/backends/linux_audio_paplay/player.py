"""``paplay`` (PulseAudio/PipeWire) WAV player backend.

Concrete ``PlayerBackend``: blocks until the given WAV finishes
playing, capped by ``timeout_s`` so a wedged ``paplay`` (which we
saw in production: see ADR pending, the 60 s watchdog from the
``speak()`` pipeline) can be killed and the state machine recovers.

Stderr goes to ``logs/player.log`` so the desktop-audio failure
mode is grep-able after the fact.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ...logging import log
from ...paths import LOGS_DIR
from ...platform.base import BackendError
from ...platform.capabilities import PLAYER_PLAY_WAV


class PaplayPlayerBackend:
    def __init__(self) -> None:
        if not shutil.which("paplay"):
            raise BackendError("paplay not on PATH (install libpulse / pipewire-pulse)")

    def capabilities(self) -> frozenset[str]:
        return frozenset({PLAYER_PLAY_WAV})

    def play_wav(self, wav_path: Path, timeout_s: float = 60.0) -> None:
        """Block until the WAV finishes; kill on timeout."""
        if not wav_path.exists():
            raise BackendError(f"WAV not found: {wav_path}")
        player_log = (LOGS_DIR / "player.log").open("ab")
        try:
            proc = subprocess.Popen(
                ["paplay", str(wav_path)],
                stdout=subprocess.DEVNULL,
                stderr=player_log,
            )
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                log(f"paplay timed out after {timeout_s}s; killing.")
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            player_log.close()
