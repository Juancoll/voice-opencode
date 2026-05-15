"""
Backwards-compatible shim over the platform layer.

The old top-level ``voice_opencode.desktop`` module exposed plain
functions (``type_text``, ``press_key``, ``click_mouse``, ``move_mouse``,
``focused_window``). Existing code (CLI, MCP server) imports these.

Now that we have a proper platform abstraction, this module just
delegates to ``voice_opencode.platform.input`` (and ``platform.wm``
for ``focused_window``). The signatures are preserved verbatim so no
caller has to change.

New code should prefer::

    from voice_opencode.platform import input as input_be, wm
    input_be.type_text("hi")
    wm.active_window()
"""

from __future__ import annotations

import os
from typing import Any, Final

from . import platform as _plat
from .platform.base import BackendError, NotSupportedError

# Preserve the legacy public symbol so old imports keep working.
YDOTOOL_SOCKET: Final[str] = os.environ.get(
    "YDOTOOL_SOCKET",
    f"/run/user/{os.getuid()}/.ydotool_socket",
)


def _swallow(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn``; on backend errors, log and return None to match
    the old "best-effort" semantics of this module.
    """
    from .logging import log
    try:
        return fn(*args, **kwargs)
    except (BackendError, NotSupportedError) as e:
        log(f"desktop: {e}")
        return None


def type_text(text: str, *, delay_ms: int = 12) -> None:
    _swallow(_plat.input.type_text, text, delay_ms=delay_ms)


def press_key(combo: str) -> None:
    _swallow(_plat.input.press_key, combo)


def move_mouse(x: int, y: int, *, absolute: bool = True) -> None:
    _swallow(_plat.input.move_mouse, x, y, absolute=absolute)


def click_mouse(
    button: str = "left",
    x: int | None = None,
    y: int | None = None,
) -> None:
    _swallow(_plat.input.click_mouse, button, x, y)


def focused_window() -> dict[str, Any]:
    """Return the active window as a dict, ``{}`` on failure.

    Returns the platform-neutral ``Window.to_dict()`` shape now —
    callers that relied on Hyprland-specific keys (``stableId`` etc.)
    will find them under ``extra``.
    """
    win = _swallow(_plat.wm.active_window)
    return win.to_dict() if win else {}
