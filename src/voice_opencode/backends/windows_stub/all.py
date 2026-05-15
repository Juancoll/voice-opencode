"""Windows backends — placeholder.

Future home for pywin32 / UIA / win32clipboard calls. The ``wire(out)``
function is the entry point for the platform layer.
"""

from __future__ import annotations

from typing import Any


def wire(out: dict[str, Any]) -> None:
    """No-op: Windows backends not implemented; Null* stay in place."""
    return
