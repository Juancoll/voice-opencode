"""X11 screen capture — placeholder (will use ``scrot`` or ``maim``)."""

from __future__ import annotations

from ...platform.base import BackendError


class X11ScreenBackend:
    def __init__(self) -> None:
        raise BackendError("X11 screen backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
