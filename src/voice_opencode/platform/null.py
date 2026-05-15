"""
Null backends.

Every Protocol gets a NullX implementation that:

* declares an empty ``capabilities()`` set,
* raises ``NotSupportedError`` from every other method.

These are the safety net: if no real backend is available for a slot
(e.g. running headless during tests, or on an unsupported platform),
we hand out the Null one rather than ``None`` so consumer code can
unconditionally call ``platform.wm.list_windows()`` and get a clean
exception instead of an ``AttributeError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import NotSupportedError


def _no(name: str) -> NotSupportedError:
    return NotSupportedError(f"{name} not supported on this platform")


class _NullBase:
    def capabilities(self) -> frozenset[str]:
        return frozenset()


class NullWindowManager(_NullBase):
    def list_windows(self) -> list[Any]:           raise _no("list_windows")
    def find_windows(self, needle: str, limit: int = 10) -> list[Any]:
                                                   raise _no("find_windows")
    def active_window(self) -> Any:                raise _no("active_window")
    def list_workspaces(self) -> list[Any]:        raise _no("list_workspaces")
    def active_workspace(self) -> Any:             raise _no("active_workspace")
    def focus_window(self, window_id: str) -> None:           raise _no("focus_window")
    def close_window(self, window_id: str) -> None:           raise _no("close_window")
    def move_window(self, window_id: str, x: int, y: int) -> None:
                                                   raise _no("move_window")
    def resize_window(self, window_id: str, w: int, h: int) -> None:
                                                   raise _no("resize_window")
    def toggle_floating(self, window_id: str) -> None:        raise _no("toggle_floating")
    def toggle_fullscreen(self, window_id: str | None = None) -> None:
                                                   raise _no("toggle_fullscreen")
    def minimize_window(self, window_id: str) -> None:        raise _no("minimize_window")
    def switch_workspace(self, workspace: str | int) -> None: raise _no("switch_workspace")
    def move_window_to_workspace(self, window_id: str, workspace: str | int) -> None:
                                                   raise _no("move_window_to_workspace")
    def send_workspace_to_monitor(self, target: str | int) -> None:
                                                   raise _no("send_workspace_to_monitor")


class NullInputBackend(_NullBase):
    def type_text(self, text: str, delay_ms: int = 12) -> None: raise _no("type_text")
    def press_key(self, combo: str) -> None:                    raise _no("press_key")
    def move_mouse(self, x: int, y: int, absolute: bool = True) -> None:
                                                                raise _no("move_mouse")
    def click_mouse(self, button: str = "left",
                    x: int | None = None, y: int | None = None) -> None:
                                                                raise _no("click_mouse")
    def scroll_mouse(self, dx: int, dy: int) -> None:           raise _no("scroll_mouse")
    def drag_mouse(self, x1: int, y1: int, x2: int, y2: int,
                   button: str = "left") -> None:               raise _no("drag_mouse")


class NullScreenBackend(_NullBase):
    def list_monitors(self) -> list[Any]:          raise _no("list_monitors")
    def focused_monitor(self) -> Any:              raise _no("focused_monitor")
    def capture_monitor(self, out_path: Path, monitor: Any = None) -> Path:
                                                   raise _no("capture_monitor")
    def capture_window(self, out_path: Path, window_id: str | None = None) -> Path:
                                                   raise _no("capture_window")
    def capture_region(self, out_path: Path, x: int, y: int, w: int, h: int) -> Path:
                                                   raise _no("capture_region")
    def capture_all(self, out_path: Path) -> Path: raise _no("capture_all")


class NullClipboardBackend(_NullBase):
    def read(self) -> str:                         raise _no("clipboard.read")
    def write(self, text: str) -> None:            raise _no("clipboard.write")
    def read_primary(self) -> str:                 raise _no("clipboard.read_primary")
    def write_primary(self, text: str) -> None:    raise _no("clipboard.write_primary")


class NullNotifyBackend(_NullBase):
    def show(self, title: str, body: str = "", urgency: str = "normal") -> None:
                                                   raise _no("notify.show")


class NullDialogBackend(_NullBase):
    def confirm(self, message: str, title: str = "Confirm") -> bool:
                                                   raise _no("dialog.confirm")
    def ask_text(self, prompt: str, default: str = "", title: str = "Input") -> str | None:
                                                   raise _no("dialog.ask_text")
    def ask_choice(self, prompt: str, choices: list[str], title: str = "Choose") -> str | None:
                                                   raise _no("dialog.ask_choice")


class NullAudioBackend(_NullBase):
    def volume_get(self) -> float:                 raise _no("audio.volume_get")
    def volume_set(self, level: float) -> None:    raise _no("audio.volume_set")
    def mute_toggle(self) -> bool:                 raise _no("audio.mute_toggle")
    def mic_mute_toggle(self) -> bool:             raise _no("audio.mic_mute_toggle")


class NullMediaBackend(_NullBase):
    def play_pause(self) -> None:                  raise _no("media.play_pause")
    def next(self) -> None:                        raise _no("media.next")
    def prev(self) -> None:                        raise _no("media.prev")
    def status(self) -> dict[str, Any]:            raise _no("media.status")


class NullAppLauncher(_NullBase):
    def list_installed(self) -> list[dict[str, Any]]: raise _no("app.list_installed")
    def list_running(self) -> list[dict[str, Any]]:   raise _no("app.list_running")
    def launch(self, app_id_or_cmd: str) -> int:      raise _no("app.launch")
    def kill(self, pid_or_app_id: int | str) -> None: raise _no("app.kill")


class NullShellBackend(_NullBase):
    def run(self, cmd: str | list[str], cwd: str | None = None,
            timeout: float = 30.0, dry_run: bool = True) -> dict[str, Any]:
                                                   raise _no("shell.run")
