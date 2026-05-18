"""whisper.cpp STT backend (shared by Linux and Windows).

whisper.cpp ships the same ``whisper-cli`` binary on every platform —
only the path changes. Override via ``WHISPER_BIN`` env (defaults to
``whisper-cli``). Model path and language come from ``config.settings``
so the backend stays consistent with the rest of the app without
forcing callers to pass them on every call.

Output is captured stdout with ``[BLANK_AUDIO]``-style tag markers
stripped. Timeout-bounded so a stuck process can't freeze the
pipeline at ``thinking`` forever.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ...config import settings
from ...logging import log
from ...paths import MODELS_DIR
from ...platform.base import BackendError
from ...platform.capabilities import STT_TRANSCRIBE

# Tag pattern: anything between [brackets], e.g. [BLANK_AUDIO]
_TAG_RE = re.compile(r"\[[^\]]+\]")


def _whisper_bin() -> str:
    """Resolve the whisper binary; honour ``WHISPER_BIN`` override."""
    return os.environ.get("WHISPER_BIN", "whisper-cli")


class WhisperCppSTTBackend:
    """whisper.cpp transcriber. Captures stdout; strips tag markers."""

    def __init__(self) -> None:
        if not shutil.which(_whisper_bin()):
            raise BackendError(
                f"whisper-cli not on PATH (looked for {_whisper_bin()!r}; "
                f"set WHISPER_BIN if it lives elsewhere)"
            )

    def capabilities(self) -> frozenset[str]:
        return frozenset({STT_TRANSCRIBE})

    def transcribe(self, wav_path: Path) -> str:
        """Run whisper-cli on ``wav_path`` and return cleaned text."""
        model = MODELS_DIR / settings.whisper_model
        if not model.exists():
            raise BackendError(f"Whisper model not found: {model}")
        cmd = [
            _whisper_bin(),
            "-m", str(model),
            "-l", settings.whisper_lang,
            "-nt",         # no timestamps
            "-np",         # no progress prints
            "-f", str(wav_path),
        ]
        log(f"Transcribing… ({' '.join(cmd)})")
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired as e:
            log(f"whisper timed out after {e.timeout}s")
            raise BackendError("whisper timeout") from e
        if res.returncode != 0:
            log(f"whisper stderr: {res.stderr}")
            raise BackendError(
                f"whisper-cli failed (rc={res.returncode})"
            )
        text = _TAG_RE.sub("", res.stdout).strip()
        log(f"STT → {text!r}")
        return text
