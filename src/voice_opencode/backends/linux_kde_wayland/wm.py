"""KDE Wayland (KWin) window manager — placeholder.

KWin's scripting/D-Bus IPC will live here. Not implemented yet; the
factory raises so the platform layer wires NullWindowManager instead.
"""

from __future__ import annotations

from ...platform.base import BackendError


class KdeWaylandWindowManager:
    def __init__(self) -> None:
        raise BackendError("KDE Wayland WM backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
