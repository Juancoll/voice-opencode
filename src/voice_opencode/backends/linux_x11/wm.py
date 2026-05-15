"""X11 window manager — placeholder.

Will be implemented over ``xdotool`` / ``wmctrl`` / ``xprop``. Not
done yet; raises so the platform layer wires NullWindowManager.
"""

from __future__ import annotations

from ...platform.base import BackendError


class X11WindowManager:
    def __init__(self) -> None:
        raise BackendError("X11 WM backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
