"""
Desktop notifications via ``notify-send`` (libnotify).

Best-effort: never raises, always returns ``None``. Controlled by the
``notify`` config flag so users can mute it without code changes.
"""

from __future__ import annotations

import subprocess

from .config import settings


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    """Show a desktop toast. Silently no-ops if ``notify`` is disabled."""
    if not settings.notify:
        return
    try:
        subprocess.run(
            [
                "notify-send",
                "-u", urgency,
                "-t", "2500",
                "-a", "voice-opencode",
                title,
                body,
            ],
            check=False,
            timeout=2,
        )
    except Exception:
        pass
