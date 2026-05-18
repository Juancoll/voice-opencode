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

## Drive audio + media from the CLI (Phase H)

Audio is driven by ``wpctl`` against the default PipeWire sink (output)
and source (microphone). Media is MPRIS via ``playerctl``, acting on
whichever player was active most recently (browser tab, mpv, Spotify…).

```bash
voice audio get               # current output volume, e.g. "0.50"
voice audio set 0.4           # set output volume (clamped to 0.0-1.0)
voice audio mute              # toggle output mute — prints "muted"/"unmuted"
voice audio mic-mute          # toggle microphone mute

voice media play              # toggle play/pause (single MPRIS verb)
voice media pause             # alias of 'play'
voice media next              # skip
voice media prev              # back
voice media status            # JSON: {player, status, title, artist}
```

Volume above 1.0 (≈100%) is silently clamped — wpctl would accept it
and route to PipeWire amplification, which can blow speakers. If you
want louder, raise the global volume in your DE first.

The status format uses ASCII Unit Separator (0x1F) internally, NOT
``|``, because YouTube titles routinely contain literal pipes:
"Foo | bar - YouTube". If you write a custom client that parses
playerctl yourself, do the same or you'll lose tokens.

No active player? Both ``status`` and the transport commands return
``error: playerctl failed: No players found`` with rc=1. That's the
expected behaviour — opening a YouTube tab and pausing it is enough
to make a player appear.

## Drive desktop apps from the CLI (Phase I)

Apps are enumerated from XDG ``applications`` directories (user dir
wins on dedup), launched via ``gtk-launch`` (correct StartupNotify /
DBusActivatable handling) with raw-command fallback, and killed by
SIGTERM via ``/proc`` scanning.

```bash
voice apps list                    # all installed .desktop apps (JSON)
voice apps running                 # running pids with optional app_id match
voice apps launch firefox          # launch by .desktop id (uses gtk-launch)
voice apps launch "/bin/sleep 30"  # raw command (Popen, detached)
voice apps kill 12345              # SIGTERM by pid
voice apps kill firefox            # SIGTERM every running firefox pid
```

A launched ``gtk-launch`` returns ``pid=0`` if the 250 ms ``/proc``
probe missed the new process (very short-lived apps, or apps whose
``comm`` differs from the basename of ``Exec=`` — Electron apps
that ``prctl(PR_SET_NAME)`` themselves). If you need a guaranteed
pid, use the raw-command path: ``voice apps launch "/usr/bin/foo"``
goes through ``Popen`` and returns the real pid.

``apps kill <app-id>`` is **pkill-style**: it SIGTERMs *every*
running pid whose ``comm`` matches the app's binary basename. If
you wanted just one window, use ``voice windows close <addr>``
instead.

Capacity tiers: ``list`` and ``running`` are ``read-only``;
``launch`` is ``assist``; ``kill`` is ``full`` (SIGTERM destroys
unsaved state in editors / terminals). See ADR-0015 for why.

## Run shell commands with rails (Phase E)

The agent's most dangerous tool. Three layered rails (ADR-0017),
all enforced by the backend:

1. **No shell, ever.** ``subprocess.run(argv, shell=False)``.
   String input goes through ``shlex.split``; list input is
   passed through verbatim.
2. **Shell metacharacters rejected.** ``;``, ``|``, ``&``,
   `` ` ``, ``$``, ``<``, ``>`` in *any* parsed token → error.
   This catches both ``"ls | wc"`` and list-form attempts.
3. **Default-deny basename allowlist.** ``Path(argv[0]).name``
   must ``re.fullmatch`` a pattern in ``settings.shell_allowlist``.
   Empty list → everything rejected.

The tool is in the ``full`` tier only; opencode never sees it in
``read-only`` or ``assist``. ``dry_run=True`` is the default; the
caller must explicitly pass ``dry_run=False`` (or ``--exec`` on
the CLI) to actually spawn.

```bash
voice shell allowlist                    # show active regex patterns
voice shell run echo hola                # dry run — prints parsed argv
voice shell run --exec echo hola         # actually run
voice shell run --exec rm -rf /tmp/x     # rejected — 'rm' not in allowlist
voice shell run "ls | wc"                # rejected — shell metachar
```

Default allowlist (read-mostly): ``ls cat head tail wc rg grep
find file stat jq yq git hg echo true false date pwd whoami
python3? node``. Notably absent: ``rm mv cp chmod chown sudo
systemctl pacman pkill kill sh bash sleep``. Add per-project
patterns in ``config.json`` under ``shell_allowlist`` (one regex
per element; matched with ``re.fullmatch`` against the basename).

Other knobs:

- ``shell_timeout_s`` — default 10 s, hard-capped at 60 s by the
  backend regardless of caller request.
- Output: stdout and stderr each truncated to 64 KB; truncation
  is visible (``[truncated: …]`` marker).
- Timeout returns ``rc=-1`` with partial output and an
  ``[timeout after Ns]`` note — never raises.
- Audit log records full argv, cwd, rc, dry_run flag, and
  stdout/stderr *lengths* (not contents — keeps logs small and
  avoids leaking secrets from environment dumps).

## Find text on the screen with OCR (Phase F)

Tesseract-backed OCR with word-level bounding boxes. Two
flavours: capture-then-OCR for "what's on screen right now",
or OCR an existing image file on disk.

```bash
voice ocr find Chrome                          # OCRs the focused monitor
voice ocr find "File menu" --region 0 0 800 60 # OCRs only that region
voice ocr file ~/Pictures/x.png "Submit"       # OCRs an existing image
voice ocr dump screenshot.png                  # all recognised text, no needle
```

`--region X Y W H` shifts every returned bbox back to
absolute screen coordinates, so the agent can immediately
click on or read text near the result via
`voice_desktop_click_mouse` / `voice_desktop_move_mouse`.

Two MCP tools, both **read-only** (no side effects, just
pixel reads):

- `screen_find_text(needle, region?)` — captures the
  focused monitor, OCRs, returns matches in screen coords.
- `ocr_find_text_in_file(path, needle)` — pure backend
  passthrough; bboxes are in the image's own coordinate
  space.

Languages and confidence floor are settings:

- `ocr_languages` — tuple, default `("spa", "eng")`. First
  language wins ties. Override per project in `config.json`.
- `ocr_min_confidence` — float 0-100, default 50.0. Rows
  below this are dropped before needle matching.

Tesseract on a 4K full-screen capture takes ~5-30 seconds
depending on font density; use `--region` for snappy
interactive use. The backend hard-caps each invocation at
60 s and runs Tesseract with `OMP_THREAD_LIMIT=1` to avoid
a CPU storm when the agent fires several OCR calls back-to-back.

## Agent memory (Phase G)

Persistent notes the agent writes between sessions. Plain
Markdown, one file per day under `<repo>/memory/YYYY-MM-DD.md`.
Each entry is an `## ISO-ts  [tags]` header followed by free-form
body. You can `cat`, edit, or delete the files by hand — there
is no database (ADR-0019).

