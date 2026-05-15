# AGENTS.md

> Entry point for AI agents (Claude, opencode, Codex, etc.) working on this repo.
> Humans: see [README.md](./README.md).

## What this repo is

A push-to-talk voice interface for [opencode](https://opencode.ai), running
fully local on Linux + Hyprland: whisper.cpp for STT, Piper for TTS,
PyQt6 system tray, optional desktop control via wtype + ydotool.

## Read these first

| File                                | Why                                                      |
|-------------------------------------|----------------------------------------------------------|
| [`_ai/ARCHITECTURE.md`](./_ai/ARCHITECTURE.md) | Module map and dependency rules                          |
| [`_ai/DECISIONS.md`](./_ai/DECISIONS.md)       | Why things are the way they are (ADRs)                   |
| [`_ai/STATE.md`](./_ai/STATE.md)               | What's installed and where (host-specific)               |
| [`_ai/RUNBOOK.md`](./_ai/RUNBOOK.md)           | How to operate / diagnose                                |
| [`_ai/CHANGELOG.md`](./_ai/CHANGELOG.md)       | Per-session log of what was done                         |
| [`_ai/SKILLS/`](./_ai/SKILLS/)                 | Recipes per recurring task (read the relevant one first) |

## Conventions

- **Language**: Python 3.11+. Type hints everywhere. `from __future__ import annotations`.
- **Layout**: `src/voice_opencode/` — see ARCHITECTURE for module roles.
  Modules form a strict layered graph; never import upward.
- **CLI**: `voice <group> <subcommand>`. Legacy flat aliases preserved
  in `cli.LEGACY`. Hyprland binds use the legacy names — do not break them.
- **Logging**: use `voice_opencode.logging.log()`, not `print()`.
- **Subprocess output**: stderr to a file in `logs/`, stdout captured.
- **Config**: read via `from .config import settings`. Never `os.environ` direct.
- **State**: pipeline phase via `state.set_state(...)` — values must be in `state.VALID_STATES`.
- **Tests**: pytest, `tests/`. No real audio/network. Use `monkeypatch` and
  `unittest.mock.patch`.

## Common commands

```bash
PYTHONPATH=src venv/bin/pytest          # run tests
venv/bin/ruff check src tests           # lint
venv/bin/mypy src/voice_opencode        # type check
./voice state | jq                      # what's the system doing right now
./install.sh                            # idempotent reinstall
```

## Workflow expectations

1. **Plan first.** For anything beyond a one-line change, lay out steps
   (a TodoWrite list, an ADR draft, or a comment) before editing.

2. **Update memory.** When you make a non-trivial change:
   - Append a one-paragraph entry to `_ai/CHANGELOG.md`.
   - If the change reverses or replaces an ADR, add a new ADR — don't
     edit the old one.
   - If the host environment changes (new package, new service), update
     `_ai/STATE.md`.

3. **Respect the layered import graph.** If you find yourself wanting
   to add an upward import, that's a smell — refactor instead.

4. **Don't break the legacy CLI.** Hyprland binds depend on
   `voice start`, `voice stop`, `voice toggle`, `voice reset`,
   `voice voices`, `voice say`, `voice ask`. The dispatcher in
   `cli.LEGACY` keeps them working; if you remove an alias, update the
   binds in `~/.config/hypr/conf.d/voice.conf` in the same change.

5. **Ask before destructive desktop actions.** ydotool gives the agent
   keyboard and mouse. When implementing or testing tools that click or
   type, default to dry-run unless the user has said go.

## Out of scope (for now)

- Cloud STT/TTS — explicit anti-goal, we want local.
- Wake-word / always-on — we chose push-to-talk on purpose (ADR-0001).
- Cross-distro packaging — Arch/CachyOS only until proven otherwise.

## Things you can change without asking

- Internal refactors that keep the public CLI and the tray menu identical.
- Adding tests, type hints, docstrings.
- Updating `_ai/` files to reflect reality.

## Things to ask first

- Adding a runtime dependency (Python or system).
- Changing the wire format of `voice state` (the tray polls it).
- Removing a CLI alias.
- Anything that runs as root or reaches outside the project dir.
