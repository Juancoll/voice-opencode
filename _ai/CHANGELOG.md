# Changelog (assistant view)

What I (the assistant) actually did, when, and why. Newest first.
This is intentionally more granular than `_ai/DECISIONS.md`.

---

## 2026-05-18 — Phase B: clipboard surface (CLI + xclip backend + tests)

- Phase 0 had wired `linux_clipboard_wayland` (wl-copy/wl-paste) and the
  MCP `clipboard_read` / `clipboard_write` tools. Phase B closes the
  remaining gaps: a real X11 backend, a CLI group so the human has parity
  with the agent, and full subprocess-mocked tests.
- ``backends/linux_clipboard_x11/xclip_backend.py``: real implementation
  (was a stub raising `BackendError`). `xclip -selection clipboard|primary
  -out/-in`, 3 s timeout, empty-selection treated as `""` (xclip exits
  non-zero in that case, same convention as the Wayland backend). All
  four caps declared: `CLIPBOARD_{READ,WRITE,READ_PRIMARY,WRITE_PRIMARY}`.
- ``backends/linux_clipboard_wayland/wlclip_backend.py``: fixed a real
  hang. `wl-copy` double-forks to keep serving paste requests; if any of
  stdout/stderr is a pipe, the daemon child inherits the fd and
  `subprocess.run` blocks until timeout. Route both to `DEVNULL`. We lose
  stderr context on failure but the returncode is enough to detect it.
  Without this, every `./voice clipboard write` hung 3 s and raised.
- ``cli.py``: new ``voice clipboard {read|write|read-primary|write-primary}``
  group. `write` / `write-primary` accept inline args (joined with spaces)
  or read from stdin when no args. Reads go to stdout without a trailing
  newline (caller decides). `BackendError`/`NotSupportedError` → rc=1
  with `error: …` on stderr (same convention as windows/workspaces).
  Registered in `COMMANDS` between `workspaces` and `platform`.
- 96 tests verde (eran 71): +18 in `test_backend_clipboard.py` covering
  both backends (init guards, capabilities, argv shape for read/write &
  primary variants, empty-selection → "", failure paths, timeouts);
  +7 in `test_cli.py` covering the new clipboard group (read/read-primary
  print to stdout, write inline + stdin, primary variant, BackendError
  propagation, unknown subcommand → rc=1).
- Smoke live OK on Wayland real: round-trip write/read on both selections,
  stdin path also works.
- ruff + mypy verde (51 source files, no new modules).

## 2026-05-15 — Phase A: window & workspace surface (CLI + tray + tests)

- Phase 0 already exposed every Hyprland WM/workspace operation
  through the MCP layer. Phase A closes the user-facing loop so the
  human has parity with the agent.
- ``cli.py``: three new command groups.
  - ``voice windows {list|find|active|focus|close|move|resize|float|fullscreen|send-to-workspace}``
  - ``voice workspaces {list|active|switch|send-to-monitor}``
  - ``voice platform {info|caps}`` — diagnostics: which backend got
    wired and which capabilities are live.
  All three delegate to ``platform.wm`` / ``platform.all_capabilities``;
  ``BackendError``/``NotSupportedError`` are translated into rc=1 with
  a ``error: …`` line on stderr so shell users get a clean signal.
- ``tray.py``: two new submenus, ``Ventanas`` and ``Workspaces``.
  Both use ``QMenu.aboutToShow`` so the listing is always fresh
  (rebuilt on open, not polled every second). Each window entry has
  a sub-submenu with Focus / Cerrar / Float / Fullscreen; each
  workspace entry switches on click. Marker ``●`` on the focused /
  active item.
- ``platform/__init__.py``: backend-unavailable warnings now require
  ``VOICE_DEBUG_BACKENDS=1``. Previously every CLI invocation printed
  four lines of "kdialog/zenity/wpctl unavailable" noise. Also fixed
  a duplicated capability re-export in ``__all__``.
- Tests: 71 passing (was 57). New ``test_backend_hyprland`` cases
  cover the entire write API by recording the exact ``hyprctl
  dispatch`` argv (focus/close/move/resize/float/fullscreen/minimize/
  workspace switch / move-to-workspace / send-to-monitor) plus
  ``list_workspaces`` active-marking. New ``test_cli`` cases cover
  ``platform info`` JSON shape, ``windows list`` round-trip,
  ``windows focus`` invocation, ``workspaces switch`` invocation, and
  BackendError propagation as rc=1.
- Smoke live: detected 4 windows across 2 workspaces correctly,
  active workspace marker correct, ``platform info`` lists 30
  capabilities. ruff + mypy clean (51 source files).

## 2026-05-15 — Phase 0: platform abstraction (``platform/`` + ``backends/``)

