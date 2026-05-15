"""
Desktop control: synthetic keyboard, mouse, and window inspection.

Two backends:

* ``wtype``    — Wayland virtual keyboard. Used for text and key combos
                 (no daemon required). Fails on locked compositors.
* ``ydotool``  — needs the user-level ``ydotool.service`` running and an
                 accessible ``/dev/uinput`` (we set the ACL via udev).
                 Used for mouse motion + clicks, and as fallback for keys.

These are the building blocks the future MCP server will expose to opencode
as tools (``type_text``, ``press_key``, ``click_mouse``, ``move_mouse``,
``focused_window``). For now they're plain Python functions wired into
the CLI under the ``desktop`` subcommand group.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Final

from .logging import log

YDOTOOL_SOCKET: Final[str] = os.environ.get(
    "YDOTOOL_SOCKET",
    f"/run/user/{os.getuid()}/.ydotool_socket",
)

# wtype modifier names ≠ user-friendly names. We translate.
_MOD_MAP: Final[dict[str, str]] = {
    "ctrl": "ctrl",   "control": "ctrl",
    "shift": "shift",
    "alt": "alt",     "meta": "alt",
    "super": "logo",  "win": "logo",  "logo": "logo",
}

# ydotool click codes (one byte = press+release).
_CLICK_CODES: Final[dict[str, str]] = {
    "left":   "0xC0",
    "right":  "0xC1",
    "middle": "0xC2",
}


def _ydotool_env() -> dict[str, str]:
    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = YDOTOOL_SOCKET
    return env


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
def type_text(text: str, *, delay_ms: int = 12) -> None:
    """
    Type ``text`` into the focused window.

    Prefers ``wtype`` (Wayland-native, no daemon). Falls back to
    ``ydotool``. Logs and no-ops if neither is installed.
    """
    if shutil.which("wtype"):
        subprocess.run(
            ["wtype", "-d", str(delay_ms), "--", text],
            check=False,
        )
        log(f"typed via wtype ({len(text)} chars)")
        return
    if shutil.which("ydotool"):
        subprocess.run(
            ["ydotool", "type", "--key-delay", str(delay_ms), "--", text],
            check=False,
            env=_ydotool_env(),
        )
        log(f"typed via ydotool ({len(text)} chars)")
        return
    log("No typer available (install wtype or ydotool).")


def press_key(combo: str) -> None:
    """
    Press a key or combination, e.g. ``"Tab"``, ``"Return"``, ``"ctrl+a"``.

    Uses wtype's ``-M``/``-m`` for modifiers. Modifier names accepted:
    ``ctrl``, ``shift``, ``alt``/``meta``, ``super``/``win``/``logo``.
    """
    if shutil.which("wtype"):
        parts = [p.strip() for p in combo.split("+") if p.strip()]
        mods: list[str] = []
        key: str | None = None
        for p in parts:
            mapped = _MOD_MAP.get(p.lower())
            if mapped:
                mods.append(mapped)
            else:
                key = p
        args = ["wtype"]
        for m in mods:
            args += ["-M", m]
        if key:
            args += ["-k", key]
        for m in reversed(mods):
            args += ["-m", m]
        subprocess.run(args, check=False)
        log(f"key via wtype: {combo}")
        return
    if shutil.which("ydotool"):
        log("Falling back to ydotool key (combo must be raw scancodes).")
        subprocess.run(["ydotool", "key", combo], check=False, env=_ydotool_env())


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------
def move_mouse(x: int, y: int, *, absolute: bool = True) -> None:
    """Move the cursor to (x, y). Requires ``ydotool``."""
    if not shutil.which("ydotool"):
        log("ydotool required for mouse movement.")
        return
    args = ["ydotool", "mousemove"]
    if absolute:
        args.append("--absolute")
    args += ["--", str(x), str(y)]
    subprocess.run(args, check=False, env=_ydotool_env())
    log(f"mouse → ({x},{y}) abs={absolute}")


def click_mouse(
    button: str = "left",
    x: int | None = None,
    y: int | None = None,
) -> None:
    """
    Click ``button`` at (x, y) or at the current cursor position.

    Buttons: ``"left" | "right" | "middle"``.
    """
    if x is not None and y is not None:
        move_mouse(x, y, absolute=True)
        time.sleep(0.05)  # let the compositor catch up before the click
    if not shutil.which("ydotool"):
        log("ydotool required for clicks.")
        return
    code = _CLICK_CODES.get(button, _CLICK_CODES["left"])
    subprocess.run(["ydotool", "click", code], check=False, env=_ydotool_env())
    log(f"click {button} at ({x},{y})")


# ---------------------------------------------------------------------------
# Window inspection
# ---------------------------------------------------------------------------
def focused_window() -> dict:
    """Return Hyprland's ``activewindow`` JSON, or ``{}`` on error."""
    try:
        r = subprocess.run(
            ["hyprctl", "-j", "activewindow"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception as e:
        log(f"hyprctl activewindow failed: {e}")
    return {}
