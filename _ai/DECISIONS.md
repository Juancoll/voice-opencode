# Architecture Decision Records

Lightweight ADRs. Newest at the top. Each entry: Context → Decision →
Alternatives → Consequences. Date format: YYYY-MM-DD.

---

---

## ADR-0016 — Capacity hot-reload via MCP respawn, not in-process signal

Date: 2026-05-18

### Context

Phase J adds a tray menu that lets the user switch ``capacity_mode``
on the fly (Solo lectura / Asistir / Completo). The MCP server
filters tools at registration time (ADR-0014), so a live MCP
process keeps the old tool set even if ``config.settings.capacity_mode``
changes underneath it. Some mechanism is needed to make the
switch take effect "now".

### Decision

The tray runs ``voice config set capacity_mode <new>`` followed by
``voice mcp stop``. No signal-based in-process reload, no IPC.

opencode spawns the MCP server lazily as a stdio child the first
time a tool is needed in a session. After ``voice mcp stop``, the
next tool call in opencode triggers a fresh spawn that reads the
new config and registers the new tool set. The user sees the
change at the next ``/tools`` listing or the next tool invocation.

### Alternatives considered

- **SIGHUP-style reload inside the running MCP**: would require
  iterating ``mcp._tool_manager`` to unregister tools and rerun
  ``_register_*`` with the new gate. FastMCP doesn't expose
  ``unregister``; we'd have to reach into private state. Brittle
  against future FastMCP versions and easy to leak handlers.
- **Persist a "pending" mode and apply on next tool call**: trivial
  to write, but tools registered at startup don't disappear at
  call time — we'd have to re-add the call-time gate that ADR-0014
  argued against (it leaves the tool *visible* to the model even
  if it errors out, which is the whole problem).
- **Run MCP as a long-lived systemd user service we restart**:
  opencode owns the lifecycle today (stdio child); making it a
  separate service means a new socket transport, a new ADR
  to flip MCP transport, and breaks the "opencode launches it"
  story. Not worth the surface area for one menu action.

### Consequences

- Switching capacity_mode mid-conversation kills any in-flight
  MCP tool call (the model retries on its own — this is normal
  MCP error handling). Acceptable: capacity changes are rare
  and user-initiated.
- The new mode is reflected in the tray immediately (the menu
  reads from ``voice state`` every 1 s and the radio buttons
  update). The model only sees it on the next MCP spawn.
- No new IPC primitive, no extra dep. The pattern is the same
  one used for "Detener agente (MCP)" already in the tray.

---

## ADR-0015 — Apps backend: gtk-launch + manual .desktop parser; `apps_kill` is `full`

Date: 2026-05-18

### Context

Phase I exposes desktop apps to the model: list installed, list
running, launch, kill. Three sub-decisions had non-obvious choices.

### Decision

**1. `gtk-launch` is the primary launcher; raw `Exec=` is a fallback.**
`gtk-launch <app-id>` is the freedesktop-blessed way to launch a
``.desktop`` entry: it handles ``StartupNotify``, ``DBusActivatable``,
``%U``/``%F`` field codes, env propagation, and reparenting to PID 1
correctly. The cost is we never see the child pid directly — we
probe ``/proc`` for the binary's ``comm`` after a 250 ms grace
window. When ``gtk-launch`` is missing or returns non-zero, we
strip field codes from the ``Exec=`` line and ``Popen`` it
ourselves with ``start_new_session=True``; that path gives a real
pid but loses StartupNotify and any DBus activation semantics.

**2. Manual ``.desktop`` parsing instead of ``configparser``.**
Real-world ``.desktop`` files have duplicate keys for i18n
(``Name=Firefox``, ``Name[es]=Zorro de Fuego``, ``Name[ach]=…``).
``configparser`` in strict mode rejects duplicates; non-strict
silently overwrites with last-wins which gives us the wrong
locale at random. We only need 4 fields (``Name``, ``Exec``,
``Icon``, ``NoDisplay``, plus the ``Type`` gate), so a 25-line
manual parser that takes the *first* occurrence and skips ``[xx]``
variants is simpler and correct.

**3. ``apps_kill`` lives in the ``full`` tier, not ``assist``.**
The conservative reading of the Phase D contract (ADR-0014) is:
``assist`` = reversible / interactive; ``full`` = irreversible
without user effort. ``apps_kill`` sends SIGTERM, which to a
text editor or terminal session destroys unsaved state and there
is no undo. The model can request it, but only when the user has
opted into ``full`` mode. ``apps_launch`` stays in ``assist`` —
launching an app is reversible (close/kill it) and is exactly
the kind of operation a voice agent should help with.

