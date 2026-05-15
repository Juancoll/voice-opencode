# Architecture Decision Records

Lightweight ADRs. Newest at the top. Each entry: Context → Decision →
Alternatives → Consequences. Date format: YYYY-MM-DD.

---

## ADR-0010 — Modular package layout under `src/`
**Date:** 2026-05-15

**Context.** The original `voice_opencode.py` grew to 876 lines mixing
config, state, audio, STT, TTS, opencode HTTP client, screenshots,
desktop control, pipeline orchestration, and CLI. Hard to read, harder
to test, impossible to import as a library.

**Decision.** Split into `src/voice_opencode/` with one responsibility
per module. Public surface matches the dependency layers documented in
`ARCHITECTURE.md`. `src/` layout (rather than flat `voice_opencode/`)
prevents accidental imports of the working dir.

**Alternatives.**
- Keep flat — rejected, doesn't scale.
- Microservices — overkill for a single-user tool.

**Consequences.** `voice_opencode.py` deleted (kept as `.legacy` until
git is initialised, then removed). Wrapper `voice` updated to set
`PYTHONPATH=src` and run `python -m voice_opencode`.

---

## ADR-0009 — CLI in subcommand groups, with legacy aliases
**Date:** 2026-05-15

**Context.** New layout suggests `voice rec start`, `voice desktop type`,
etc. But existing Hyprland binds use `voice start`, `voice stop`, etc.
Breaking them mid-refactor is rude.

**Decision.** Implement the new grouped CLI in `cli.COMMANDS`. Maintain
a `cli.LEGACY` table that maps the old flat names to the new ones.
Print a deprecation notice only when `VOICE_DEPRECATION_WARN=1` to avoid
log spam.

**Alternatives.**
- Hard break — rejected, would silently brick F9.
- Auto-rewrite the user's Hyprland config — too invasive.

**Consequences.** Both surfaces tested. Future work: once user updates
binds to grouped form, remove `LEGACY`.

---

## ADR-0008 — Per-state visual icons in the tray (white solid base)
**Date:** 2026-05-15

**Context.** First icons used `currentColor`, which DankMaterialShell
rendered black on a dark bar — invisible.

**Decision.** Switch all icons to explicit fills. Idle/paused = white
(visible against dark themes), recording = red, thinking = amber,
speaking = green, error = white mic + red badge. Paused = white mic +
red diagonal slash.

**Alternatives.**
- Use system theme `mic-symbolic` icons — rejected, can't differentiate
  pipeline phases.

---

## ADR-0007 — `wtype` + `ydotool` (user daemon, not root)
**Date:** 2026-05-15

**Context.** Phase 3 will let opencode drive the desktop (type, click).
Wayland blocks legacy synthetic input.

**Decision.**
- `wtype` for text and key combos (uses Wayland virtual_keyboard, no
  daemon, well-supported on Hyprland).
- `ydotool` for mouse motion + clicks. Run via the bundled
  `systemd --user` unit so the daemon owns the user's UID — no root
  required because `/dev/uinput` already has an ACL for the user
  (group `input` + udev ACL on this CachyOS box).

**Alternatives.**
- `ydotool` for everything: rejected, less ergonomic for keys.
- X11 + `xdotool`: not relevant in a Wayland session.

**Consequences.** New runtime dep on the `ydotool` user service.
Installer enables it.

---

## ADR-0006 — Dataclass-based Settings with frozen instance
**Date:** 2026-05-15

**Context.** Old config used module-level globals like `VOICE_NAME`
populated by `_cfg(...)` calls. Hard to test, no schema, mutating from
inside a process required teaching every reader.

**Decision.** Define `Settings` as a frozen dataclass. A single
`load()` resolves env > json > defaults. Public singleton `settings`.
Mutators (`set_value`, `reload`) rebind the singleton.

**Alternatives.**
- pydantic — extra dependency for two-dozen fields, not worth it.
- TOML — less round-trippable from a CLI than JSON.

---

## ADR-0005 — Tray polls `voice state` instead of importing the package
**Date:** 2026-05-14

**Context.** Tray could call into the package directly (cheaper). But
that means in-process imports of `requests`, `subprocess`, etc., and
makes the tray die when the package is reinstalled.

**Decision.** Tray shells out to the wrapper for both reads (`state`)
and writes (`rec toggle`, `config set`). One subprocess per tick is
trivial cost (~5 ms) vs. the safety of process isolation.

---

## ADR-0004 — opencode session id persisted in `STATE_DIR`
**Date:** 2026-05-13

**Context.** Each `voice` invocation is a new process. To keep
conversation context, the opencode session id must outlive the process.

**Decision.** Write to `$XDG_RUNTIME_DIR/voice-opencode/session.id`.
File is wiped automatically at reboot (XDG runtime semantics) and on
explicit `voice session reset`.

---

## ADR-0003 — opencode talks via `serve` HTTP, not embedded
**Date:** 2026-05-12

**Context.** Spawning `opencode` per call would lose context and incur
tool-load latency every time.

**Decision.** Run `opencode serve --port 4096` as a `systemd --user`
service. Talk to it via the HTTP `/session/<id>/message` endpoint with a
`requests.post`. Screenshots go inline as `data:image/png;base64,…`.

---

## ADR-0002 — whisper.cpp + piper as local backbone
**Date:** 2026-05-12

**Context.** Need offline STT and TTS, low latency, Spanish support.

**Decision.** `whisper.cpp` ggml-small (good Spanish, ~466 MB) for STT.
Piper voices in `voices/` for TTS, default `es_AR-daniela-high` (female,
22050 Hz). Both invoked as binaries; no Python bindings.

---

## ADR-0001 — Push-to-talk, not VAD or wake-word
**Date:** 2026-05-12

**Context.** Always-on listening is creepy and burns CPU.

**Decision.** Bind F9 in Hyprland: hold to record, release to send.
Implemented as `bind` (press) + `bindr` (release).
