"""
MCP server exposing platform-neutral desktop tools to opencode.

Transport: stdio (what opencode's local MCP integration expects).

The tool surface is generated dynamically from the wired backends'
``capabilities()`` sets. A tool is registered iff at least one backend
claims to support its capability — the model never sees a tool that
would always fail.

Safety rails (kept identical across platforms):

* Per-call agent lock (acquired only for *acting* tools).
* Sliding-window rate limit (default 30 calls / 5 s).
* Hard blocklist of dangerous key combos.
* Every call appended to ``logs/agent.log`` as JSON Lines.

Run as::

    voice mcp serve
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import agent
from . import platform as plat
from .logging import log
from .paths import SCREENSHOT_FILE
from .platform import capabilities as cap
from .platform.base import BackendError, NotSupportedError
from .platform.types import Window

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
    """Pre-flight checks. Returns an error string or ``None``."""
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


def _err(e: Exception) -> str:
    return f"error: {e}"


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------
def build_server() -> Any:
    """Construct the FastMCP server with all *available* tools registered."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="voice-opencode-desktop",
        instructions=(
            "Desktop control tools for the local session. "
            "Use capture_screen() before and after acting to verify outcomes. "
            "Prefer press_key for navigation, type_text for content, "
            "click_mouse only when keyboard navigation isn't possible. "
            "Use focus_window() / list_windows() instead of clicking title "
            "bars; it's much more reliable."
        ),
    )

    _register_input(mcp)
    _register_screen(mcp)
    _register_windows(mcp)
    _register_clipboard(mcp)
    _register_dialogs(mcp)
    _register_misc(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Tool groups
# ---------------------------------------------------------------------------
def _register_input(mcp: Any) -> None:
    if plat.supported(cap.INPUT_TYPE_TEXT):
        @mcp.tool(description="Type literal text into the focused window.")
        def type_text(text: str, delay_ms: int = 12) -> str:
            if (e := _guard("type_text", {"len": len(text), "delay_ms": delay_ms})):
                return e
            with _acting():
                try:
                    plat.input.type_text(text, delay_ms=delay_ms)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("type_text", {"len": len(text)})
            return f"typed {len(text)} chars"

    if plat.supported(cap.INPUT_PRESS_KEY):
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
            if (e := _guard("press_key", {"combo": combo})):
                return e
            with _acting():
                try:
                    plat.input.press_key(combo)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("press_key", {"combo": combo})
            return f"pressed {combo}"

    if plat.supported(cap.INPUT_MOVE_MOUSE):
        @mcp.tool(description="Move the mouse cursor to absolute (x, y).")
        def move_mouse(x: int, y: int) -> str:
            if (e := _guard("move_mouse", {"x": x, "y": y})):
                return e
            with _acting():
                try:
                    plat.input.move_mouse(x, y, absolute=True)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("move_mouse", {"x": x, "y": y})
            return f"moved to ({x},{y})"

    if plat.supported(cap.INPUT_CLICK_MOUSE):
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
            if (e := _guard("click_mouse", {"button": button, "x": x, "y": y})):
                return e
            with _acting():
                try:
                    plat.input.click_mouse(button, x, y)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("click_mouse", {"button": button, "x": x, "y": y})
            return (f"clicked {button} at ({x},{y})"
                    if x is not None else f"clicked {button}")

    if plat.supported(cap.INPUT_SCROLL_MOUSE):
        @mcp.tool(description="Scroll the mouse wheel by (dx, dy) ticks.")
        def scroll_mouse(dx: int = 0, dy: int = 0) -> str:
            if (e := _guard("scroll_mouse", {"dx": dx, "dy": dy})):
                return e
            with _acting():
                try:
                    plat.input.scroll_mouse(dx, dy)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("scroll_mouse", {"dx": dx, "dy": dy})
            return f"scrolled ({dx},{dy})"


def _register_screen(mcp: Any) -> None:
    if plat.supported(cap.SCREEN_CAPTURE_MONITOR):
        @mcp.tool(
            description=(
                "Capture a screenshot. scope ∈ {'monitor', 'window', 'all', "
                "'region'}. For 'region' you must pass x, y, w, h. Returns "
                "{path, bytes}. Read the PNG from `path` if you need pixels."
            )
        )
        def capture_screen(
            scope: str = "monitor",
            save_to: str | None = None,
            x: int = 0, y: int = 0, w: int = 0, h: int = 0,
        ) -> dict[str, Any]:
            if (e := _guard("capture_screen", {"scope": scope})):
                return {"error": e}
            out = Path(save_to) if save_to else SCREENSHOT_FILE
            try:
                if scope == "monitor":
                    p = plat.screen.capture_monitor(out)
                elif scope == "window":
                    p = plat.screen.capture_window(out)
                elif scope == "all":
                    p = plat.screen.capture_all(out)
                elif scope == "region":
                    if w <= 0 or h <= 0:
                        return {"error": "region scope needs w>0 and h>0"}
                    p = plat.screen.capture_region(out, x, y, w, h)
                else:
                    return {"error": f"unknown scope: {scope}"}
            except (BackendError, NotSupportedError) as exc:
                agent.audit("capture_screen", {"scope": scope}, result=str(exc))
                return {"error": str(exc)}
            size = p.stat().st_size
            agent.audit("capture_screen",
                        {"scope": scope, "path": str(p), "bytes": size})
            return {"path": str(p), "bytes": size}

    if plat.supported(cap.SCREEN_LIST_MONITORS):
        @mcp.tool(description="List all monitors with name, geometry, focus state.")
        def list_monitors() -> list[dict[str, Any]]:
            if (e := _guard("list_monitors", {})):
                return [{"error": e}]
            try:
                mons = [m.to_dict() for m in plat.screen.list_monitors()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            agent.audit("list_monitors", {"count": len(mons)})
            return mons


def _register_windows(mcp: Any) -> None:
    if plat.supported(cap.WM_LIST_WINDOWS):
        @mcp.tool(description="List all top-level windows (class, title, geometry).")
        def list_windows() -> list[dict[str, Any]]:
            if (e := _guard("list_windows", {})):
                return [{"error": e}]
            try:
                wins = [w.to_dict() for w in plat.wm.list_windows()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            agent.audit("list_windows", {"count": len(wins)})
            return wins

    if plat.supported(cap.WM_FIND_WINDOWS):
        @mcp.tool(
            description=(
                "Find windows whose class or title contains `needle` "
                "(case-insensitive). Returns at most `limit` matches."
            )
        )
        def find_windows(needle: str, limit: int = 10) -> list[dict[str, Any]]:
            if (e := _guard("find_windows", {"needle": needle})):
                return [{"error": e}]
            try:
                wins = plat.wm.find_windows(needle, limit=max(1, limit))
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            agent.audit("find_windows",
                        {"needle": needle, "count": len(wins)})
            return [w.to_dict() for w in wins]

    if plat.supported(cap.WM_ACTIVE_WINDOW):
        @mcp.tool(description="Return the currently focused window.")
        def focused_window() -> dict[str, Any]:
            if (e := _guard("focused_window", {})):
                return {"error": e}
            try:
                w: Window | None = plat.wm.active_window()
            except (BackendError, NotSupportedError) as exc:
                return {"error": str(exc)}
            agent.audit("focused_window", {"id": w.id if w else None})
            return w.to_dict() if w else {}

    if plat.supported(cap.WM_LIST_WORKSPACES):
        @mcp.tool(description="List all workspaces / virtual desktops.")
        def list_workspaces() -> list[dict[str, Any]]:
            if (e := _guard("list_workspaces", {})):
                return [{"error": e}]
            try:
                ws = [w.to_dict() for w in plat.wm.list_workspaces()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            agent.audit("list_workspaces", {"count": len(ws)})
            return ws

    if plat.supported(cap.WM_ACTIVE_WORKSPACE):
        @mcp.tool(description="Return the currently active workspace.")
        def active_workspace() -> dict[str, Any]:
            if (e := _guard("active_workspace", {})):
                return {"error": e}
            try:
                ws = plat.wm.active_workspace()
            except (BackendError, NotSupportedError) as exc:
                return {"error": str(exc)}
            agent.audit("active_workspace", {"id": ws.id if ws else None})
            return ws.to_dict() if ws else {}

    if plat.supported(cap.WM_FOCUS_WINDOW):
        @mcp.tool(
            description=(
                "Focus a window. `target` is a backend id or a "
                "case-insensitive substring of the class/title."
            )
        )
        def focus_window(target: str) -> str:
            if (e := _guard("focus_window", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.focus_window(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("focus_window", {"target": target})
            return f"focused {target}"

    if plat.supported(cap.WM_CLOSE_WINDOW):
        @mcp.tool(description="Politely close a window (id or class/title substring).")
        def close_window(target: str) -> str:
            if (e := _guard("close_window", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.close_window(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("close_window", {"target": target})
            return f"closed {target}"

    if plat.supported(cap.WM_MOVE_WINDOW):
        @mcp.tool(description="Move a (floating) window to absolute (x, y).")
        def move_window(target: str, x: int, y: int) -> str:
            if (e := _guard("move_window", {"target": target, "x": x, "y": y})):
                return e
            with _acting():
                try:
                    plat.wm.move_window(target, x, y)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("move_window", {"target": target, "x": x, "y": y})
            return f"moved {target} to ({x},{y})"

    if plat.supported(cap.WM_RESIZE_WINDOW):
        @mcp.tool(description="Resize a (floating) window to absolute (w, h).")
        def resize_window(target: str, w: int, h: int) -> str:
            if w <= 0 or h <= 0:
                return f"invalid dims {w}x{h}"
            if (e := _guard("resize_window", {"target": target, "w": w, "h": h})):
                return e
            with _acting():
                try:
                    plat.wm.resize_window(target, w, h)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("resize_window", {"target": target, "w": w, "h": h})
            return f"resized {target} to {w}x{h}"

    if plat.supported(cap.WM_TOGGLE_FLOATING):
        @mcp.tool(description="Toggle floating mode for a window.")
        def toggle_floating(target: str) -> str:
            if (e := _guard("toggle_floating", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.toggle_floating(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("toggle_floating", {"target": target})
            return f"toggled floating {target}"

    if plat.supported(cap.WM_TOGGLE_FULLSCREEN):
        @mcp.tool(
            description=(
                "Toggle fullscreen on `target` (or the active window if "
                "`target` is None)."
            )
        )
        def toggle_fullscreen(target: str | None = None) -> str:
            if (e := _guard("toggle_fullscreen", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.toggle_fullscreen(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("toggle_fullscreen", {"target": target})
            return f"toggled fullscreen {target or '<active>'}"

    if plat.supported(cap.WM_SWITCH_WORKSPACE):
        @mcp.tool(description="Switch to workspace by id or name.")
        def switch_workspace(workspace: str) -> str:
            if (e := _guard("switch_workspace", {"workspace": workspace})):
                return e
            with _acting():
                try:
                    plat.wm.switch_workspace(workspace)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("switch_workspace", {"workspace": workspace})
            return f"switched to workspace {workspace}"

    if plat.supported(cap.WM_MOVE_TO_WORKSPACE):
        @mcp.tool(description="Move `target` window to `workspace` (silent).")
        def move_window_to_workspace(target: str, workspace: str) -> str:
            if (e := _guard("move_window_to_workspace",
                            {"target": target, "workspace": workspace})):
                return e
            with _acting():
                try:
                    plat.wm.move_window_to_workspace(target, workspace)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("move_window_to_workspace",
                        {"target": target, "workspace": workspace})
            return f"moved {target} to workspace {workspace}"

    if plat.supported(cap.WM_SEND_WS_TO_MONITOR):
        @mcp.tool(
            description=(
                "Send the active workspace to another monitor. "
                "`target` ∈ {'l','r','u','d'} or a numeric monitor id."
            )
        )
        def send_workspace_to_monitor(target: str) -> str:
            if (e := _guard("send_workspace_to_monitor", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.send_workspace_to_monitor(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("send_workspace_to_monitor", {"target": target})
            return f"sent active workspace to monitor {target}"


def _register_clipboard(mcp: Any) -> None:
    if plat.supported(cap.CLIPBOARD_READ):
        @mcp.tool(description="Read the system clipboard as text.")
        def clipboard_read() -> str:
            if (e := _guard("clipboard_read", {})):
                return e
            try:
                text = plat.clipboard.read()
            except (BackendError, NotSupportedError) as exc:
                return _err(exc)
            agent.audit("clipboard_read", {"len": len(text)})
            return text

    if plat.supported(cap.CLIPBOARD_WRITE):
        @mcp.tool(description="Write text to the system clipboard.")
        def clipboard_write(text: str) -> str:
            if (e := _guard("clipboard_write", {"len": len(text)})):
                return e
            with _acting():
                try:
                    plat.clipboard.write(text)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("clipboard_write", {"len": len(text)})
            return f"wrote {len(text)} chars to clipboard"


def _register_dialogs(mcp: Any) -> None:
    """Notifications + blocking dialogs (notify-send + kdialog/zenity)."""

    if plat.supported(cap.NOTIFY_SHOW):
        @mcp.tool(description=(
            "Show a desktop notification (non-blocking). "
            "Use for status updates the user can ignore."
        ))
        def notify(title: str, body: str = "", urgency: str = "normal") -> str:
            if (e := _guard("notify", {"len_title": len(title)})):
                return e
            try:
                plat.notify.show(title, body, urgency=urgency)
            except (BackendError, NotSupportedError) as exc:
                return _err(exc)
            agent.audit("notify", {"title": title[:80], "urgency": urgency})
            return "notified"

    if plat.supported(cap.DIALOG_CONFIRM):
        @mcp.tool(description=(
            "Ask the user a Yes/No question via a modal dialog. "
            "BLOCKS until the user answers. Use sparingly — every call "
            "interrupts the user. Returns 'yes' or 'no'."
        ))
        def ask_confirm(message: str, title: str = "Confirm") -> str:
            if (e := _guard("ask_confirm", {"len_msg": len(message)})):
                return e
            with _acting():
                try:
                    ok = plat.dialog.confirm(message, title=title)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("ask_confirm", {"answer": "yes" if ok else "no"})
            return "yes" if ok else "no"

    if plat.supported(cap.DIALOG_ASK_TEXT):
        @mcp.tool(description=(
            "Ask the user for a line of text via a modal input dialog. "
            "BLOCKS until the user submits or cancels. "
            "Returns the text, or empty string if cancelled."
        ))
        def ask_user(prompt: str, default: str = "", title: str = "Input") -> str:
            if (e := _guard("ask_user", {"len_prompt": len(prompt)})):
                return e
            with _acting():
                try:
                    ans = plat.dialog.ask_text(prompt, default=default, title=title)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("ask_user", {"cancelled": ans is None, "len": len(ans or "")})
            return ans if ans is not None else ""

    if plat.supported(cap.DIALOG_ASK_CHOICE):
        @mcp.tool(description=(
            "Ask the user to pick one option from a list via a modal menu. "
            "BLOCKS until the user picks or cancels. "
            "Returns the chosen option, or empty string if cancelled."
        ))
        def ask_choice(prompt: str, choices: list[str], title: str = "Choose") -> str:
            if (e := _guard("ask_choice", {"n": len(choices)})):
                return e
            if not choices:
                return "error: choices must be non-empty"
            with _acting():
                try:
                    ans = plat.dialog.ask_choice(prompt, choices, title=title)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            agent.audit("ask_choice", {"cancelled": ans is None, "choice": ans})
            return ans if ans is not None else ""


def _register_misc(mcp: Any) -> None:
    @mcp.tool(description="Sleep for ms milliseconds. Use to let UIs settle. Max 5000.")
    def sleep_ms(ms: int) -> str:
        ms = max(0, min(ms, 5000))
        time.sleep(ms / 1000.0)
        agent.audit("sleep_ms", {"ms": ms})
        return f"slept {ms}ms"

    @mcp.tool(description="Report the active platform and the wired backend capabilities.")
    def platform_info() -> dict[str, Any]:
        return {
            "platform": plat.active_platform,
            "capabilities": sorted(plat.all_capabilities()),
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def serve() -> None:
    """Run the MCP server over stdio."""
    log(f"MCP server started (platform={plat.active_platform}).")
    try:
        build_server().run(transport="stdio")
    finally:
        agent.release()
        log("MCP server stopped.")