### Alternatives considered

- **``xdg-open`` instead of ``gtk-launch``**: ``xdg-open`` is for
  URLs and MIME types, not app ids. It works for files
  (``xdg-open foo.pdf``) but you can't ``xdg-open firefox``.
  Wrong tool.
- **DBus activation via ``gdbus`` for ``DBusActivatable=true``
  apps**: more correct, but adds a dbus parsing step for every
  launch and only matters for a minority of apps. ``gtk-launch``
  already does this internally.
- **Put ``apps_kill`` in ``assist``**: would let `assist` mode
  accidentally close an editor by ambiguous voice command
  ("cierra firefox" intending ``close_window``, ending up at
  ``apps_kill firefox``). Keeping it in ``full`` forces a
  conscious capacity bump for irreversible process termination.
- **Match ``comm`` by ``argv[0]`` from ``/proc/<pid>/cmdline``
  instead of ``/proc/<pid>/comm``**: more robust (no 15-char
  truncation) but slower (one read per pid + null-separated
  parse). For our cadence (sub-second listing is fine),
  ``comm`` is enough.

### Consequences

- A launch via ``gtk-launch`` may return pid=0 if the probe
  window misses (very short-lived processes, or the binary's
  ``comm`` differs from the basename of ``Exec=``). Documented
  in the docstring; callers that need the pid for guaranteed
  follow-up should use the raw-command path.
- Apps with custom ``StartupWMClass`` (e.g. Electron apps that
  rename their main thread) won't match by ``comm``; their pid
  may not be findable via this backend. Future enhancement:
  also scan ``/proc/<pid>/cmdline`` as a second pass.
- ``apps_kill firefox`` in ``full`` mode SIGTERMs *every* running
  ``firefox`` pid — not just one window. This mirrors
  ``pkill firefox`` and is what the user almost certainly means
  when they say "kill firefox". If they want a single window,
  ``close_window <id>`` is the right tool.
- The 250 ms probe delay adds latency to every ``apps_launch``
  via ``gtk-launch``. Acceptable for voice cadence.

---

## ADR-0014 — Capacity modes: filter MCP tools at registration, not at invocation
**Date:** 2026-05-18

**Context.** Phase 3 wired 33 MCP tools that opencode can call without
the user being in the loop. Some are pure observation (``list_windows``,
``capture_screen``); some are reversible (``type_text``, ``focus_window``);
one is destructive (``close_window``); Phase E will add the worst
(``run_shell``). Different scenarios want different surfaces:

* **Pair-programming** — model should observe only, never touch the desktop.
* **Daily voice assistant** — the current default, can drive UI but not
  close apps from under the user.
* **Automation script** — the user delegates everything, including
  ``close_window`` (and eventually ``run_shell``).

Hard-coding the model's behaviour in the system prompt ("please don't
close windows unless asked") is the worst of all worlds: it's
unenforceable and it bloats the prompt. We need an enforced switch.

**Decision.**

1. **Three tiers, monotonic.** ``read-only`` ⊂ ``assist`` ⊂ ``full``.
   Default is ``assist`` (matches Phase 3 behaviour minus
   ``close_window``). Anything that isn't one of the three names falls
   back to ``assist`` with a warning (fail-open on the existing
   default, not surprise-locked).
2. **Filter at registration, not at invocation.** A tool that's not
   allowed under the current tier is **never registered** with FastMCP.
   The model can't see it, can't call it, can't even know it exists.
   The alternative (register everything, reject at call time) leaks
   the tool surface and invites prompt-injection attacks that say
   "ignore the policy".
3. **The mapping is the contract.** ``capacity.TIER_BY_TOOL`` is a
   flat dict, one line per tool. Adding a new MCP tool means adding
   it there in the same commit. Tools missing from the dict default
   to ``full`` (safe-by-default — a forgotten entry hides the tool
   from the restricted modes rather than silently exposing it).
4. **One combined gate.** ``mcp_server._expose(cap, tool_name)``
   returns ``plat.supported(cap) and capacity.allows(tool_name)``.
   The single helper means a consumer can't forget one half. The 25
   ``if plat.supported(...):`` guards became ``if _expose(...):`` with
   no other change.
