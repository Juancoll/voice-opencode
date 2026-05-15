"""Hyprland window manager backend.

Implements ``WindowManager`` over Hyprland's ``hyprctl`` IPC
(JSON read + dispatch write).

* Read calls go through ``hyprctl -j <subcommand>`` and translate the
  raw JSON into the platform-neutral ``Window`` / ``Workspace`` /
  ``Monitor`` dataclasses.
* Write calls go through ``hyprctl dispatch ...``.
* Window targeting accepts either a Hyprland address (``0x...``) or a
  case-insensitive substring of class/title; the backend resolves the
  latter via ``find_windows`` before dispatching.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from ...logging import log
from ...platform.base import BackendError
from ...platform.capabilities import (
    WM_ACTIVE_WINDOW,
    WM_ACTIVE_WORKSPACE,
    WM_CLOSE_WINDOW,
    WM_FIND_WINDOWS,
    WM_FOCUS_WINDOW,
    WM_LIST_WINDOWS,
    WM_LIST_WORKSPACES,
    WM_MOVE_TO_WORKSPACE,
    WM_MOVE_WINDOW,
    WM_RESIZE_WINDOW,
    WM_SEND_WS_TO_MONITOR,
    WM_SWITCH_WORKSPACE,
    WM_TOGGLE_FLOATING,
    WM_TOGGLE_FULLSCREEN,
)
from ...platform.types import Rect, Window, Workspace

_TIMEOUT_S = 2.0
_NOISE_KEYS = frozenset(
    {
        "stableId", "focusHistoryID", "swallowing", "grouped", "tags",
        "xdgTag", "xdgDescription", "contentType", "inhibitingIdle",
        "initialClass", "initialTitle", "fullscreenClient",
        "overFullscreen",
    }
)


class HyprlandWindowManager:
    """``WindowManager`` over Hyprland."""

    def __init__(self) -> None:
        if not shutil.which("hyprctl"):
            raise BackendError("hyprctl not on PATH")
        # Sanity ping; if Hyprland isn't running we want to know now.
        self._ipc_json("version")

    # ----------------------------------------------------------------
    # capabilities
    # ----------------------------------------------------------------
    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                WM_LIST_WINDOWS, WM_FIND_WINDOWS, WM_ACTIVE_WINDOW,
                WM_LIST_WORKSPACES, WM_ACTIVE_WORKSPACE,
                WM_FOCUS_WINDOW, WM_CLOSE_WINDOW,
                WM_MOVE_WINDOW, WM_RESIZE_WINDOW,
                WM_TOGGLE_FLOATING, WM_TOGGLE_FULLSCREEN,
                WM_SWITCH_WORKSPACE, WM_MOVE_TO_WORKSPACE,
                WM_SEND_WS_TO_MONITOR,
                # Hyprland has no "minimize"; intentionally not declared.
            }
        )

    # ----------------------------------------------------------------
    # hyprctl wrappers
    # ----------------------------------------------------------------
    def _ipc_json(self, *args: str) -> Any:
        cmd = ["hyprctl", "-j", *args]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise BackendError(f"{' '.join(cmd)}: {e}") from e
        if r.returncode != 0:
            raise BackendError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
        if not r.stdout.strip():
            return None
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise BackendError(f"{' '.join(cmd)}: bad JSON ({e})") from e

    def _ipc_dispatch(self, *args: str) -> str:
        cmd = ["hyprctl", "dispatch", *args]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise BackendError(f"{' '.join(cmd)}: {e}") from e
        if r.returncode != 0:
            raise BackendError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
        out = r.stdout.strip()
        # hyprctl reports some failures with rc=0 + an error string.
        if out and "error" in out.lower() and "ok" not in out.lower():
            raise BackendError(f"{' '.join(cmd)}: {out}")
        return out

    # ----------------------------------------------------------------
    # Translators
    # ----------------------------------------------------------------
    @staticmethod
    def _to_window(raw: dict[str, Any]) -> Window:
        ws = raw.get("workspace") or {}
        at = raw.get("at") or [0, 0]
        sz = raw.get("size") or [0, 0]
        return Window(
            id=str(raw.get("address", "")),
            pid=int(raw.get("pid", 0)),
            app_id=str(raw.get("class", "")),
            title=str(raw.get("title", "")),
            rect=Rect(int(at[0]), int(at[1]), int(sz[0]), int(sz[1])),
            monitor_id=int(raw.get("monitor", -1)),
            workspace_id=int(ws.get("id", -1)),
            focused=False,  # filled by active_window()
            floating=bool(raw.get("floating", False)),
            fullscreen=bool(raw.get("fullscreen", 0)),
            minimized=bool(raw.get("hidden", False)),
            pinned=bool(raw.get("pinned", False)),
            extra={
                k: v for k, v in raw.items()
                if k not in _NOISE_KEYS and k not in {
                    "address", "pid", "class", "title", "at", "size",
                    "monitor", "workspace", "floating", "fullscreen",
                    "hidden", "pinned",
                }
            },
        )

    @staticmethod
    def _to_workspace(raw: dict[str, Any], active_id: int) -> Workspace:
        return Workspace(
            id=int(raw.get("id", 0)),
            name=str(raw.get("name", "")),
            monitor_id=int(raw.get("monitorID", -1)),
            window_count=int(raw.get("windows", 0)),
            active=int(raw.get("id", 0)) == active_id,
        )

    # ----------------------------------------------------------------
    # Read API
    # ----------------------------------------------------------------
    def list_windows(self) -> list[Window]:
        from dataclasses import replace
        raw = self._ipc_json("clients") or []
        active = self.active_window()
        active_id = active.id if active else ""
        out: list[Window] = []
        for c in raw:
            w = self._to_window(c)
            if w.id == active_id:
                w = replace(w, focused=True)
            out.append(w)
        return out

    def find_windows(self, needle: str, limit: int = 10) -> list[Window]:
        if not needle:
            return []
        pat = re.compile(re.escape(needle), re.IGNORECASE)
        return [
            w for w in self.list_windows()
            if pat.search(w.app_id) or pat.search(w.title)
        ][: max(1, limit)]

    def active_window(self) -> Window | None:
        raw = self._ipc_json("activewindow")
        if not raw:
            return None
        from dataclasses import replace
        return replace(self._to_window(raw), focused=True)

    def list_workspaces(self) -> list[Workspace]:
        raw = self._ipc_json("workspaces") or []
        active = self._ipc_json("activeworkspace") or {}
        active_id = int(active.get("id", -1))
        return [self._to_workspace(ws, active_id) for ws in raw]

    def active_workspace(self) -> Workspace | None:
        raw = self._ipc_json("activeworkspace")
        if not raw:
            return None
        return self._to_workspace(raw, int(raw.get("id", -1)))

    # ----------------------------------------------------------------
    # Targeting
    # ----------------------------------------------------------------
    def _resolve_target(self, target: str) -> str:
        """Return ``address:0xABCD…`` for any acceptable ``target``."""
        if not target:
            raise BackendError("empty window target")
        if target.startswith("address:"):
            return target
        if target.startswith("0x"):
            return f"address:{target}"
        match = self.find_windows(target, limit=1)
        if not match:
            raise BackendError(f"no window matches {target!r}")
        return f"address:{match[0].id}"

    # ----------------------------------------------------------------
    # Write API
    # ----------------------------------------------------------------
    def focus_window(self, window_id: str) -> None:
        self._ipc_dispatch("focuswindow", self._resolve_target(window_id))

    def close_window(self, window_id: str) -> None:
        self._ipc_dispatch("closewindow", self._resolve_target(window_id))

    def move_window(self, window_id: str, x: int, y: int) -> None:
        self._ipc_dispatch(
            "movewindowpixel",
            f"exact {x} {y}",
            "," + self._resolve_target(window_id),
        )

    def resize_window(self, window_id: str, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            raise BackendError(f"invalid dims {w}x{h}")
        self._ipc_dispatch(
            "resizewindowpixel",
            f"exact {w} {h}",
            "," + self._resolve_target(window_id),
        )

    def toggle_floating(self, window_id: str) -> None:
        self._ipc_dispatch("togglefloating", self._resolve_target(window_id))

    def toggle_fullscreen(self, window_id: str | None = None) -> None:
        if window_id:
            self.focus_window(window_id)
        self._ipc_dispatch("fullscreen", "0")

    def minimize_window(self, window_id: str) -> None:
        # Hyprland has no native minimize. Move to special:scratch as the
        # closest semantic equivalent (out of view, recoverable).
        self._ipc_dispatch(
            "movetoworkspacesilent",
            f"special:scratch,{self._resolve_target(window_id)}",
        )
        log("minimize_window: moved to special:scratch (Hyprland has no minimize)")

    def switch_workspace(self, workspace: str | int) -> None:
        self._ipc_dispatch("workspace", str(workspace))

    def move_window_to_workspace(
        self, window_id: str, workspace: str | int,
    ) -> None:
        self._ipc_dispatch(
            "movetoworkspacesilent",
            f"{workspace},{self._resolve_target(window_id)}",
        )

    def send_workspace_to_monitor(self, target: str | int) -> None:
        self._ipc_dispatch("movecurrentworkspacetomonitor", str(target))
