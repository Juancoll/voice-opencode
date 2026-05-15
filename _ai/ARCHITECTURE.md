# Architecture

```
voice-opencode/
├── voice                          # bash wrapper → python -m voice_opencode
├── src/voice_opencode/
│   ├── __init__.py                # version, package marker
│   ├── __main__.py                # `python -m voice_opencode` entry
│   ├── paths.py                   # PROJECT_ROOT, STATE_DIR, all file paths
│   ├── logging.py                 # log() — stderr + logs/voice.log
│   ├── notify.py                  # notify() — notify-send wrapper
│   ├── config.py                  # Settings dataclass, env > json > defaults
│   ├── state.py                   # pipeline phase + pause sentinel
│   ├── audio.py                   # arecord start/stop/is_recording
│   ├── stt.py                     # whisper-cli wrapper
│   ├── tts.py                     # piper-tts + paplay; voice metadata
│   ├── screenshot.py              # shim → platform.screen
│   ├── desktop.py                 # shim → platform.input + platform.wm
│   ├── opencode_client.py         # health() + Session class
│   ├── agent.py                   # MCP agent-mode lock + audit log
│   ├── mcp_server.py              # capability-driven MCP server (stdio)
│   ├── pipeline.py                # orchestration: rec → stt → opencode → tts
│   ├── cli.py                     # dispatcher: grouped + legacy aliases
│   ├── tray.py                    # PyQt6 QSystemTrayIcon
│   │
│   ├── platform/                  # ← OS/DE-agnostic abstraction layer
│   │   ├── __init__.py            # detect(), singletons (wm/input/screen/…)
│   │   ├── base.py                # Protocols: WindowManager, InputBackend, …
│   │   ├── types.py               # Window, Workspace, Monitor, Rect (frozen)
│   │   ├── capabilities.py        # constant strings, e.g. WM_FOCUS_WINDOW
│   │   └── null.py                # NullX implementations (raise NotSupported)
│   │
│   └── backends/                  # ← concrete platform implementations
│       ├── linux_hyprland/        # hyprctl WM (windows + workspaces)
│       ├── linux_wlroots/         # grim screen capture, wlr-randr fallback
│       ├── linux_kde_wayland/     # KWin scripting (stub for now)
│       ├── linux_x11/             # xdotool/wmctrl (stub for now)
│       ├── linux_input/           # ydotool + wtype (display-server agnostic)
│       ├── linux_clipboard_wayland/  # wl-copy / wl-paste
│       ├── linux_clipboard_x11/   # xclip (stub)
│       ├── linux_audio_pipewire/  # wpctl + playerctl (stubs, Phase H)
│       ├── linux_dialog_kde/      # kdialog (stub) + libnotify (real)
│       ├── linux_dialog_gtk/      # zenity (stub fallback)
│       ├── macos_stub/            # placeholder for AppleScript/Quartz
│       └── windows_stub/          # placeholder for pywin32/UIA
├── tests/                         # pytest suite (no audio/network)
├── icons/                         # SVG icons per state
├── voices/                        # Piper .onnx + .onnx.json sidecars
├── models/                        # whisper-cli ggml-*.bin
├── _ai/                           # this folder — assistant memory
├── config.json                    # user overrides over DEFAULTS
├── pyproject.toml                 # build, ruff, mypy, pytest config
└── install.sh                     # idempotent setup
```

## The platform layer

The single most important rule of this codebase: **consumers
(``mcp_server``, ``cli``, ``tray``, ``pipeline``) never import a backend
module directly**. They go through ``voice_opencode.platform``.

```
                     consumers (mcp_server, cli, tray, pipeline)
                                       │
                                       ▼
                          voice_opencode.platform
                                       │
                          (auto-detect; settings.platform_override)
                                       │
                                       ▼
                  ┌────────┬────────┬────────┬────────┬────────┐
              wm  │ input  │ screen │clipboard│ notify │ … etc. │
                  └────────┴────────┴────────┴────────┴────────┘
                       │       │       │
                       ▼       ▼       ▼
                  Hyprland  ydotool  grim   …(real)
                  KWin     wtype     spectacle
                  X11      …
                  (or NullX if no real backend is available)
```

### Public API

```python
from voice_opencode import platform as plat

plat.active_platform                # "linux-hyprland" / "linux-x11" / …
plat.all_capabilities()             # frozenset of cap strings currently active
plat.supported(cap.WM_FOCUS_WINDOW) # True iff some backend declares that cap

plat.wm.list_windows()              # list[Window]
plat.input.type_text("hi")
plat.screen.capture_monitor(out)
plat.clipboard.read()
```

The singletons (``wm``, ``input``, ``screen``, ``clipboard``, ``notify``,
``dialog``, ``audio``, ``media``, ``apps``, ``shell``) **always** resolve
to *something*: a real backend if one was wired, otherwise a Null
implementation that raises ``NotSupportedError`` from every method.

