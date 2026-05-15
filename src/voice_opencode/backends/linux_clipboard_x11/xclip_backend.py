"""X11 clipboard via ``xclip`` — placeholder."""

from __future__ import annotations

from ...platform.base import BackendError


class XclipClipboardBackend:
    def __init__(self) -> None:
        raise BackendError("xclip clipboard backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
