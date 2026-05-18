"""
Desktop notifications — shim over ``platform.notify``.

Best-effort: never raises, always returns ``None``. Controlled by the
``notify`` config flag so users can mute toasts without code changes.

The actual ``notify-send`` / equivalent invocation lives in whichever
``NotifyBackend`` the platform layer wired for this host (currently
``LibnotifyBackend`` on Linux). Keeping a thin shim here means the
two existing call sites (``pipeline.py``, ``cli.py``) keep their
``from .notify import notify`` import and short signature without
having to reach into ``voice_opencode.platform`` themselves — and the
``settings.notify`` mute is enforced in exactly one place.
"""

from __future__ import annotations

from . import platform as _plat
from .config import settings


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    """Show a desktop toast. Silently no-ops if ``notify`` is disabled
    or the active platform has no notify backend."""
    if not settings.notify:
        return
    try:
        _plat.notify.show(title, body, urgency)
    except Exception:
        # Same contract as before: a missing/broken notify daemon
        # must never propagate up the pipeline. The backend itself
        # already logs subprocess failures.
        pass
