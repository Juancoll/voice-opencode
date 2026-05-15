"""
MCP server exposing the desktop primitives to opencode (Phase 3).

Transport: stdio (what opencode's local MCP integration expects).

Tools (all small, opinionated — easier for the model to compose):

    type_text(text, delay_ms=12)         -> str
    press_key(combo)                     -> str
    move_mouse(x, y)                     -> str
    click_mouse(button="left", x=None, y=None) -> str
    focused_window()                     -> dict
    capture_screen(scope="monitor", save_to=None) -> dict
        returns {"path": "...", "bytes": N} (no base64 inline by default
        — opencode can read the file, and we don't want to bloat the wire)
    list_monitors()                      -> list[dict]
    sleep_ms(ms)                         -> str   (so the model can wait
        for a UI to settle without spinning)

Safety rails:

* Agent-mode lock is held only for the duration of each *acting* tool
  call (type, key, click, move). Read-only tools (capture, focused,
  monitors) don't take the lock — they shouldn't block the user.
  This way F9 stays usable between agent actions.
* Per-tool rate limit (default 30 calls / 5 s window). Hitting it returns
  an error string to the model rather than throwing — the model can
  back off.
* Hard blocklist of dangerous key combos (``ctrl+alt+backspace``,
  ``ctrl+alt+f1..f12``, ``alt+sysrq+*``).
* Every call is appended to ``logs/agent.log`` as JSON Lines.

Run as::

    voice mcp serve

opencode is wired to launch this via its MCP config.
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import agent, desktop, screenshot
from .logging import log
from .paths import SCREENSHOT_FILE

# ---------------------------------------------------------------------------
# Safety primitives
# ---------------------------------------------------------------------------
_RATE_WINDOW_S: float = 5.0
_RATE_LIMIT: int = 30
_calls: deque[float] = deque(maxlen=_RATE_LIMIT * 4)

_DANGEROUS_KEYS: frozenset[str] = frozenset(
    {
        "ctrl+alt+backspace",
        "ctrl+alt+delete",
        "alt+sysrq",
        *(f"ctrl+alt+f{i}" for i in range(1, 13)),
    }
)


def _rate_check() -> bool:
    """Return True if we're within the rate limit; False if throttled."""
    now = time.monotonic()
    while _calls and now - _calls[0] > _RATE_WINDOW_S:
        _calls.popleft()
    if len(_calls) >= _RATE_LIMIT:
        return False
    _calls.append(now)
    return True


def _is_dangerous_key(combo: str) -> bool:
    norm = "+".join(p.strip().lower() for p in combo.split("+"))
    return norm in _DANGEROUS_KEYS


def _guard(tool: str, args: dict[str, Any]) -> str | None:
    """Run pre-flight safety checks. Returns an error string or ``None``."""
    if not _rate_check():
        agent.audit(tool, args, result="rate_limited")
        return f"rate limit exceeded ({_RATE_LIMIT} calls / {_RATE_WINDOW_S}s)"
    return None


