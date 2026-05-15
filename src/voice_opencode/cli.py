"""
Command-line interface for voice-opencode.

Two surfaces share the same backend:

* **New grouped CLI** — what we recommend going forward::

      voice rec start | stop | toggle | status
      voice tray
      voice session reset | status
      voice tts say "hola" | voices
      voice ask "..." [--no-tts]
      voice config show | init | set <k> <v> | path | get <k>
      voice state                      (JSON for the tray)
      voice pause | resume
      voice desktop type "..." | key <combo> | click [btn] [x y]
                    | move <x> <y> | capture [scope] [path] | focused

* **Legacy flat aliases** — preserved so existing Hyprland binds and
  scripts keep working without changes. They print a deprecation hint
  to stderr only when ``VOICE_DEPRECATION_WARN=1`` so we don't spam logs.

      voice start | stop | toggle | reset | status | voices | say | ask
      voice type | key | click | move | capture | focused

The dispatcher is hand-rolled rather than ``argparse`` subparsers because
we need to (a) accept the legacy flat layout and (b) pass arbitrary
trailing strings (for ``say "hola que tal"``) without quoting tricks.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import agent, audio, config, desktop, paths, pipeline, screenshot, state, tts
from . import platform as plat
from .logging import log
from .notify import notify
from .opencode_client import Session, health
from .platform.base import BackendError, NotSupportedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


def _deprecated(old: str, new: str) -> None:
    if os.environ.get("VOICE_DEPRECATION_WARN") == "1":
        _eprint(f"[voice] '{old}' is deprecated, use '{new}'")


def _usage(rc: int = 1) -> int:
    _eprint(__doc__ or "")
    return rc


# ---------------------------------------------------------------------------
# Command implementations (UI-agnostic)
# ---------------------------------------------------------------------------
def cmd_rec(args: list[str]) -> int:
    if not args:
        _eprint("Usage: voice rec [start|stop|toggle|status]")
        return 1
    sub = args[0]
    if sub == "start":
        pipeline.start_recording()
    elif sub == "stop":
        pipeline.stop_and_run()
    elif sub == "toggle":
        pipeline.toggle()
    elif sub == "status":
        print("recording:", audio.is_recording())
    else:
        _eprint(f"Unknown: rec {sub}")
        return 1
    return 0


def cmd_session(args: list[str]) -> int:
    sub = args[0] if args else "status"
    if sub == "reset":
        Session.forget()
        log("Session forgotten.")
        notify("🆕 Sesión reiniciada", "")
    elif sub in ("status", "id"):
        sid = Session.current_id()
        print(sid or "<none>")
    else:
        _eprint(f"Unknown: session {sub}")
        return 1
    return 0


def cmd_tts(args: list[str]) -> int:
    if not args:
        _eprint("Usage: voice tts [say <text>|voices]")
        return 1
    sub, rest = args[0], args[1:]
    if sub == "say":
        tts.speak(" ".join(rest))
    elif sub == "voices":
        _print_voices()
    else:
        _eprint(f"Unknown: tts {sub}")
        return 1
    return 0


def _print_voices() -> None:
    cur = tts.current_voice().stem
    print(f"Voices in {paths.VOICES_DIR}:")
    for v in tts.list_voices():
        mark = "*" if v.stem == cur else " "
        speakers = ",".join(v.speakers) if v.speakers else "single"
        print(f"{mark} {v.stem:35s} speakers={speakers:10s} {v.sample_rate} Hz")
    print(f"\nCurrent: {cur} (speaker_id={config.settings.speaker_id})")
    print("Change permanently:  ./voice config set voice <NAME>")
    print("Add a new voice:     ./download-voice.sh <lang>/<region>/<name>/<quality>")


def cmd_ask(args: list[str]) -> int:
    no_tts = "--no-tts" in args
    if no_tts:
        args = [a for a in args if a != "--no-tts"]
    if not args:
        _eprint("Usage: voice ask <text...> [--no-tts]")
        return 1
    msg = " ".join(args)
    shot = screenshot.capture()
    try:
        reply = Session.get_or_create().ask(msg, screenshot=shot)
    except Exception as e:
        _eprint(f"opencode error: {e}")
        notify("❌ opencode", str(e)[:200], urgency="critical")
        return 1
    print(reply)
    if not no_tts:
        try:
            tts.speak(reply)
        except Exception as e:
            _eprint(f"TTS error: {e}")
            return 1
    return 0


def cmd_config(args: list[str]) -> int:
    sub = args[0] if args else "show"
    if sub == "show":
        print(json.dumps(config.as_dict(), indent=2, ensure_ascii=False))
    elif sub == "init":
        try:
            config.write_defaults()
            print(f"Wrote defaults to {paths.CONFIG_FILE}")
        except FileExistsError as e:
            _eprint(str(e))
            return 1
    elif sub == "set" and len(args) >= 3:
        key, val = args[1], args[2]
        try:
            parsed = config.set_value(key, val)
            print(f"Set {key} = {parsed}  →  {paths.CONFIG_FILE}")
        except KeyError as e:
            _eprint(str(e))
            return 1
    elif sub == "get" and len(args) >= 2:
        d = config.as_dict()
        if args[1] not in d:
            _eprint(f"Unknown key: {args[1]}")
            return 1
        print(d[args[1]])
    elif sub == "path":
        print(paths.CONFIG_FILE)
    else:
        _eprint("Usage: voice config [show|init|get <k>|set <k> <v>|path]")
        return 1
    return 0


def cmd_state(_: list[str]) -> int:
    sid = Session.current_id()
    out = {
        "state":      state.get_state(),
        "paused":     state.is_paused(),
        "agent":      agent.is_active(),
        "recording":  audio.is_recording(),
        "server":     health(),
        "session":    sid,
        "voice":      config.settings.voice,
        "speaker_id": config.settings.speaker_id,
        "screenshot": config.settings.screenshot,
        "context":    config.settings.keep_context,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_pause(_: list[str]) -> int:
    state.set_paused(True)
    log("Paused.")
    notify("⏸  Voice en pausa", "F9 ignorado hasta que reanudes")
    return 0


def cmd_resume(_: list[str]) -> int:
    state.set_paused(False)
    log("Resumed.")
    notify("▶  Voice reanudado", "")
    return 0


def cmd_tray(_: list[str]) -> int:
    # Re-exec into the tray module, replacing this process.
    os.execv(sys.executable, [sys.executable, "-m", "voice_opencode.tray"])


def cmd_status(_: list[str]) -> int:
    s = config.settings
    print("recording:       ", audio.is_recording())
    print("server:          ", "up" if health() else "down")
    print("session:         ", Session.current_id() or "<none>")
    print()
    print("voice:           ", s.voice, f"(speaker_id={s.speaker_id})")
    print("whisper model:   ", s.whisper_model, f"(lang={s.whisper_lang})")
    print("keep context:    ", s.keep_context)
    print("screenshot:      ", s.screenshot, f"(scope={s.screenshot_scope})")
    print("notify:          ", s.notify)
    print()
    exists = "(exists)" if paths.CONFIG_FILE.exists() else "(missing — using defaults)"
    print(f"config file:      {paths.CONFIG_FILE} {exists}")
    return 0


# ---- mcp subgroup -----------------------------------------------------------
def cmd_mcp(args: list[str]) -> int:
    """
    voice mcp serve            — run the MCP server over stdio (for opencode)
    voice mcp status           — is an agent currently in control?
    voice mcp stop             — kill any running MCP server (release the lock)
    voice mcp log [-n N]       — tail the audit log
    """
    sub = args[0] if args else "status"
    if sub == "serve":
        from . import mcp_server
        mcp_server.serve()
        return 0
    if sub == "status":
        active = agent.is_active()
        print("agent:", "active" if active else "idle")
        return 0
    if sub == "stop":
        import signal
        import subprocess as sp
        # Find any running 'voice_opencode.mcp_server' or 'voice mcp serve' processes.
        try:
            r = sp.run(
                ["pgrep", "-f", "voice_opencode.*mcp"],
                capture_output=True, text=True,
            )
            killed = 0
            for pid in r.stdout.split():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    killed += 1
                except Exception:
                    pass
            agent.release()
            print(f"stopped {killed} mcp process(es); lock released")
        except FileNotFoundError:
            agent.release()
            print("pgrep not found; released lock only")
        return 0
    if sub == "log":
        n = 50
        if len(args) >= 3 and args[1] == "-n":
            try:
                n = int(args[2])
            except ValueError:
                pass
        if not paths.AGENT_LOG_FILE.exists():
            print("(no agent log yet)")
            return 0
        lines = paths.AGENT_LOG_FILE.read_text().splitlines()[-n:]
        print("\n".join(lines))
        return 0
    _eprint(f"Unknown: mcp {sub}")
    return 1


# ---- desktop subgroup -------------------------------------------------------
def cmd_desktop(args: list[str]) -> int:
    if not args:
        _eprint("Usage: voice desktop [type|key|click|move|capture|focused] ...")
        return 1
    sub, rest = args[0], args[1:]
    if sub == "type":
        if not rest:
            _eprint("Usage: voice desktop type <text...>")
            return 1
        desktop.type_text(" ".join(rest))
    elif sub == "key":
        if not rest:
            _eprint("Usage: voice desktop key <combo>   e.g. Tab | ctrl+a")
            return 1
        desktop.press_key(rest[0])
    elif sub == "click":
        button = "left"
        if rest and rest[0] in ("left", "right", "middle"):
            button = rest.pop(0)
        if len(rest) == 2:
            desktop.click_mouse(button, int(rest[0]), int(rest[1]))
        elif not rest:
            desktop.click_mouse(button)
        else:
            _eprint("Usage: voice desktop click [left|right|middle] [x y]")
            return 1
    elif sub == "move":
        if len(rest) != 2:
            _eprint("Usage: voice desktop move <x> <y>")
            return 1
        desktop.move_mouse(int(rest[0]), int(rest[1]))
    elif sub == "capture":
        scope = "monitor"
        if rest and rest[0] in ("monitor", "all", "window"):
            scope = rest.pop(0)
        out = Path(rest[0]) if rest else paths.SCREENSHOT_FILE
        result = screenshot.capture_to(out, scope=scope)
        if result:
            print(result)
            return 0
        return 1
    elif sub == "focused":
        print(json.dumps(desktop.focused_window(), ensure_ascii=False, indent=2))
    else:
        _eprint(f"Unknown: desktop {sub}")
        return 1
    return 0


# ---- windows subgroup -------------------------------------------------------
def cmd_windows(args: list[str]) -> int:
    """
    voice windows list                              — JSON list of all windows
    voice windows find <needle> [limit]             — substring search
    voice windows active                            — focused window as JSON
    voice windows focus <id|substring>              — focus a window
    voice windows close <id|substring>              — politely close
    voice windows move <id> <x> <y>                 — move floating window
    voice windows resize <id> <w> <h>               — resize floating window
    voice windows float <id>                        — toggle floating
    voice windows fullscreen [id]                   — toggle fullscreen
    voice windows send-to-workspace <id> <ws>       — silent move to workspace
    """
    if not args:
        _eprint(cmd_windows.__doc__)
        return 1
    sub, rest = args[0], args[1:]
    try:
        if sub == "list":
            print(json.dumps(
                [w.to_dict() for w in plat.wm.list_windows()],
                ensure_ascii=False, indent=2,
            ))
        elif sub == "find":
            if not rest:
                _eprint("Usage: voice windows find <needle> [limit]")
                return 1
            needle = rest[0]
            limit = int(rest[1]) if len(rest) >= 2 else 10
            print(json.dumps(
                [w.to_dict() for w in plat.wm.find_windows(needle, limit=limit)],
                ensure_ascii=False, indent=2,
            ))
        elif sub == "active":
            w = plat.wm.active_window()
            print(json.dumps(w.to_dict() if w else {}, ensure_ascii=False, indent=2))
        elif sub == "focus" and rest:
            plat.wm.focus_window(rest[0])
            print(f"focused {rest[0]}")
        elif sub == "close" and rest:
            plat.wm.close_window(rest[0])
            print(f"closed {rest[0]}")
        elif sub == "move" and len(rest) == 3:
            plat.wm.move_window(rest[0], int(rest[1]), int(rest[2]))
            print(f"moved {rest[0]} to ({rest[1]},{rest[2]})")
        elif sub == "resize" and len(rest) == 3:
            plat.wm.resize_window(rest[0], int(rest[1]), int(rest[2]))
            print(f"resized {rest[0]} to {rest[1]}x{rest[2]}")
        elif sub == "float" and rest:
            plat.wm.toggle_floating(rest[0])
            print(f"toggled floating {rest[0]}")
        elif sub == "fullscreen":
            plat.wm.toggle_fullscreen(rest[0] if rest else None)
            print(f"toggled fullscreen {rest[0] if rest else '<active>'}")
        elif sub == "send-to-workspace" and len(rest) == 2:
            plat.wm.move_window_to_workspace(rest[0], rest[1])
            print(f"moved {rest[0]} → workspace {rest[1]}")
        else:
            _eprint(cmd_windows.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


# ---- workspaces subgroup ----------------------------------------------------
def cmd_workspaces(args: list[str]) -> int:
    """
    voice workspaces list                  — all workspaces as JSON
    voice workspaces active                — currently active workspace
    voice workspaces switch <id|name>      — switch to workspace
    voice workspaces send-to-monitor <t>   — send active ws to another monitor
                                             (l|r|u|d or numeric id)
    """
    if not args:
        _eprint(cmd_workspaces.__doc__)
        return 1
    sub, rest = args[0], args[1:]
    try:
        if sub == "list":
            print(json.dumps(
                [w.to_dict() for w in plat.wm.list_workspaces()],
                ensure_ascii=False, indent=2,
            ))
        elif sub == "active":
            w = plat.wm.active_workspace()
            print(json.dumps(w.to_dict() if w else {}, ensure_ascii=False, indent=2))
        elif sub == "switch" and rest:
            plat.wm.switch_workspace(rest[0])
            print(f"switched to workspace {rest[0]}")
        elif sub == "send-to-monitor" and rest:
            plat.wm.send_workspace_to_monitor(rest[0])
            print(f"sent active workspace to monitor {rest[0]}")
        else:
            _eprint(cmd_workspaces.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


# ---- platform subgroup (diagnostics) ----------------------------------------
def cmd_platform(args: list[str]) -> int:
    """
    voice platform info        — active platform and full capability set
    voice platform caps        — just the capability set, one per line
    """
    sub = args[0] if args else "info"
    if sub == "info":
        print(json.dumps(
            {
                "platform":     plat.active_platform,
                "override":     config.settings.platform_override,
                "capabilities": sorted(plat.all_capabilities()),
            },
            indent=2, ensure_ascii=False,
        ))
    elif sub == "caps":
        for c in sorted(plat.all_capabilities()):
            print(c)
    else:
        _eprint("Usage: voice platform [info|caps]")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------
COMMANDS: dict[str, Callable[[list[str]], int]] = {
    # New grouped layout
    "rec":      cmd_rec,
    "session":  cmd_session,
    "tts":      cmd_tts,
    "ask":      cmd_ask,
    "config":   cmd_config,
    "state":    cmd_state,
    "pause":    cmd_pause,
    "resume":   cmd_resume,
    "tray":     cmd_tray,
    "status":   cmd_status,
    "desktop":    cmd_desktop,
    "windows":    cmd_windows,
    "workspaces": cmd_workspaces,
    "platform":   cmd_platform,
    "mcp":        cmd_mcp,
}

# Legacy flat aliases — preserved for Hyprland binds and muscle memory.
LEGACY: dict[str, tuple[str, list[str]]] = {
    "start":    ("rec",     ["start"]),
    "stop":     ("rec",     ["stop"]),
    "toggle":   ("rec",     ["toggle"]),
    "reset":    ("session", ["reset"]),
    "voices":   ("tts",     ["voices"]),
    "say":      ("tts",     ["say"]),       # extra args appended by dispatcher
    "type":     ("desktop", ["type"]),
    "key":      ("desktop", ["key"]),
    "click":    ("desktop", ["click"]),
    "move":     ("desktop", ["move"]),
    "capture":  ("desktop", ["capture"]),
    "focused":  ("desktop", ["focused"]),
}


def main(argv: list[str] | None = None) -> int:
    paths.ensure_dirs()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _usage()

    head, rest = argv[0], argv[1:]

    if head in LEGACY:
        new_head, prefix = LEGACY[head]
        _deprecated(head, f"{new_head} {prefix[0]}")
        return COMMANDS[new_head](prefix + rest)

    if head in COMMANDS:
        return COMMANDS[head](rest)

    _eprint(f"Unknown command: {head}")
    return _usage()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
