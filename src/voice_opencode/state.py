"""
Pipeline state surfaced to the tray and to ``voice state``.

The pipeline writes a single word to ``STATE_FILE`` as it moves through
phases. The tray polls and updates its icon accordingly.

States:

    idle       waiting for input
    recording  arecord is running
    thinking   whisper + opencode call in flight
    speaking   piper + paplay playing the answer
    error      something blew up; cleared on next idle

Pause is a separate concern (presence of ``PAUSE_FILE``) so it survives
across phases without polluting the state machine.
"""

from __future__ import annotations

from typing import Final

from .paths import PAUSE_FILE, STATE_FILE

VALID_STATES: Final[frozenset[str]] = frozenset(
    {"idle", "recording", "thinking", "speaking", "error"}
)


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------
def set_state(state: str) -> None:
    """Write current pipeline phase. Ignores unknown values."""
    if state not in VALID_STATES:
        return
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(state)
    except Exception:
        pass


def get_state() -> str:
    """Read current phase. Defaults to ``"idle"`` if missing/corrupt."""
    try:
        s = STATE_FILE.read_text().strip()
        return s if s in VALID_STATES else "idle"
    except Exception:
        return "idle"


# ---------------------------------------------------------------------------
# Pause (sentinel file)
# ---------------------------------------------------------------------------
def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_paused(value: bool) -> None:
    if value:
        PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FILE.touch()
    else:
        PAUSE_FILE.unlink(missing_ok=True)
