"""``zenity`` blocking dialogs — placeholder fallback.

Used when ``kdialog`` is not installed. Real implementation in Phase C.
"""

from __future__ import annotations

from ...platform.base import BackendError


class ZenityBackend:
    def __init__(self) -> None:
        raise BackendError("zenity dialog backend not implemented yet")

    def capabilities(self) -> frozenset[str]:    # pragma: no cover
        return frozenset()
