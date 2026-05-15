# Changelog (assistant view)

What I (the assistant) actually did, when, and why. Newest first.
This is intentionally more granular than `_ai/DECISIONS.md`.

---

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
- `git init` + initial commit.

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
