"""Terminal-based log viewer backend for Linux.

``tail_file(path)`` opens the user's preferred terminal emulator with
``tail -f path`` inside it. Probes a fixed list of terminals in order
of preference (``foot``, ``kitty``, ``alacritty``, ``xterm``); the
first one on ``PATH`` wins. Falls back to ``xdg-open`` if none are
available — that opens the file in whatever text viewer the user has
associated, which is at least useful even if it doesn't auto-follow.

This pattern (probe-and-exec) lived inside ``tray.py`` until Phase
A.7; extracting it lets the Windows backend (Phase C) substitute a
PowerShell ``Get-Content -Wait`` console without touching the tray.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ...logging import log
from ...platform.base import BackendError
from ...platform.capabilities import LOGVIEW_TAIL_FILE

# Preference order — first existing wins.
_TERMINALS: tuple[str, ...] = ("foot", "kitty", "alacritty", "xterm")


class TerminalLogViewerBackend:
    """Open ``tail -f <path>`` in the first available terminal."""

    def capabilities(self) -> frozenset[str]:
        return frozenset({LOGVIEW_TAIL_FILE})

    def tail_file(self, path: Path) -> None:
        path = Path(path)
        for term in _TERMINALS:
            if shutil.which(term):
                subprocess.Popen([term, "-e", "tail", "-f", str(path)])
                log(f"logview: opened {path} in {term}")
                return
        # Fallback: text viewer (no follow, but at least visible).
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)])
            log(f"logview: no terminal found, xdg-open {path}")
            return
        raise BackendError(
            f"no terminal emulator and no xdg-open found to view {path}"
        )
