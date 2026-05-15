# Architecture

```
voice-opencode/
├── voice                     # bash wrapper → python -m voice_opencode
├── src/voice_opencode/
│   ├── __init__.py           # version, package marker
│   ├── __main__.py           # `python -m voice_opencode` entry
│   ├── paths.py              # PROJECT_ROOT, STATE_DIR, all file paths
│   ├── logging.py            # log() — stderr + logs/voice.log
│   ├── notify.py             # notify() — notify-send wrapper
│   ├── config.py             # Settings dataclass, env > json > defaults
│   ├── state.py              # pipeline phase + pause sentinel
│   ├── audio.py              # arecord start/stop/is_recording
│   ├── stt.py                # whisper-cli wrapper
│   ├── tts.py                # piper-tts + paplay; voice metadata
│   ├── screenshot.py         # grim wrapper (monitor/window/all)
│   ├── opencode_client.py    # health() + Session class
│   ├── desktop.py            # wtype + ydotool + hyprctl activewindow
│   ├── pipeline.py           # orchestration: rec → stt → opencode → tts
│   ├── cli.py                # dispatcher: grouped + legacy aliases
│   └── tray.py               # PyQt6 QSystemTrayIcon
├── tests/                    # pytest suite (no audio/network)
├── icons/                    # SVG icons per state
├── voices/                   # Piper .onnx + .onnx.json sidecars
├── models/                   # whisper-cli ggml-*.bin
├── _ai/                      # this folder — assistant memory
├── config.json               # user overrides over DEFAULTS
├── pyproject.toml            # build, ruff, mypy, pytest config
└── install.sh                # idempotent setup
```

## Module dependency graph

Lower modules have no awareness of higher ones. Imports flow downward only.

```
            cli.py ── tray.py
              │           │
              ├── pipeline.py ─────────────┐
              │     │                      │
              │     ├── audio.py           ├── desktop.py
              │     ├── stt.py             │
              │     ├── tts.py             │
              │     ├── screenshot.py ─────┤
              │     ├── opencode_client.py │
              │     └── state.py           │
              │                            │
              ├── config.py ───────────────┤
              ├── notify.py ───────────────┤
              ├── logging.py ──────────────┤
              └── paths.py ────────────────┘   ← no internal imports
```

Rule: a module higher in the tree may import from anything below it; the
reverse is forbidden. ``screenshot.py`` deliberately calls ``hyprctl``
itself instead of importing ``desktop.py`` to avoid a cycle.

## Process model

Three independent processes share state through ``$XDG_RUNTIME_DIR/voice-opencode/``:

| Process                              | Role                                      | Lifetime          |
|--------------------------------------|-------------------------------------------|-------------------|
| `opencode serve` (systemd user unit) | LLM HTTP API on 127.0.0.1:4096            | session           |
| `voice tray` (PyQt6 QSystemTrayIcon) | UI; polls `voice state` every 1s          | session           |
| `voice <cmd>` (CLI invocation)       | one-shot per Hyprland keypress / tray act | sub-second to ~1m |
| `arecord` (started by `voice rec start`) | captures WAV, killed by `voice rec stop` | press → release   |

State files written to ``$XDG_RUNTIME_DIR/voice-opencode/``:

| File          | Writer                | Reader                |
|---------------|-----------------------|-----------------------|
| `rec.pid`     | audio.start           | audio.is_recording / stop |
| `rec.wav`     | arecord               | stt.transcribe        |
| `state`       | pipeline phases       | tray.refresh + cli `state` |
| `paused`      | cli `pause/resume`    | pipeline.start_recording, tray |
| `session.id`  | opencode_client       | opencode_client       |
| `screen.png`  | screenshot.capture    | opencode_client.ask   |

## Configuration resolution

Three layers, highest priority first:

1. **Environment variables** — mapped in ``ENV_MAP`` in ``config.py``.
2. **`config.json`** — user file at the repo root.
3. **`DEFAULTS`** — frozen dataclass instance in ``config.py``.

Resolution happens once on import. Call ``config.reload()`` after writing
to ``config.json`` to refresh in-memory ``settings``. The CLI's
``config set`` already does this.

## Extension points

- **New CLI command**: add a `cmd_*` function in ``cli.py`` and register
  it in ``COMMANDS``. Add a legacy alias in ``LEGACY`` if needed.
- **New voice**: drop ``<stem>.onnx`` + ``<stem>.onnx.json`` in ``voices/``
  (use ``download-voice.sh``). The tray menu auto-discovers them.
- **New STT model**: drop in ``models/`` and set ``whisper_model`` in config.
- **New screenshot scope**: extend ``screenshot.capture_to`` and pass the
  new value through ``settings.screenshot_scope``.
- **New tool for the agent (Phase 3)**: add a function to ``desktop.py``,
  expose it from the CLI (``desktop`` group), then wrap it in the future
  MCP server.
