"""``notify-send`` (libnotify) notifications.

Implemented now since Phase C will reuse it. Works on KDE, GNOME and
any Linux DE that has a notification daemon — the package name is
historical (``linux_dialog_kde``); we'll re-organise if it bothers.
"""

from __future__ import annotations

import shutil
import subprocess

from ...logging import log
from ...platform.base import BackendError
from ...platform.capabilities import NOTIFY_SHOW

_VALID_URGENCY = frozenset({"low", "normal", "critical"})


class LibnotifyBackend:
    def __init__(self) -> None:
        if not shutil.which("notify-send"):
            raise BackendError("notify-send not on PATH")

    def capabilities(self) -> frozenset[str]:
        return frozenset({NOTIFY_SHOW})

    def show(self, title: str, body: str = "", urgency: str = "normal") -> None:
        if urgency not in _VALID_URGENCY:
            urgency = "normal"
        try:
            subprocess.run(
                [
                    "notify-send",
                    "-a", "voice-opencode",
                    "-u", urgency,
                    title, body,
                ],
                check=False, timeout=3,
            )
        except subprocess.TimeoutExpired:
            log("notify-send timed out")
