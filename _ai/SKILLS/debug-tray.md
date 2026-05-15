# Skill: debug the tray

## Symptoms → checks

### Icon not visible at all
```bash
pgrep -af voice_opencode.tray            # is the process alive?
gdbus call --session --dest org.kde.StatusNotifierWatcher \
    --object-path /StatusNotifierWatcher \
    --method org.freedesktop.DBus.Properties.Get \
    org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
# Look for a :1.NNN/StatusNotifierItem entry
```

If the process is alive **and** registered, the panel is the problem.
For DankMaterialShell: `pkill dms; dms &`.

### Icon black / monochrome / invisible
The icon SVG uses `currentColor`. Replace fills with explicit colors
(see `icons/voice-idle.svg` for the pattern).

### Icon doesn't update
Tray polls every 1 s. If state seems stuck, check what `voice state`
returns:
```bash
voice state | jq
```
If `state` field stays at `recording`, the pipeline crashed — see
`logs/voice.log` for the last error and `set_state("error")` should
have run. If it didn't, look for an unhandled exception path in
`pipeline.py`.

### Menu items don't trigger anything
Tray invokes the `voice` wrapper. Run the same command from a terminal
to see the real error:
```bash
voice rec toggle    # what the "Toggle grabación" menu does
```

### Tray crashes on launch
```bash
voice tray         # foreground; reproduce the crash
```
Common: `QSystemTrayIcon.isSystemTrayAvailable()` returns False because
no SNI host is up. Start DMS first.
