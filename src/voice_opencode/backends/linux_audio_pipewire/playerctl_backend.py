"""MPRIS media control via ``playerctl`` — placeholder.

Real implementation arrives in Phase H.
"""

from __future__ import annotations

from ...platform.base import BackendError


class PlayerctlMediaBackend:
    def __init__(self) -> None:
        raise BackendError("playerctl media backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
