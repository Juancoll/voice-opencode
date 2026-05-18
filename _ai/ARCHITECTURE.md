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
│   ├── audit_viewer.py            # Qt dialog tailing agent.log (Phase J)
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
│       ├── linux_clipboard_wayland/  # wl-copy / wl-paste (real, Phase B)
│       ├── linux_clipboard_x11/   # xclip (real, Phase B)
│       ├── linux_audio_pipewire/  # wpctl + playerctl (real, Phase H)
│       ├── linux_apps_xdg/        # gtk-launch + /proc + .desktop (real, Phase I)
│       ├── linux_shell_posix/     # subprocess + allowlist + no-shell (real, Phase E)
│       ├── linux_ocr_tesseract/   # tesseract TSV + word-level match (real, Phase F)
│       ├── linux_dialog_kde/      # kdialog (real, Phase C) + libnotify (real)
│       ├── linux_dialog_gtk/      # zenity (real, Phase C — fallback)
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
``dialog``, ``audio``, ``media``, ``apps``, ``shell``, ``ocr``) **always** resolve
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
                        ├── capacity.py
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

## Capacity modes (Phase D)

Orthogonal to the capability filter: ``capacity.py`` decides which MCP
tools are *exposed* based on ``settings.capacity_mode`` (``read-only``
/ ``assist`` (default) / ``full``). Tools that aren't allowed by the
active tier are **not registered** with the FastMCP server — the model
literally cannot see them, so there's nothing to bypass.

The mapping (tool → minimum tier) lives in ``capacity.TIER_BY_TOOL``
and is **the contract**: adding a new MCP tool means adding it to that
dict in the same change. Tools not in the dict default to ``full``
(safe-by-default: a forgotten tool is hidden from the restricted modes
rather than silently leaked).

``mcp_server._expose(capability, tool_name)`` combines both filters:
``plat.supported(capability) and capacity.allows(tool_name)``. The
single helper means consumers cannot forget one half of the check.

The active tier and exposed tool count are logged to ``logs/voice.log``
on every MCP server startup, so audit trails always state what the
model could see at the time. ``platform_info`` (the tool the model uses
to discover itself) now also returns ``capacity_mode`` so the model
knows its own bounds without re-querying.

See ADR-0014 for the rationale and trade-offs.

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

## Dialog backends — exit-code contract (Phase C)

All ``DialogBackend`` implementations (currently ``kdialog`` and
``zenity``) share the same exit-code → return-value mapping. New
backends (rofi/wofi/yad/…) MUST follow it. See ADR-0013 for rationale.

| Subprocess rc | ``confirm`` returns | ``ask_text`` / ``ask_choice`` return |
|---------------|---------------------|--------------------------------------|
| 0             | ``True``            | the answer (trailing ``\n`` stripped) |
| 1             | ``False``           | ``None`` (user cancelled)            |
| anything else | raise ``BackendError`` (with stderr context) |                                      |

Notes:

* Timeouts are very generous (300s) because dialogs are inherently
  interactive. A caller wanting tighter bounds wraps the call itself.
* ``ask_choice`` with an empty ``choices`` list MUST raise
  ``BackendError("ask_choice needs at least one option")`` — fail fast
  rather than open an empty menu.
* ``LibnotifyBackend`` (notify) is separate from ``DialogBackend``: it
  is fire-and-forget and declares only ``NOTIFY_SHOW``.

The MCP tools wrap ``confirm`` / ``ask_text`` / ``ask_choice`` inside
``with _acting():`` (so F9 is blocked while the user is in the dialog)
and translate the Pythonic return values to strings the model can read:
``"yes"`` / ``"no"`` for confirm, the answer or ``""`` for cancel for
the others.
- **New platform**: see "Adding a backend" above.
