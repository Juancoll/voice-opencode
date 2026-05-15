# voice-opencode

Push-to-talk voice frontend for [opencode](https://opencode.ai), running
fully local on Linux + Hyprland.

Hold **F9**, speak, release: your voice is transcribed locally with
`whisper.cpp`, a screenshot of the focused monitor is attached, the
prompt is sent to a persistent opencode session via its HTTP API, and
the reply is spoken back through `piper-tts`. 100% local STT/TTS.

```
F9 hold ─► arecord ─► whisper.cpp ─► opencode HTTP ─► piper ─► paplay
                                  ▲
                       grim (focused monitor screenshot)
```

> AI agents working on this repo: start at [`AGENTS.md`](./AGENTS.md).

## Layout

```
voice-opencode/
├── voice                       # bash wrapper (runs the venv + package)
├── pyproject.toml              # build / ruff / mypy / pytest config
├── src/voice_opencode/         # the package (16 modules, layered)
│   ├── paths.py logging.py notify.py    # base layer
│   ├── config.py state.py               # config + pipeline state
│   ├── audio.py stt.py tts.py           # I/O services
│   ├── screenshot.py opencode_client.py
│   ├── desktop.py                       # wtype + ydotool wrappers
│   ├── pipeline.py                      # orchestration
│   ├── cli.py tray.py __main__.py       # entry points
├── tests/                      # pytest suite (no real audio/network)
├── _ai/                        # knowledge base for AI agents
│   ├── ARCHITECTURE.md DECISIONS.md STATE.md RUNBOOK.md CHANGELOG.md
│   └── SKILLS/                 # task-specific recipes
├── icons/                      # SVG tray icons (one per state)
├── models/ggml-small.bin       # whisper model (466 MB)
├── voices/*.onnx               # Piper voices
├── opencode-serve.service      # systemd --user unit
├── install.sh download-voice.sh
├── venv/                       # Python venv
└── logs/                       # runtime logs
```

Runtime state (recording PID, current session id, pipeline phase) lives in
`$XDG_RUNTIME_DIR/voice-opencode/`.

## Install

The bundled installer is idempotent — safe to re-run:

```bash
./install.sh             # runtime install
./install.sh --dev       # also install ruff/mypy/pytest for development
```

It will:

1. Install required pacman packages (sudo via askpass).
2. Create the Python venv and install runtime deps (`requests`, `PyQt6`)
   plus `pip install -e .` so the package is importable.
3. Download the whisper model (`ggml-small.bin`, ~466 MB) if missing.
4. Download the default Spanish voice (`es_AR-daniela-high`) if no voices yet.
5. Install and enable `opencode-serve.service` and `ydotool.service` user units.
6. Drop the `voice-opencode.desktop` launcher into `~/.local/share/applications/`.
7. Append the Hyprland binds + tray autostart to `~/.config/hypr/conf.d/`
   if not already present.

## Run the opencode server (persistent session)

```bash
systemctl --user enable --now opencode-serve.service
journalctl --user -u opencode-serve -f
```

Edit `WorkingDirectory=` in the service file to point at the project you want
opencode to operate on (defaults to `$HOME`).

## Hyprland keybind (push-to-talk)

`install.sh` writes `~/.config/hypr/conf.d/voice.conf`:

```ini
bind  = , F9, exec, ~/gitr/voice-opencode/voice start
bindr = , F9, exec, ~/gitr/voice-opencode/voice stop
bind  = SUPER, F9, exec, ~/gitr/voice-opencode/voice reset
```

Reload: `hyprctl reload`.

## CLI

The CLI is grouped by domain. **Legacy flat aliases** are preserved so
existing Hyprland binds keep working — see `cli.LEGACY` for the full table.

### Recording

```bash
voice rec start          # alias: voice start
voice rec stop           # alias: voice stop
voice rec toggle         # alias: voice toggle
```

### Session

```bash
voice session reset      # alias: voice reset (forget current opencode session)
voice session id         # print current session id
```

### TTS

```bash
voice tts say "hola"     # alias: voice say "hola"
voice tts voices         # alias: voice voices (list installed voices)
```

### Conversation

```bash
voice ask "¿qué hora es?"           # text → opencode → speech
voice ask "explícate" --no-tts      # only print the reply
```

### Config

```bash
voice config get                       # full settings as JSON
voice config get voice                 # one key
voice config set voice es_AR-daniela-high
voice config set attach_screenshot false
```

### Status / state / pause

```bash
voice status            # human-readable
voice state             # JSON (used by the tray; wire format is stable)
voice pause             # ignore F9
voice resume            # re-enable F9
```

### Tray

```bash
voice tray              # launch the PyQt6 system tray icon
```

### Desktop control building blocks

These wrap `wtype` (text/keys) and `ydotool` (mouse/clicks). They are the
primitives the MCP server exposes to opencode.

```bash
voice desktop type "hola juan"           # alias: voice type ...
voice desktop key  "ctrl+a"              # alias: voice key ...
voice desktop click 800 450              # left click; "right" / "middle" optional
voice desktop move 1200 300
voice desktop capture window /tmp/w.png  # screenshot focused window/monitor/screen
voice desktop focused                    # JSON of active window
```

### MCP server (Phase 3 — agentic desktop control)

```bash
voice mcp serve              # run the MCP server (opencode launches this for you)
voice mcp status             # is an agent currently acting?
voice mcp stop               # kill any running mcp processes, release the lock
voice mcp log -n 20          # tail the audit log (logs/agent.log)
```

`install.sh` registers the server in `~/.config/opencode/opencode.json`
as `voice_desktop`. Tools surface to the model as
`voice_desktop_<name>` (e.g. `voice_desktop_capture_screen`,
`voice_desktop_click_mouse`).

The model has 8 tools:

| Tool                | Acts? | Description                                |
|---------------------|-------|--------------------------------------------|
| `type_text`         | yes   | type literal text into focused window      |
| `press_key`         | yes   | press a key combo (with safety blocklist)  |
| `move_mouse`        | yes   | move cursor to absolute (x, y)             |
| `click_mouse`       | yes   | left/right/middle click, optional move     |
| `focused_window`    | no    | Hyprland active window JSON                |
| `capture_screen`    | no    | screenshot to a path; returns {path,bytes} |
| `list_monitors`     | no    | Hyprland monitors with geometry            |
| `sleep_ms`          | no    | wait for a UI to settle (max 5000 ms)      |

Safety rails:

- Hard blocklist for dangerous combos
  (`ctrl+alt+backspace`, `ctrl+alt+f1..f12`, `alt+sysrq`).
- Rate limit: 30 calls / 5 s per server instance.
- "Acting" tools (the four marked above) hold a desktop lock that
  blocks F9 while they're running, so you can interrupt by pulling the
  plug (`voice mcp stop` or the tray menu).
- Every call is appended to `logs/agent.log` as JSON Lines.

### Tray icon

A PyQt6 `QSystemTrayIcon` (`voice_opencode.tray`) speaks the
StatusNotifierItem protocol so it integrates with DankMaterialShell,
waybar, etc. It polls `voice state` once per second and reflects the
pipeline phase (idle / recording / thinking / speaking / paused / error).

The menu lets you toggle recording, reset the session, pause F9, switch
voice, toggle the screenshot/context attachments, view the log, or quit.

## How it works

- **Recording**: `arecord` writes 16 kHz mono 16-bit WAV — the format
  whisper.cpp ingests natively (no resampling).
- **STT**: `whisper-cli` with the `small` multilingual model, language
  forced to Spanish (`-l es`).
- **Screenshot**: `grim` of the focused monitor (resolved via
  `hyprctl monitors -j`), attached as a file part of the user message.
- **Session**: on first message we `POST /session` and persist the id
  under `$XDG_RUNTIME_DIR/voice-opencode/session.id`. Subsequent messages
  reuse it, so opencode keeps full context. `voice session reset`
  deletes the id.
- **Reply**: posted via `POST /session/:id/message`, which blocks until
  opencode finishes responding and returns the message parts.
- **TTS**: text is cleaned (code fences stripped, markdown bullets
  removed) and piped to `piper --output-raw` then to `paplay`.

## Config

Settings live in `config.json` (project root). Env vars override.
Use `voice config get/set` rather than editing the file by hand.

| Key                  | Env                    | Default                | Purpose                              |
|----------------------|------------------------|------------------------|--------------------------------------|
| `voice`              | `VOICE`                | `es_AR-daniela-high`   | Piper voice (partial match OK)       |
| `speaker_id`         | `SPEAKER_ID`           | `0`                    | Multi-speaker model index            |
| `whisper_model`      | `WHISPER_MODEL`        | `ggml-small.bin`       | Model file under `models/`           |
| `whisper_lang`       | `WHISPER_LANG`         | `es`                   | `auto` to detect                     |
| `opencode_host`      | `OPENCODE_HOST`        | `127.0.0.1`            | opencode serve host                  |
| `opencode_port`      | `OPENCODE_PORT`        | `4096`                 | opencode serve port                  |
| `attach_screenshot`  | `VOICE_ATTACH_SCREEN`  | `true`                 | Attach focused-monitor capture       |
| `keep_context`       | `VOICE_KEEP_CONTEXT`   | `true`                 | Reuse session id across turns        |
| `notify`             | `VOICE_NOTIFY`         | `true`                 | `notify-send` on errors              |

## Development

```bash
PYTHONPATH=src venv/bin/pytest          # 24 tests, no real audio/network
venv/bin/ruff check src tests           # lint
venv/bin/mypy src/voice_opencode        # type check
```

See [`_ai/ARCHITECTURE.md`](./_ai/ARCHITECTURE.md) for the module map and
[`_ai/SKILLS/`](./_ai/SKILLS/) for task-specific recipes (add a CLI
command, add a voice, debug the tray, etc.).

## Troubleshooting

- `voice status` is the first stop.
- `voice state | jq` shows the tray's view of the pipeline.
- Logs are in `logs/voice.log`, `logs/arecord.log`, `logs/piper.log`.
- If whisper picks up only `[BLANK_AUDIO]`, your mic is too quiet —
  check `pavucontrol` input levels, or speak louder.
- The first message after server boot is slow because opencode loads
  the model provider lazily; subsequent calls are much faster.
- More recipes: [`_ai/RUNBOOK.md`](./_ai/RUNBOOK.md) and
  [`_ai/SKILLS/debug-pipeline.md`](./_ai/SKILLS/debug-pipeline.md).
