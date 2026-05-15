"""
Screenshot capture via ``grim`` (Wayland).

Three scopes:

    monitor   the focused Hyprland monitor (default for opencode prompts)
    window    geometry of the active Hyprland window
    all       the entire compositor output

The ``capture()`` function used by the pipeline picks scope from settings
and writes to the canonical ``SCREENSHOT_FILE``. ``capture_to()`` is the
generic primitive used by the CLI's ``desktop capture`` command.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from .config import settings
from .logging import log
from .paths import SCREENSHOT_FILE


def _focused_monitor_name() -> str | None:
    try:
        r = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            for m in json.loads(r.stdout):
                if m.get("focused"):
                    return m.get("name")
    except Exception as e:
        log(f"hyprctl monitors failed: {e}")
    return None


def _active_window_geometry() -> tuple[int, int, int, int] | None:
    """Return (x, y, w, h) of the focused Hyprland window, or ``None``."""
    try:
        r = subprocess.run(
            ["hyprctl", "-j", "activewindow"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            w = json.loads(r.stdout)
            at = w.get("at")
            sz = w.get("size")
            if at and sz:
                return at[0], at[1], sz[0], sz[1]
    except Exception as e:
        log(f"hyprctl activewindow failed: {e}")
    return None


def capture_to(out_path: Path, scope: str = "monitor") -> Path | None:
    """
    Take a screenshot to ``out_path``.

    scope ∈ {"monitor", "window", "all"}. Returns the path on success,
    ``None`` on failure (and logs the grim stderr).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["grim"]
    if scope == "monitor":
        name = _focused_monitor_name()
        if name:
            cmd += ["-o", name]
    elif scope == "window":
        geom = _active_window_geometry()
        if geom:
            x, y, w, h = geom
            cmd += ["-g", f"{x},{y} {w}x{h}"]
    cmd.append(str(out_path))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        log(f"grim failed: {r.stderr.strip()}")
        return None
    return out_path


def capture() -> Path | None:
    """Pipeline entry-point. Honours ``settings.screenshot`` flag."""
    if not settings.screenshot:
        return None
    path = capture_to(SCREENSHOT_FILE, scope=settings.screenshot_scope)
    if path:
        log(f"Screenshot captured ({path.stat().st_size} bytes, "
            f"scope={settings.screenshot_scope}).")
    return path


def to_data_url(path: Path) -> str:
    """Encode a PNG as a ``data:`` URL for the opencode JSON payload."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"
