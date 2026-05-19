"""``notify-send`` (libnotify) one-shot toasts.

Works on KDE, GNOME and any Linux DE with a notification daemon. The
package name is historical (``linux_dialog_kde``); we'll reorganise
if it bothers later.

Persistent per-turn bubble used to live here too (ADR-0025) but was
ripped out in ADR-0026 in favour of an in-process PyQt6 HUD (see
``hud.py``) because the KDE notification daemon ignores ``-r <id>``
once a bubble has auto-expired, leaving the user with a stale toast
that survives across turns. The HUD is owned end-to-end by the tray
so replace is a direct widget mutation, no IPC roundtrip with a
flaky daemon.
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
