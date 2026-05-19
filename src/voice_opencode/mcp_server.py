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

from . import agent, capacity, config
from . import platform as plat
from .logging import log
from .notify import turn_update
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

# ---------------------------------------------------------------------------
# Focus guard (ADR-0025): block input-injection tools unless the agent
# explicitly focused a window recently and the WM still reports that
# window as focused. Prevents typing into the wrong window when the
# user was on a different one (e.g. typing into the opencode terminal
# when the agent meant to fill a browser form).
# ---------------------------------------------------------------------------
_FOCUS_GUARD_TTL_S: float = 15.0
_focus_state: dict[str, Any] = {"target": "", "expected_id": "", "ts": 0.0}


def _record_focus(target: str, resolved_id: str | None) -> None:
    """Called from focus_window after a successful focus call."""
    _focus_state["target"] = target
    _focus_state["expected_id"] = resolved_id or ""
    _focus_state["ts"] = time.monotonic()


def _focus_guard(tool: str) -> str | None:
    """Block ``tool`` if no recent focus_window OR the WM no longer
    reports the expected window as active. Returns an error string or
    ``None``. Skipped silently if the WM backend can't tell us the
    active window (no capability)."""
    age = time.monotonic() - _focus_state["ts"]
    if not _focus_state["target"] or age > _FOCUS_GUARD_TTL_S:
        return (
            f"refused: call focus_window first (none in last "
            f"{int(_FOCUS_GUARD_TTL_S)}s). This guard prevents typing "
            f"into the wrong window."
        )
    if not plat.supported(cap.WM_ACTIVE_WINDOW):
        return None  # Can't verify; trust the recent focus_window.
    try:
        active = plat.wm.active_window()
    except (BackendError, NotSupportedError):
        return None  # Treat as best-effort: skip verification.
    expected = _focus_state["expected_id"]
    if expected and active and active.id != expected:
        return (
            f"refused: focused window changed (expected={expected}, "
            f"actual={active.id}). Re-call focus_window before {tool}."
        )
    return None


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
        _audit(tool, args, result="rate_limited")
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


def _fmt_action(tool: str, args: dict[str, Any]) -> str:
    """One-line label for the per-turn notification body. Truncates
    long values so the toast stays readable."""
    parts: list[str] = []
    for k, v in args.items():
        s = repr(v) if isinstance(v, str) else str(v)
        if len(s) > 30:
            s = s[:27] + "…"
        parts.append(f"{k}={s}")
    body = ", ".join(parts)
    out = f"{tool}({body})"
    if len(out) > 80:
        out = out[:79] + "…"
    return out


def _audit(tool: str, args: dict[str, Any], result: str = "ok") -> None:
    """Wrapper around ``agent.audit`` that also updates the per-turn
    persistent notification (ADR-0025) so the user sees what the
    agent is doing in real time. Best-effort: notify failures are
    swallowed."""
    agent.audit(tool, args, result=result)
    if result == "ok":
        try:
            turn_update("⚙️ Agente actuando", _fmt_action(tool, args))
        except Exception:
            pass


def _err(e: Exception) -> str:
    return f"error: {e}"


