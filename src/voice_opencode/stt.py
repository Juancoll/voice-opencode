"""
Speech-to-text — thin shim over ``platform.stt``.

The real whisper.cpp invocation lives in
``backends/common_whisper_cpp/stt.py`` (cross-platform: same CLI on
Linux and Windows, only the binary path differs via ``WHISPER_BIN``).
This module exists solely so that ``pipeline.py`` (and any future
caller) can keep doing ``from . import stt; stt.transcribe(wav)``
without caring which backend is wired underneath.
"""

from __future__ import annotations

from pathlib import Path

from . import platform as _plat


def transcribe(wav: Path) -> str:
    """Delegate to the active STT backend."""
    return _plat.stt.transcribe(wav)
