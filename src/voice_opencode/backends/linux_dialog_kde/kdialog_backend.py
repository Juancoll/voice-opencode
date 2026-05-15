"""``kdialog`` blocking dialogs — placeholder.

Real implementation arrives in Phase C.
"""

from __future__ import annotations

from ...platform.base import BackendError


class KdialogBackend:
    def __init__(self) -> None:
        raise BackendError("kdialog dialog backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
