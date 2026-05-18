"""
Platform-neutral data types.

These dataclasses are the *only* shapes that cross the boundary between
backends and consumers (MCP server, CLI, tray). Backends translate
their native shapes into these; consumers never see backend-specific
fields.

Keep them frozen and serialisable. Adding a field is fine; removing or
renaming one is a breaking change for every backend.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Rect:
    """A pixel rectangle. ``x, y`` is the top-left corner."""

    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class Monitor:
    """A physical (or virtual) display."""

    id: int                  # backend-local id
    name: str                # e.g. "DP-3", "HDMI-A-1", "\\\\.\\DISPLAY1"
    rect: Rect
    scale: float = 1.0
    focused: bool = False
    primary: bool = False
    refresh_hz: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rect"] = self.rect.to_dict()
        return d


@dataclass(frozen=True)
class Workspace:
    """A virtual desktop / workspace.

    Some platforms have no native workspaces (e.g. plain X11 with single
    desktop). In that case backends should return a single Workspace
    with id=0, name="default".
    """

    id: int
    name: str
    monitor_id: int = -1     # -1 ⇒ unknown / not bound to a monitor
    window_count: int = 0
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Window:
    """A top-level application window.

    ``id`` is the backend's stable identifier (Hyprland address,
    X11 window id, HWND, etc.) as a string so we can pass it back
    in tool calls without precision loss.
    """

    id: str
    pid: int
    app_id: str              # class / WM_CLASS / bundle id / exe name
    title: str
    rect: Rect
    monitor_id: int = -1
    workspace_id: int = -1
    focused: bool = False
    floating: bool = False
    fullscreen: bool = False
    minimized: bool = False
    pinned: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rect"] = self.rect.to_dict()
        return d


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OcrMatch:
    """A single text match found by the OCR backend.

    ``text`` is the joined text of one or more consecutive words on
    the same line that, concatenated, matched the search needle (or
    just one word if ``find_text`` was called without a needle).
    ``rect`` is the union of those words' bounding boxes in image
    pixel coordinates (top-left origin). ``confidence`` is 0-100,
    the minimum confidence across the joined words.
    """

    text: str
    rect: Rect
    confidence: float        # 0.0–100.0 (Tesseract scale)
    line: int = -1           # block_num × 1000 + line_num; for ordering
    word_index: int = -1     # first word's index within the line

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rect"] = self.rect.to_dict()
        return d


# ---------------------------------------------------------------------------
# Input enumerations
# ---------------------------------------------------------------------------
MOUSE_BUTTONS = ("left", "right", "middle")
"""Canonical mouse button names. Backends translate to native codes."""

KEY_MODIFIERS = ("ctrl", "shift", "alt", "super", "meta")
"""Canonical modifier names accepted in key combos like 'ctrl+shift+a'.

'super' and 'meta' are aliases on most platforms (Win key / Cmd key).
Backends MUST accept both and translate as appropriate.
"""
