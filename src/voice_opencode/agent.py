"""
Agent-mode state and audit log.

When the MCP server is acting on the desktop, we want:

* a clear signal to the user (tray icon overlay + menu indicator),
* push-to-talk (F9) blocked so the user doesn't fight the agent,
* a kill-switch from the tray,
* an immutable audit trail of every tool call.

The presence of ``AGENT_FILE`` means "agent has control". The MCP server
takes the lock on startup and releases it on exit. While the lock is
held, ``state.is_paused()`` short-circuits to True via ``is_blocking()``
so the pipeline ignores F9.

All tool invocations are appended to ``logs/agent.log`` as JSON lines.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .paths import AGENT_FILE, AGENT_LOG_FILE


def is_active() -> bool:
    """Return True if an MCP agent currently holds the desktop lock."""
    if not AGENT_FILE.exists():
        return False
    try:
        pid = int(AGENT_FILE.read_text().strip() or "0")
    except Exception:
        return False
    if pid <= 0:
        return True
    # Best effort: stale lock cleanup if the holder died.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        AGENT_FILE.unlink(missing_ok=True)
        return False
    except PermissionError:
        return True


def acquire(pid: int | None = None) -> None:
    """Mark the desktop as agent-controlled. Idempotent for the same pid."""
    AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_FILE.write_text(str(pid if pid is not None else os.getpid()))


def release() -> None:
    """Release the lock. Safe to call when not held."""
    AGENT_FILE.unlink(missing_ok=True)


def is_blocking() -> bool:
    """
    True iff the user's input pipeline (F9) should ignore events.
    Combines the explicit pause sentinel with agent-mode.
    """
    from . import state as _state  # local to avoid cycle

    return _state.is_paused() or is_active()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def audit(tool: str, args: dict[str, Any], result: str = "ok") -> None:
    """Append one JSON line per tool call. Never raises."""
    try:
        AGENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tool": tool,
                "args": args,
                "result": result,
            },
            ensure_ascii=False,
        )
        with AGENT_LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass
