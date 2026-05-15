"""
Input backend using ``ydotool`` + (optionally) ``wtype``.

Rationale:

* On Wayland, ``wtype`` is the cleanest text/key path (no daemon).
* For mouse motion + clicks we *always* need ``ydotool`` because no
  Wayland compositor exposes a virtual pointer protocol uniformly.
* ``ydotool`` also works on X11 (it talks to ``/dev/uinput``), so this
  module is reusable across Linux display servers.

Capabilities:

* type_text, press_key   → wtype if available, else ydotool
* move_mouse, click_mouse, scroll_mouse, drag_mouse → ydotool

If neither tool is on PATH, ``capabilities()`` returns an empty set and
calling any method raises ``BackendError``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Final

from ...logging import log
from ...platform.base import BackendError
from ...platform.capabilities import (
    INPUT_CLICK_MOUSE,
    INPUT_DRAG_MOUSE,
    INPUT_MOVE_MOUSE,
    INPUT_PRESS_KEY,
    INPUT_SCROLL_MOUSE,
    INPUT_TYPE_TEXT,
)

YDOTOOL_SOCKET: Final[str] = os.environ.get(
    "YDOTOOL_SOCKET",
    f"/run/user/{os.getuid()}/.ydotool_socket",
)

_MOD_MAP: Final[dict[str, str]] = {
    "ctrl": "ctrl",   "control": "ctrl",
    "shift": "shift",
    "alt": "alt",     "meta": "alt",
    "super": "logo",  "win": "logo",  "logo": "logo",
}

_CLICK_CODES: Final[dict[str, str]] = {
    "left":   "0xC0",
    "right":  "0xC1",
    "middle": "0xC2",
}


def _ydotool_env() -> dict[str, str]:
    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = YDOTOOL_SOCKET
    return env


def _run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    """Run ``cmd`` swallowing stdout, log+raise on non-zero exit."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise BackendError(f"{cmd[0]}: {e}") from e
    if r.returncode != 0:
        raise BackendError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")


class YdotoolInputBackend:
    """Composite input backend: wtype for keyboard, ydotool for everything."""

    def __init__(self) -> None:
        self._wtype = shutil.which("wtype")
        self._ydotool = shutil.which("ydotool")
        if not self._wtype and not self._ydotool:
            raise BackendError(
                "neither wtype nor ydotool found; install at least ydotool"
            )

    # ---------------------------------------------------------------
    # capabilities()
    # ---------------------------------------------------------------
    def capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        if self._wtype or self._ydotool:
            caps |= {INPUT_TYPE_TEXT, INPUT_PRESS_KEY}
        if self._ydotool:
            caps |= {
                INPUT_MOVE_MOUSE, INPUT_CLICK_MOUSE,
                INPUT_SCROLL_MOUSE, INPUT_DRAG_MOUSE,
            }
        return frozenset(caps)

    # ---------------------------------------------------------------
    # Keyboard
    # ---------------------------------------------------------------
    def type_text(self, text: str, delay_ms: int = 12) -> None:
        if self._wtype:
            _run(["wtype", "-d", str(delay_ms), "--", text])
            log(f"typed via wtype ({len(text)} chars)")
            return
        if self._ydotool:
            _run(
                ["ydotool", "type", "--key-delay", str(delay_ms), "--", text],
                env=_ydotool_env(),
            )
            log(f"typed via ydotool ({len(text)} chars)")
            return
        raise BackendError("no typer available")

    def press_key(self, combo: str) -> None:
        if self._wtype:
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
            _run(args)
            log(f"key via wtype: {combo}")
            return
        if self._ydotool:
            _run(["ydotool", "key", combo], env=_ydotool_env())
            log(f"key via ydotool: {combo}")
            return
        raise BackendError("no key emitter available")

    # ---------------------------------------------------------------
    # Mouse
    # ---------------------------------------------------------------
    def move_mouse(self, x: int, y: int, absolute: bool = True) -> None:
        if not self._ydotool:
            raise BackendError("ydotool required for mouse movement")
        args = ["ydotool", "mousemove"]
        if absolute:
            args.append("--absolute")
        args += ["--", str(x), str(y)]
        _run(args, env=_ydotool_env())
        log(f"mouse → ({x},{y}) abs={absolute}")

    def click_mouse(
        self, button: str = "left",
        x: int | None = None, y: int | None = None,
    ) -> None:
        if not self._ydotool:
            raise BackendError("ydotool required for clicks")
        if button not in _CLICK_CODES:
            raise BackendError(f"unknown button: {button}")
        if x is not None and y is not None:
            self.move_mouse(x, y, absolute=True)
            time.sleep(0.05)  # compositor settle
        _run(["ydotool", "click", _CLICK_CODES[button]], env=_ydotool_env())
        log(f"click {button} at ({x},{y})")

    def scroll_mouse(self, dx: int, dy: int) -> None:
        if not self._ydotool:
            raise BackendError("ydotool required for scroll")
        # ydotool scroll takes a single axis at a time; do dy first.
        if dy:
            _run(["ydotool", "mousemove", "--wheel", "--", "0", str(dy)],
                 env=_ydotool_env())
        if dx:
            _run(["ydotool", "mousemove", "--wheel", "--", str(dx), "0"],
                 env=_ydotool_env())
        log(f"scroll dx={dx} dy={dy}")

    def drag_mouse(
        self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
    ) -> None:
        if not self._ydotool:
            raise BackendError("ydotool required for drag")
        if button not in _CLICK_CODES:
            raise BackendError(f"unknown button: {button}")
        # ydotool click codes for press / release individually:
        # press=0x40+n, release=0x80+n. Use 'mousedown' / 'mouseup' aliases.
        press_code = {"left": "0x40", "right": "0x41", "middle": "0x42"}[button]
        release_code = {"left": "0x80", "right": "0x81", "middle": "0x82"}[button]
        self.move_mouse(x1, y1)
        time.sleep(0.05)
        _run(["ydotool", "click", press_code], env=_ydotool_env())
        self.move_mouse(x2, y2)
        time.sleep(0.05)
        _run(["ydotool", "click", release_code], env=_ydotool_env())
        log(f"drag {button} ({x1},{y1})→({x2},{y2})")
