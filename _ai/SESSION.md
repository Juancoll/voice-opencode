# Session handoff — for the next opencode instance

> Drop this file into context (or just `cat _ai/SESSION.md`) at the
> start of a new session on any machine. It captures everything an
> incoming agent needs to continue the project without re-reading the
> entire repo.
>
> Also read **AGENTS.md** for repo-wide conventions and **`_ai/CHANGELOG.md`**
> for the full granular history. This file is the *current cursor*.

Last updated: 2026-05-18 — end of Phase B.

---

## Goal

Voice interface for opencode at `~/gitr/voice-opencode/` with
push-to-talk (F9), screenshot context, female Spanish voice, system
tray, MCP server exposing OS-agnostic desktop tools to opencode for
agentic control, modular Python package with `_ai/` knowledge base,
published on GitHub.

## Constraints & preferences (locked-in)

- Local STT (whisper.cpp small) + local TTS (piper-tts).
- Push-to-talk via Hyprland F9 (Wayland) — never wake-word.
- Persistent opencode session via `opencode serve` HTTP on `127.0.0.1:4096`.
- Screenshot of focused monitor attached to every prompt.
- Female Spanish voice: `es_AR-daniela-high` speaker_id=0.
- Settings via `config.json`, env vars override.
- Tray = PyQt6 `QSystemTrayIcon` (SNI). Left-click opens menu (no toggle).
- Click izquierdo abre menú (no toggle).
- sudo via `SUDO_ASKPASS=/usr/bin/ksshaskpass sudo -A`.
- CLI agrupada (`voice <group> <sub>`) + `LEGACY` aliases preservados
  para los keybinds de Hyprland (`voice start/stop/toggle/...`).
- Style: `pyproject.toml` + ruff + mypy + pytest, `src/` layout, una
  responsabilidad por módulo, `_ai/` como memoria, `AGENTS.md` entry
  point.
- MCP design: many small tools, stdio transport, per-call lock,
  read-only tools no toman lock, safety rails obligatorios.
- Iconos visibles en dark **y** light mode (white fill + dark stroke).
- **Arquitectura agnóstica al SO/DE**: consumidores nunca importan
  backends directos; sólo a través de `voice_opencode.platform`.
  Backends viven en `backends/<os>_<system>/` e implementan Protocols.
  Capability-driven tool registration en MCP.
- Phases plan: **A → B → C → D → H → I → J → E → F → G**.
  Una fase = un commit. Esperar OK del usuario entre fases.
- Defaults Phase choices: kdialog para Phase C, modo `assist` por
  defecto, memory en Markdown plano, browser saltado.

## Phase plan & status

| Phase | Topic                              | Status        |
|-------|------------------------------------|---------------|
| 0     | Platform abstraction layer         | ✅ done (63f0755) |
| A     | Windows + workspaces (CLI + tray)  | ✅ done       |
| B     | Clipboard (read/write + tools/CLI) | ✅ done       |
| C     | Notify / ask_user / confirm (kdialog→zenity) | ⏭ next |
| D     | Capacity modes (read-only/assist/full) filtering MCP tools | pending |
| H     | Audio / media (wpctl + playerctl backends) | pending  |
| I     | Apps (launch_app, list windows enriched) | pending    |
| J     | Audit viewer + kill switch en tray | pending       |
| E     | run_shell with safety rails        | pending       |
| F     | OCR find_text (Tesseract)          | pending       |
| G     | Memory (Markdown plano)            | pending       |

## What just shipped (Phase B, this commit)

- `backends/linux_clipboard_x11/xclip_backend.py`: real implementation
  (was a stub raising `BackendError`). `xclip -selection
  clipboard|primary -out/-in`, 3 s timeout, empty selection treated as
  `""` (xclip exits non-zero in that case). All four caps declared:
  `CLIPBOARD_{READ,WRITE,READ_PRIMARY,WRITE_PRIMARY}`.
- `backends/linux_clipboard_wayland/wlclip_backend.py`: fixed a real
  hang. `wl-copy` double-forks to serve future pastes; if any of
  stdout/stderr is a pipe, the daemon child inherits the fd and
  `subprocess.run` blocks until timeout. Route both to DEVNULL.
- `cli.py`: new `voice clipboard
  {read|write|read-primary|write-primary}` group. Write subcommands
  accept inline args (joined with spaces) or read from stdin when no
  args. Reads go to stdout without a trailing newline.
- 96 tests verde (eran 71): +18 in `test_backend_clipboard.py`
  covering both backends (init guards, capabilities, argv shape for
  read/write & primary variants, empty-selection → "", failures,
  timeouts); +7 in `test_cli.py` covering the new clipboard group.
- Smoke live OK on Wayland real: round-trip write/read on both
  selections, stdin path also works.
- ruff + mypy verde (51 source files, no new modules).

## Next concrete steps for the incoming agent