def _expose(capability: str, tool_name: str) -> bool:
    """Tool is registered iff backend supports it AND capacity tier allows it."""
    return plat.supported(capability) and capacity.allows(tool_name)


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
    _register_audio(mcp)
    _register_media(mcp)
    _register_apps(mcp)
    _register_shell(mcp)
    _register_ocr(mcp)
    _register_memory(mcp)
    _register_misc(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Tool groups
# ---------------------------------------------------------------------------
def _register_input(mcp: Any) -> None:
    if _expose(cap.INPUT_TYPE_TEXT, "type_text"):
        @mcp.tool(description="Type literal text into the focused window.")
        def type_text(text: str, delay_ms: int = 12) -> str:
            if (e := _guard("type_text", {"len": len(text), "delay_ms": delay_ms})):
                return e
            if (e := _focus_guard("type_text")):
                _audit("type_text", {"len": len(text)}, result=e)
                return e
            with _acting():
                try:
                    plat.input.type_text(text, delay_ms=delay_ms)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("type_text", {"len": len(text)})
            return f"typed {len(text)} chars"

    if _expose(cap.INPUT_PRESS_KEY, "press_key"):
        @mcp.tool(
            description=(
                "Press a key or combination, e.g. 'Tab', 'Return', 'ctrl+a'. "
                "Modifiers: ctrl, shift, alt, super."
            )
        )
        def press_key(combo: str) -> str:
            if _is_dangerous_key(combo):
                _audit("press_key", {"combo": combo}, result="blocked")
                return f"blocked: '{combo}' is on the safety blocklist"
            if (e := _guard("press_key", {"combo": combo})):
                return e
            if (e := _focus_guard("press_key")):
                _audit("press_key", {"combo": combo}, result=e)
                return e
            with _acting():
                try:
                    plat.input.press_key(combo)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("press_key", {"combo": combo})
            return f"pressed {combo}"

    if _expose(cap.INPUT_MOVE_MOUSE, "move_mouse"):
        @mcp.tool(description="Move the mouse cursor to absolute (x, y).")
        def move_mouse(x: int, y: int) -> str:
            if (e := _guard("move_mouse", {"x": x, "y": y})):
                return e
            with _acting():
                try:
                    plat.input.move_mouse(x, y, absolute=True)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("move_mouse", {"x": x, "y": y})
            return f"moved to ({x},{y})"

    if _expose(cap.INPUT_CLICK_MOUSE, "click_mouse"):
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
            if (e := _focus_guard("click_mouse")):
                _audit("click_mouse",
                            {"button": button, "x": x, "y": y}, result=e)
                return e
            with _acting():
                try:
                    plat.input.click_mouse(button, x, y)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("click_mouse", {"button": button, "x": x, "y": y})
            return (f"clicked {button} at ({x},{y})"
                    if x is not None else f"clicked {button}")

    if _expose(cap.INPUT_SCROLL_MOUSE, "scroll_mouse"):
        @mcp.tool(description="Scroll the mouse wheel by (dx, dy) ticks.")
        def scroll_mouse(dx: int = 0, dy: int = 0) -> str:
            if (e := _guard("scroll_mouse", {"dx": dx, "dy": dy})):
                return e
            with _acting():
                try:
                    plat.input.scroll_mouse(dx, dy)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("scroll_mouse", {"dx": dx, "dy": dy})
            return f"scrolled ({dx},{dy})"


def _register_screen(mcp: Any) -> None:
    if _expose(cap.SCREEN_CAPTURE_MONITOR, "capture_screen"):
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
                _audit("capture_screen", {"scope": scope}, result=str(exc))
                return {"error": str(exc)}
            size = p.stat().st_size
            _audit("capture_screen",
                        {"scope": scope, "path": str(p), "bytes": size})
            return {"path": str(p), "bytes": size}

    if _expose(cap.SCREEN_LIST_MONITORS, "list_monitors"):
        @mcp.tool(description="List all monitors with name, geometry, focus state.")
        def list_monitors() -> list[dict[str, Any]]:
            if (e := _guard("list_monitors", {})):
                return [{"error": e}]
            try:
                mons = [m.to_dict() for m in plat.screen.list_monitors()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            _audit("list_monitors", {"count": len(mons)})
            return mons


def _register_windows(mcp: Any) -> None:
    if _expose(cap.WM_LIST_WINDOWS, "list_windows"):
        @mcp.tool(description="List all top-level windows (class, title, geometry).")
        def list_windows() -> list[dict[str, Any]]:
            if (e := _guard("list_windows", {})):
                return [{"error": e}]
            try:
                wins = [w.to_dict() for w in plat.wm.list_windows()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            _audit("list_windows", {"count": len(wins)})
            return wins

    if _expose(cap.WM_FIND_WINDOWS, "find_windows"):
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
            _audit("find_windows",
                        {"needle": needle, "count": len(wins)})
            return [w.to_dict() for w in wins]

    if _expose(cap.WM_ACTIVE_WINDOW, "focused_window"):
        @mcp.tool(description="Return the currently focused window.")
        def focused_window() -> dict[str, Any]:
            if (e := _guard("focused_window", {})):
                return {"error": e}
            try:
                w: Window | None = plat.wm.active_window()
            except (BackendError, NotSupportedError) as exc:
                return {"error": str(exc)}
            _audit("focused_window", {"id": w.id if w else None})
            return w.to_dict() if w else {}

    if _expose(cap.WM_LIST_WORKSPACES, "list_workspaces"):
        @mcp.tool(description="List all workspaces / virtual desktops.")
        def list_workspaces() -> list[dict[str, Any]]:
            if (e := _guard("list_workspaces", {})):
                return [{"error": e}]
            try:
                ws = [w.to_dict() for w in plat.wm.list_workspaces()]
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            _audit("list_workspaces", {"count": len(ws)})
            return ws

    if _expose(cap.WM_ACTIVE_WORKSPACE, "active_workspace"):
        @mcp.tool(description="Return the currently active workspace.")
        def active_workspace() -> dict[str, Any]:
            if (e := _guard("active_workspace", {})):
                return {"error": e}
            try:
                ws = plat.wm.active_workspace()
            except (BackendError, NotSupportedError) as exc:
                return {"error": str(exc)}
            _audit("active_workspace", {"id": ws.id if ws else None})
            return ws.to_dict() if ws else {}

    if _expose(cap.WM_FOCUS_WINDOW, "focus_window"):
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
            # Record which window is now expected to hold focus, so the
            # focus_guard below can verify type/click/press happen on it.
            resolved_id: str | None = None
            if plat.supported(cap.WM_ACTIVE_WINDOW):
                try:
                    aw = plat.wm.active_window()
                    resolved_id = aw.id if aw else None
                except (BackendError, NotSupportedError):
                    pass
            _record_focus(target, resolved_id)
            _audit("focus_window",
                        {"target": target, "resolved_id": resolved_id})
            return f"focused {target}"

    if _expose(cap.WM_CLOSE_WINDOW, "close_window"):
        @mcp.tool(description="Politely close a window (id or class/title substring).")
        def close_window(target: str) -> str:
            if (e := _guard("close_window", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.close_window(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("close_window", {"target": target})
            return f"closed {target}"

    if _expose(cap.WM_MOVE_WINDOW, "move_window"):
        @mcp.tool(description="Move a (floating) window to absolute (x, y).")
        def move_window(target: str, x: int, y: int) -> str:
            if (e := _guard("move_window", {"target": target, "x": x, "y": y})):
                return e
            with _acting():
                try:
                    plat.wm.move_window(target, x, y)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("move_window", {"target": target, "x": x, "y": y})
            return f"moved {target} to ({x},{y})"

    if _expose(cap.WM_RESIZE_WINDOW, "resize_window"):
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
            _audit("resize_window", {"target": target, "w": w, "h": h})
            return f"resized {target} to {w}x{h}"

    if _expose(cap.WM_TOGGLE_FLOATING, "toggle_floating"):
        @mcp.tool(description="Toggle floating mode for a window.")
        def toggle_floating(target: str) -> str:
            if (e := _guard("toggle_floating", {"target": target})):
                return e
            with _acting():
                try:
                    plat.wm.toggle_floating(target)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("toggle_floating", {"target": target})
            return f"toggled floating {target}"

    if _expose(cap.WM_TOGGLE_FULLSCREEN, "toggle_fullscreen"):
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
            _audit("toggle_fullscreen", {"target": target})
            return f"toggled fullscreen {target or '<active>'}"

    if _expose(cap.WM_SWITCH_WORKSPACE, "switch_workspace"):
        @mcp.tool(description="Switch to workspace by id or name.")
        def switch_workspace(workspace: str) -> str:
            if (e := _guard("switch_workspace", {"workspace": workspace})):
                return e
            with _acting():
                try:
                    plat.wm.switch_workspace(workspace)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("switch_workspace", {"workspace": workspace})
            return f"switched to workspace {workspace}"

    if _expose(cap.WM_MOVE_TO_WORKSPACE, "move_window_to_workspace"):
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
            _audit("move_window_to_workspace",
                        {"target": target, "workspace": workspace})
            return f"moved {target} to workspace {workspace}"

    if _expose(cap.WM_SEND_WS_TO_MONITOR, "send_workspace_to_monitor"):
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
            _audit("send_workspace_to_monitor", {"target": target})
            return f"sent active workspace to monitor {target}"


def _register_clipboard(mcp: Any) -> None:
    if _expose(cap.CLIPBOARD_READ, "clipboard_read"):
        @mcp.tool(description="Read the system clipboard as text.")
        def clipboard_read() -> str:
            if (e := _guard("clipboard_read", {})):
                return e
            try:
                text = plat.clipboard.read()
            except (BackendError, NotSupportedError) as exc:
                return _err(exc)
            _audit("clipboard_read", {"len": len(text)})
            return text

    if _expose(cap.CLIPBOARD_WRITE, "clipboard_write"):
        @mcp.tool(description="Write text to the system clipboard.")
        def clipboard_write(text: str) -> str:
            if (e := _guard("clipboard_write", {"len": len(text)})):
                return e
            with _acting():
                try:
                    plat.clipboard.write(text)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("clipboard_write", {"len": len(text)})
            return f"wrote {len(text)} chars to clipboard"


def _register_dialogs(mcp: Any) -> None:
    """Notifications + blocking dialogs (notify-send + kdialog/zenity)."""

    if _expose(cap.NOTIFY_SHOW, "notify"):
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
            _audit("notify", {"title": title[:80], "urgency": urgency})
            return "notified"

    if _expose(cap.DIALOG_CONFIRM, "ask_confirm"):
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
            _audit("ask_confirm", {"answer": "yes" if ok else "no"})
            return "yes" if ok else "no"

    if _expose(cap.DIALOG_ASK_TEXT, "ask_user"):
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
            _audit("ask_user", {"cancelled": ans is None, "len": len(ans or "")})
            return ans if ans is not None else ""

    if _expose(cap.DIALOG_ASK_CHOICE, "ask_choice"):
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
            _audit("ask_choice", {"cancelled": ans is None, "choice": ans})
            return ans if ans is not None else ""


def _register_audio(mcp: Any) -> None:
    """Output/mic volume + mute (wpctl on Linux/PipeWire)."""

    if _expose(cap.AUDIO_VOLUME_GET, "audio_get_volume"):
        @mcp.tool(description=(
            "Read the current output volume as a float in 0.0-1.0."
        ))
        def audio_get_volume() -> str:
            if (e := _guard("audio_get_volume", {})):
                return e
            try:
                level = plat.audio.volume_get()
            except (BackendError, NotSupportedError) as exc:
                return _err(exc)
            _audit("audio_get_volume", {"level": level})
            return f"{level:.2f}"

    if _expose(cap.AUDIO_VOLUME_SET, "audio_set_volume"):
        @mcp.tool(description=(
            "Set the output volume. `level` is clamped to 0.0-1.0. "
            "1.0 is 100%; higher values are silently capped."
        ))
        def audio_set_volume(level: float) -> str:
            if (e := _guard("audio_set_volume", {"level": level})):
                return e
            with _acting():
                try:
                    plat.audio.volume_set(level)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("audio_set_volume", {"level": level})
            return f"set volume to {max(0.0, min(1.0, level)):.2f}"

    if _expose(cap.AUDIO_MUTE_TOGGLE, "audio_mute_toggle"):
        @mcp.tool(description=(
            "Toggle output mute. Returns 'muted' or 'unmuted' "
            "reflecting the NEW state."
        ))
        def audio_mute_toggle() -> str:
            if (e := _guard("audio_mute_toggle", {})):
                return e
            with _acting():
                try:
                    muted = plat.audio.mute_toggle()
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("audio_mute_toggle", {"muted": muted})
            return "muted" if muted else "unmuted"

    if _expose(cap.AUDIO_MIC_MUTE_TOGGLE, "audio_mic_mute_toggle"):
        @mcp.tool(description=(
            "Toggle microphone mute. Returns 'muted' or 'unmuted' "
            "reflecting the NEW state."
        ))
        def audio_mic_mute_toggle() -> str:
            if (e := _guard("audio_mic_mute_toggle", {})):
                return e
            with _acting():
                try:
                    muted = plat.audio.mic_mute_toggle()
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("audio_mic_mute_toggle", {"muted": muted})
            return "muted" if muted else "unmuted"


def _register_media(mcp: Any) -> None:
    """MPRIS transport control (playerctl on Linux)."""

    if _expose(cap.MEDIA_PLAY_PAUSE, "media_play_pause"):
        @mcp.tool(description=(
            "Toggle play/pause on the currently active MPRIS player "
            "(browser tab, mpv, Spotify, ...). Same as the keyboard "
            "media key."
        ))
        def media_play_pause() -> str:
            if (e := _guard("media_play_pause", {})):
                return e
            with _acting():
                try:
                    plat.media.play_pause()
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("media_play_pause", {})
            return "toggled"

    if _expose(cap.MEDIA_NEXT, "media_next"):
        @mcp.tool(description="Skip to the next track on the active player.")
        def media_next() -> str:
            if (e := _guard("media_next", {})):
                return e
            with _acting():
                try:
                    plat.media.next()
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("media_next", {})
            return "next"

    if _expose(cap.MEDIA_PREV, "media_prev"):
        @mcp.tool(description="Go to the previous track on the active player.")
        def media_prev() -> str:
            if (e := _guard("media_prev", {})):
                return e
            with _acting():
                try:
                    plat.media.prev()
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("media_prev", {})
            return "prev"

    if _expose(cap.MEDIA_STATUS, "media_status"):
        @mcp.tool(description=(
            "Report the active MPRIS player and current track. "
            "Returns {player, status, title, artist} as a dict."
        ))
        def media_status() -> dict[str, Any]:
            if (e := _guard("media_status", {})):
                return {"error": e}
            try:
                d = plat.media.status()
            except (BackendError, NotSupportedError) as exc:
                return {"error": str(exc)}
            _audit("media_status",
                        {"player": d.get("player"), "status": d.get("status")})
            return d


def _register_apps(mcp: Any) -> None:
    """Desktop app launcher (xdg + /proc on Linux)."""

    if _expose(cap.APP_LIST_INSTALLED, "apps_list_installed"):
        @mcp.tool(description=(
            "List installed desktop apps from XDG ``applications`` "
            "directories. Returns a list of {id, name, exec, icon, "
            "no_display}. ``id`` is the .desktop filename without "
            "extension and is what you pass to apps_launch."
        ))
        def apps_list_installed() -> list[dict[str, Any]]:
            if (e := _guard("apps_list_installed", {})):
                return [{"error": e}]
            try:
                d = plat.apps.list_installed()
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            _audit("apps_list_installed", {"count": len(d)})
            return d

    if _expose(cap.APP_LIST_RUNNING, "apps_list_running"):
        @mcp.tool(description=(
            "List running processes whose ``comm`` matches a known "
            "app id. Returns {pid, comm, app_id?}; ``app_id`` is "
            "absent for processes not tied to a known desktop entry."
        ))
        def apps_list_running() -> list[dict[str, Any]]:
            if (e := _guard("apps_list_running", {})):
                return [{"error": e}]
            try:
                d = plat.apps.list_running()
            except (BackendError, NotSupportedError) as exc:
                return [{"error": str(exc)}]
            _audit("apps_list_running", {"count": len(d)})
            return d

    if _expose(cap.APP_LAUNCH, "apps_launch"):
        @mcp.tool(description=(
            "Launch an installed app by ``id`` (preferred — uses "
            "gtk-launch) or a raw shell-style command. Returns the "
            "new pid (0 if it couldn't be determined for a "
            "detached gtk-launch)."
        ))
        def apps_launch(app_id_or_cmd: str) -> dict[str, Any]:
            if (e := _guard("apps_launch", {"target": app_id_or_cmd})):
                return {"error": e}
            with _acting():
                try:
                    pid = plat.apps.launch(app_id_or_cmd)
                except (BackendError, NotSupportedError) as exc:
                    return {"error": str(exc)}
            _audit("apps_launch",
                        {"target": app_id_or_cmd, "pid": pid})
            return {"pid": pid}

    if _expose(cap.APP_KILL, "apps_kill"):
        @mcp.tool(description=(
            "SIGTERM a process by pid (int) or every running pid of "
            "an installed app id (string). Raises if no match."
        ))
        def apps_kill(pid_or_app_id: int | str) -> str:
            if (e := _guard("apps_kill", {"target": pid_or_app_id})):
                return e
            with _acting():
                try:
                    plat.apps.kill(pid_or_app_id)
                except (BackendError, NotSupportedError) as exc:
                    return _err(exc)
            _audit("apps_kill", {"target": pid_or_app_id})
            return "killed"


def _register_shell(mcp: Any) -> None:
    """Arbitrary shell exec with safety rails (Phase E).

    Tier-gated to ``full`` only (see ``capacity.TIER_BY_TOOL``).
    The backend enforces a default-deny allowlist on top — even
    in ``full``, only commands whose basename fullmatches a regex
    in ``settings.shell_allowlist`` can be executed. See ADR-0017.
    """

    if _expose(cap.SHELL_RUN, "shell_run"):
        @mcp.tool(description=(
            "Execute a shell command with rails. argv is parsed with "
            "shlex; shell metacharacters (|, ;, &&, $(...), `...`, >) "
            "are rejected. argv[0] basename must fullmatch an entry in "
            "the user's allowlist. Defaults to dry_run=True; pass "
            "dry_run=False to actually run. Returns {rc, stdout, "
            "stderr, dry_run, cmd, cwd}; rc=-1 means timeout."
        ))
        def shell_run(
            cmd: str,
            cwd: str | None = None,
            timeout: float = 10.0,
            dry_run: bool = True,
        ) -> dict[str, Any]:
            if (e := _guard("shell_run", {"cmd": cmd, "dry_run": dry_run})):
                return {"error": e}
            with _acting():
                try:
                    r = plat.shell.run(cmd, cwd=cwd,
                                       timeout=timeout, dry_run=dry_run)
                except (BackendError, NotSupportedError) as exc:
                    _audit("shell_run",
                                {"cmd": cmd, "denied": True}, str(exc))
                    return {"error": str(exc)}
            _audit("shell_run", {
                "cmd":     r.get("cmd"),
                "cwd":     r.get("cwd"),
                "rc":      r.get("rc"),
                "dry_run": r.get("dry_run"),
                "stdout_len": len(r.get("stdout", "")),
                "stderr_len": len(r.get("stderr", "")),
            })
            return r


def _register_ocr(mcp: Any) -> None:
    """OCR on a captured screen region or arbitrary PNG (Phase F).

    Two tools, both read-only:

    * ``screen_find_text(needle, region=None)`` — convenience that
      captures (focused monitor or region) and OCRs in one call.
      This is the composed helper alluded to in ADR-0018.
    * ``ocr_find_text_in_file(path, needle)`` — OCR a PNG the agent
      already has (e.g. a previous screenshot it cached). Lets the
      agent reuse pixels without re-capturing.

    Both return ``[{text, rect:{x,y,w,h}, confidence}]`` in reading
    order, empty list when nothing matches.
    """

    if _expose(cap.OCR_FIND_TEXT, "screen_find_text"):
        @mcp.tool(description=(
            "Capture the focused monitor (or a region) and find every "
            "occurrence of `needle` on screen via OCR. Returns a list "
            "of {text, rect, confidence} in reading order; empty list "
            "if nothing matches. `region` is an optional [x,y,w,h] in "
            "screen pixels — much faster than a full 4K capture. "
            "Bounding boxes are returned in screen coordinates (i.e. "
            "shifted by the region offset when a region is used)."
        ))
        def screen_find_text(
            needle: str,
            region: list[int] | None = None,
        ) -> list[dict[str, Any]]:
            if (e := _guard("screen_find_text",
                            {"needle": needle, "region": region})):
                return [{"error": e}]
            import tempfile
            from pathlib import Path as _P
            tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            png = _P(tf.name)
            tf.close()
            try:
                if region:
                    if len(region) != 4:
                        return [{"error": "region must be [x,y,w,h]"}]
                    plat.screen.capture_region(png, *region)
                    ox, oy = region[0], region[1]
                else:
                    plat.screen.capture_monitor(png)
                    ox, oy = 0, 0
                matches = plat.ocr.find_text(png, needle)
            except (BackendError, NotSupportedError) as exc:
                _audit("screen_find_text",
                            {"needle": needle, "region": region}, str(exc))
                return [{"error": str(exc)}]
            finally:
                png.unlink(missing_ok=True)
            out = []
            for m in matches:
                d = m.to_dict()
                d["rect"]["x"] += ox
                d["rect"]["y"] += oy
                out.append(d)
            _audit("screen_find_text", {
                "needle": needle, "region": region, "matches": len(out),
            })
            return out

    if _expose(cap.OCR_FIND_TEXT, "ocr_find_text_in_file"):
        @mcp.tool(description=(
            "OCR an existing PNG file on disk and return every match "
            "of `needle`. Useful when the agent already has a "
            "screenshot or a downloaded image and doesn't want to "
            "re-capture. Same output shape as screen_find_text."
        ))
        def ocr_find_text_in_file(
            path: str,
            needle: str,
        ) -> list[dict[str, Any]]:
            if (e := _guard("ocr_find_text_in_file",
                            {"path": path, "needle": needle})):
                return [{"error": e}]
            from pathlib import Path as _P
            try:
                matches = plat.ocr.find_text(_P(path), needle)
            except (BackendError, NotSupportedError) as exc:
                _audit("ocr_find_text_in_file",
                            {"path": path, "needle": needle}, str(exc))
                return [{"error": str(exc)}]
            _audit("ocr_find_text_in_file", {
                "path": path, "needle": needle, "matches": len(matches),
            })
            return [m.to_dict() for m in matches]


def _register_memory(mcp: Any) -> None:
    """Plain-Markdown agent memory (Phase G).

    Four tools — three read-only readers (search/recent/list_days)
    plus one assist-tier writer (append). The append tool writes to
    ``memory/YYYY-MM-DD.md`` in the project root; the agent uses
    this to persist context across opencode sessions (ADR-0019).

    No capability gate: this is pure filesystem text on our own repo,
    not a desktop capability. We only gate by capacity tier.
    """
    from . import memory as mem  # local import: stdlib-only module

    def _entry_to_dict(e: mem.MemoryEntry) -> dict[str, Any]:
        return {
            "ts":   e.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "tags": list(e.tags),
            "body": e.body,
            "file": e.file.name,
        }

    if capacity.allows("memory_search"):
        @mcp.tool(description=(
            "Search the agent's plain-Markdown memory. Case-insensitive "
            "substring match over body and tags. Returns newest first. "
            "Empty query returns []. Each result: {ts, tags, body, file}."
        ))
        def memory_search(query: str, limit: int = 20) -> list[dict[str, Any]]:
            results = mem.search(query, limit=limit)
            _audit("memory_search",
                        {"query": query, "limit": limit, "matches": len(results)})
            return [_entry_to_dict(e) for e in results]

    if capacity.allows("memory_recent"):
        @mcp.tool(description=(
            "Return the most recent N entries across all days, newest first. "
            "Useful at the start of a session to recover context. Each "
            "result: {ts, tags, body, file}."
        ))
        def memory_recent(n: int = 10) -> list[dict[str, Any]]:
            results = mem.recent(n)
            _audit("memory_recent", {"n": n, "matches": len(results)})
            return [_entry_to_dict(e) for e in results]

    if capacity.allows("memory_list_days"):
        @mcp.tool(description=(
            "List every day (YYYY-MM-DD) that has at least one memory "
            "entry, newest first. Useful before calling memory_search "
            "to see how much history exists."
        ))
        def memory_list_days() -> list[str]:
            days = mem.list_days()
            _audit("memory_list_days", {"days": len(days)})
            return days

    if capacity.allows("memory_append"):
        @mcp.tool(description=(
            "Append a new entry to today's memory file. Body is free-form "
            "Markdown; tags are optional short strings (no commas or ']'). "
            "Returns {ts, tags, body, file}. Use this to persist anything "
            "you want to remember in future sessions: decisions, fixes, "
            "context about the user's environment."
        ))
        def memory_append(
            text: str,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            tag_tuple = tuple(tags) if tags else ()
            try:
                entry = mem.append(text, tags=tag_tuple)
            except ValueError as e:
                _audit("memory_append",
                            {"chars": len(text), "denied": True}, str(e))
                return {"error": str(e)}
            _audit("memory_append", {
                "ts":   entry.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "tags": list(entry.tags),
                "chars": len(entry.body),
                "file":  entry.file.name,
            })
            return _entry_to_dict(entry)

    if capacity.allows("memory_delete"):
        @mcp.tool(description=(
            "Delete a memory entry by its ISO timestamp (the 'ts' field "
            "returned by memory_append / memory_search / memory_recent, "
            "format 'YYYY-MM-DD HH:MM:SS'). Returns the deleted entry. "
            "If the day file becomes empty it is removed. Destructive: "
            "only available in the full capacity tier."
        ))
        def memory_delete(ts: str) -> dict[str, Any]:
            try:
                entry = mem.delete(ts)
            except ValueError as e:
                _audit("memory_delete",
                            {"ts": ts, "denied": True}, str(e))
                return {"error": str(e)}
            _audit("memory_delete", {
                "ts":   entry.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "tags": list(entry.tags),
                "file": entry.file.name,
            })
            return _entry_to_dict(entry)

    if capacity.allows("memory_edit"):
        @mcp.tool(description=(
            "Replace the body of an existing memory entry. Identifies the "
            "entry by its ISO timestamp ('YYYY-MM-DD HH:MM:SS'). The ts "
            "and tags are preserved; only the body changes. Same body "
            "rules as memory_append (non-empty, no '## ' header lines). "
            "Destructive: only available in the full capacity tier."
        ))
        def memory_edit(ts: str, new_text: str) -> dict[str, Any]:
            try:
                entry = mem.edit(ts, new_text)
            except ValueError as e:
                _audit("memory_edit",
                            {"ts": ts, "denied": True}, str(e))
                return {"error": str(e)}
            _audit("memory_edit", {
                "ts":    entry.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "tags":  list(entry.tags),
                "chars": len(entry.body),
                "file":  entry.file.name,
            })
            return _entry_to_dict(entry)


def _register_misc(mcp: Any) -> None:
    if capacity.allows("sleep_ms"):
        @mcp.tool(description="Sleep for ms milliseconds. Use to let UIs settle. Max 5000.")
        def sleep_ms(ms: int) -> str:
            ms = max(0, min(ms, 5000))
            time.sleep(ms / 1000.0)
            _audit("sleep_ms", {"ms": ms})
            return f"slept {ms}ms"

    if capacity.allows("platform_info"):
        @mcp.tool(description=(
            "Report the host snapshot the agent is running on: active "
            "platform string ('linux-hyprland', 'linux-x11', etc.), "
            "Wayland/X11 session type, desktop environment, set of "
            "detected desktop tools on PATH (hyprctl, kdialog, wpctl, "
            "playerctl, tesseract, …), captured XDG env vars, and the "
            "wired backend capability set. Useful as a first call to "
            "decide which downstream tools will actually work — e.g. "
            "check 'kdialog' in tools before calling ask_user, or "
            "is_hyprland before workspace ops."
        ))
        def platform_info() -> dict[str, Any]:
            info = plat.platform_info()
            return {
                **info.to_dict(),
                "override":      config.settings.platform_override,
                "capabilities":  sorted(plat.all_capabilities()),
                "capacity_mode": capacity.current_mode(),
            }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def serve() -> None:
    """Run the MCP server over stdio."""
    mode = capacity.current_mode()
    exposed = sorted(capacity.tools_for(mode))
    log(f"MCP server started (platform={plat.active_platform}, "
        f"capacity={mode}, tools={len(exposed)}).")
    try:
        build_server().run(transport="stdio")
    finally:
        agent.release()
        log("MCP server stopped.")
