"""``notify-send`` (libnotify) notifications.

Implemented now since Phase C will reuse it. Works on KDE, GNOME and
any Linux DE that has a notification daemon — the package name is
historical (``linux_dialog_kde``); we'll re-organise if it bothers.

``show_persistent`` + ``dismiss`` (ADR-0025) keep a single notification
alive for the duration of an action (e.g. an agent turn) and update
its text in place via ``notify-send --replace-id``. Closure goes via
``gdbus`` because ``notify-send`` itself can't close, only create or
replace.
"""

from __future__ import annotations

import shutil
import subprocess

from ...logging import log
from ...platform.base import BackendError, NotSupportedError
from ...platform.capabilities import NOTIFY_REPLACE, NOTIFY_SHOW

_VALID_URGENCY = frozenset({"low", "normal", "critical"})


class LibnotifyBackend:
    def __init__(self) -> None:
        if not shutil.which("notify-send"):
            raise BackendError("notify-send not on PATH")
        self._has_gdbus = bool(shutil.which("gdbus"))

    def capabilities(self) -> frozenset[str]:
        caps = {NOTIFY_SHOW}
        # ``--print-id`` + ``--replace-id`` are libnotify ≥0.7. Cheap
        # to advertise even if gdbus is missing — dismiss falls back
        # to a sentinel replace.
        caps.add(NOTIFY_REPLACE)
        return frozenset(caps)

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

    def show_persistent(
        self,
        title: str,
        body: str = "",
        urgency: str = "normal",
        replace_id: int = 0,
    ) -> int:
        """Show or update a notification; return its id.

        ``replace_id=0`` creates a new one. A long expire (1 day) keeps
        it visible until ``dismiss`` is called or the user clicks it.
        """
        if urgency not in _VALID_URGENCY:
            urgency = "normal"
        argv = [
            "notify-send",
            "-a", "voice-opencode",
            "-u", urgency,
            "-p",
            "-t", "86400000",  # 24h — effectively until dismissed
        ]
        if replace_id > 0:
            argv += ["-r", str(replace_id)]
        argv += [title, body]
        try:
            cp = subprocess.run(
                argv, check=False, timeout=3,
                capture_output=True, text=True,
            )
            out = (cp.stdout or "").strip()
            if not out:
                return replace_id  # daemon swallowed; reuse old id
            return int(out)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            log(f"notify-send persistent failed: {exc}")
            return replace_id

    def dismiss(self, notification_id: int) -> None:
        """Close the notification by id via the freedesktop D-Bus API."""
        if notification_id <= 0:
            return
        if not self._has_gdbus:
            # No way to close; the long expiry will time it out.
            raise NotSupportedError(
                "dismiss requires gdbus; install glib2 / glib2-tools"
            )
        try:
            subprocess.run(
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.freedesktop.Notifications",
                    "--object-path", "/org/freedesktop/Notifications",
                    "--method", "org.freedesktop.Notifications.CloseNotification",
                    str(notification_id),
                ],
                check=False, timeout=3,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            log("gdbus CloseNotification timed out")