5. **The tool→tier policy is conservative.** Destructive operations
   live in ``full`` only (``close_window`` today; ``run_shell``
   tomorrow). Synthetic input (``type_text``, ``click_mouse``) stays
   in ``assist`` because that's what makes assist useful — but the
   user can opt out by switching to ``read-only`` when working on
   something they don't want touched. We will revisit this if it bites.
6. **Read settings dynamically.** ``capacity.current_mode()`` reads
   ``config.settings.capacity_mode`` on every call (via
   ``from . import config``, not ``from .config import settings``),
   so ``config.reload()`` and test monkeypatches actually take effect.
   Caching the value on import was tried first and silently broke
   tests — a footgun worth a sentence in the ADR.
7. **Audit visibility.** The active tier and exposed-tool count are
   logged to ``logs/voice.log`` on every MCP server startup; the
   ``platform_info`` tool also returns ``capacity_mode`` so the model
   can describe its own bounds.

**Alternatives considered.**

* *One enable/disable flag per tool*. 33 booleans → tray menu hell and
  user can't reason about it. The three-tier abstraction is the right
  granularity for a voice assistant.
* *Per-call policy hooks* (let the user run a Python predicate per
  call). Powerful but over-engineered; nobody will write it.
* *Per-tool confirmation dialog*. Tried mentally: every ``focus_window``
  would interrupt the user. Confirmation is reserved for ``full``-tier
  destructive tools, and even then it's the model's job to call
  ``ask_confirm`` first (a soft contract documented in the tool
  description, not enforced).
* *Reject at invocation time* (register all, return ``"error: blocked
  by capacity mode"``). Tool surface leaks; invites the model to
  retry with a different framing; wastes the round-trip. Rejected.
* *Sandboxes / namespaces per tier*. Overkill — we're not running
  untrusted code, just deciding which capabilities to advertise.

**Consequences.**

* Switching modes requires restarting ``opencode serve`` (FastMCP
  registers tools once at startup). That's fine — mode changes are
  rare. The tray can show the current mode and "restart MCP" action
  in a future commit.
* ``platform_info`` now returns ``capacity_mode`` — a wire-format
  addition (additive, no breakage). AGENTS.md guidance on
  "anything that changes the wire format" doesn't strictly apply
  because nothing reads ``platform_info`` programmatically yet.
* CLI surface unchanged: ``voice config set capacity_mode full`` (and
  ``$VOICE_CAPACITY_MODE``) already worked thanks to the generic
  config CLI. No new commands.
* Adding Phase E's ``run_shell`` is now: implement the tool, add
  ``"run_shell": "full"`` to ``TIER_BY_TOOL``, done.
* New skill ``_ai/SKILLS/add-mcp-tool.md`` (Phase 3 had the rough
  recipe; Phase D forces us to formalise the tier-mapping step).

---

## ADR-0013 — Dialog backends: shared exit-code contract, kdialog preferred over zenity
**Date:** 2026-05-18

**Context.** Phase C had to land three blocking-dialog operations
(``confirm``, ``ask_text``, ``ask_choice``) plus a non-blocking
``notify``. We had two real candidates on Linux: ``kdialog`` (KDE/Qt)
and ``zenity`` (GNOME/GTK). Both ship the dialogs we want, both speak
exit codes, but they disagree on flags, on stdout format, and on how
they signal cancel. The MCP tools must also expose these primitives to
opencode in a way the model can reason about — Python ``None`` doesn't
survive a JSON-RPC boundary cleanly.

We also needed to stay consistent with the rest of the codebase:
``BackendError`` for "the backend itself broke" vs. an in-band value
for "the user said no / cancelled". Mixing those two is the path to
exception-driven control flow.

**Decision.**

1. **One shared exit-code contract for every ``DialogBackend``** (see
   ARCHITECTURE.md → Dialog backends). All current and future backends
   (rofi/wofi/yad/AppleScript/PowerShell-WPF/…) MUST map:
   * rc 0 → user confirmed → returns the answer (or ``True``).
   * rc 1 → user cancelled → returns ``None`` (or ``False`` for confirm).
   * rc other → ``BackendError`` with the subprocess stderr included.
2. **kdialog is the default; zenity is the fallback.** Detection order
   in ``platform/__init__.py`` is: try kdialog first; if its
   ``__init__`` raises ``BackendError("kdialog not installed")``, try
   zenity. We deliberately pick kdialog first because the host
   environment is KDE/Plasma-flavoured (DankMaterialShell) and kdialog
   themes match. The order is a single, locally-changeable line, not a
   protocol guarantee.
3. **Notifications are a separate Protocol.** ``NotifyBackend`` (with
   the single capability ``NOTIFY_SHOW``) is implemented by
   ``LibnotifyBackend`` and is desktop-agnostic. It is not part of
   ``DialogBackend`` because notifications are fire-and-forget and have
   no cancel/timeout semantics worth modelling.
4. **MCP tools translate the Pythonic return to strings.** The model
   sees ``"yes"`` / ``"no"`` for confirm and the answer or ``""`` for
   the others. We pass the user's text through unchanged (no
   sanitisation in the backend — that's the caller's job).
