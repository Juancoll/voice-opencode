# Runbook — operating voice-opencode

## Start everything by hand

```bash
systemctl --user start opencode-serve.service ydotool.service
~/gitr/voice-opencode/voice tray &
```

(Hyprland `exec-once` does this on login; this is the manual fallback.)

## Push-to-talk

Hold **F9** → talk → release. The tray icon cycles
`idle → recording → thinking → speaking → idle`.

`Super+F9` resets the opencode session (forgets context).

## Pause/resume

```bash
voice pause     # F9 ignored
voice resume    # F9 re-enabled
```

The tray menu has a checkbox for the same.

## Inspect what's happening

```bash
voice state                   # JSON snapshot (used by the tray)
voice status                  # human-readable
voice platform info           # which backend got wired + capability set
voice platform caps           # capabilities, one per line (handy for grep)
journalctl --user -u opencode-serve -f
tail -f logs/voice.log logs/arecord.log logs/piper.log
```

## Drive windows / workspaces from the CLI (Phase A)

The same surface the agent sees over MCP is mirrored on the CLI so a
human can use it from a script or keybinding. Window targets accept
either an ``address:0x…`` id, a bare ``0x…`` address, or an
app-id / title substring (resolved via ``find_windows``).

```bash
voice windows list                          # everything as JSON
voice windows find chrome                   # substring search
voice windows active                        # focused window
voice windows focus chrome                  # focus first match
voice windows close 0xCAFE                  # polite close
voice windows float chrome                  # toggle floating
voice windows fullscreen                    # active window fullscreen
voice windows send-to-workspace chrome 3    # silent move

voice workspaces list                       # all workspaces
voice workspaces switch 5                   # go to ws 5
voice workspaces send-to-monitor l          # current ws → monitor on the left
```

The tray's ``Ventanas`` and ``Workspaces`` submenus are rebuilt from
these calls every time they open, so they always show fresh state
without polling the WM each second.

## Drive the clipboard from the CLI (Phase B)

Mirrors what the MCP tools see. Backend auto-detected (wl-clipboard on
Wayland, xclip on X11).

```bash
voice clipboard read                       # print CLIPBOARD selection to stdout
voice clipboard read-primary               # print PRIMARY selection
voice clipboard write "hola mundo"         # inline args (joined with spaces)
echo "from pipe" | voice clipboard write   # or read stdin when no args
voice clipboard write-primary "p"
```

Reads go to stdout WITHOUT a trailing newline (the caller decides).
Empty selection prints nothing and exits 0 (consistent with the
backend-level "empty is empty, not an error" rule).

Gotcha: ``wl-copy`` double-forks to keep serving paste requests. The
backend routes its stdout/stderr to ``/dev/null`` so the parent doesn't
hang waiting for the daemon child's fds to close. Don't reintroduce
``capture_output=True`` there — it WILL hang for 3 s on every write.

## Show notifications & ask the user (Phase C)

Non-blocking notification via ``notify-send`` (works on any DE with a
notification daemon):

```bash
voice dialog notify "Title" "Body text"           # urgency=normal
voice dialog notify "Title" "" critical           # red banner
```

Blocking dialogs via ``kdialog`` (preferred) or ``zenity`` (fallback).
Exit codes are scripted-friendly: ``confirm`` returns rc=0 for Yes, rc=2
for No, rc=1 only on backend error. ``ask`` / ``choose`` print the
answer to stdout, or nothing on cancel.

```bash
voice dialog confirm "¿Borrar el archivo?" "Confirmar"
# echo $?   → 0 (Yes), 2 (No), 1 (error)

name=$(voice dialog ask "¿Tu nombre?" "Juan" "Hola")
color=$(voice dialog choose "Color:" red green blue)
```

The agent sees these as four MCP tools — ``notify``, ``ask_confirm``,
``ask_user``, ``ask_choice`` — all capability-guarded so they don't
appear if neither kdialog nor zenity is installed. The three blocking
ones hold the agent lock, so F9 push-to-talk is silently ignored while
the user is in the dialog.

