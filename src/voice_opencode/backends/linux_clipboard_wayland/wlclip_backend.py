"""Wayland clipboard via ``wl-copy`` / ``wl-paste``."""

from __future__ import annotations

import shutil
import subprocess

from ...platform.base import BackendError
from ...platform.capabilities import (
    CLIPBOARD_READ,
    CLIPBOARD_READ_PRIMARY,
    CLIPBOARD_WRITE,
    CLIPBOARD_WRITE_PRIMARY,
)

_TIMEOUT_S = 3.0


class WlClipboardBackend:
    def __init__(self) -> None:
        if not shutil.which("wl-copy") or not shutil.which("wl-paste"):
            raise BackendError("wl-clipboard not installed (need wl-copy/wl-paste)")

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                CLIPBOARD_READ, CLIPBOARD_WRITE,
                CLIPBOARD_READ_PRIMARY, CLIPBOARD_WRITE_PRIMARY,
            }
        )

    def _read(self, primary: bool = False) -> str:
        cmd = ["wl-paste", "-n"]
        if primary:
            cmd.append("--primary")
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError("wl-paste timeout") from e
        # Empty selection makes wl-paste exit non-zero; treat as empty string.
        return r.stdout if r.returncode == 0 else ""

    def _write(self, text: str, primary: bool = False) -> None:
        cmd = ["wl-copy"]
        if primary:
            cmd.append("--primary")
        # wl-copy daemonises (double-fork) to serve future paste requests.
        # If any of stdout/stderr is a pipe, the daemon child inherits the
        # fd and subprocess.run blocks on its close → timeout. Route both
        # to /dev/null. We lose stderr context on failure but the returncode
        # is enough to detect breakage.
        try:
            r = subprocess.run(
                cmd,
                input=text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError("wl-copy timeout") from e
        if r.returncode != 0:
            raise BackendError(f"wl-copy failed (rc={r.returncode})")

    def read(self) -> str:                          return self._read(primary=False)
    def write(self, text: str) -> None:             self._write(text, primary=False)
    def read_primary(self) -> str:                  return self._read(primary=True)
    def write_primary(self, text: str) -> None:     self._write(text, primary=True)