1. **Phase C — Dialogs.** Implementar
   `linux_dialog_kde/kdialog_backend.py` real (currently stub raising
   `BackendError`). Capabilities `dialog.notify`, `dialog.ask_user`,
   `dialog.confirm`. CLI `voice dialog ask "..."`. MCP tool
   `ask_user` con timeout. Fallback a
   `linux_dialog_gtk/zenity_backend.py`.
2. **Phase D — Capacity modes.** En `mcp_server.py` filtrar
   herramientas según `settings.capacity_mode`:
   - `read-only`: sólo `list_*`, `find_*`, `active_*`, `capture_*`,
     `clipboard_read`, `platform_info`.
   - `assist` (default): + write WM, type/click confirmados, dialogs.
   - `full`: + `run_shell` (Phase E), todo.
3. **Phase H — Audio / media.** Implementar
   `linux_audio_pipewire/wpctl_backend.py` + `playerctl_backend.py`
   (stubs). Capabilities `audio.*` + `media.*`. CLI `voice audio
   {get|set|mute}` / `voice media {play|next|prev|status}`. MCP tools.

## Critical context to keep in your head

- `gh` autenticado como `Juancoll` (token con scopes
  `gist, read:org, repo, workflow`).
- Repo público: <https://github.com/Juancoll/voice-opencode>, branch
  `main`. Commits previos: `40a41db`, `9d231ac`, `69e82c2`, `63f0755`,
  `f95ad2f`, + el commit Phase B que estás creando ahora.
- Wrapper `./voice` exporta `PYTHONPATH=src` antes de
  `python -m voice_opencode`. Activa el venv local.
- ydotool socket en `/run/user/1000/.ydotool_socket`.
- Detección platform: `HYPRLAND_INSTANCE_SIGNATURE` >
  `XDG_SESSION_TYPE` + `XDG_CURRENT_DESKTOP` > `DISPLAY` >
  `sys.platform`. `_state` cache en `platform/__init__.py`;
  `_reset_for_tests()` para forzar re-detección.
- `__getattr__` a nivel de módulo permite
  `from voice_opencode.platform import wm` con detección lazy.
- Backends que fallan en `__init__` → `_try()` los descarta y deja
  `Null*` en el slot. Los warnings van a `logs/voice.log` solo si
  `VOICE_DEBUG_BACKENDS=1`.
- Tests Hyprland mockean `subprocess.run` para evitar shell-out real.
- mypy 51 archivos; ruff verde con `# noqa: F405` en `__all__`.
- DMS = DankMaterialShell, SNI host correcto.
- Hyprland minimize_window mapeado a `special:scratch` (no native
  minimize). Capability `WM_MINIMIZE_WINDOW` deliberadamente NO
  declarada por el backend Hyprland — es contrato.
- Capability strings son contrato estable (renombrar = breaking).
- `agent.py` usa import lazy de `state` para evitar ciclo.

## Key ADRs (read `_ai/DECISIONS.md` for full)

- ADR-0007: wtype + ydotool sin root.
- ADR-0008: iconos white fill + dark stroke para dark/light.
- ADR-0009: CLI agrupada + LEGACY.
- ADR-0010: paquete modular `src/`.
- ADR-0011: MCP per-call lock, many small tools, stdio.
- ADR-0012: Platform abstraction layer (`platform/` Protocols +
  `backends/<os>_<system>/`); consumidores nunca importan backends;
  capability-driven MCP tool registration; Null backends raise
  `NotSupportedError`.

## Environment variables (optional)

| Variable                    | Effect                                           |
|-----------------------------|--------------------------------------------------|
| `VOICE_PLATFORM`            | Force a specific platform (e.g. `linux-x11`).    |
| `VOICE_CAPACITY_MODE`       | `read-only` / `assist` (default) / `full`.       |
| `VOICE_DEBUG_BACKENDS=1`    | Log per-backend init failures (else silent).     |
| `VOICE_DEPRECATION_WARN=1`  | Warn when a legacy CLI alias is used.            |

## Sanity checklist for the next session

```bash
cd ~/gitr/voice-opencode                     # or wherever the repo lives
git pull
PYTHONPATH=src venv/bin/pytest -q            # should be 96 passing
venv/bin/ruff check src tests                # all clean
venv/bin/mypy src/voice_opencode             # no issues, 51 files
./voice platform info                        # confirm correct backend
./voice state | jq                           # what's the system doing right now
```

If any of those fail, fix them **before** starting Phase C.

## Files to read first when resuming

1. `AGENTS.md` — repo conventions.
2. `_ai/SESSION.md` — this file.
3. `_ai/CHANGELOG.md` — granular history newest first.
4. `_ai/ARCHITECTURE.md` — module map / dependency rules.
5. `_ai/DECISIONS.md` — ADRs.
6. `_ai/STATE.md` — host-specific environment snapshot.
7. `_ai/RUNBOOK.md` — operational recipes.
