"""macOS backends — placeholder.

Future home for AppleScript / NSWorkspace / Quartz calls.
The ``wire(out)`` function is the entry point for the platform layer.
"""

from __future__ import annotations

from typing import Any


def wire(out: dict[str, Any]) -> None:
    """No-op: macOS backends not implemented; Null* stay in place."""
    return