5. **Acting tools hold the agent lock.** ``ask_confirm`` / ``ask_user``
   / ``ask_choice`` wrap the call in ``with _acting():`` so F9
   push-to-talk is blocked while the user is staring at the dialog.
   ``notify`` does NOT take the lock; it's non-blocking.
6. **Dialog timeouts default to 300s.** They're interactive by nature;
   the rate limit and the lock are the real safety, not the timeout.

**Alternatives considered.**

* *Detect the DE and pick accordingly* (XDG_CURRENT_DESKTOP). Cute, but
  it adds a special case for every DE; the "try kdialog, fall back to
  zenity" rule covers 99% of installs and the user can override with
  ``VOICE_PLATFORM``.
* *Single in-process Qt dialog* (``QInputDialog`` from PyQt6). Pulls a
  Qt event loop into the CLI process; complicates the tray's lifecycle;
  doesn't work for the MCP server (separate process). Rejected.
* *Raise an exception on cancel* (``DialogCancelled``). Forces every
  caller into try/except for the *normal* path. Rejected — cancel is a
  value, not an error.
* *Different return shapes per backend*. Would put translation logic
  in every caller. Rejected — the Protocol is the contract.
* *Notify as a sub-method of DialogBackend*. Pollutes the capability
  set (every DialogBackend would have to claim ``NOTIFY_SHOW``).
  Rejected — orthogonal concerns get orthogonal Protocols.

**Consequences.**

* Adding a new dialog backend (rofi, wofi, yad, AppleScript) is a
  copy-paste of ``kdialog_backend.py`` with new argv and the same
  exit-code mapping. See ``_ai/SKILLS/build-dialog-backend.md``.
* The CLI grew ``voice dialog {notify|confirm|ask|choose}``. ``confirm``
  uses rc=0 for Yes and **rc=2 for No** (not 1, which is reserved for
  "backend error") so shells can distinguish.
* The MCP tool surface grew by 4 tools — ``notify``, ``ask_confirm``,
  ``ask_user``, ``ask_choice`` — all capability-guarded so they
  disappear cleanly on platforms without either backend.
* tests/test_backend_dialog.py mocks ``subprocess.run`` so the test
  suite never pops a real window. The smoke test ``./voice dialog
  notify ...`` is the live check.


**Date:** 2026-05-15

**Context.** Phase A (Hyprland window control) was about to land as a
top-level ``windows.py`` module that called ``hyprctl`` directly, with
``mcp_server.py`` importing it by name. The same was already true of
``desktop.py`` (wtype + ydotool + hyprctl) and ``screenshot.py`` (grim
+ hyprctl). Result: every consumer module had Hyprland and wlroots
baked into its imports. Porting to KDE/X11/Windows would mean
rewriting consumers, not adding adapters.

**Decision.** Insert a platform abstraction layer between consumers
and the OS:

* ``platform/base.py`` — Protocols (PEP 544) for ``WindowManager``,
  ``InputBackend``, ``ScreenBackend``, ``ClipboardBackend``,
  ``NotifyBackend``, ``DialogBackend``, ``AudioBackend``,
  ``MediaBackend``, ``AppLauncher``, ``ShellBackend``.
* ``platform/types.py`` — frozen dataclasses (``Window``, ``Workspace``,
  ``Monitor``, ``Rect``) shared by every backend.
* ``platform/capabilities.py`` — stable string constants for every
  abstract operation. Each backend declares which it supports via
  ``capabilities() -> frozenset[str]``.
* ``platform/__init__.py`` — auto-detect (env vars + ``sys.platform``),
  honour ``settings.platform_override``, expose lazy module-level
  singletons (``wm``, ``input``, ``screen`` …). Always returns a
  *something* — Null backends raise ``NotSupportedError`` rather than
  letting consumers crash on ``None``.
* ``backends/<os>_<system>/`` — concrete implementations
  (``linux_hyprland``, ``linux_wlroots``, ``linux_input``,
  ``linux_clipboard_wayland``, ``linux_dialog_kde``…). Stubs for
  ``macos_stub`` and ``windows_stub`` so the wiring compiles.
