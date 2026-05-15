"""
Text-to-speech via Piper (`piper-tts` binary) piped into PipeWire/PulseAudio.

A Piper voice is two files: ``<stem>.onnx`` + ``<stem>.onnx.json``. The
JSON sidecar carries sample rate and the speaker map for multi-speaker
models. We read it to build the right ``paplay`` command and to know
whether to pass ``--speaker``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import settings
from .logging import log
from .paths import LOGS_DIR, VOICES_DIR

PIPER_BIN = os.environ.get("PIPER_BIN", "piper-tts")


# ---------------------------------------------------------------------------
# Voice metadata
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceInfo:
    path: Path
    dataset: str
    num_speakers: int
    speakers: list[str]
    sample_rate: int
    language: str

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def is_multispeaker(self) -> bool:
        return self.num_speakers > 1


def voice_info(voice: Path) -> VoiceInfo:
    """Parse the ``.onnx.json`` sidecar; fall back to safe defaults."""
    cfg = voice.with_suffix(voice.suffix + ".json")
    try:
        data = json.loads(cfg.read_text())
        return VoiceInfo(
            path=voice,
            dataset=data.get("dataset", voice.stem),
            num_speakers=int(data.get("num_speakers", 1)),
            speakers=list(data.get("speaker_id_map", {}).keys()),
            sample_rate=int(data["audio"]["sample_rate"]),
            language=data.get("language", {}).get("name_native", "?"),
        )
    except Exception as e:
        log(f"Could not parse {cfg}: {e}; using defaults.")
        return VoiceInfo(
            path=voice, dataset=voice.stem, num_speakers=1,
            speakers=[], sample_rate=22050, language="?",
        )


def resolve_voice(name: str) -> Path:
    """Find a voice .onnx in ``voices/`` by stem or substring."""
    direct = VOICES_DIR / f"{name}.onnx"
    if direct.exists():
        return direct
    matches = sorted(VOICES_DIR.glob(f"*{name}*.onnx"))
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"Voice '{name}' not found in {VOICES_DIR}. "
        f"Available: {[p.stem for p in VOICES_DIR.glob('*.onnx')]}"
    )


def list_voices() -> list[VoiceInfo]:
    """Return metadata for every voice in ``voices/``, sorted by stem."""
    return [voice_info(v) for v in sorted(VOICES_DIR.glob("*.onnx"))]


def current_voice() -> VoiceInfo:
    """The voice currently selected via config."""
    return voice_info(resolve_voice(settings.voice))


# ---------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------
# Markdown patterns we strip before TTS. Reading code aloud is awful.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`]+)`")
_MD_PREFIX_RE = re.compile(r"^[#>\-\*]+\s*", re.MULTILINE)
_WS_RE = re.compile(r"\s+")


def clean_for_tts(text: str) -> str:
    """Strip markdown/code so the spoken version sounds natural."""
    text = _FENCE_RE.sub(" (bloque de código omitido) ", text)
    text = _INLINE_RE.sub(r"\1", text)
    text = _MD_PREFIX_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def speak(text: str) -> None:
    """Synthesise ``text`` with the current voice and play it.

    A wedged ``paplay`` would otherwise freeze the pipeline at
    ``speaking`` forever; we cap with a generous timeout so the state
    machine can recover.
    """
    text = clean_for_tts(text)
    if not text:
        log("Nothing to speak.")
        return

    voice = current_voice()
    if not voice.path.exists():
        raise FileNotFoundError(f"Piper voice not found: {voice.path}")
    log(f"TTS ← {text[:80]!r}{'…' if len(text) > 80 else ''}")

    piper_cmd: list[str] = [PIPER_BIN, "--model", str(voice.path), "--output-raw"]
    if voice.is_multispeaker:
        piper_cmd += ["--speaker", str(settings.speaker_id)]

    player_cmd = [
        "paplay",
        "--raw",
        f"--rate={voice.sample_rate}",
        "--format=s16le",
        "--channels=1",
    ]

    # Open log files for the lifetime of the child processes only, then close.
    piper_log = (LOGS_DIR / "piper.log").open("ab")
    player_log = (LOGS_DIR / "player.log").open("ab")
    try:
        piper = subprocess.Popen(
            piper_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=piper_log,
        )
        player = subprocess.Popen(
            player_cmd,
            stdin=piper.stdout,
            stderr=player_log,
        )
        # Free our copy of piper.stdout so the player gets EOF on piper exit.
        if piper.stdout is not None:
            piper.stdout.close()
        assert piper.stdin is not None
        try:
            piper.stdin.write(text.encode("utf-8"))
        finally:
            piper.stdin.close()
        # 60s should cover any sane reply length.
        try:
            player.wait(timeout=60)
        except subprocess.TimeoutExpired:
            log("paplay timed out; killing TTS chain.")
            player.kill()
            piper.kill()
        try:
            piper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            piper.kill()
        if piper.returncode not in (0, None):
            log(f"piper exit code {piper.returncode}")
    finally:
        piper_log.close()
        player_log.close()
