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
journalctl --user -u opencode-serve -f
tail -f logs/voice.log logs/arecord.log logs/piper.log
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
