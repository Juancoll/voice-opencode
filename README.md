# voice-opencode

[![CI](https://github.com/Juancoll/voice-opencode/actions/workflows/ci.yml/badge.svg)](https://github.com/Juancoll/voice-opencode/actions/workflows/ci.yml)

Push-to-talk voice frontend for [opencode](https://opencode.ai), running
fully local on Linux + Hyprland. Plus a **48-tool MCP server** that lets
opencode drive your desktop — windows, clipboard, audio, apps, OCR on
the screen, shell commands with safety rails, persistent memory.

Hold **F9**, speak, release: your voice is transcribed locally with
`whisper.cpp`, a screenshot of the focused monitor is attached, the
prompt is sent to a persistent opencode session via its HTTP API, and
the reply is spoken back through `piper-tts`. 100% local STT/TTS.

```
F9 hold ─► arecord ─► whisper.cpp ─► opencode HTTP ─► piper ─► paplay
                                  ▲
                       grim (focused monitor screenshot)
```

> **AI agents** working on this repo: start at [`AGENTS.md`](./AGENTS.md).

## Why

opencode is a great terminal AI but it lives inside a terminal. This
project gives it:

- **Voice in/out** — hands-free coding, doc lookups, debugging chat.
- **Eyes** — a screenshot of what you're looking at goes with every
  prompt, so "fix this error" works without copy-paste.
- **Hands** — a capability-tiered MCP server with 48 tools so the model
  can actually act on your desktop when you ask it to.
- **Memory** — plain-Markdown notes the agent appends and searches
  across sessions, so it remembers what you did last week.

Everything is local. No cloud STT, no cloud TTS, no telemetry.

## Quickstart

Arch / CachyOS only for now (the installer detects your display server
and desktop environment and picks the right packages).

```bash
git clone https://github.com/Juancoll/voice-opencode ~/gitr/voice-opencode
cd ~/gitr/voice-opencode
./install.sh                  # pacman deps + venv + voices + systemd units
systemctl --user enable --now opencode-serve.service
hyprctl reload                # picks up the F9 bind
voice tray &                  # system tray icon
```

Press **F9**, hold, talk, release. The reply is spoken back and shown in
the tray.

## What the agent can do

The MCP server exposes 48 tools to opencode, gated by a **three-tier
capacity mode** (set via tray menu or `voice config set capacity_mode`):

| Tier        | What it can do                                                       |
|-------------|----------------------------------------------------------------------|
| `read-only` | inspect windows/monitors, read clipboard, list apps, OCR the screen, search memory — 20 tools |
| `assist`    | + type/click, switch workspaces, set volume, launch apps, ask dialogs, write memory — 45 tools (default) |
| `full`      | + close windows, kill apps, run shell commands (allowlisted) — 48 tools |

Highlights by area:

- **Windows / workspaces** — list, focus, move, resize, fullscreen,
  switch workspaces (Hyprland-native; KWin/X11 stubs included).
- **Input** — type literal text, send key combos (with a hardcoded
  blocklist for `ctrl+alt+backspace` and friends), click, scroll.
- **Screen** — capture monitor / region / window, list monitors.
- **OCR** — *"find the text 'Submit' on the screen and click it"*
  works. Tesseract with Spanish + English by default, word-level
  bounding boxes, `--region` for snappy interactive use on 4K
  displays.
- **Clipboard** — read/write both selections (Wayland + X11 backends).
- **Dialogs** — `notify`, `ask_confirm`, `ask_user`, `ask_choice`
  (kdialog preferred, zenity fallback).
- **Audio / media** — volume get/set, mute toggle, mic mute, MPRIS
  play/pause/next/prev/status.
- **Apps** — list installed (XDG .desktop), list running, launch,
  kill (`full` tier only).
- **Shell** — `shell_run` with three layered safety rails: no shell
  ever (`shell=False`), shell metacharacters rejected on string and
  list input, default-deny regex allowlist on `argv[0]` basename.
  Defaults to dry-run. `full` tier only. See
  [ADR-0017](./_ai/DECISIONS.md).
- **Memory** — plain Markdown at `memory/YYYY-MM-DD.md`. Four tools:
  `memory_search`, `memory_recent`, `memory_list_days` (read-only),
  `memory_append` (assist). See [ADR-0019](./_ai/DECISIONS.md).

Every tool call is appended to `logs/agent.log` as JSON Lines and
viewable from the tray (**Agente → Ver auditoría…**).

## CLI

The CLI is grouped by domain. Legacy flat aliases are preserved so
existing Hyprland binds keep working — see `cli.LEGACY` for the full
table.

```bash
# Recording (legacy aliases shown in comments — wired to Hyprland F9)
voice rec start         # alias: voice start
voice rec stop          # alias: voice stop
voice rec toggle        # alias: voice toggle

# Conversation
voice ask "explain this file"
voice ask "what's wrong here?" --no-tts

# Session lifecycle
voice session reset     # alias: voice reset
voice session id

# State / control
voice state | jq        # JSON snapshot — pipeline phase, capacity, agent
voice status            # human-readable
voice pause             # ignore F9
voice resume            # re-enable F9

# Tray + tools
voice tray              # PyQt6 system tray icon
voice tts say "hola"
voice tts voices

# Desktop primitives (same as the MCP exposes)
voice windows list
voice windows focus org.kde.kate
voice workspaces switch 2
voice clipboard read
voice clipboard write "hello"
voice dialog confirm "delete this?"
voice audio set 0.5
voice media play-pause
voice apps launch firefox
voice ocr find "Submit" --region 0 0 800 200
voice memory append "fixed the audio bug" --tag audio --tag bug
voice memory search audio
voice shell run --exec ls

# MCP server (opencode launches this for you on demand)
voice mcp serve
voice mcp status
voice mcp stop
voice mcp log -n 20

# Capacity mode (or use the tray)
voice config set capacity_mode read-only   # safest
voice config set capacity_mode assist      # default
voice config set capacity_mode full        # all rails

# Diagnostics
voice platform info     # backend + capabilities currently wired
voice platform caps
```

## Config

`config.json` at the project root. Env vars override. Edit via
`voice config get/set`, not by hand.

| Key                  | Env                     | Default                | Purpose                                    |
|----------------------|-------------------------|------------------------|--------------------------------------------|
| `voice`              | `VOICE`                 | `es_AR-daniela-high`   | Piper voice (partial match OK)             |
| `speaker_id`         | `SPEAKER_ID`            | `0`                    | Multi-speaker model index                  |
| `whisper_model`      | `WHISPER_MODEL`         | `ggml-small.bin`       | Model file under `models/`                 |
| `whisper_lang`       | `WHISPER_LANG`          | `es`                   | `auto` to detect                           |
| `opencode_host`      | `OPENCODE_HOST`         | `127.0.0.1`            | opencode serve host                        |
| `opencode_port`      | `OPENCODE_PORT`         | `4096`                 | opencode serve port                        |
| `attach_screenshot`  | `VOICE_ATTACH_SCREEN`   | `true`                 | Attach focused-monitor capture per prompt  |
| `keep_context`       | `VOICE_KEEP_CONTEXT`    | `true`                 | Reuse session id across turns              |
| `notify`             | `VOICE_NOTIFY`          | `true`                 | `notify-send` on errors                    |
| `capacity_mode`      | `VOICE_CAPACITY_MODE`   | `assist`               | `read-only` / `assist` / `full`            |
| `shell_allowlist`    | —                       | (~20 read-mostly cmds) | Regex patterns for `shell_run`             |
| `shell_timeout_s`    | —                       | `10.0`                 | `shell_run` default timeout (60 s hard cap)|
| `ocr_languages`      | —                       | `("spa", "eng")`       | Tesseract `-l` tuple                       |
| `ocr_min_confidence` | —                       | `50.0`                 | Drop OCR rows below this confidence        |

## Architecture in 30 seconds

```
            consumers (cli, tray, pipeline, mcp_server)
                              │
                              ▼
              voice_opencode.platform     ← Protocols + capability strings
                              │
       ┌──────────────┬───────┴────────┬──────────────┐
       │              │                │              │
   backends/      backends/        backends/      backends/
   linux_hypr…    linux_input…     linux_apps…    macos_stub
```

Consumers never import a backend directly — only through the
``voice_opencode.platform`` package. Backends declare capabilities;
the MCP server only registers a tool when (a) some backend claims
the capability **and** (b) the current capacity tier allows it.

See [`_ai/ARCHITECTURE.md`](./_ai/ARCHITECTURE.md) for the full module
map and [`_ai/DECISIONS.md`](./_ai/DECISIONS.md) for 19 ADRs explaining
why things are the way they are.

## Development

```bash
PYTHONPATH=src venv/bin/pytest          # 283 tests, no audio/network
venv/bin/ruff check src tests           # lint
venv/bin/mypy src/voice_opencode        # 60 files type-checked
```

CI runs all three on every push against Python 3.11, 3.12, 3.13.

Recipes for common changes are in [`_ai/SKILLS/`](./_ai/SKILLS/):

- `add-cli-command.md` — wire a new subcommand
- `add-voice.md` — install a new Piper voice
- `debug-tray.md` — when the system tray icon vanishes
- `debug-pipeline.md` — when F9 does nothing
- `build-dialog-backend.md` — add a new dialog backend

## Phases & status

Built in 11 focused phases (see [`_ai/CHANGELOG.md`](./_ai/CHANGELOG.md)
for the granular per-commit log):

| Phase | Topic                                          | Status |
|-------|------------------------------------------------|--------|
| 0     | Platform abstraction layer                     | ✅     |
| A     | Windows + workspaces                           | ✅     |
| B     | Clipboard                                      | ✅     |
| C     | Notify / ask_user / confirm                    | ✅     |
| D     | Capacity modes                                 | ✅     |
| E     | `shell_run` with safety rails                  | ✅     |
| F     | OCR find_text (Tesseract)                      | ✅     |
| G     | Memory (plain Markdown)                        | ✅     |
| H     | Audio / media (PipeWire + MPRIS)               | ✅     |
| I     | Apps (XDG launcher)                            | ✅     |
| J     | Audit viewer + capacity kill-switch in tray    | ✅     |
| K     | OS-agnostic detection helpers                  | planned (optional) |

## Troubleshooting

- `voice status` first.
- `voice state | jq` shows the tray's view.
- Logs: `logs/voice.log`, `logs/agent.log`, `logs/arecord.log`,
  `logs/piper.log`.
- `[BLANK_AUDIO]` from whisper = mic too quiet; check `pavucontrol`.
- First message after boot is slow (opencode lazy-loads the model
  provider); subsequent calls are much faster.
- More recipes in [`_ai/RUNBOOK.md`](./_ai/RUNBOOK.md).

## License

MIT.
