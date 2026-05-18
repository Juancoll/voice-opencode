"""
Backend Protocols (PEP 544).

These are the contracts every backend must satisfy. The MCP server,
CLI, and tray import *only* from this module (and ``types`` /
``capabilities``); they never know which concrete backend is in use.

A backend is free to implement only a subset of methods. It declares
the methods it actually supports via ``capabilities()``. Methods that
aren't declared as supported MAY raise ``NotSupportedError``;
consumers must check the capability set before calling.

Why Protocols and not ABCs:

* No inheritance ceremony for backends.
* Tests can supply plain dicts/dataclasses that quack right.
* Easier mypy stories across heterogeneous backend trees.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .types import Monitor, OcrMatch, Window, Workspace


class NotSupportedError(RuntimeError):
    """Raised when a backend method isn't supported on this platform.

    Consumers SHOULD check ``capabilities()`` before calling, but
    backends MUST still raise this rather than silently no-op.
    """


class BackendError(RuntimeError):
    """Raised when a backend operation fails (subprocess error, etc.).

    The message should include enough context to debug (the underlying
    command, exit code, stderr).
    """


# ---------------------------------------------------------------------------
# Window manager
# ---------------------------------------------------------------------------
@runtime_checkable
class WindowManager(Protocol):
    """Window and workspace control."""

    def capabilities(self) -> frozenset[str]: ...

    # Read
    def list_windows(self) -> list[Window]: ...
    def find_windows(self, needle: str, limit: int = 10) -> list[Window]: ...
    def active_window(self) -> Window | None: ...
    def list_workspaces(self) -> list[Workspace]: ...
    def active_workspace(self) -> Workspace | None: ...

    # Write
    def focus_window(self, window_id: str) -> None: ...
    def close_window(self, window_id: str) -> None: ...
    def move_window(self, window_id: str, x: int, y: int) -> None: ...
    def resize_window(self, window_id: str, w: int, h: int) -> None: ...
    def toggle_floating(self, window_id: str) -> None: ...
    def toggle_fullscreen(self, window_id: str | None = None) -> None: ...
    def minimize_window(self, window_id: str) -> None: ...
    def switch_workspace(self, workspace: str | int) -> None: ...
    def move_window_to_workspace(
        self, window_id: str, workspace: str | int,
    ) -> None: ...
    def send_workspace_to_monitor(self, target: str | int) -> None: ...


# ---------------------------------------------------------------------------
# Input synthesis (keyboard + mouse)
# ---------------------------------------------------------------------------
@runtime_checkable
class InputBackend(Protocol):
    """Synthesise keyboard and pointer events."""

    def capabilities(self) -> frozenset[str]: ...

    def type_text(self, text: str, delay_ms: int = 12) -> None: ...
    def press_key(self, combo: str) -> None: ...
    def move_mouse(self, x: int, y: int, absolute: bool = True) -> None: ...
    def click_mouse(
        self, button: str = "left",
        x: int | None = None, y: int | None = None,
    ) -> None: ...
    def scroll_mouse(self, dx: int, dy: int) -> None: ...
    def drag_mouse(
        self, x1: int, y1: int, x2: int, y2: int,
        button: str = "left",
    ) -> None: ...


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------
@runtime_checkable
class ScreenBackend(Protocol):
    """Enumerate displays and grab pixels."""

    def capabilities(self) -> frozenset[str]: ...

    def list_monitors(self) -> list[Monitor]: ...
    def focused_monitor(self) -> Monitor | None: ...

    def capture_monitor(
        self, out_path: Path, monitor: Monitor | str | None = None,
    ) -> Path: ...
    def capture_window(
        self, out_path: Path, window_id: str | None = None,
    ) -> Path: ...
    def capture_region(
        self, out_path: Path, x: int, y: int, w: int, h: int,
    ) -> Path: ...
    def capture_all(self, out_path: Path) -> Path: ...


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------
@runtime_checkable
class ClipboardBackend(Protocol):
    """System clipboard and (where applicable) primary selection."""

    def capabilities(self) -> frozenset[str]: ...

    def read(self) -> str: ...
    def write(self, text: str) -> None: ...
    def read_primary(self) -> str: ...
    def write_primary(self, text: str) -> None: ...


# ---------------------------------------------------------------------------
# Notifications & dialogs
# ---------------------------------------------------------------------------
@runtime_checkable
class NotifyBackend(Protocol):
    """Non-blocking system notifications."""

    def capabilities(self) -> frozenset[str]: ...

    def show(
        self, title: str, body: str = "", urgency: str = "normal",
    ) -> None: ...


@runtime_checkable
class DialogBackend(Protocol):
    """Blocking, modal dialogs that ask the user something."""

    def capabilities(self) -> frozenset[str]: ...

    def confirm(self, message: str, title: str = "Confirm") -> bool: ...
    def ask_text(
        self, prompt: str, default: str = "", title: str = "Input",
    ) -> str | None: ...
    def ask_choice(
        self, prompt: str, choices: list[str], title: str = "Choose",
    ) -> str | None: ...


# ---------------------------------------------------------------------------
# Audio / media
# ---------------------------------------------------------------------------
@runtime_checkable
class AudioBackend(Protocol):
    """Volume + mute control. Per-app routing is out of scope here."""

    def capabilities(self) -> frozenset[str]: ...

    def volume_get(self) -> float: ...                # 0.0–1.0
    def volume_set(self, level: float) -> None: ...   # clamped 0.0–1.0
    def mute_toggle(self) -> bool: ...                # returns new muted state
    def mic_mute_toggle(self) -> bool: ...


@runtime_checkable
class MediaBackend(Protocol):
    """MPRIS-style media transport control."""

    def capabilities(self) -> frozenset[str]: ...

    def play_pause(self) -> None: ...
    def next(self) -> None: ...
    def prev(self) -> None: ...
    def status(self) -> dict[str, Any]: ...           # {player, status, title, artist}


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
@runtime_checkable
class AppLauncher(Protocol):
    """Launch, enumerate, and kill desktop apps."""

    def capabilities(self) -> frozenset[str]: ...

    def list_installed(self) -> list[dict[str, Any]]: ...
    def list_running(self) -> list[dict[str, Any]]: ...
    def launch(self, app_id_or_cmd: str) -> int: ...   # returns pid
    def kill(self, pid_or_app_id: int | str) -> None: ...


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
@runtime_checkable
class ShellBackend(Protocol):
    """Run shell commands with rails (timeout, blocklist, dry-run)."""

    def capabilities(self) -> frozenset[str]: ...

    def run(
        self,
        cmd: str | list[str],
        cwd: str | None = None,
        timeout: float = 30.0,
        dry_run: bool = True,
    ) -> dict[str, Any]: ...                          # {rc, stdout, stderr, dry_run}


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
@runtime_checkable
class OCRBackend(Protocol):
    """Optical character recognition on a raster image.

    Intentionally decoupled from ``ScreenBackend``: a backend may be
    present without the other. The caller is responsible for producing
    the image (typically via ``screen.capture_*``). This keeps OCR
    composable — it works on any PNG, not just screenshots.

    Higher-level convenience ("find this text on screen *right now*")
    lives in the MCP server / CLI as a thin composition of
    ``screen.capture_monitor`` + ``ocr.find_text``.
    """

    def capabilities(self) -> frozenset[str]: ...

    def find_text(
        self,
        image_path: Path,
        needle: str,
        languages: tuple[str, ...] | None = None,
        min_confidence: float = 50.0,
    ) -> list[OcrMatch]:
        """Find every occurrence of ``needle`` in ``image_path``.

        - Case-insensitive substring match on the joined text of one or
          more consecutive words on the same OCR line.
        - ``languages`` overrides the configured default
          (e.g. ``("spa", "eng")``).
        - Words below ``min_confidence`` (0-100, Tesseract scale) are
          dropped before matching.
        - Returns matches in reading order (top-to-bottom, left-to-right
          within a line). Empty list if nothing matches.
        """
        ...

    def dump_text(
        self,
        image_path: Path,
        languages: tuple[str, ...] | None = None,
        min_confidence: float = 50.0,
    ) -> list[OcrMatch]:
        """Return every detected word with bbox + confidence.

        Same as ``find_text`` with an empty needle: useful for debugging
        and for the agent when it wants a screen reader-style dump.
        """
        ...