```bash
voice memory append "fixed the audio bug" --tag audio --tag bug
voice memory search audio                     # newest first
voice memory recent 10                        # last 10 entries
voice memory days                             # list days that have notes
```

Four MCP tools:

- `memory_search(query, limit=20)` — read-only
- `memory_recent(n=10)` — read-only
- `memory_list_days()` — read-only
- `memory_append(text, tags?)` — assist tier (writes to disk)

Rules the writer enforces (to keep the parser trivial):

- text cannot be empty after `strip()`
- no tag may contain `,` or `]`
- no body line may start with `## ` (would split the entry on
  next read — indent it or use `###` instead)

The `memory/` directory is gitignored; each user's memory is
local.

## Audit & capacity from the tray (Phase J)

The tray's **Agente** submenu has two entries:

- **Ver auditoría…** opens a modeless dialog showing the last N
  rows of ``logs/agent.log`` (every MCP tool call). Auto-refreshes
  every 1.5 s; spinbox controls how many lines (10–5000); manual
  Refresh button for impatience. Read-only view — the file itself
  is the source of truth and is also tail-able from a terminal
  with ``voice mcp log -n 200`` or ``tail -f logs/agent.log``.
- **Modo de capacidad** is an exclusive radio group: *Solo lectura*,
  *Asistir (recomendado)*, *Completo (destructivo)*. Picking one
  does two things atomically: writes ``capacity_mode`` to the
  config file *and* runs ``voice mcp stop`` to kill any live MCP
  server, so opencode spawns a fresh one with the new tool tier
  on the next tool call. The radio always reflects the persisted
  value (re-synced once per second from ``voice state``).

The tray status line **agente: 🤖 activo / —** lights up while the
MCP server holds the desktop lock (it acquires on startup and
releases on exit). When active, F9 push-to-talk is suppressed so
the user doesn't fight the agent.

Forcing an immediate switch from the CLI:

```bash
voice config set capacity_mode read-only
voice mcp stop          # next opencode tool call respawns MCP in read-only

voice mcp log -n 100    # tail the audit log
voice mcp status        # is anyone holding the lock right now?
```

## Capacity modes — limit what opencode can do (Phase D)

Three tiers control which MCP tools opencode sees. Switching is a
single config write; the MCP server reads the setting at startup.

| Mode        | What the model can do                                            | Tool count* |
|-------------|------------------------------------------------------------------|-------------|
| `read-only` | Observe only — list windows, capture screen, read clipboard, notify, audio_get_volume, media_status, apps_list_* | 15 |
| `assist`    | + drive UI: type, click, focus, move, dialogs, write clipboard, audio set/mute, media transport, apps_launch | 39 |
| `full`      | + destructive: `close_window`, `apps_kill` (and future `run_shell`) | 41        |

\* on this host. Real count depends on which backend caps are wired.

Default is **assist**. Change it:

```bash
voice config set capacity_mode read-only      # observe-only
voice config set capacity_mode assist         # default
voice config set capacity_mode full           # everything

# Or one-shot for a single invocation:
VOICE_CAPACITY_MODE=full ./voice mcp serve
```

After changing the mode, **restart the MCP server** so opencode
re-reads the tool list:

```bash
systemctl --user restart opencode-serve     # or kill -HUP the mcp process
```

Verify what the model currently sees:

```bash
# Live count per mode (no MCP client needed):
PYTHONPATH=src venv/bin/python -c "
from voice_opencode import mcp_server
srv = mcp_server.build_server()
print(sorted(t.name for t in srv._tool_manager.list_tools()))
"

# Or from the model itself: it calls platform_info() which now returns
# {platform, capabilities, capacity_mode}.
```

Audit trail — every MCP startup logs the active tier:

```bash
tail -n3 logs/voice.log
# MCP server started (platform=linux-hyprland, capacity=assist, tools=28).
```

Mapping (tool → minimum tier) lives in
``src/voice_opencode/capacity.py`` → ``TIER_BY_TOOL``. To re-tier a
tool, edit that dict; no other change needed. Adding a new MCP tool
without adding it to the dict means it defaults to ``full`` (hidden
from read-only and assist) — that's by design, see ADR-0014.

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
