# Changelog (assistant view)

What I (the assistant) actually did, when, and why. Newest first.
This is intentionally more granular than `_ai/DECISIONS.md`.

---

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
