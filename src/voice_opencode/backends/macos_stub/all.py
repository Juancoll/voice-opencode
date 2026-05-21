"""macOS backends — placeholder.

Entry point for the platform layer when ``detect_platform()`` returns
``PLATFORM_MACOS``. Currently a no-op: every capability falls through
to the ``NullBackend`` shim from ``platform/null.py`` so the pipeline
fails loudly ("not implemented") instead of pretending to work.

To bring macOS online, follow ``_ai/SKILLS/adding-a-backend.md``.
Multi-OS scope and the per-capability roadmap live in ADR-0031.
"""

from __future__ import annotations

from typing import Any


def wire(out: dict[str, Any]) -> None:
    """No-op: macOS backends not implemented; Null* stay in place."""
    return
