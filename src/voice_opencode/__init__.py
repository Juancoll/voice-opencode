"""
voice-opencode: push-to-talk voice interface for opencode.

Public API (stable):
    from voice_opencode import config, paths, pipeline, desktop, tts, stt
    pipeline.run_after_stop()
    desktop.type_text("hola")

CLI entry-point: `python -m voice_opencode` or the `voice` wrapper.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]
