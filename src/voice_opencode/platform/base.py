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

    # Optional: replace-id support for "live" notifications that update
    # in place instead of stacking. Backends that don't support it
    # (e.g. macOS Notification Center) should raise NotSupportedError;
    # callers MUST gate on ``NOTIFY_REPLACE``.
    def show_persistent(
        self,
        title: str,
        body: str = "",
        urgency: str = "normal",
        replace_id: int = 0,
    ) -> int:
        """Show or update a notification. Returns the notification id.

        If ``replace_id`` is nonzero, the existing notification with
        that id is updated in place; otherwise a new one is created.
        Use ``dismiss(id)`` to close it explicitly.
        """
        ...

    def dismiss(self, notification_id: int) -> None:
        """Close a notification by id. No-op if already gone."""
        ...


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


# ---------------------------------------------------------------------------
# Voice pipeline surfaces (Phase A — see ADR-0023)
# ---------------------------------------------------------------------------
@runtime_checkable
class RecorderBackend(Protocol):
    """Microphone capture to a WAV file.

    Implementations must:

    * spawn a detached process / thread, return immediately from ``start``;
    * write the WAV to ``out_path`` such that ``stop()`` leaves a
      valid, finalised file (whisper.cpp must be able to read it);
    * persist the recorder's identity (PID / handle) somewhere the
      backend can find it on the next ``stop()`` call, since the
      backend may be re-instantiated between ``start`` and ``stop``
      (each F9 keypress is its own Python process);
    * return a *minimum size threshold*-aware result from ``stop()``:
      if the recording is too small to be real speech (tap rather
      than utterance) return ``None`` — the caller treats ``None``
      as "no usable audio".

    The format is fixed to what whisper.cpp expects: 16 kHz mono
    16-bit LE. Hardcoded on purpose: changing it requires changing
    the STT side too.
    """

    def capabilities(self) -> frozenset[str]: ...

    def is_recording(self) -> bool: ...
    def start(self, out_path: Path) -> None: ...
    def stop(self, out_path: Path) -> Path | None: ...


@runtime_checkable
class PlayerBackend(Protocol):
    """Synchronous WAV playback.

    Blocks until the file finishes playing (or until ``timeout_s``
    elapses, whichever comes first). The TTS pipeline relies on this
    blocking semantics to know when speech is done.
    """

    def capabilities(self) -> frozenset[str]: ...

    def play_wav(self, wav_path: Path, timeout_s: float = 60.0) -> None: ...


@runtime_checkable
class TTSBackend(Protocol):
    """Offline text-to-speech.

    Implementations should *only* synthesise — playback is a separate
    surface (``PlayerBackend``). This keeps TTS engine and audio sink
    independently swappable.
    """

    def capabilities(self) -> frozenset[str]: ...

    def list_voices(self) -> list[str]: ...
    def synthesize(
        self,
        text: str,
        voice: str,
        out_wav: Path,
        *,
        speaker_id: int | None = None,
    ) -> Path: ...


@runtime_checkable
class STTBackend(Protocol):
    """Offline speech-to-text.

    Takes a WAV file (produced by ``RecorderBackend``) and returns the
    transcript as plain text. The backend is responsible for picking
    the language / model based on its own configuration.
    """

    def capabilities(self) -> frozenset[str]: ...

    def transcribe(self, wav_path: Path) -> str: ...


# ---------------------------------------------------------------------------
# Log viewer (tray "Ver logs" action) — see ADR-0023.
# ---------------------------------------------------------------------------
@runtime_checkable
class LogViewerBackend(Protocol):
    """Open a "follow"-mode viewer on a text file (typically a log).

    The tray's "Ver logs" entry uses this so the choice of terminal
    (Linux: foot/kitty/alacritty/xterm + ``tail -f``; Windows:
    ``powershell Get-Content -Wait`` in a console window) lives in
    the backend rather than scattered ``subprocess.run(['which', …])``
    probes inside ``tray.py``. Pure side-effecting; no return value.
    """

    def capabilities(self) -> frozenset[str]: ...

    def tail_file(self, path: Path) -> None: ...