- Inserted a platform layer between consumers and the OS so the
  codebase is OS- and DE-agnostic. Consumers (mcp_server, cli, tray,
  pipeline) import from ``voice_opencode.platform`` only.
- New ``platform/`` package: ``base.py`` (Protocols), ``types.py``
  (Window/Workspace/Monitor/Rect dataclasses), ``capabilities.py``
  (stable cap-string constants), ``null.py`` (NotSupportedError
  fallbacks), ``__init__.py`` (detection + lazy singletons).
- New ``backends/`` package with sub-packages per (OS, subsystem):
  ``linux_hyprland`` (WM via hyprctl), ``linux_wlroots`` (grim screen
  capture, wlr-randr fallback), ``linux_input`` (ydotool + wtype),
  ``linux_clipboard_wayland`` (wl-clipboard), ``linux_dialog_kde``
  (libnotify real, kdialog stub), and stubs for ``linux_kde_wayland``,
  ``linux_x11``, ``linux_clipboard_x11``, ``linux_audio_pipewire``,
  ``linux_dialog_gtk``, ``macos_stub``, ``windows_stub``.
- ``desktop.py`` and ``screenshot.py`` rewritten as thin shims over
  ``platform.input/wm`` and ``platform.screen`` so external imports
  keep working.
- ``mcp_server.py`` rewritten capability-driven: each tool only
  registers if ``plat.supported(cap)`` is True. Tool surface now
  expands to include ``list_windows``, ``find_windows``, ``focus_window``,
  ``close_window``, ``move_window``, ``resize_window``, ``toggle_floating``,
  ``toggle_fullscreen``, ``switch_workspace``, ``move_window_to_workspace``,
  ``send_workspace_to_monitor``, ``list_workspaces``, ``active_workspace``,
  ``clipboard_read``, ``clipboard_write``, ``scroll_mouse``,
  ``platform_info`` (always-on diagnostic).
- ``Settings`` gained ``platform_override`` and ``capacity_mode``
  (``capacity_mode`` is a placeholder for Phase D).
- New tests: ``test_platform.py`` (10 detection + wiring cases),
  ``test_backend_hyprland.py`` (6 cases with mocked hyprctl).
  ``test_desktop_keys.py`` migrated to target the new backend.
  ``test_mcp_server.py`` rewritten to verify capability-driven
  registration (no caps → no tools, full caps → all tools).
- Smoke-tested live on this host: detected ``linux-hyprland``, 30
  capabilities active, real WM/screen/clipboard/notify backends, the
  rest fall through to ``Null*``.
- 57 tests verde, ruff verde, mypy verde (51 source files).
- ADR-0012 added; ARCHITECTURE rewritten.

## 2026-05-15 — Audit fixes, robust subprocess handling, icons for dark/light

- Auditoría aplicada (CRITICAL + HIGH):
  - `agent.is_active()`: maneja sentinel vacío/corrupto y `pid <= 0` como
    "released" en vez de "alive forever"; limpia `AGENT_FILE` en cada caso.
  - `agent.audit()`: timestamps ISO-8601 UTC con `Z` (antes hora local
    ambigua).
  - `cli.cmd_session`: añadido alias `id` para `voice session id` (estaba
    documentado pero no implementado).
  - `cli.cmd_ask`: captura excepciones de `Session.get_or_create()` y
    `tts.speak()`, notifica al usuario y devuelve exit code != 0.
  - `config._coerce`: errores de casting incluyen el nombre de la clave;
    `int()` envuelto para mensajes claros.
  - `stt.transcribe`: timeout de 120 s a `whisper-cli` (antes podía
    colgar la pipeline indefinidamente).
  - `tts.speak`: timeout de 60 s a `paplay`, cierra fds de log
    correctamente, mata la cadena piper→paplay si se cuelga.
  - `screenshot.capture`: maneja `TimeoutExpired` de `grim` sin propagar.
  - `audio.start_recording` / `tts.speak`: cierran nuestro fd del log
    tras `Popen` (el hijo conserva su copia); evita fd leak.
  - `tray._update_menu`: guarda defensiva contra `session` no-`str`,
    elipsis sólo si la cadena se trunca.
  - `tts.voice_info`: log explícito al fallback con la causa.
- Bump `__version__` a `0.3.0` (alineado con `pyproject.toml`).
- Iconos rediseñados con `fill="#ffffff"` + `stroke="#202124"` para que
  el micrófono sea visible tanto en barras claras como oscuras
  (DankMaterialShell light theme mostraba el icono casi invisible).
- 33 tests verde, ruff verde, mypy verde (18 source files).

## 2026-05-15 — Docs, installer polish, initial commit
- Wrote all SKILLS recipes (`add-cli-command`, `add-voice`,
  `add-config-key`, `add-tray-menu-item`, `debug-tray`, `debug-pipeline`,
  `build-mcp-tool`) and a `SKILLS/README.md` index.