Switch backends manually if you want to test the zenity path:

```bash
# Force zenity even on a KDE box (must have zenity installed)
sudo pacman -S zenity
# kdialog still wins via wiring order; uninstall kdialog or hack
# platform/__init__.py to flip the order. There's no env knob — this is
# rare enough that adding one is YAGNI.
```

## Switch voice

```bash
voice tts voices                    # list
voice config set voice es_AR-daniela-high
voice config set speaker_id 0
```

For multi-speaker models like `es_ES-sharvard-medium` the speaker_id
matters (0=M, 1=F).

## MCP / agent mode (Phase 3)

opencode launches our MCP server on its own (config in
`~/.config/opencode/opencode.json`). Tools are exposed as
`voice_desktop_<name>`. Useful in prompts:

> "Use voice_desktop_capture_screen to see my screen, then describe it."
>
> "Open my browser, go to grim.app, take a screenshot. Use voice_desktop tools."

Inspect / manage:

```bash
voice mcp status              # is an agent acting right now?
voice mcp log -n 20           # tail the audit log (logs/agent.log)
voice mcp stop                # kill any running mcp server, release lock
voice mcp serve               # run the server in the foreground (debugging)
```

The tray menu has a "Detener agente (MCP)" item that calls `mcp stop`.

While an MCP tool is *acting* (typing, clicking, key presses), F9 is
silently ignored — that's the lock. Read-only tools (capture, focused,
list_monitors) don't take the lock.

## Change opencode working directory

Edit `WorkingDirectory=` in `~/.config/systemd/user/opencode-serve.service`
then `systemctl --user daemon-reload && systemctl --user restart opencode-serve`.

---

## Diagnostics

### "opencode server not reachable"
```bash
systemctl --user status opencode-serve
curl -s http://127.0.0.1:4096/global/health
```

### Tray icon missing
```bash
pgrep -af voice_opencode.tray   # is it running?
gdbus call --session --dest org.kde.StatusNotifierWatcher \
    --object-path /StatusNotifierWatcher \
    --method org.freedesktop.DBus.Properties.Get \
    org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
# Should list a :1.NNN/StatusNotifierItem
```

If running but DMS doesn't show it: restart DMS (`pkill dms; dms &`).

### `voice rec start` does nothing
- Check pause: `voice state | jq .paused`
- Check arecord: `arecord -l` should list a capture device.
- Check log: `tail logs/arecord.log`.

### ydotool says socket missing
```bash
systemctl --user status ydotool      # should be active
ls /run/user/$UID/.ydotool_socket    # should exist
```
If `/dev/uinput` is `crw-------`, you're not in `input` group:
`sudo usermod -aG input $USER` then log out + back in.

### Whisper model not found
```bash
ls -la models/                       # ggml-small.bin must be ~466 MB
```
Re-run `./install.sh` to fetch.

### MCP server not appearing in opencode
```bash
cat ~/.config/opencode/opencode.json | jq .mcp
# voice_desktop should be type=local, command=[…/voice, mcp, serve], enabled=true

# Verify the server can boot at all:
./voice mcp serve <<< '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"x","version":"0"}}}'
# Should answer with serverInfo.name = voice-opencode-desktop

systemctl --user restart opencode-serve   # to pick up config changes
journalctl --user -u opencode-serve -f    # look for MCP load errors
```

### Agent stuck holding the lock
```bash
voice mcp status     # if "active" but no tool is actually running:
voice mcp stop       # kills any voice_opencode mcp processes + releases lock
```

---

## Reinstall on a fresh machine

```bash
git clone <this-repo> ~/gitr/voice-opencode
cd ~/gitr/voice-opencode
./install.sh
```

The installer is idempotent — safe to re-run after upgrades or to
re-enable services that got disabled.

---

## Run tests / lint

```bash
PYTHONPATH=src venv/bin/pytest
venv/bin/ruff check src tests
venv/bin/mypy src/voice_opencode
```