* ``mcp_server.py`` registers a tool **iff** ``platform.supported(cap)``
  returns True for its capability constant. The model never sees a
  tool that's guaranteed to fail on this host.

**Alternatives considered.**

* *Keep flat modules and add ``if`` branches.* Initially simpler;
  becomes a nightmare at four backends.
* *Subclass-based base classes (ABC).* More ceremony, no benefit over
  Protocols, harder for tests to inject fakes.
* *PyAutoGUI-style monolith.* Loses fidelity (no workspaces, no MPRIS,
  no per-tool capability filtering).

**Consequences.**

* Consumers (CLI, MCP server, tray, pipeline) import from
  ``voice_opencode.platform`` only — never from a backend directly.
  ``desktop.py`` and ``screenshot.py`` are now thin shims that delegate
  to the platform layer (kept to preserve external import paths).
* Adding a new platform = writing one or more backend classes that
  satisfy the Protocols + wiring them in ``_build()``. No consumer
  edits required.
* Capability strings are part of the public contract: renaming one is
  a breaking change for every backend.
* Tests now cover (a) detection logic, (b) each backend with mocked
  subprocess, (c) the MCP registration is capability-driven.
* Mypy file count went from 18 to 51; the ``Null*`` family adds noise
  but each is one-line per method.

---

## ADR-0011 — MCP server with per-call agent lock
**Date:** 2026-05-15

**Context.** Phase 3 needed opencode to drive the desktop. We had the
primitives (`desktop.py`) but no protocol. The Model Context Protocol
(MCP) is the de-facto way to expose tools to LLM clients, and opencode
supports local MCP servers via stdio out of the box.

Two questions had to be answered:

1. **One generic tool or many small ones?**
2. **When does the agent "have control" of the desktop?**

**Decision.**

1. **Many small tools** (`type_text`, `press_key`, `move_mouse`,
   `click_mouse`, `focused_window`, `capture_screen`, `list_monitors`,
   `sleep_ms`). The model autocompletes them better than a generic
   `do(action, args)`. Tool schemas come from Python type hints via
   `FastMCP` (`mcp.server.fastmcp`).

2. **Per-call lock** (`agent.acquire/release` wrapped in an `_acting()`
   context manager), not server-lifetime. Reasoning: opencode keeps the
   stdio MCP subprocess alive between user messages — if the lock were
   server-lifetime, F9 would be blocked all the time. Holding the lock
   only during *acting* tools (type/key/click/move) lets the user
   interleave voice prompts with agent action and keeps the tray icon
   honest about who's currently driving.

**Safety rails (mandatory).**

- Hard blocklist of dangerous combos (`ctrl+alt+backspace`,
  `ctrl+alt+f1..f12`, `alt+sysrq`, `ctrl+alt+delete`).
- Rate limit: 30 calls / 5 s per server instance, returned as an error
  string so the model can back off rather than crash.
- Audit log to `logs/agent.log` (JSON Lines) for every call.
- Read-only tools (`focused_window`, `capture_screen`, `list_monitors`)
  do not take the lock — they shouldn't disrupt the user.

**Alternatives.**

- HTTP/SSE transport — opencode's local config expects stdio; SSE adds
  complexity for no win on a same-host setup.
- Hold the lock for the whole server lifetime — rejected (see above).
- One mega-tool `do(...)` — rejected for tool-discovery reasons.
- Confirm-before-act prompt for every action — too noisy. Instead we
  rely on the rate limit, the kill-switch (`voice mcp stop` + tray
  menu item), and F9 being blocked during acting calls so the user can
  pull the plug fast.

**Consequences.**

- New runtime dep: `mcp` Python SDK (≥ 1.27).
- New module pair: `agent.py` (lock + audit) and `mcp_server.py` (tool
  registration + safety guards).
- New CLI subgroup `voice mcp serve|status|stop|log`.
- New state file `$XDG_RUNTIME_DIR/voice-opencode/agent` (presence
  means an MCP tool is currently acting).
- Tray gained an "agent: 🤖 activo" status line and a "Detener agente
  (MCP)" action.
- `pipeline.start_recording` now consults `agent.is_blocking()`
  (which combines pause + agent lock) instead of `state.is_paused()`.
- opencode global config (`~/.config/opencode/opencode.json`) registers
  the server as `voice_desktop` so tools surface as
  `voice_desktop_<tool>`.

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
