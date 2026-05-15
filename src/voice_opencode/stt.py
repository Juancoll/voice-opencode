"""
Speech-to-text via whisper.cpp (`whisper-cli` binary).

We invoke the CLI rather than binding to libwhisper because the official
Arch package ships only the binary. Output is captured stdout, with
``[BLANK_AUDIO]``-style markers stripped.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .config import settings
from .logging import log
from .paths import MODELS_DIR

WHISPER_BIN = os.environ.get("WHISPER_BIN", "whisper-cli")

# Tag pattern: anything between [brackets], e.g. [BLANK_AUDIO]
_TAG_RE = re.compile(r"\[[^\]]+\]")


def model_path() -> Path:
    """Resolve the configured whisper model file."""
    return MODELS_DIR / settings.whisper_model


def transcribe(wav: Path) -> str:
    """Run whisper-cli on ``wav`` and return cleaned text."""
    model = model_path()
    if not model.exists():
        raise FileNotFoundError(f"Whisper model not found: {model}")
    cmd = [
        WHISPER_BIN,
        "-m", str(model),
        "-l", settings.whisper_lang,
        "-nt",         # no timestamps
        "-np",         # no progress prints
        "-f", str(wav),
    ]
    log(f"Transcribing… ({' '.join(cmd)})")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        log(f"whisper timed out after {e.timeout}s")
        raise RuntimeError("whisper timeout") from e
    if res.returncode != 0:
        log(f"whisper stderr: {res.stderr}")
        raise RuntimeError("whisper failed")
    text = _TAG_RE.sub("", res.stdout).strip()
    log(f"STT → {text!r}")
    return text
