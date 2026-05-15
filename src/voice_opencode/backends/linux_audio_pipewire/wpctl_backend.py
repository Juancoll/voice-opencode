"""PipeWire / WirePlumber audio control via ``wpctl`` — placeholder.

Real implementation arrives in Phase H.
"""

from __future__ import annotations

from ...platform.base import BackendError


class WpctlAudioBackend:
    def __init__(self) -> None:
        raise BackendError("wpctl audio backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