@contextmanager
def _acting():
    """Hold the agent lock for the duration of an acting tool call."""
    agent.acquire()
    try:
        yield
    finally:
        agent.release()


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------
def build_server() -> Any:
    """Construct the FastMCP server with all tools registered."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="voice-opencode-desktop",
        instructions=(
            "Desktop control tools for the local Linux/Hyprland session. "
            "Use capture_screen() before and after acting to verify outcomes. "
            "Prefer press_key for navigation, type_text for content, "
            "click_mouse only when keyboard navigation isn't possible."
        ),
    )

    @mcp.tool(description="Type literal text into the focused window.")
    def type_text(text: str, delay_ms: int = 12) -> str:
        if (err := _guard("type_text", {"len": len(text), "delay_ms": delay_ms})):
            return err
        with _acting():
            desktop.type_text(text, delay_ms=delay_ms)
        agent.audit("type_text", {"len": len(text)})
        return f"typed {len(text)} chars"

    @mcp.tool(
        description=(
            "Press a key or combination, e.g. 'Tab', 'Return', 'ctrl+a'. "
            "Modifiers: ctrl, shift, alt, super."
        )
    )
    def press_key(combo: str) -> str:
        if _is_dangerous_key(combo):
            agent.audit("press_key", {"combo": combo}, result="blocked")
            return f"blocked: '{combo}' is on the safety blocklist"
        if (err := _guard("press_key", {"combo": combo})):
            return err
        with _acting():
            desktop.press_key(combo)
        agent.audit("press_key", {"combo": combo})
        return f"pressed {combo}"

    @mcp.tool(description="Move the mouse cursor to absolute (x, y).")
    def move_mouse(x: int, y: int) -> str:
        if (err := _guard("move_mouse", {"x": x, "y": y})):
            return err
        with _acting():
            desktop.move_mouse(x, y, absolute=True)
        agent.audit("move_mouse", {"x": x, "y": y})
        return f"moved to ({x},{y})"

    @mcp.tool(
        description=(
            "Click a mouse button. Optionally move to (x, y) first. "
            "button ∈ {'left', 'right', 'middle'}."
        )
    )
    def click_mouse(
        button: str = "left",
        x: int | None = None,
        y: int | None = None,
    ) -> str:
        if button not in ("left", "right", "middle"):
            return f"invalid button: {button}"
        if (err := _guard("click_mouse", {"button": button, "x": x, "y": y})):
            return err
        with _acting():
            desktop.click_mouse(button, x, y)
        agent.audit("click_mouse", {"button": button, "x": x, "y": y})
        return f"clicked {button} at ({x},{y})" if x is not None else f"clicked {button}"

    @mcp.tool(description="Return the currently focused Hyprland window as JSON.")
    def focused_window() -> dict[str, Any]:
        if (err := _guard("focused_window", {})):
            return {"error": err}
        agent.audit("focused_window", {})
        return desktop.focused_window()

    @mcp.tool(
        description=(
            "Capture a screenshot. scope ∈ {'monitor', 'window', 'all'}. "
            "Returns {path, bytes}. Read the PNG from `path` if you need pixels."
        )
    )
    def capture_screen(scope: str = "monitor", save_to: str | None = None) -> dict[str, Any]:
        if (err := _guard("capture_screen", {"scope": scope})):
            return {"error": err}
        out = Path(save_to) if save_to else SCREENSHOT_FILE
        result = screenshot.capture_to(out, scope=scope)
        if not result:
            agent.audit("capture_screen", {"scope": scope}, result="grim_failed")
            return {"error": "grim failed; see logs/voice.log"}
        size = result.stat().st_size
        agent.audit("capture_screen", {"scope": scope, "path": str(result), "bytes": size})
        return {"path": str(result), "bytes": size}

    @mcp.tool(description="List all Hyprland monitors with geometry and focus state.")
    def list_monitors() -> list[dict[str, Any]]:
        import json
        import subprocess

        if (err := _guard("list_monitors", {})):
            return [{"error": err}]
        try:
            r = subprocess.run(
                ["hyprctl", "-j", "monitors"],
                capture_output=True, text=True, timeout=2,
            )
            mons = json.loads(r.stdout) if r.returncode == 0 else []
            agent.audit("list_monitors", {"count": len(mons)})
            return mons
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool(description="Sleep for ms milliseconds. Use to let UIs settle. Max 5000.")
    def sleep_ms(ms: int) -> str:
        ms = max(0, min(ms, 5000))
        time.sleep(ms / 1000.0)
        agent.audit("sleep_ms", {"ms": ms})
        return f"slept {ms}ms"

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def serve() -> None:
    """Run the MCP server over stdio. The agent lock is held only during
    actual acting calls (see ``_acting``), not for the whole server lifetime.
    """
    log("MCP server started.")
    try:
        build_server().run(transport="stdio")
    finally:
        agent.release()  # belt-and-braces: ensure no stale lock on exit
        log("MCP server stopped.")
