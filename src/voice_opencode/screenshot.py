"""
Screenshot capture — backwards-compatible shim over ``platform.screen``.

The pipeline and CLI still import ``screenshot.capture()`` and
``screenshot.capture_to(path, scope=...)``. This module preserves
those signatures while delegating to the active screen backend.
"""

from __future__ import annotations

import base64
from pathlib import Path

from . import platform as _plat
from .config import settings
from .logging import log
from .paths import SCREENSHOT_FILE
from .platform.base import BackendError, NotSupportedError


def capture_to(out_path: Path, scope: str = "monitor") -> Path | None:
    """
    Take a screenshot to ``out_path``.

    scope ∈ {"monitor", "window", "all"}. Returns the path on success,
    ``None`` on failure (logged).
    """
    try:
        if scope == "monitor":
            return _plat.screen.capture_monitor(out_path)
        if scope == "window":
            return _plat.screen.capture_window(out_path)
        if scope == "all":
            return _plat.screen.capture_all(out_path)
        log(f"unknown screenshot scope: {scope!r}")
        return None
    except (BackendError, NotSupportedError) as e:
        log(f"screenshot: {e}")
        return None


def capture() -> Path | None:
    """Pipeline entry-point. Honours ``settings.screenshot``."""
    if not settings.screenshot:
        return None
    path = capture_to(SCREENSHOT_FILE, scope=settings.screenshot_scope)
    if path:
        log(f"Screenshot captured ({path.stat().st_size} bytes, "
            f"scope={settings.screenshot_scope}).")
    return path


def to_data_url(path: Path) -> str:
    """Encode a PNG as a ``data:`` URL for the opencode JSON payload."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"
