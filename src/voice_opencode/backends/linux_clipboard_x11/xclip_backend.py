"""X11 clipboard via ``xclip``.

We use ``xclip -selection clipboard`` for the CLIPBOARD selection and
``-selection primary`` for the PRIMARY selection. ``-out`` reads, the
default (no flag) writes from stdin.
"""

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


class XclipClipboardBackend:
    def __init__(self) -> None:
        if not shutil.which("xclip"):
            raise BackendError("xclip not installed")

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                CLIPBOARD_READ, CLIPBOARD_WRITE,
                CLIPBOARD_READ_PRIMARY, CLIPBOARD_WRITE_PRIMARY,
            }
        )

    def _read(self, primary: bool = False) -> str:
        sel = "primary" if primary else "clipboard"
        cmd = ["xclip", "-selection", sel, "-out"]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError("xclip read timeout") from e
        # xclip exits non-zero on empty selection; treat as empty string.
        return r.stdout if r.returncode == 0 else ""

    def _write(self, text: str, primary: bool = False) -> None:
        sel = "primary" if primary else "clipboard"
        cmd = ["xclip", "-selection", sel, "-in"]
        try:
            r = subprocess.run(
                cmd, input=text, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError("xclip write timeout") from e
        if r.returncode != 0:
            raise BackendError(f"xclip failed: {r.stderr.strip()}")

    def read(self) -> str:                          return self._read(primary=False)
    def write(self, text: str) -> None:             self._write(text, primary=False)
    def read_primary(self) -> str:                  return self._read(primary=True)
    def write_primary(self, text: str) -> None:     self._write(text, primary=True)
