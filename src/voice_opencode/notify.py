"""
Desktop notifications + per-turn HUD client.

Two surfaces:

* ``notify(title, body)`` — one-shot toasts via libnotify
  (``platform.notify``). Used for errors and incidental events
  outside the active turn (busy, paused, no-audio).

* ``turn_start`` / ``turn_update`` / ``turn_end`` — drive the in-
  process ``TurnHUD`` widget (see ``hud.py``) via a Unix socket
  owned by the tray. The HUD lives as long as the tray does and
  guarantees in-place updates without the KDE/Plasma replace-id bug
  we hit with ``notify-send -r`` (ADR-0026).

Both are best-effort: never raise, always honour ``settings.notify``
so the user can mute everything with one config flag.
"""

from __future__ import annotations

from . import hud
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


# ---------------------------------------------------------------------------
# Turn HUD helpers (ADR-0026).
# ---------------------------------------------------------------------------
# The HUD is a single widget hosted by the tray. We address it through
# its Unix socket; if the tray is down the calls silently no-op and
# the pipeline keeps running (the user already gets the toast errors
# via ``notify()`` above).
#
# Icons are emoji so the HUD doesn't need an icon theme and matches
# the rest of the project (TTS, logs, status labels).

def turn_start(title: str, body: str = "") -> None:
    """Open the per-turn HUD with an initial title + body."""
    if not settings.notify:
        return
    icon, label = _split_emoji(title)
    hud.send("show", icon=icon, title=label, subtitle=body)


def turn_update(title: str, body: str = "") -> None:
    """Replace the HUD contents in place. Same UX as ``turn_start``."""
    if not settings.notify:
        return
    icon, label = _split_emoji(title)
    hud.send("update", icon=icon, title=label, subtitle=body)


def turn_end() -> None:
    """Hide the HUD. Always safe to call (no-op if not visible)."""
    hud.send("hide")


def _split_emoji(title: str) -> tuple[str, str]:
    """Split ``"🎙 Grabando…"`` -> ``("🎙", "Grabando…")``.

    Callers use leading emojis for status; the HUD renders the icon
    in a dedicated column for a cleaner look. If no leading emoji is
    detected we fall back to a bullet so the icon column is never
    empty.
    """
    title = title.strip()
    if not title:
        return ("•", "")
    first, _, rest = title.partition(" ")
    # Heuristic: if the first token has any non-ASCII char treat it as the icon.
    if any(ord(c) > 127 for c in first):
        return (first, rest.strip())
    return ("•", title)
