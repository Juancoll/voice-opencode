"""
Audio recording — shim over ``platform.recorder``.

Backwards-compatible thin layer: existing callers do
``from .audio import start, stop, is_recording`` with no path argument
and get the canonical ``REC_WAV_FILE`` written / read for them. The
real subprocess invocation (``arecord`` on Linux, ``sounddevice`` on
Windows once that backend exists) lives in the wired
``RecorderBackend``.

Keeping a shim instead of rewriting every call site means:

* ``pipeline.py`` keeps its short imports;
* the ``REC_WAV_FILE`` path stays a project-wide singleton (the
  pipeline lock + STT + tests all reference it);
* if a future caller wants to record to a different file it can call
  ``platform.recorder.start(custom_path)`` directly.
"""

from __future__ import annotations

from pathlib import Path

from . import platform as _plat
from .paths import REC_WAV_FILE


def is_recording() -> bool:
    """True iff the wired recorder reports an active capture."""
    return bool(_plat.recorder.is_recording())


def start() -> None:
    """Begin recording to the canonical ``REC_WAV_FILE``.

    The recorder backend itself is responsible for the "already
    recording" guard and for emitting any subprocess-level log line.
    """
    _plat.recorder.start(REC_WAV_FILE)


def stop() -> Path | None:
    """Stop recording. Returns the WAV path on success, ``None`` if
    nothing was running or the captured audio was too short."""
    return _plat.recorder.stop(REC_WAV_FILE)