- Rewrote top-level `README.md` to reflect the new grouped CLI, the
  `src/` layout, the `_ai/` knowledge base, and the dev workflow.
- `install.sh`: added `--dev` flag (installs ruff/mypy/pytest), added
  `pip install -e .` so the package is importable without the wrapper's
  `PYTHONPATH=src` trick.
- Removed `voice_opencode.py.legacy` and `voice_tray.py.legacy`.
- `git init` + initial commit (`40a41db`).

---

## 2026-05-15 — Phase 3: MCP server (`voice_desktop`)
- New module `agent.py`: process-level lock (`AGENT_FILE`) + JSONL audit
  log (`logs/agent.log`) + `is_blocking()` that combines pause + agent.
- New module `mcp_server.py`: FastMCP-based server over stdio with 8
  tools (`type_text`, `press_key`, `move_mouse`, `click_mouse`,
  `focused_window`, `capture_screen`, `list_monitors`, `sleep_ms`).
- Safety rails: dangerous-key blocklist, 30 calls / 5 s rate limit,
  per-call lock via `_acting()` context manager (read-only tools don't
  take the lock so F9 stays usable).
- New CLI subgroup `voice mcp serve|status|stop|log`.
- `pipeline.start_recording` now consults `agent.is_blocking()`.
- Tray gained an "agente: 🤖 activo" status line, a "Detener agente
  (MCP)" action, and treats agent-mode as `thinking` for icon purposes.
- `voice state` JSON gained `agent: bool` (additive, doesn't break the
  tray's existing wire format).
- Wired into opencode at `~/.config/opencode/opencode.json` as
  `voice_desktop` — verified end-to-end: opencode invoked
  `voice_desktop_list_monitors`, the server returned the monitors JSON,
  the model formatted the answer.
- Added `tests/test_agent.py` (4 tests) and `tests/test_mcp_server.py`
  (5 tests). Total: 33 tests, all green; ruff + mypy clean (18 source files).
- Recorded in ADR-0011.

---

## 2026-05-15 — Modular refactor + dev tooling + assistant memory
- Split `voice_opencode.py` (876 lines) into 13 modules under
  `src/voice_opencode/`.
- Tray moved from a sibling script to `voice_opencode.tray`.
- New CLI in subcommand groups (`rec`, `session`, `tts`, `desktop`,
  `config`) with legacy aliases (`start`, `stop`, `voices`, `type`, …)
  preserved so Hyprland binds keep working.
- Added `pyproject.toml` (setuptools + ruff + mypy + pytest config).
- Added 24 unit tests; all green.
- Lint + type-check green (`ruff check`, `mypy`).
- Created `_ai/` knowledge base: ARCHITECTURE, DECISIONS, CHANGELOG,
  RUNBOOK, STATE, SKILLS/.
- Created `AGENTS.md` at the repo root as entry point for agentic tools.

## 2026-05-15 — Desktop control building blocks
- Installed `ydotool` (user systemd unit, no root). `/dev/uinput`
  already had ACL for `juan` thanks to `input` group + udev rule.
- Wired CLI commands `voice type|key|click|move|capture|focused`.
- These are the primitives the future MCP server will expose to opencode.

## 2026-05-15 — Tray icons rewritten with explicit fills
- Original SVGs used `currentColor` → invisible on dark DMS bar.
- Now: white mic for idle/paused, red filled circle for recording,
  amber dots for thinking, green speaker for speaking, red badge for error.

## 2026-05-15 — System tray (PyQt6 QSystemTrayIcon)
- Built `voice_tray.py` with QMenu, polls `voice state` every 1 s.
- Verified registration with `org.kde.StatusNotifierWatcher` →
  picked up by DankMaterialShell automatically.
- Added `~/.local/share/applications/voice-opencode.desktop`.
- Hyprland `exec-once` for tray autostart.

## 2026-05-15 — Pipeline state events + pause
- `STATE_FILE` carries the current phase (idle/recording/thinking/
  speaking/error). `PAUSE_FILE` sentinel disables F9 without touching
  the state machine.
- New CLI: `pause`, `resume`, `state`, `tray`.

## 2026-05-13 — Multi-voice support + screenshots in prompts
- `_voice_info()` reads the Piper sidecar `.json` to detect
  multi-speaker models and the right sample rate.
- `capture_screenshot()` shoots only the focused monitor by default.
- Passed inline as a base64 data URL to opencode `/session/<id>/message`.

## 2026-05-12 — First working pipeline
- arecord → whisper-cli → opencode HTTP → piper → paplay.
- Hyprland `bind`/`bindr` on F9.
- Persistent session via `$XDG_RUNTIME_DIR/voice-opencode/session.id`.
- 8 Spanish voices downloaded; settled on `es_AR-daniela-high`.
- `opencode serve` as `systemd --user` unit.
