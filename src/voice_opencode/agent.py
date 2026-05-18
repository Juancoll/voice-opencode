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
        raw = AGENT_FILE.read_text().strip()
        if not raw:
            # Half-written or corrupt sentinel: treat as released.
            AGENT_FILE.unlink(missing_ok=True)
            return False
        pid = int(raw)
    except (ValueError, OSError):
        AGENT_FILE.unlink(missing_ok=True)
        return False
    if pid <= 0:
        AGENT_FILE.unlink(missing_ok=True)
        return False
    # Best effort: stale lock cleanup if the holder died.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        AGENT_FILE.unlink(missing_ok=True)
        return False
    except PermissionError:
        # Another user owns the pid (shouldn't happen in single-user box,
        # but be conservative — treat as alive).
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
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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


def audit_tail(n: int = 50) -> list[dict[str, Any]]:
    """Return the last ``n`` audit entries, newest last. Malformed lines skipped.

    Never raises; returns ``[]`` on any I/O or parse failure of the
    whole file. Individual unparseable lines are dropped silently.
    """
    if n <= 0 or not AGENT_LOG_FILE.exists():
        return []
    try:
        # Audit logs are append-only JSONL; a full read is fine for
        # the viewer's expected size (thousands of lines, not millions).
        lines = AGENT_LOG_FILE.read_text(encoding="utf-8",
                                         errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-n:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out
