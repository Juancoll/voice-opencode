"""``wlroots`` screen backend (Hyprland, Sway, etc.).

Captures via ``grim``. Monitor enumeration goes through ``hyprctl
monitors`` when running under Hyprland; on other wlroots compositors
we fall back to ``wlr-randr`` if available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...logging import log
from ...platform.base import BackendError
from ...platform.capabilities import (
    SCREEN_CAPTURE_ALL,
    SCREEN_CAPTURE_MONITOR,
    SCREEN_CAPTURE_REGION,
    SCREEN_CAPTURE_WINDOW,
    SCREEN_LIST_MONITORS,
)
from ...platform.types import Monitor, Rect

_TIMEOUT_S = 10.0


def _run(cmd: list[str], timeout: float = _TIMEOUT_S) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise BackendError(f"{cmd[0]}: {e}") from e
    return r.returncode, r.stdout, r.stderr


class WlrootsScreenBackend:
    """``ScreenBackend`` over ``grim`` + ``hyprctl/wlr-randr``."""

    def __init__(self) -> None:
        if not shutil.which("grim"):
            raise BackendError("grim not on PATH")
        self._has_hyprctl = shutil.which("hyprctl") is not None
        self._has_wlr_randr = shutil.which("wlr-randr") is not None

    def capabilities(self) -> frozenset[str]:
        caps = {
            SCREEN_CAPTURE_MONITOR, SCREEN_CAPTURE_REGION, SCREEN_CAPTURE_ALL,
            SCREEN_CAPTURE_WINDOW,
        }
        if self._has_hyprctl or self._has_wlr_randr:
            caps.add(SCREEN_LIST_MONITORS)
        return frozenset(caps)

    # ----------------------------------------------------------------
    # Monitors
    # ----------------------------------------------------------------
    def list_monitors(self) -> list[Monitor]:
        if self._has_hyprctl:
            return self._list_hyprctl()
        if self._has_wlr_randr:
            return self._list_wlrrandr()
        raise BackendError("need hyprctl or wlr-randr to enumerate monitors")

    def focused_monitor(self) -> Monitor | None:
        for m in self.list_monitors():
            if m.focused:
                return m
        return None

    def _list_hyprctl(self) -> list[Monitor]:
        rc, out, err = _run(["hyprctl", "-j", "monitors"], timeout=2)
        if rc != 0:
            raise BackendError(f"hyprctl monitors: {err.strip()}")
        raw: list[dict[str, Any]] = json.loads(out) if out.strip() else []
        result: list[Monitor] = []
        for m in raw:
            result.append(
                Monitor(
                    id=int(m.get("id", 0)),
                    name=str(m.get("name", "")),
                    rect=Rect(
                        int(m.get("x", 0)), int(m.get("y", 0)),
                        int(m.get("width", 0)), int(m.get("height", 0)),
                    ),
                    scale=float(m.get("scale", 1.0)),
                    focused=bool(m.get("focused", False)),
                    primary=False,  # wlroots has no concept of primary
                    refresh_hz=float(m.get("refreshRate", 0.0)),
                )
            )
        return result

    def _list_wlrrandr(self) -> list[Monitor]:
        # wlr-randr's text output is hard to parse; this is a best-effort
        # fallback only used when hyprctl is absent.
        rc, out, err = _run(["wlr-randr"], timeout=2)
        if rc != 0:
            raise BackendError(f"wlr-randr: {err.strip()}")
        result: list[Monitor] = []
        current: dict[str, Any] = {}
        for line in out.splitlines():
            if not line.startswith(" ") and line.strip():
                if current:
                    result.append(self._mon_from_wlr(current))
                current = {"name": line.strip().split()[0]}
            elif "current" in line and "Hz" in line:
                # e.g. "    1920x1080 px, 60.000 Hz (current)"
                parts = line.strip().split()
                if "x" in parts[0]:
                    w, h = parts[0].split("x")
                    current["width"], current["height"] = int(w), int(h)
                    try:
                        current["hz"] = float(parts[2])
                    except (IndexError, ValueError):
                        pass
        if current:
            result.append(self._mon_from_wlr(current))
        return result

    @staticmethod
    def _mon_from_wlr(d: dict[str, Any]) -> Monitor:
        return Monitor(
            id=0, name=str(d.get("name", "")),
            rect=Rect(0, 0, int(d.get("width", 0)), int(d.get("height", 0))),
            scale=1.0, focused=False, primary=False,
            refresh_hz=float(d.get("hz", 0.0)),
        )

    # ----------------------------------------------------------------
    # Capture
    # ----------------------------------------------------------------
    def _grim(self, args: list[str], out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rc, _stdout, err = _run(["grim", *args, str(out_path)])
        if rc != 0:
            raise BackendError(f"grim failed: {err.strip()}")
        return out_path

    def _active_window_monitor_name(self) -> str | None:
        """Resolve the monitor *containing the currently focused window*.

        This is the monitor the user is actually looking at, which is
        what we want for the per-turn screenshot. The naive alternative
        — ``focused_monitor()`` — follows the *cursor* / last focus,
        and so the screenshot can silently capture the wrong screen
        whenever the cursor or a floating overlay (e.g. our own HUD)
        crosses a monitor boundary. In production this manifested as
        the model describing UI the user could not see.

        Hyprland exposes ``activewindow.monitor`` as a numeric id; we
        cross-reference it with ``hyprctl monitors`` to get the name
        that ``grim -o`` expects.
        """
        if not self._has_hyprctl:
            return None
        try:
            rc, out, _ = _run(["hyprctl", "-j", "activewindow"], timeout=2)
            if rc != 0 or not out.strip():
                return None
            w = json.loads(out)
            mon_id = w.get("monitor")
            if mon_id is None:
                return None
            for m in self.list_monitors():
                if m.id == int(mon_id):
                    return m.name
        except Exception as e:  # pragma: no cover — defensive
            log(f"active-window monitor resolution failed: {e}")
        return None

    def capture_monitor(
        self, out_path: Path, monitor: Monitor | str | None = None,
    ) -> Path:
        name: str | None = None
        if isinstance(monitor, Monitor):
            name = monitor.name
        elif isinstance(monitor, str):
            name = monitor
        else:
            # Prefer the monitor that holds the active window — the one
            # the user is actually typing into. Fall back to the cursor-
            # following "focused" monitor only if no active window can
            # be resolved (e.g. nothing is focused).
            name = self._active_window_monitor_name()
            if not name:
                mon = self.focused_monitor()
                name = mon.name if mon else None
        args = ["-o", name] if name else []
        return self._grim(args, out_path)

    def capture_window(
        self, out_path: Path, window_id: str | None = None,
    ) -> Path:
        # Without an explicit window_id, grab the active Hyprland window.
        rect: Rect | None = None
        if self._has_hyprctl:
            try:
                rc, out, _ = _run(["hyprctl", "-j", "activewindow"], timeout=2)
                if rc == 0 and out.strip():
                    w = json.loads(out)
                    at = w.get("at") or [0, 0]
                    sz = w.get("size") or [0, 0]
                    rect = Rect(int(at[0]), int(at[1]), int(sz[0]), int(sz[1]))
            except Exception as e:  # pragma: no cover — defensive
                log(f"hyprctl activewindow failed: {e}")
        if not rect:
            raise BackendError("could not resolve window geometry")
        return self.capture_region(out_path, rect.x, rect.y, rect.w, rect.h)

    def capture_region(
        self, out_path: Path, x: int, y: int, w: int, h: int,
    ) -> Path:
        return self._grim(["-g", f"{x},{y} {w}x{h}"], out_path)

    def capture_all(self, out_path: Path) -> Path:
        return self._grim([], out_path)
