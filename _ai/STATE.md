# Environment snapshot

What's installed and where, on this machine, as of the last assistant
session. Refresh this whenever something material changes.

> The repo's ``install.sh`` is now multi-distro
> (``pacman``/``apt``/``dnf``) and binds keys on Hyprland and XFCE
> automatically. See ADR-0022. On apt/dnf hosts, piper-tts and
> whisper.cpp are fetched into ``vendor/`` and exposed via
> ``.voice-env`` (sourced by the ``./voice`` wrapper).

## Host

- OS: CachyOS / Arch Linux, Linux kernel ≥ 6.x
- Compositor: Hyprland (Wayland)
- Panel/shell: DankMaterialShell (`dms`, quickshell-based)
- Default terminal: `foot`
- Python: 3.14 (`/usr/bin/python`) — venv uses the same.
- User: `juan`, member of `input` group
- Repo location: `~/git/voice-opencode` (canonical; exposed via
  `VOICE_OPENCODE_HOME` in `~/.config/environment.d/voice-opencode.conf`
  so Hyprland binds and the autostart entry never need a hardcoded
  path). Manifest at `~/.config/voice-opencode/install.manifest.json`
  lists every file the install touched outside the repo.

## Pacman packages (runtime)

| Package           | Purpose                                |
|-------------------|----------------------------------------|
| `sox`             | audio utilities (also pulled by piper) |
| `whisper.cpp`     | STT — provides `/usr/bin/whisper-cli`  |
| `piper-tts-bin`   | TTS — provides `/usr/bin/piper-tts`    |
| `grim`            | Wayland screenshots                    |
| `wtype`           | Wayland virtual keyboard               |
| `ydotool`         | Synthetic mouse + keyboard via uinput  |
| `wl-clipboard`    | Wayland clipboard (`wl-copy`/`wl-paste`) |
| `xclip`           | X11 clipboard (only needed in X11 sessions) |
| `kdialog`         | Blocking dialogs (preferred — Qt/KDE)  |
| `zenity`          | Blocking dialogs (fallback — GTK; install only on non-KDE hosts) |
| `libnotify`       | `notify-send`                          |
| `wireplumber`     | `wpctl` (PipeWire audio control)       |
| `playerctl`       | MPRIS media transport CLI              |
| `gtk3`            | `gtk-launch` (XDG app launcher, Phase I) |
| `tesseract`       | OCR engine (Phase F)                   |
| `tesseract-data-eng` | OCR English language data           |
| `tesseract-data-spa` | OCR Spanish language data           |
| `alsa-utils`      | `arecord`                              |
| `python`          | for the venv                           |

(Plus the desktop you already have: `pipewire`, `pipewire-pulse`, `paplay`, etc.)

## Python venv

`venv/` at repo root, created by `install.sh`. Contains:

- `requests` — HTTP to opencode serve
- `PyQt6` — system tray
- `mcp` — MCP server SDK (FastMCP)
- `pytest`, `ruff`, `mypy`, `types-requests` (dev only)

## Models and voices

- `models/ggml-small.bin` (466 MB) — whisper.cpp small Spanish-capable model
- `voices/` — 8 Piper voices, default `es_AR-daniela-high` (single-speaker, 22050 Hz)
  - `es_AR-daniela-high`     ← current default
  - `es_ES-carlfm-x-low`
  - `es_ES-davefx-medium`
  - `es_ES-mls_10246-low`
  - `es_ES-mls_9972-low`
  - `es_ES-sharvard-medium`  (multi-speaker: 0=M, 1=F)
  - `es_MX-ald-medium`
  - `es_MX-claude-high`

## systemd --user units

- `opencode-serve.service` (custom, in `~/.config/systemd/user/`)
  - Runs `opencode serve --port 4096`
  - WorkingDirectory: `$HOME` by default — edit if you want it scoped to a project
- `ydotool.service` (provided by package, enabled by us)
  - Runs `ydotoold` as the user, listens on `/run/user/$UID/.ydotool_socket`

## Hyprland config

- `~/.config/hypr/conf.d/voice.conf` — F9 push-to-talk binds + Super+F9 reset
- `~/.config/hypr/conf.d/autostart.conf` — `exec-once` for the tray
- `~/.config/hypr/hyprland.conf` line 28 sources `conf.d/*.conf`

## XDG entries

- `~/.local/share/applications/voice-opencode.desktop` — appears in app menus

## Runtime state

`$XDG_RUNTIME_DIR/voice-opencode/`:

- `rec.pid`     — arecord PID, presence ⇒ recording
- `rec.wav`     — last raw capture
- `tts.wav`     — last Piper synthesis (played by the player backend)
- `state`       — current pipeline phase (`idle|recording|thinking|speaking|error`)
- `paused`      — sentinel; if present, F9 is ignored
- `agent`       — sentinel; if present, MCP tool is currently acting (F9 also ignored)
- `session.id`  — opencode session uuid
- `server.url`  — (reserved for future use)
- `screen.png`  — last screenshot sent to opencode
- `turn.notify-id` — id of the active per-turn notification (ADR-0025);
  pipeline writes it on `turn_start`, MCP server reads it to update
  the same on-screen bubble via `notify-send -r <id>`, pipeline
  clears it on `turn_end`.

Plus, in the repo:

- `logs/agent.log` — JSON-Lines audit of every MCP tool invocation

## opencode integration

`~/.config/opencode/opencode.json` registers our MCP server as
`voice_desktop` AND **must declare a permission policy that pre-allows
everything**, otherwise the headless server stalls indefinitely on
``permission.asked`` events that nobody can answer (no TUI is
attached):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "voice_desktop": {
      "type": "local",
      "command": ["/home/juan/git/voice-opencode/voice", "mcp", "serve"],
      "enabled": true
    }
  },
  "permission": {
    "read": "allow", "edit": "allow", "glob": "allow", "grep": "allow",
    "list": "allow", "bash": "allow", "task": "allow",
    "external_directory": "allow",
    "todowrite": "allow", "question": "allow",
    "webfetch": "allow", "websearch": "allow",
    "repo_clone": "allow", "repo_overview": "allow",
    "lsp": "allow", "doom_loop": "allow", "skill": "allow"
  }
}
```

The ``external_directory`` rule is the critical one: without it, any
MCP tool that writes to ``$XDG_RUNTIME_DIR/voice-opencode/`` (e.g.
``capture_screen``) causes opencode to fire a permission prompt that
times out after 600s, leaving the pipeline frozen on ``thinking``.

opencode launches the subprocess on-demand and keeps it alive between
messages. Tools surface to the model as `voice_desktop_<name>`
(e.g. `voice_desktop_list_monitors`).

## Known quirks

- `qt.qpa.wayland: Failed to create grabbing popup` warnings on tray
  startup are benign; menu still works.
- `arecord` with PipeWire's ALSA shim sometimes records empty audio if
  the PipeWire default source changes mid-press; check `pavucontrol`
  if STT keeps returning empty strings.
- DMS uses ayatana-appindicator path under the hood; PyQt6's SNI works,
  Qt5 fallback would not.

## Environment variables (optional)

| Variable                  | Effect                                               |
|---------------------------|------------------------------------------------------|
| `VOICE_PLATFORM`          | Force a specific platform (e.g. `linux-x11`).        |
| `VOICE_CAPACITY_MODE`     | `read-only` / `assist` (default) / `full`.           |
| `VOICE_DEBUG_BACKENDS=1`  | Log per-backend init failures (else silent).         |
| `VOICE_DEPRECATION_WARN=1`| Warn when a legacy CLI alias is used.                |