### Capabilities

Every backend exposes ``capabilities() -> frozenset[str]``. The MCP
server only registers a tool when at least one wired backend declares
the matching capability — so the model never sees a tool that's
guaranteed to fail on this host.

The capability constants live in ``platform/capabilities.py`` and are
**stable strings** (we use them in audit logs and config). Renaming one
is a breaking change for every backend.

### Adding a backend

1. Create ``backends/<os>_<system>/`` with a package ``__init__.py``.
2. Implement one or more of the Protocols from ``platform/base.py``.
3. Each implementation declares which abstract operations it supports
   via ``capabilities()``.
4. Wire it in ``platform/__init__.py``'s ``_build()`` for the relevant
   ``PLATFORM_*`` value (or call ``_wire_common_linux``).
5. Add a ``test_backend_<name>.py`` that mocks the underlying CLI tool
   so the test runs anywhere.

## Module dependency graph

Lower modules have no awareness of higher ones. Imports flow downward only.

```
                      cli.py ── tray.py
                        │           │
                        ├── pipeline.py
                        │     │
                        │     ├── audio.py
                        │     ├── stt.py
                        │     ├── tts.py
                        │     ├── screenshot.py ─┐  (shim)
                        │     ├── opencode_client.py │
                        │     └── state.py            │
                        │                            ▼
                        ├── mcp_server.py ── platform/
                        ├── desktop.py ─────────┘   (shim)
                        ├── agent.py
                        ├── config.py
                        ├── notify.py
                        ├── logging.py
                        └── paths.py
```

The platform layer sits *below* every consumer. Backends sit below the
platform layer and never import each other.

## Process model

Three independent processes share state through ``$XDG_RUNTIME_DIR/voice-opencode/``:

| Process                              | Role                                      | Lifetime          |
|--------------------------------------|-------------------------------------------|-------------------|
| `opencode serve` (systemd user unit) | LLM HTTP API on 127.0.0.1:4096            | session           |
| `voice tray` (PyQt6 QSystemTrayIcon) | UI; polls `voice state` every 1s          | session           |
| `voice <cmd>` (CLI invocation)       | one-shot per Hyprland keypress / tray act | sub-second to ~1m |
| `voice mcp serve` (spawned by opencode) | exposes desktop tools to the model     | session           |
| `arecord` (started by `voice rec start`) | captures WAV, killed by `voice rec stop` | press → release   |

State files written to ``$XDG_RUNTIME_DIR/voice-opencode/``:

| File          | Writer                | Reader                |
|---------------|-----------------------|-----------------------|
| `rec.pid`     | audio.start           | audio.is_recording / stop |
| `rec.wav`     | arecord               | stt.transcribe        |
| `state`       | pipeline phases       | tray.refresh + cli `state` |
| `paused`      | cli `pause/resume`    | pipeline.start_recording, tray |
| `agent`       | mcp_server (per call) | tray, agent.is_active, cli `state` |
| `session.id`  | opencode_client       | opencode_client       |
| `screen.png`  | screenshot.capture    | opencode_client.ask   |

Plus, in the repo:

| File              | Writer                              | Reader              |
|-------------------|-------------------------------------|---------------------|
| `logs/agent.log`  | mcp_server (every tool call)        | `voice mcp log`     |
| `logs/voice.log`  | logging.log()                       | tray "Ver logs"     |

## Configuration resolution

Three layers, highest priority first:

1. **Environment variables** — mapped in ``ENV_MAP`` in ``config.py``.
2. **`config.json`** — user file at the repo root.
3. **`DEFAULTS`** — frozen dataclass instance in ``config.py``.

Resolution happens once on import. Call ``config.reload()`` after writing
to ``config.json`` to refresh in-memory ``settings``. The CLI's
``config set`` already does this.

Settings of note:

* ``platform_override`` — force a specific backend wiring (default: auto).
* ``capacity_mode`` — agent capability filter; ``"assist"`` by default.
  Phase D will use this to filter MCP tool registration further.

## Extension points

- **New CLI command**: add a `cmd_*` function in ``cli.py`` and register
  it in ``COMMANDS``. Add a legacy alias in ``LEGACY`` if needed.
- **New voice**: drop ``<stem>.onnx`` + ``<stem>.onnx.json`` in ``voices/``
  (use ``download-voice.sh``). The tray menu auto-discovers them.
- **New STT model**: drop in ``models/`` and set ``whisper_model`` in config.
- **New screenshot scope**: extend ``platform/base.py:ScreenBackend`` and
  every backend that implements it.
- **New MCP tool**: add a capability constant in ``platform/capabilities.py``,
  declare support in the relevant backend(s), then register the
  ``@mcp.tool`` wrapper in ``mcp_server._register_*`` guarded by
  ``plat.supported(...)``. If it acts on the desktop, wrap in ``with
  _acting():`` so F9 is blocked during execution.
- **New platform**: see "Adding a backend" above.
