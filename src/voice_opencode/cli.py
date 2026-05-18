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
        "capacity":   config.settings.capacity_mode,
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


# ---- clipboard subgroup -----------------------------------------------------
def cmd_clipboard(args: list[str]) -> int:
    """
    voice clipboard read                       — print CLIPBOARD selection
    voice clipboard write <text...>            — write CLIPBOARD selection
    voice clipboard read-primary               — print PRIMARY selection
    voice clipboard write-primary <text...>    — write PRIMARY selection

    With no <text...> for write/write-primary, stdin is used.
    """
    if not args:
        _eprint(cmd_clipboard.__doc__)
        return 1
    sub, rest = args[0], args[1:]

    def _payload() -> str:
        if rest:
            return " ".join(rest)
        return sys.stdin.read()

    try:
        if sub == "read":
            sys.stdout.write(plat.clipboard.read())
        elif sub == "read-primary":
            sys.stdout.write(plat.clipboard.read_primary())
        elif sub == "write":
            text = _payload()
            plat.clipboard.write(text)
            _eprint(f"wrote {len(text)} chars to clipboard")
        elif sub == "write-primary":
            text = _payload()
            plat.clipboard.write_primary(text)
            _eprint(f"wrote {len(text)} chars to primary")
        else:
            _eprint(cmd_clipboard.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


# ---- dialog subgroup --------------------------------------------------------
def cmd_dialog(args: list[str]) -> int:
    """
    voice dialog notify <title> [body] [urgency]   — non-blocking notification
    voice dialog confirm <message> [title]         — Yes/No, rc=0 if Yes
    voice dialog ask <prompt> [default] [title]    — text input, prints answer
    voice dialog choose <prompt> <opt1> <opt2> ... — menu, prints choice

    For confirm: rc=0 (Yes), rc=2 (No), rc=1 (backend error).
    For ask / choose: prints answer to stdout, empty stdout on cancel.
    Urgency for notify: low|normal|critical (default normal).
    """
    if not args:
        _eprint(cmd_dialog.__doc__)
        return 1
    sub, rest = args[0], args[1:]
    try:
        if sub == "notify":
            if not rest:
                _eprint("Usage: voice dialog notify <title> [body] [urgency]")
                return 1
            title = rest[0]
            body = rest[1] if len(rest) >= 2 else ""
            urgency = rest[2] if len(rest) >= 3 else "normal"
            plat.notify.show(title, body, urgency=urgency)
        elif sub == "confirm":
            if not rest:
                _eprint("Usage: voice dialog confirm <message> [title]")
                return 1
            msg = rest[0]
            title = rest[1] if len(rest) >= 2 else "Confirm"
            ok = plat.dialog.confirm(msg, title=title)
            return 0 if ok else 2
        elif sub == "ask":
            if not rest:
                _eprint("Usage: voice dialog ask <prompt> [default] [title]")
                return 1
            prompt = rest[0]
            default = rest[1] if len(rest) >= 2 else ""
            title = rest[2] if len(rest) >= 3 else "Input"
            ans = plat.dialog.ask_text(prompt, default=default, title=title)
            if ans is not None:
                print(ans)
        elif sub == "choose":
            if len(rest) < 2:
                _eprint("Usage: voice dialog choose <prompt> <opt1> <opt2> ...")
                return 1
            prompt = rest[0]
            choices = rest[1:]
            ans = plat.dialog.ask_choice(prompt, choices, title="Choose")
            if ans is not None:
                print(ans)
        else:
            _eprint(cmd_dialog.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


# ---- audio subgroup ---------------------------------------------------------
def cmd_audio(args: list[str]) -> int:
    """
    voice audio get               — print volume (0.00-1.00)
    voice audio set <level>       — set volume (clamped to 0.0-1.0)
    voice audio mute              — toggle output mute, prints new state
    voice audio mic-mute          — toggle microphone mute, prints new state
    """
    if not args:
        _eprint(cmd_audio.__doc__)
        return 1
    sub, rest = args[0], args[1:]
    try:
        if sub == "get":
            print(f"{plat.audio.volume_get():.2f}")
        elif sub == "set":
            if not rest:
                _eprint("Usage: voice audio set <level>")
                return 1
            try:
                level = float(rest[0])
            except ValueError:
                _eprint(f"invalid level: {rest[0]!r}")
                return 1
            plat.audio.volume_set(level)
        elif sub == "mute":
            muted = plat.audio.mute_toggle()
            print("muted" if muted else "unmuted")
        elif sub == "mic-mute":
            muted = plat.audio.mic_mute_toggle()
            print("muted" if muted else "unmuted")
        else:
            _eprint(cmd_audio.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


# ---- media subgroup ---------------------------------------------------------
def cmd_media(args: list[str]) -> int:
    """
    voice media play              — toggle play/pause on the active player
    voice media pause             — alias of 'play' (single MPRIS toggle)
    voice media next              — skip to next track
    voice media prev              — back to previous track
    voice media status            — print player/status/title/artist as JSON
    """
    if not args:
        _eprint(cmd_media.__doc__)
        return 1
    sub = args[0]
    try:
        if sub in ("play", "pause", "toggle"):
            plat.media.play_pause()
        elif sub == "next":
            plat.media.next()
        elif sub == "prev":
            plat.media.prev()
        elif sub == "status":
            print(json.dumps(plat.media.status(), indent=2, ensure_ascii=False))
        else:
            _eprint(cmd_media.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


def cmd_apps(args: list[str]) -> int:
    """
    voice apps list                  — installed desktop apps (JSON)
    voice apps running               — running apps with pids (JSON)
    voice apps launch <id-or-cmd>    — launch by .desktop id or raw command
    voice apps kill <pid-or-id>      — SIGTERM by pid or app id
    """
    if not args:
        _eprint(cmd_apps.__doc__)
        return 1
    sub = args[0]
    try:
        if sub == "list":
            print(json.dumps(plat.apps.list_installed(), indent=2, ensure_ascii=False))
        elif sub == "running":
            print(json.dumps(plat.apps.list_running(), indent=2, ensure_ascii=False))
        elif sub == "launch":
            if len(args) < 2:
                _eprint("Usage: voice apps launch <id-or-cmd>")
                return 1
            pid = plat.apps.launch(" ".join(args[1:]))
            print(pid)
        elif sub == "kill":
            if len(args) < 2:
                _eprint("Usage: voice apps kill <pid-or-id>")
                return 1
            target: int | str = int(args[1]) if args[1].isdigit() else args[1]
            plat.apps.kill(target)
        else:
            _eprint(cmd_apps.__doc__)
            return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1
    return 0


def cmd_shell(args: list[str]) -> int:
    """
    voice shell allowlist            — print active shell allowlist regexes
    voice shell run <cmd>            — dry-run a command (preview argv)
    voice shell run --exec <cmd>     — actually execute (still gated by allowlist)
    """
    if not args:
        _eprint(cmd_shell.__doc__)
        return 1
    sub = args[0]
    try:
        if sub == "allowlist":
            for p in config.settings.shell_allowlist:
                print(p)
            return 0
        if sub == "run":
            rest = args[1:]
            do_exec = False
            if rest and rest[0] == "--exec":
                do_exec = True
                rest = rest[1:]
            if not rest:
                _eprint("Usage: voice shell run [--exec] <cmd...>")
                return 1
            cmd_str = " ".join(rest)
            r = plat.shell.run(cmd_str, dry_run=not do_exec)
            print(json.dumps(r, indent=2, ensure_ascii=False))
            return 1 if r.get("rc", 0) not in (0,) else 0
        _eprint(cmd_shell.__doc__)
        return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1


# ---- platform subgroup (diagnostics) ----------------------------------------
def cmd_ocr(args: list[str]) -> int:
    """
    voice ocr find <text> [--region X Y W H]   — search needle on screen
    voice ocr dump [--region X Y W H]          — list every detected word
    voice ocr file <path> <text>               — OCR an existing PNG

    Default capture is the focused monitor. ``--region`` is much faster
    on 4K displays; coordinates are absolute screen pixels.
    """
    if not args:
        _eprint(cmd_ocr.__doc__)
        return 1
    sub = args[0]
    rest = args[1:]
    # Parse --region X Y W H from anywhere in rest.
    region = None
    if "--region" in rest:
        i = rest.index("--region")
        try:
            x, y, w, h = (int(t) for t in rest[i + 1:i + 5])
        except (ValueError, IndexError):
            _eprint("--region needs four integers: X Y W H")
            return 1
        region = (x, y, w, h)
        rest = rest[:i] + rest[i + 5:]

    try:
        if sub in ("find", "dump"):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                png = Path(tf.name)
            try:
                if region:
                    plat.screen.capture_region(png, *region)
                else:
                    plat.screen.capture_monitor(png)
                if sub == "find":
                    if not rest:
                        _eprint("Usage: voice ocr find <text>")
                        return 1
                    needle = " ".join(rest)
                    matches = plat.ocr.find_text(png, needle)
                else:
                    matches = plat.ocr.dump_text(png)
            finally:
                png.unlink(missing_ok=True)
            # If a region was used, shift bboxes back to screen coords.
            if region:
                ox, oy, _, _ = region
                matches = [
                    type(m)(
                        text=m.text,
                        rect=type(m.rect)(
                            x=m.rect.x + ox, y=m.rect.y + oy,
                            w=m.rect.w, h=m.rect.h,
                        ),
                        confidence=m.confidence,
                        line=m.line, word_index=m.word_index,
                    ) for m in matches
                ]
            print(json.dumps(
                [m.to_dict() for m in matches], indent=2, ensure_ascii=False,
            ))
            return 0
        if sub == "file":
            if len(rest) < 2:
                _eprint("Usage: voice ocr file <path> <text...>")
                return 1
            path = Path(rest[0])
            needle = " ".join(rest[1:])
            matches = plat.ocr.find_text(path, needle)
            print(json.dumps(
                [m.to_dict() for m in matches], indent=2, ensure_ascii=False,
            ))
            return 0
        _eprint(cmd_ocr.__doc__)
        return 1
    except (BackendError, NotSupportedError) as e:
        _eprint(f"error: {e}")
        return 1


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
    "clipboard":  cmd_clipboard,
    "dialog":     cmd_dialog,
    "audio":      cmd_audio,
    "media":      cmd_media,
    "apps":       cmd_apps,
    "shell":      cmd_shell,
    "ocr":        cmd_ocr,
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
