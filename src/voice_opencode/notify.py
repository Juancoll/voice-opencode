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

Turn notification (ADR-0025): the pipeline opens one persistent
notification at the start of a turn (recording → thinking → speaking)
and the MCP server updates its body with each tool call so the user
sees what the agent is doing in real time. The id is persisted in
``$XDG_RUNTIME_DIR/voice-opencode/turn.notify-id`` so the MCP server
process (separate from the pipeline process) can replace the same
notification instead of opening a parallel one.
"""

from __future__ import annotations

from . import platform as _plat
from .config import settings
from .paths import STATE_DIR
from .platform import capabilities as _cap

_TURN_ID_FILE = STATE_DIR / "turn.notify-id"


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
# Turn notification helpers (ADR-0025).
# ---------------------------------------------------------------------------
def _read_turn_id() -> int:
    try:
        return int(_TURN_ID_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def _write_turn_id(nid: int) -> None:
    try:
        _TURN_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TURN_ID_FILE.write_text(str(nid))
    except OSError:
        pass


def _clear_turn_id() -> None:
    try:
        _TURN_ID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def turn_start(title: str, body: str = "") -> None:
    """Open or refresh the per-turn persistent notification.

    Idempotent: if a notification id already exists for this turn,
    the body is updated in place. Falls back to a regular toast if
    the backend doesn't support replace-id."""
    if not settings.notify:
        return
    if not _plat.supported(_cap.NOTIFY_REPLACE):
        notify(title, body, urgency="critical")
        return
    try:
        nid = _plat.notify.show_persistent(
            title, body, urgency="critical",
            replace_id=_read_turn_id(),
        )
        if nid > 0:
            _write_turn_id(nid)
    except Exception:
        pass


def turn_update(title: str, body: str = "") -> None:
    """Update the existing turn notification body. No-op if no turn
    is active (no id on disk)."""
    if not settings.notify:
        return
    if not _plat.supported(_cap.NOTIFY_REPLACE):
        return
    nid = _read_turn_id()
    if nid <= 0:
        return
    try:
        _plat.notify.show_persistent(
            title, body, urgency="critical", replace_id=nid,
        )
    except Exception:
        pass


def turn_end() -> None:
    """Close the turn notification, if any. Always safe to call."""
    nid = _read_turn_id()
    if nid <= 0:
        return
    try:
        _plat.notify.dismiss(nid)
    except Exception:
        pass
    _clear_turn_id()
