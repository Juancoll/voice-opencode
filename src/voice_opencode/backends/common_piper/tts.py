"""Piper TTS backend (shared by Linux and Windows).

Piper ships the same CLI on every platform — only the binary path
changes. Override via the ``PIPER_BIN`` environment variable; the
default ``piper-tts`` works for the Arch package, the GitHub release
binaries (when extracted to ``vendor/piper/piper`` or onto PATH),
and the Windows ``piper.exe`` once that platform is wired.

Synthesis is to a WAV file, *not* a streamed pipe. The decoupling
from the audio player is deliberate (ADR-0023) so the same engine
can drive paplay on Linux and WASAPI on Windows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ...logging import log
from ...paths import LOGS_DIR, VOICES_DIR
from ...platform.base import BackendError
from ...platform.capabilities import TTS_LIST_VOICES, TTS_SYNTHESIZE


def _piper_bin() -> str:
    """Resolve the piper binary; honour ``PIPER_BIN`` override."""
    return os.environ.get("PIPER_BIN", "piper-tts")


def _is_multispeaker(voice: Path) -> bool:
    cfg = voice.with_suffix(voice.suffix + ".json")
    try:
        data = json.loads(cfg.read_text())
        return int(data.get("num_speakers", 1)) > 1
    except Exception:
        return False


class PiperTTSBackend:
    """Piper synthesiser. Writes a WAV; never plays it (use PlayerBackend)."""

    def __init__(self) -> None:
        if not shutil.which(_piper_bin()):
            raise BackendError(
                f"piper binary not on PATH (looked for {_piper_bin()!r}; "
                f"set PIPER_BIN if it lives elsewhere)"
            )

    def capabilities(self) -> frozenset[str]:
        return frozenset({TTS_SYNTHESIZE, TTS_LIST_VOICES})

    def list_voices(self) -> list[str]:
        """Stems of every ``.onnx`` file in ``voices/``, sorted."""
        return sorted(p.stem for p in VOICES_DIR.glob("*.onnx"))

    def synthesize(
        self,
        text: str,
        voice: str,
        out_wav: Path,
        *,
        speaker_id: int | None = None,
    ) -> Path:
        """Synthesise ``text`` with ``voice`` (stem or absolute path)
        into ``out_wav``. Returns the path."""
        voice_path = self._resolve(voice)
        if not voice_path.exists():
            raise BackendError(f"Piper voice not found: {voice_path}")

        cmd: list[str] = [
            _piper_bin(),
            "--model", str(voice_path),
            "--output_file", str(out_wav),
        ]
        if speaker_id is not None and _is_multispeaker(voice_path):
            cmd += ["--speaker", str(speaker_id)]

        piper_log = (LOGS_DIR / "piper.log").open("ab")
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=piper_log,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log("piper-tts timed out during synthesis")
            raise BackendError("piper-tts synthesis timeout") from None
        finally:
            piper_log.close()

        if proc.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size == 0:
            log(f"piper exit code {proc.returncode}; no WAV produced")
            raise BackendError(
                f"piper-tts failed (rc={proc.returncode}); see logs/piper.log"
            )
        return out_wav

    # ------------------------------------------------------------------
    def _resolve(self, voice: str) -> Path:
        """Stem ('es_AR-daniela-high'), substring, or absolute path."""
        p = Path(voice)
        if p.is_absolute() and p.exists():
            return p
        direct = VOICES_DIR / f"{voice}.onnx"
        if direct.exists():
            return direct
        matches = sorted(VOICES_DIR.glob(f"*{voice}*.onnx"))
        if matches:
            return matches[0]
        raise BackendError(
            f"Voice {voice!r} not found in {VOICES_DIR}. "
            f"Available: {[p.stem for p in VOICES_DIR.glob('*.onnx')]}"
        )
