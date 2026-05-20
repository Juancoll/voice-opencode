# Architecture Decision Records

Lightweight ADRs. Newest at the top. Each entry: Context → Decision →
Alternatives → Consequences. Date format: YYYY-MM-DD.

---

---

## ADR-0030 — Streaming LLM replies via SSE (revert ADR-0028)

Date: 2026-05-20

### Context

With the non-streaming ``ask()`` path the user sat in front of a
static "🧠 Pensando…" HUD for 19–74 seconds per turn (90–95% of the
wall-clock of an average turn). They had no signal that the model
was alive, much less *what* it was about to say. opencode's HTTP
API already exposes a Server-Sent-Events feed at ``GET /event`` that
emits ``message.part.delta`` events as the model generates — we just
weren't consuming it.

A separate user-visible bug emerged during the iteration: ADR-0028
parked the HUD off-screen with ``hyprctl movewindowpixel`` while
``grim`` ran, to keep "Pensando…" out of the screenshot. With the
new streaming code the first delta now arrives 5+ seconds after the
screenshot, which means the HUD stayed at (-99999, -99999) for the
whole gap — the user perceived this as "the agent closes the window
mid-turn".

### Decision

1. Add ``ask_stream(prompt, screenshot) -> Iterator[str]`` to the
   ``LLMBackend`` Protocol from ADR-0029. ``ask()`` becomes a
   ``"".join(ask_stream(...)).strip()`` wrapper.

2. ``OpencodeBackend.ask_stream``:
   * opens ``GET /event`` **before** posting the message, so no
     opening deltas are missed;
   * POSTs on a background thread (the SSE iterator drives the
     generator on the foreground thread);
   * filters events by ``properties.sessionID`` (when present) and
     yields only ``message.part.delta`` events with ``field=="text"``;
   * stops on ``session.idle`` or ``session.error`` for our session;
   * ignores ``message.part.updated`` snapshots — they're cumulative
     and would double-emit text already seen as deltas;
   * on SSE-connect or POST failure calls ``self.abort()`` first,
     then re-raises (same contract as non-streaming ``ask``).

3. ``pipeline.stop_and_run`` iterates the stream, accumulates the
   reply in a list, and calls ``turn_update("🧠 Pensando…", tail)``
   every ~80ms with the last 80 chars of the partial reply. TTS sees
   the same fully-joined reply it always did — no change to the
   speak path in this phase.

4. **Revert ADR-0028.** Delete ``screenshot._hud_offscreen``,
   ``_hyprctl_dispatch``, ``_HUD_PARK_*`` constants and the
   ``relocate`` op on the HUD socket. Accept that "🧠 Pensando…"
   shows up in the screenshot; the model occasionally references it
   but that's a softer failure than a HUD flicker the user reads as
   a crash.

### Alternatives considered

* **Token-by-token output via the OpenAI streaming endpoint.** Would
  give us a smoother HUD scroll, but bypasses opencode's session
  model (tools, MCP servers, screenshots-as-parts) which we depend
  on. Rejected.

* **Polling ``GET /session/.../message`` for partial state.** Simpler
  client code, but the API only commits *complete* parts, so this
  is effectively non-streaming. Rejected.

* **Keep the parking helper, send a "relocate" op on context exit.**
  Tried and discarded: the user reported the resulting flicker
  (move-off, screenshot, move-back) was worse than just accepting
  the HUD pixels in the shot.

### Consequences

* HUD updates while the model talks; first visible motion is bounded
  by how soon the provider emits its first delta (~5s for
  claude-opus-4.7 via copilot in our measurements).

* TTS still waits for the *full* reply — Phase 2 (streamed TTS by
  sentence) is a follow-up ADR, not in scope here.

* Chunk granularity is provider-dependent. claude-opus-4.7 ships
  whole sentences per delta; other providers (raw Anthropic, OpenAI)
  will produce many small deltas. The HUD throttle (80ms) hides the
  difference.

* F9-cancel mid-turn still works: ``abort()`` aborts the in-flight
  POST and the SSE iterator unblocks at the next event, then the
  ``finally`` closes the connection.

* Diagnostics: ``logs/voice.log`` gains ``SSE: first delta after N
  events``, ``SSE: session.idle received, closing stream``, ``SSE:
  stream closed (events=X, deltas=Y)``, and ``Stream finished: N
  deltas, M HUD updates, reply=X chars``.

* Tests: 7 new in ``tests/test_llm_stream.py`` (delta order, session
  filter, non-text field filter, stop-on-idle, ask-as-wrapper,
  abort-on-SSE-error, abort-on-POST-error) plus
  ``test_stop_and_run_streams_partial_text_to_hud`` in the pipeline
  suite. ADR-0028's screenshot-parking tests deleted (3 of them).

* Live verification: a "1 al 5" prompt against the real
  ``opencode serve`` returned 2 deltas over 6.75s wall-clock (first
  delta @ 5.58s), full reply reconstructed correctly.

---

## ADR-0029 — LLM backend abstraction (Protocol + factory)

Date: 2026-05-20

### Context

The pipeline was hard-wired to ``opencode serve``: ``pipeline.py`` and
``cli.py`` both did ``from .opencode_client import Session`` and
called HTTP-specific methods directly. The user asked whether the
agent could be swapped for Claude Code, Hermes, Ollama, etc. — a
reasonable ask: opencode is one of many local agent frontends, and
locking the voice loop to a single vendor defeats the "fully local,
your-choice-of-model" promise.

There are two natural shapes for this kind of swap:

* **Subclass / factory** — define an abstract base class, ship one
  subclass per backend, pick at runtime.
* **Protocol + factory** — define a structural type (PEP 544), let
  each backend be a plain class with the right methods, pick at
  runtime.

The second is lighter — no ``ABC`` inheritance noise, easier to
satisfy from an existing class, plays well with ``isinstance``
checks thanks to ``@runtime_checkable``.

### Decision

Introduce ``voice_opencode.llm`` with:

* ``LLMBackend`` (``@runtime_checkable Protocol``): contract every
  backend must satisfy — ``name``, ``health()``, ``session_id()``,
  ``ensure_session()``, ``ask(prompt, screenshot)``, ``abort()``,
  ``forget()``.
* ``get_backend()`` — module-level singleton factory. Reads
  ``settings.llm_backend`` once, instantiates the matching adapter,
  caches it. Unknown name → ``RuntimeError`` (fail-loud, never
  silently fall back).
* ``reset_backend_cache()`` — drops the singleton; wired into
  ``config.reload()`` so a config change re-resolves on next
  ``get_backend()`` call.

Selection is **config-only** (``VOICE_LLM_BACKEND`` env var or
``llm_backend`` in ``config.json``). No CLI command swaps backends at
runtime: a turn that started against backend A and finished against
backend B would be a debugging nightmare.

Move the existing opencode HTTP logic into ``OpencodeBackend`` (new
class in ``opencode_client.py``) implementing the Protocol. The old
``Session`` class is kept as a thin compatibility shim — it now
delegates to ``OpencodeBackend`` — so any external scripts importing
it continue to work, but new code uses ``get_backend()``.

``pipeline.py`` and ``cli.py`` lose all direct references to
``Session``/``health`` and go through ``get_backend()``. Error
messages now interpolate ``backend.name`` (e.g. ``❌ opencode``
becomes dynamic), so swapping in a different backend shows the right
label in the HUD without code changes.

Screenshot handling is **per-backend**: ``ask()`` accepts a
``Path | None``; each implementation decides whether to inline as a
data URL (opencode), upload, or ignore. We deliberately do *not*
expose a ``supports_vision`` flag — the screenshot is captured
unconditionally because (a) ADR-0028 already made capture cheap
(local grim), and (b) a backend that ignores it costs nothing.

### Alternatives considered

* **Plugin discovery via entry points.** Overkill for an in-tree
  refactor; would only matter if backends shipped as separate
  packages. Rejected for now.
* **Per-turn backend selection.** Lets the user say "ask claude
  about this one". Rejected — state of the conversation is
  per-backend, so mixing creates broken sessions. Revisit only if a
  concrete need emerges.
* **Drop ``Session`` shim.** Cleanest API but breaks any external
  caller (notably hand-written debug scripts the user mentioned).
  Cheaper to keep the shim and mark it legacy in the docstring.
* **Abstract base class.** Forces every backend to inherit from a
  voice-opencode type, complicating reuse if someone wants to wrap
  an existing client. Protocol is strictly more flexible.

### Consequences

* +1 module (``llm.py``), +1 class (``OpencodeBackend``), refactor of
  ``pipeline.py`` + ``cli.py``. Net diff ~200 lines.
* +10 tests in ``tests/test_llm_backend.py`` covering the Protocol
  contract, factory caching, unknown-name failure, env-var override,
  ``config.reload()`` cache invalidation, and the opencode adapter's
  session-id + abort paths.
* ``conftest.tmp_state`` now reloads ``opencode_client`` and ``llm``
  alongside ``paths`` so test isolation extends to the backend
  singleton. Without this, ``isinstance(b, OpencodeBackend)`` can
  fail under parallel-file runs because reload creates a new class
  object.
* Adding a new backend = one new file + one ``elif`` in
  ``get_backend()``. No pipeline changes needed.
* The HUD error label is now backend-dependent (``❌ opencode``,
  ``❌ claude_code``, …). User-visible but consistent with logs.
* Performance: zero. The factory caches the instance; ``ask()`` is
  the same HTTP call it was before.

---

## ADR-0028 — Screenshot captures the monitor of the active *window*, and hides the HUD during grim

Date: 2026-05-20

### Context

User report, immediately after ADR-0027 landed: "el agente me acaba
de describir una pantalla que no se ve". The model was confidently
narrating UI from a monitor the user was not looking at, on a
dual-head Hyprland setup (DP-3 + DP-4, 2560×1440 each).

Two distinct bugs were stacked on top of each other:

1. **Wrong monitor.** ``WlrootsScreenBackend.capture_monitor()`` with
   no explicit monitor argument fell back to ``focused_monitor()``,
   which reads the ``focused`` flag from ``hyprctl monitors``. That
   flag follows the **cursor / last focus**, not the active window.
   A common trigger: the cursor drifts to the other monitor (or any
   floating pinned overlay — like our brand-new HUD from ADR-0026 —
   briefly touches it), so grim grabs the wrong head and the model
   hallucinates whatever is on the unfocused screen.
2. **HUD bleed-through.** Even when grim picks the right monitor,
   the HUD is a floating, pinned, always-on-top widget present
   throughout ``thinking`` and ``speaking``. The PNG sent to the
   model includes "🧠 Pensando…" / "🔊 Respondiendo" pixels. The
   model OCRs them, either parrots them back or treats them as part
   of the user's context. We just spent an ADR putting the HUD on
   top of everything; now it's contaminating the very signal it was
   supposed to support.

### Decision

Two narrow, independent fixes:

**1. Resolve the capture monitor from the active window, not the
cursor.** New private helper
``WlrootsScreenBackend._active_window_monitor_name()`` reads
``hyprctl -j activewindow``, looks up the numeric ``monitor`` id,
and cross-references ``hyprctl -j monitors`` to get the name grim
expects. ``capture_monitor(monitor=None)`` calls it first and falls
back to the previous ``focused_monitor()`` behaviour only when no
active window can be resolved (e.g. nothing is focused). An
explicit ``monitor=`` argument still bypasses both probes.

**2. Park the HUD off-screen for the duration of grim.** New
``screenshot._hud_offscreen()`` context manager dispatches
``hyprctl movewindowpixel exact -99999 -99999, title:voice-opencode-hud``
before yielding, sleeps 30ms so Hyprland commits the move before grim
sweeps the framebuffer, and yields. We deliberately do **not**
restore the HUD's position on exit: the pipeline always calls
``turn_update(...)`` immediately after ``capture()`` (either
"🔊 Respondiendo" on success or "❌ opencode" on failure), and every
``turn_update`` re-runs ``TurnHUD._apply_hyprland_rules`` which
re-pins the widget into the bottom-right of the active monitor.
``capture()`` wraps the existing ``capture_to(...)`` call in that
context manager.

Both fixes are best-effort. On X11 / other compositors without
``hyprctl`` they degrade to no-ops: the resolve helper returns
``None`` (so we fall back to focused_monitor), and the HUD parker
silently skips the dispatch (HUD may appear in the shot, same as
before this ADR existed).

### Alternatives

* **scope="window" by default.** Captures the focused window only,
  no monitor context. Considered and offered to the user; rejected
  because they want to keep peripheral context (terminal output,
  open browser tab next to the editor, etc.).
* **scope="all" by default.** Stitched 5120×1440 PNG, ~1.5 MB.
  Never wrong, always slow, and the model has to figure out which
  half matters. Worst tradeoff.
* **Disable screenshots entirely.** Already a config flag
  (``settings.screenshot = False``); didn't want to be the default
  because text-only voice loses a lot of "look at this and tell me
  what to do" use cases.
* **Have the tray hide the HUD via a socket op.** Adds a synchronous
  round-trip (pipeline → tray → Qt event loop → tray → pipeline)
  per capture. ``hyprctl dispatch`` is fire-and-forget and just as
  effective. We accept that the HUD is briefly invisible during the
  grim call as a feature, not a bug.
* **Compute current HUD geometry, ``hyprctl getwindows``, mask the
  region in post-processing.** Way too much complexity for an issue
  a 30ms sleep + offscreen move solves.

### Consequences

* `capture()` is now ~30ms slower in steady state (the deliberate
  ``time.sleep(0.03)`` to let Hyprland commit the move). Negligible
  against the 25s opencode round-trip that triggered this report.
* If the pipeline somehow dies between ``capture()`` and the next
  ``turn_update`` (which would be a separate bug), the HUD stays
  off-screen until the next ``turn_start``. Acceptable — the HUD is
  non-essential UX and the next F9 brings it back.
* New file ``tests/test_screen_active_monitor.py``: 8 tests covering
  the active-window resolution (DP-4 wins when activewindow.monitor=1
  even if DP-3 is "focused"), the focused fallback when nothing is
  focused, explicit ``monitor=`` bypassing both probes, the
  defensive ``None`` when ``activewindow`` lacks a ``monitor`` key,
  the HUD parker dispatching once with negative coords, the silent
  no-op when ``hyprctl`` is missing, ``capture()`` strictly running
  grim inside the parker context, and the early-return when
  ``settings.screenshot`` is False.
* The MCP ``capture_screen`` tool and the CLI ``voice desktop
  capture`` and ``voice ocr find`` also benefit transparently from
  the active-window resolution — they all funnel through
  ``capture_monitor``.

---

## ADR-0027 — F9-while-busy cancels the active turn (no more "Ocupado" toast)

Date: 2026-05-19

### Context

After ADR-0026 landed the per-turn HUD, the user reported two related
annoyances on the very next live test:

1. Pressing F9 while a turn is already running (`thinking` or
   `speaking`) did nothing useful — it only stacked low-urgency
   "⏳ Ocupado / Esperá a que termine el turno actual" libnotify
   toasts on top of the HUD, one per F9 press. Three quick presses
   produced three notifications.
2. The intuitive expectation when the user hits F9 mid-reply is
   "stop, I changed my mind", not "remind me politely that I have
   to wait". A runaway tool loop (the same kind ADR-bd363b2 patched
   for HTTP timeouts) couldn't be killed from the keyboard.

The pipeline is multi-process (Hyprland dispatches each F9 bind as a
fresh ``voice`` process), so any "cancel" mechanism has to signal
across processes.

### Decision

Reinterpret F9-while-busy as **cancel the running turn** when the
pipeline state is in `{thinking, speaking}`. Specifically, the
opening branch of ``pipeline.stop_and_run`` now:

1. Reads the lockfile to find the holder PID + age.
2. If the holder is live + fresh AND ``state.get_state()`` is in
   ``_CANCELLABLE_STATES``, calls ``_cancel_active_turn(holder_pid)``
   and returns.
3. Otherwise proceeds into ``_pipeline_lock`` as before (which
   silently drops on contention).

``_cancel_active_turn`` does, in order:

1. ``Session(sid).abort()`` so the opencode server stops the runaway
   tool loop (otherwise killing the local process leaves the model
   spinning server-side and the next ``ask()`` blocks).
2. ``os.kill(holder_pid, signal.SIGINT)`` — **SIGINT, not SIGTERM**,
   because SIGINT raises ``KeyboardInterrupt`` in pure-Python blocking
   calls (``time.sleep``, ``subprocess.wait``, ``requests``) which
   lets the holder's ``_pipeline_lock`` ``finally`` clause run and
   unlink the lockfile cleanly. SIGTERM would bypass ``finally`` and
   leave the lock for the TTL stealer.
3. Best-effort ``pkill -x paplay`` in case the holder was mid-TTS;
   ``Popen`` defaults don't share a signal mask between the python
   parent and the audio subprocess.
4. ``set_state("idle")`` + HUD ``turn_update("🛑 Cancelado")`` +
   ``turn_end()`` from the **canceller** process. The HUD socket
   lives in the tray and is reachable from any process, so we can
   render cancellation before the dying holder has a chance to.

Also part of this ADR: the libnotify ``notify(...)`` toasts that
``pipeline.stop_and_run`` emitted on every error path (`❌ Error STT`,
`❌ opencode`, `❌ Error TTS`, `🤷 Nada que transcribir`, `🤐 Sin
respuesta`) are removed. The HUD now owns 100% of the per-turn user
feedback, which is the entire point of ADR-0026. Duplicate toasts on
top of the HUD are exactly what we are trying to stop. ``notify(...)``
remains in use for the "agent active / paused" pre-flight toasts in
``start_recording`` (those are not part of a turn lifecycle).

### Alternatives

* **Cancel flag file polled by the holder.** Requires the holder to
  insert a poll in every blocking call; ``requests.post`` doesn't
  cooperate. Cross-process signal is simpler.
* **SIGTERM the holder.** Skips ``finally``; leaves a stale lockfile
  every time, forcing the next turn to wait for TTL or steal.
* **Keep the "Ocupado" toast, just use replace-id.** Doesn't address
  the user's real complaint, which is that the toast is the wrong
  *action*, not the wrong *count*.
* **Make F9-while-busy enqueue the next utterance.** Too clever for
  push-to-talk; the user wanted a brake pedal, not a queue.

### Consequences

* F9 during a runaway tool loop now actually stops it (combined
  with `bd363b2`'s HTTP timeout abort, the pipeline has two
  independent cancellation paths: timeout and human).
* The shared ``logs/voice.log`` will get a single "F9 mid-turn:
  cancelling holder pid=… age=… state=…" line per cancellation,
  which is easy to grep when diagnosing what the user did.
* ``_pipeline_lock`` keeps the ``notify_on_busy`` knob for backward
  compatibility (one call-site, tests), but the default is now False
  and ``stop_and_run`` no longer passes True.
* Lock-during-recording (`state == "recording"`) is still a silent
  drop — pressing F9 again before the first audio chunk lands is
  almost always a Hyprland key-repeat artefact, not an intent to
  cancel.
* Risk: SIGINT to the holder propagates KeyboardInterrupt up its
  call stack; if a caller catches `BaseException` it could swallow
  it. Audited the pipeline path — only `Exception` is caught, so
  KeyboardInterrupt always escapes to the ``finally`` clause.

---

## ADR-0026 — Per-turn HUD as an in-process PyQt6 widget (replaces libnotify replace-id)

Date: 2026-05-19

### Context

ADR-0025 introduced a persistent per-turn notification driven by
``notify-send -p -r <id>`` + ``gdbus CloseNotification``. In production
on Plasma 6 / KDE the daemon **silently ignores ``-r <id>`` once the
bubble has auto-expired** (or once the daemon's internal id table has
rotated). The user keeps the previous title forever, ``CloseNotification``
no-ops, and the next ``turn_start`` opens a *new* bubble alongside the
stale one — exactly the bug the ADR was meant to prevent. We also
have zero control over position, font size, dismiss timing and TTL,
because libnotify is fire-and-forget over D-Bus.

We need:

* In-place updates we can trust (every ``turn_update`` always lands
  in the same widget; nothing rotates underneath us).
* Explicit lifecycle: open at recording, update per phase / per tool,
  close on every exit including error paths.
* Consistent look across KDE / GNOME / Hyprland / Sway.
* No new system dependencies beyond what the tray already pulls in.

### Decision

Replace the libnotify persistent bubble with a **TurnHUD QWidget**
hosted in the tray process:

* ``src/voice_opencode/hud.py`` exposes ``TurnHUD`` (frameless,
  ``WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus |
  BypassWindowManagerHint``, translucent rounded panel with icon
  column + title + subtitle, fade in/out 180ms, positioned in the
  bottom-right of the screen under the cursor) and ``HudServer``
  (Unix-socket listener bound to
  ``$XDG_RUNTIME_DIR/voice-opencode/hud.sock``, mode 0600).

* The pipeline + the MCP server are *clients* of the socket. They
  call ``notify.turn_start`` / ``turn_update`` / ``turn_end`` which
  serialise one JSON object per line (``{"op":"show|update|hide",
  "icon":"🎙","title":"Grabando…","subtitle":"…"}``) and connect with
  a 50ms timeout. If the socket isn't there (tray off), the call
  silently no-ops — the pipeline never blocks on the HUD.

* The libnotify backend keeps ``show(title, body, urgency)`` for
  one-shot toasts (errors, "Ocupado", "Sin audio"). ``show_persistent``
  and ``dismiss`` are removed; the capability ``NOTIFY_REPLACE`` is
  retired.

### Alternatives

* **Keep libnotify, switch to ``mako`` / ``dunst``.** Fixes the bug
  for the maintainer but not for users on KDE / GNOME defaults; we
  can't ship a notification daemon as a dep.
* **Talk to the D-Bus Notifications interface directly.** Same
  daemon, same bug, just without the ``notify-send`` wrapper.
* **GTK4 + gtk4-layer-shell.** Better placement on KDE/Sway/Hyprland
  via layer-shell, but mixes a second GUI toolkit in the same
  process, doesn't help on GNOME-Wayland (no layer-shell), and adds
  ``gtk4`` + ``gobject-introspection`` system deps.
* **Backend-per-DE (Qt on KDE, GTK on GNOME, AppKit on macOS).**
  Four times the code and bug surface for marginal cosmetic gain.
  PyQt6 already abstracts the platform well enough.
* **Spawn a fresh QApplication per turn.** 200–400ms cold start
  visible on every F9; no.

### Consequences

* Linux-first; macOS/Windows get the toasts via libnotify equivalents
  in Phase B (HUD will be a separate backend then).
* The HUD only lives while the tray runs. If the tray is killed mid-
  turn the bubble disappears but the pipeline keeps working (best-
  effort contract); ``turn_end`` calls become silent no-ops.
* The pipeline + MCP server now have a *cross-process* dependency
  on the tray for visual feedback, but it's failure-soft: a missing
  socket only loses the HUD, not the turn itself. Tests cover the
  "socket missing" branch.
* ADR-0025 is **superseded for the visual feedback part** (focus
  guard, MCP audit wrapper and the ``turn_start`` / ``turn_update``
  / ``turn_end`` API stay; only the libnotify backing changes).

---

## ADR-0025 — Input-injection safety: focus guard + live per-turn notification

Date: 2026-05-19

### Context

The MCP server exposes input-injection tools (`type_text`, `press_key`,
`click_mouse`) backed by `ydotool` on Hyprland. `ydotool` writes to a
shared `/dev/uinput` virtual device — it does NOT have any concept of
target window. Whatever has keyboard focus at the moment the keystroke
is dispatched receives it.

Two failure modes observed in the wild:

1. The agent decides to "type the answer in the chat" while the user
   has already switched to a terminal. The text is injected into the
   terminal and, on a bad day, executes.
2. The user has no idea what the agent is doing in the middle of a
   long turn. Toast notifications stack up (one per tool call), are
   read in the wrong order, or auto-dismiss before being read.

We need (a) a hard guard that refuses to inject input unless the
window the agent thinks it's typing into is still the one with focus,
and (b) a single, persistent on-screen indicator that always reflects
*what* the agent is doing right now.

### Decision

**Focus guard (combined check).** Every input-injection MCP tool runs
through `_focus_guard(tool_name)` before doing any work. The guard
refuses unless **both** conditions hold:

1. A successful `focus_window(...)` MCP call happened within
   `_FOCUS_GUARD_TTL_S = 5.0` seconds. The guard remembers the
   `target` string and the resolved backend window id in
   `_focus_state` (module-level dict).
2. The platform's current `wm.active_window().id` equals the id
   remembered from that `focus_window` call.

If the WM backend doesn't expose `WM_ACTIVE_WINDOW` (e.g.
`linux-generic` fallback), condition (2) is skipped and the guard
trusts the timestamp alone (best-effort, logged as degraded).

On refusal the tool returns a string starting with `refused:` that
names the missing focus target and the actual one, so the model can
recover by calling `focus_window` and retrying. No exception is raised
— refusals are normal control flow.

**Live per-turn notification.** The pipeline owns one persistent
notification per turn:

- `turn_start(title, body)` is called when recording begins. It opens
  a notification via `notify-send -p -t 86400000` (urgency=critical,
  parses the returned id) and writes the id to
  `$XDG_RUNTIME_DIR/voice-opencode/turn.notify-id`.
- `turn_update(title, body)` re-uses the same id via `notify-send -r
  <id>` so the bubble updates in place instead of stacking. Called by
  the pipeline at each phase (Transcribiendo / Pensando / Respondiendo)
  AND by the MCP server's `_audit(...)` wrapper on every successful
  acting tool, with a one-line `tool(args)` label truncated to ≤80
  chars by `_fmt_action`.
- `turn_end()` dismisses the notification via
  `org.freedesktop.Notifications.CloseNotification` over `gdbus` and
  removes the id file. Called from every pipeline exit path
  (success / error / empty transcript).

The file-based id is the IPC handle: the pipeline (one process) and
the MCP server (a separate process started by `opencode serve`) both
read/write `turn.notify-id` to update the same bubble. No socket, no
DBus signal — a 4-byte text file is enough because there is at most
one turn in flight.

**Audit hook.** A new `_audit(tool, args, result="ok")` wrapper in
`mcp_server.py` replaces direct `agent.audit(...)` calls at every
acting-tool site (62 call sites). It (i) writes the JSONL audit entry
as before and (ii) calls `turn_update("⚙️ Agente actuando",
_fmt_action(tool, args))` on `result == "ok"`. Non-ok results
(refusals, rate-limit hits) are audited but not surfaced as
notification updates — the existing notification text stays so the
user sees the *current* action, not the last refused one.

**Capability flag.** New `NOTIFY_REPLACE = "notify.replace"` advertises
the persistent-bubble feature. `LibnotifyBackend` advertises it
(parses `notify-send -p` stdout); `NullNotifyBackend` does not. The
`turn_*` helpers degrade gracefully: if `NOTIFY_REPLACE` is absent,
`turn_start` falls back to a plain `notify(...)` with no replace
contract (bubbles will stack on such systems).

### Alternatives considered

- **Exclusive uinput grab.** Would let the agent type "into nothing"
  even when the user is at the keyboard. Requires CAP_SYS_ADMIN-level
  privileges on `/dev/uinput` and breaks regular typing system-wide.
  Rejected.
- **Tray widget instead of notifications.** A persistent line in the
  PyQt6 tray menu would survive any notification daemon. Rejected for
  now because the tray menu is not visible unless the user clicks the
  icon; a toast in the corner of the screen is the right UX for "the
  agent is doing X right now". Reconsider if multiple notification
  daemons turn out to handle `--replace-id` differently.
- **Per-tool toasts (no persistent bubble).** Rejected: tested live,
  stacks of 5+ toasts during a single MCP turn, ordering is daemon-
  dependent, important steps scroll off.
- **D-Bus method calls instead of `notify-send`.** Equivalent semantics
  with one extra dep (`pydbus` or raw `dbus-next`). Deferred — the
  CLI invocation works, is debuggable from a terminal, and matches
  the rest of the platform layer's shell-out style.
- **Focus guard with only timestamp OR only id-match.** Each alone has
  a hole (timestamp-only: user can grab focus within the TTL;
  id-only: stale snapshot of the wrong window passes). The combined
  check is what the user requested and it closes both.

### Consequences

- Any new acting tool added to the MCP surface MUST call `_audit(...)`
  instead of `agent.audit(...)` to participate in the live indicator.
  Enforced by review only (no static check yet).
- Any new acting tool that injects input MUST go through
  `_focus_guard("name")`. A unit test must cover refusal when no
  focus is recorded.
- Tools that don't inject input (screen capture, file reads, sleep,
  apps_list_installed, …) are not guarded — they're safe regardless
  of focus.
- The pipeline now depends on `STATE_DIR` being writable. Already true
  on every supported platform (`$XDG_RUNTIME_DIR/voice-opencode`).
- Users on a notification daemon that ignores `--replace-id` will see
  stacked bubbles. Mitigated by the capability flag fallback. Verified
  working on the user's setup (KDE/Plasma StatusNotifierHost on
  Hyprland).

---

## ADR-0024 — Windows as second platform: scope and deferral plan (Phase C)

Date: 2026-05-19

### Context

Phase A (ADR-0023) extracted every voice-pipeline subsystem behind
`platform/` Protocols so the recorder, player, TTS, STT, and log
viewer can each be swapped for an OS-native implementation without
touching consumer code. Phase B (ADR-0022) generalised `install.sh`
to pacman/apt/dnf with vendored upstream binaries on distros that
don't package whisper.cpp and piper-tts.

This ADR covers Phase C — bringing Windows up to second-platform
parity. The work has *not* been started; this document captures the
plan so it can be implemented in one focused session on the Windows
machine without re-deriving the architecture.

The user has a Windows box and wants `voice-opencode` working there
with the same CLI, the same MCP surface, and the same tray. The
Phase A/B architecture was specifically built to make this a wiring
job, not a port.

### Decision

Implement Windows as a third platform under `backends/windows_*`
following the same Protocol/wiring discipline as Linux. Defer the
actual code to a separate session run *on the Windows machine* (the
opencode CLI keeps repo state synchronised; the dev workflow will be:
push from Linux, pull on Windows, work in Windows opencode, push,
pull back on Linux to review).

**In scope for Phase C:**

| Subsystem      | Linux today                       | Windows plan                                   |
|----------------|-----------------------------------|------------------------------------------------|
| `recorder`     | `linux_audio_arecord`             | `windows_audio_wasapi` (PyAudio or sounddevice — 16 kHz mono S16) |
| `player`       | `linux_audio_paplay`              | `windows_audio_wasapi` (winsound.PlaySound or sounddevice playback) |
| `tts`          | `common_piper` (works as-is)      | reused — only `PIPER_BIN` points at `piper.exe` |
| `stt`          | `common_whisper_cpp` (works as-is)| reused — only `WHISPER_BIN` points at `whisper-cli.exe` |
| `notify`       | libnotify via `notify-send`       | `windows_notify_winrt` (Toast via WinRT) or fall back to taskbar balloon |
| `screen`       | `wlroots`/`grim` or `scrot`       | `windows_screen_mss` (mss is cross-platform; trivially zero-dep) |
| `clipboard`    | wl-clipboard / xclip              | `windows_clipboard_winapi` (pywin32 OpenClipboard / pyperclip) |
| `wm`           | hyprctl                           | `windows_wm_uia` — list/focus via `pywinauto`/UIA; resize via `SetWindowPos` |
| `input`        | wtype / ydotool                   | `windows_input_sendinput` — `SendInput` for keys/mouse (no admin needed) |
| `apps`         | `gtk-launch` + `.desktop`         | `windows_apps_startmenu` — enumerate `%APPDATA%\Microsoft\Windows\Start Menu` `.lnk` files; launch via `os.startfile` |
| `shell`        | `linux_shell_posix`               | `windows_shell_powershell` — same allowlist contract, dispatch via `pwsh -NoProfile -Command` |
| `ocr`          | `linux_ocr_tesseract`             | reused — tesseract for Windows is binary-compatible; vendored |
| `media`        | `wpctl` + `playerctl`             | `windows_media_smtc` — `WindowsMediaControl` via WinRT bindings |
| `logview`      | `linux_logview_terminal`          | `windows_logview_powershell` — opens a console with `Get-Content -Wait` |
| `dialog`       | kdialog / zenity                  | `windows_dialog_winforms` — `MessageBox.Show` via pythonnet, or `tkinter.messagebox` as zero-dep fallback |
| keybind        | Hyprland conf / xfconf-query      | global hotkey via `keyboard` package or a tiny C# helper running in the tray; F9 → `voice toggle` |
| autostart      | XDG `.desktop`                    | Registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` or Startup folder `.lnk` |
| installer      | `install.sh` (bash)               | `install.ps1` (PowerShell) — mirrors the bash flow: detect pip, create venv, fetch piper.exe/whisper.cpp.exe via Invoke-WebRequest, write a `.voice-env.ps1`, drop a Startup shortcut |
| paths          | `runtime_dir()`/`state_dir()`/`config_dir()` already branch on `sys.platform` (Phase A.6) | re-uses A.6 helpers as-is — `%LOCALAPPDATA%\voice-opencode\runtime` etc. |

**Out of scope for Phase C MVP:**

- MPRIS-style cross-app media keys (Windows SMTC works differently;
  bind to specific players only if requested).
- OCR — defer until a user asks; tesseract works but the use cases
  (Hyprland-window-aware screenshot pipeline) don't translate
  one-to-one to Windows windowing yet.
- Wake-word / always-on — same anti-goal as Linux (ADR-0001).
- Multi-monitor virtual desktops — Windows 10/11 virtual desktops
  API is awkward; defer until a user uses them.
- macOS — separate ADR if and when, not bundled with Windows.

**Deferral mechanism.** All Windows code lives under
`backends/windows_*` (currently `windows_stub/` is an empty
placeholder). The Phase A wiring in `platform/__init__.py` already
has a `_wire_windows()` branch ready (matching `_wire_common_linux`
on the dispatch by `sys.platform` / `detect_platform()`); today it
no-ops to the Null backends. Phase C implementation = fill in the
backends, populate `_wire_windows`, and write `install.ps1`. No
changes needed to any module outside `backends/windows_*` and
`install.ps1`.

### Alternatives considered

1. **Run under WSL2 + WSLg** instead of native Windows. Rejected:
   the user wants native Windows behaviour (Start Menu integration,
   global hotkeys binding to a Windows session, tray in the Windows
   notification area). WSL2 would force a Linux desktop session
   running atop Windows, which defeats the point.
2. **Use PyInstaller + ship a one-file `.exe`** instead of a
   git-clone-and-install flow. Rejected as the *default*: a one-file
   exe hides the venv, breaks editable dev iteration, and complicates
   shipping the `.lnk`/MCP integration. May be revisited later as an
   *additional* distribution channel for non-developer users.
3. **Embed a Python interpreter in a native wrapper** (Tauri/Electron
   shell). Rejected: this is a 1500-LOC project, not a desktop app
   that benefits from a JS frontend.
4. **Skip Windows entirely** and focus on more Linux distros.
   Rejected: the user explicitly wants Windows support and the
   Phase A architecture investment was made on the explicit
   assumption that Windows would follow. Walking that back would
   waste the design.
5. **Use the `keyboard` pip package on Windows but vendor a tiny
   `xremap`-style helper** for global hotkeys. The `keyboard` package
   is the simpler default; revisit if it proves unreliable on Windows
   11 with secure-input apps.

### Consequences

Positive:

- Estimated implementation: one focused session per backend,
  roughly 4–6 sessions total. The Phase A architecture caps the
  surface area per backend at ~80 LOC + ~50 LOC of tests. No
  cross-cutting changes; each backend can be reviewed and merged
  independently.
- The CLI (`voice ...`), the tray (PyQt6 QSystemTrayIcon works on
  Windows out of the box), and the MCP server are platform-agnostic
  consumers — they need zero changes.
- `common_piper` and `common_whisper_cpp` will be reused as-is. The
  decision to name them `common_` (ADR-0023) pays off here: no
  rename, no file move, just point `PIPER_BIN`/`WHISPER_BIN` at the
  `.exe` paths in `install.ps1`.
- `paths.py` already branches on `sys.platform` (Phase A.6), so the
  state/runtime/config directory plumbing works the day a Windows
  backend is wired.

Negative / mitigations:

- Cross-session development overhead: edits made on Linux must be
  pulled on Windows and vice-versa. Mitigated by treating the repo
  as the source of truth and using opencode on both ends. Sessions
  on each machine should always pull before starting.
- Windows pip + venv on Windows have their own peculiarities (long
  paths, file locks held by antivirus). The install.ps1 will need
  explicit error messages for these; not insurmountable but worth a
  half-session of polish.
- `pyaudio`/`sounddevice` may pull native deps. Acceptable: the
  Windows binary wheels ship prebuilt.
- pywin32 / pythonnet / mss are larger dep closures than the Linux
  side. Trade-off accepted — the alternative is shelling out to
  PowerShell for every WM call, which is slow and brittle.

Open questions (do not block Phase C; revisit before implementation):

- Should the Windows installer prefer scoop/chocolatey for system
  deps where available, or stick to manual `Invoke-WebRequest`?
  Leaning towards manual to avoid forcing a package manager onto
  the user.
- Single global hotkey listener (one process registers F9 once and
  routes to `voice toggle`) vs spawning `voice toggle` per press?
  Linux Hyprland spawns per press; Windows would prefer the persistent
  approach to avoid Python startup latency. Decide during impl.

Rules going forward:

- Anything Windows-only goes under `backends/windows_*` with a
  matching capability constant and Null fallback.
- Consumer code (CLI, tray, MCP, pipeline) must remain
  OS-agnostic. Reviewer enforces by grep'ing for `sys.platform` /
  `os.name` outside of `paths.py` and `platform/__init__.py`.
- `install.ps1` is the Windows counterpart of `install.sh`. Both
  scripts should produce the same "user-visible" state — same tray,
  same MCP integration, same key bind contract — diverging only in
  the implementation.

---

## ADR-0023 — Voice pipeline as platform surfaces (Phase A)

Date: 2026-05-19

### Context

`voice-opencode` ran exclusively on Linux + Hyprland from day one.
The `platform/` package and the `BackendError` Protocol layer
(introduced for window-management, screen capture, clipboard,
input, notify, dialog, audio, media, apps, shell, ocr) cleanly
isolated *desktop integration* from the rest of the code — but
the *voice pipeline itself* (record → STT → opencode → TTS →
play) bypassed that abstraction entirely.

The four pipeline modules had hard-coded subprocess calls baked
into the top-level `voice_opencode.*` namespace:

- `audio.py` — `subprocess.Popen(["arecord", "-D", "default", "-f",
  "S16_LE", "-r", "16000", "-c", "1", str(out)])`,
- `tts.py` — `subprocess.Popen("piper-tts … | paplay", shell=True)`
  (a literal pipe between two binaries),
- `stt.py` — `subprocess.run(["whisper-cli", "-m", model, "-l", lang,
  "-nt", "-np", "-f", wav])`,
- `tray.py:_open_logs` — inline `subprocess.run(["which", term])`
  probes for `foot`/`kitty`/`alacritty`/`xterm` and an `xdg-open`
  fallback.

Three problems made this unworkable for Windows portability and
for swapping any single component:

1. `tts.py` chained Piper to paplay via a *shell pipe*. To replace
   paplay with WASAPI on Windows you would have to rewrite `speak()`,
   not configure a backend.
2. Each module raised `RuntimeError` (not `BackendError`) on
   subprocess failure, so callers had no uniform way to distinguish
   "feature absent on this host" from "bug in our code".
3. `pipeline.py` imported the modules directly (`from . import
   audio, stt, tts`). Any attempt to substitute a different recorder
   or player at runtime required monkeypatching globals.

The platform layer already had the right shape for everything else
in the app. The fix was to extend it, not to invent a new pattern.

### Decision

Add four new `Protocol`s in `platform/base.py` covering the voice
pipeline plus one for the tray's log viewer:

| Protocol            | Method(s)                                          | Capability constant(s)                                  |
|---------------------|----------------------------------------------------|---------------------------------------------------------|
| `RecorderBackend`   | `start(out)`, `stop(out)→Path?`, `is_recording()`  | `RECORDER_START`, `RECORDER_STOP`, `RECORDER_IS_RECORDING` |
| `PlayerBackend`     | `play_wav(path, timeout_s=60)`                     | `PLAYER_PLAY_WAV`                                       |
| `TTSBackend`        | `list_voices()`, `synthesize(text, voice, out, *, speaker_id=None)→Path` | `TTS_LIST_VOICES`, `TTS_SYNTHESIZE`     |
| `STTBackend`        | `transcribe(wav)→str`                              | `STT_TRANSCRIBE`                                        |
| `LogViewerBackend`  | `tail_file(path)`                                  | `LOGVIEW_TAIL_FILE`                                     |

Each Protocol has a matching `Null*Backend` in `platform/null.py`
that raises `BackendError("…feature not available")` on every method
*except* boolean queries — `NullRecorderBackend.is_recording()`
returns `False`, not raise, so pipeline guards keep working when no
real recorder is wired (tests, headless CI).

Move the existing logic verbatim into per-backend files:

- `backends/linux_audio_arecord/recorder.py` — same arecord args,
  same PID-file convention, same SIGINT shutdown, same 4 KB minimum
  threshold.
- `backends/linux_audio_paplay/player.py` — paplay with the 60 s
  watchdog kill that already existed.
- `backends/common_piper/tts.py` — Piper CLI, **but** synthesises
  to a WAV file instead of streaming through a pipe. Honours
  `PIPER_BIN` env override. Detects multi-speaker models via the
  `.onnx.json` sidecar and adds `--speaker N` only when applicable.
- `backends/common_whisper_cpp/stt.py` — whisper-cli, honours
  `WHISPER_BIN`. Reads model/language from `config.settings` so
  callers don't pass them.
- `backends/linux_logview_terminal/logview_backend.py` — the
  `foot > kitty > alacritty > xterm` probe + `xdg-open` fallback,
  using `shutil.which` instead of `subprocess.run(['which', …])`.

Note the deliberate `common_` prefix on the Piper and whisper
backends instead of `linux_`. Both ship identical CLIs on Linux and
Windows; only the binary path differs (`PIPER_BIN`/`WHISPER_BIN`).
Putting them under `common_` documents that they will be reused as-is
by the Windows platform wiring in Phase C without code duplication —
the file naming itself communicates portability scope.

The old modules become thin shims so consumers don't have to change:

- `audio.py` (28 LOC) → `start_recording()` / `stop_recording()` /
  `is_recording()` delegate to `_plat.recorder.*` with `REC_WAV_FILE`.
- `tts.py` retains three domain responsibilities (voice metadata,
  markdown cleaning, orchestration) and `speak()` now calls
  `_plat.tts.synthesize(text, voice, tmp_wav)` then
  `_plat.player.play_wav(tmp_wav)`. The intermediate WAV lives in
  `STATE_DIR/tts.wav`.
- `stt.py` (20 LOC) → `transcribe(wav)` delegates to `_plat.stt`.
- `tray._open_logs` → 5-line wrapper around `_plat.logview.tail_file`.

The TTS↔player split (WAV intermediate) is the load-bearing
architectural choice: it lets the same Piper engine drive any
PlayerBackend, and any TTSBackend drive paplay. Cross-platform
parity collapses to "swap two thin backends" instead of "rewrite
the pipeline".

`paths.py` gains `runtime_dir()` / `state_dir()` / `config_dir()`
helpers that branch on `sys.platform` (XDG on Linux,
`%LOCALAPPDATA%`/`%APPDATA%` on Windows). Module-level constants
stay as compatibility shims around them.

### Alternatives considered

1. **Leave the voice pipeline as-is and gate Windows behind a
   second top-level entry point** (`voice_opencode_win/`). Rejected:
   forks the codebase, duplicates the CLI/tray/MCP layers, and the
   user wants the *same* CLI on both OSes.
2. **One mega-Protocol `VoicePipelineBackend` instead of four small
   ones**. Rejected: tightly couples the four subsystems exactly when
   we want them swappable independently (e.g. swap player without
   touching TTS).
3. **Run Piper as a subprocess pipe even on Windows** (`piper.exe |
   ffplay -`). Possible but adds `ffplay`/`ffmpeg` as a dependency
   and inherits the same "no swap without rewriting `speak`" problem
   we just fixed.
4. **Embed libpiper/libwhisper as Python bindings**. Tempting (no
   subprocess overhead), but neither project ships official Python
   wheels for Linux+Windows and we don't want to maintain build
   scripts. The CLI wrapping is good enough at ~50 ms per call.
5. **Use `pyaudio`/`sounddevice` for recording and playback**.
   Rejected: brings PortAudio into the dependency closure and offers
   no real benefit over `arecord`/`paplay` on Linux. Reconsider for
   Windows where the native equivalent is friendlier than spawning
   PowerShell.

### Consequences

Positive:

- `tts.speak()` is now ~10 lines that compose two backends instead
  of 30 lines orchestrating a shell pipe. Same audible output, but
  the engines and the sinks are independently swappable — verified
  by live smoke: Piper → WAV → paplay on Linux today; the same Piper
  can drive WASAPI on Windows tomorrow with zero changes to
  `tts.speak()`.
- The "is Windows supported?" question collapses to "wire
  `windows_*` backends in `_wire_windows()`". Phase C is now a
  scoped engineering task, not a research project.
- `BackendError` is the single failure type the pipeline has to
  handle. `pipeline.py`'s try/except blocks were already shaped for
  it (the screen/clipboard backends raise the same type).
- 67 source files → 71. The growth is in the `backends/` tree;
  the surface area of `src/voice_opencode/*.py` (the modules
  consumers import) shrank.
- Test count went from 345 (pre-Phase A) to 412. Every new backend
  is independently testable with mocked subprocess; the shims need
  no tests of their own beyond the existing pipeline coverage.

Negative / mitigations:

- One extra WAV write per reply (~200 KB to tmpfs on Linux,
  `%LOCALAPPDATA%` on Windows). Inconsequential at speech rates;
  measured at <1 ms overhead on this host.
- `tts.wav` and `rec.wav` now live side-by-side in `STATE_DIR`.
  Documented in `STATE.md`. On crash these can leak; tmpfs cleans
  them at logout, Windows requires explicit cleanup (deferred to
  Phase C).
- Backend wiring in `platform/__init__.py` grew six new imports.
  Acceptable — the alternative was scattering the wiring across the
  consumer modules, which is exactly what Phase A was undoing.

Rules going forward:

- New cross-platform pipeline components (e.g. an alternative TTS
  engine like Coqui XTTS) live under `backends/common_*`.
- Anything that uses OS-specific APIs (PipeWire, Core Audio, WASAPI)
  lives under `backends/{linux,macos,windows}_*`.
- Consumer modules in `src/voice_opencode/` may import from
  `.platform` but never directly from `.backends` — that boundary
  keeps the Phase A separation enforceable by static analysis.

---

## ADR-0022 — Multi-distro install policy (pacman / apt / dnf)

Date: 2026-05-19

### Context

`install.sh` was hard-coded to `pacman` from day one (the project
started on CachyOS). After Phase A introduced `common_piper` and
`common_whisper_cpp` backends with `PIPER_BIN`/`WHISPER_BIN`
environment overrides, the Python side was actually portable to any
Linux distro. The remaining barrier was the installer:

1. `install.sh` aborted on non-Arch hosts (`pacman not found`).
2. Even if it didn't, package names differ across distros:
   `libnotify` vs `libnotify-bin`, `gtk3` vs `libgtk-3-bin`,
   `tesseract-data-spa` vs `tesseract-ocr-spa` vs
   `tesseract-langpack-spa`, `python` vs `python3`, etc.
3. The two pieces that *most* need to be local — `whisper.cpp` and
   `piper-tts` — exist in Arch's AUR-adjacent repos (`whisper.cpp`,
   `piper-tts-bin`) but not in Debian/Ubuntu/Fedora repos.
4. Keybinds were Hyprland-only via `~/.config/hypr/conf.d/voice.conf`.
   A Linux Lite/XFCE user had no automated path to bind F9.

The target second platform is **Linux Lite / Ubuntu / XFCE / X11**
(user's daily-driver laptop). The original goal — "this app should
not require recompilation, package porting, or distro forks to run
on a different Linux box" — needs the installer to take ownership of
the distro differences.

### Decision

Extend `install.sh` along three orthogonal axes — package manager,
vendored binaries, and keybind backend — all detected at runtime.
The Python side is untouched.

**1. Package-manager detection.** A small `PKG_MGR` probe runs
`pacman`/`apt`/`dnf` in fixed order; first found wins. Three explicit
package arrays follow (one `case "$PKG_MGR" in` block) that list the
distro-specific name for each logical dependency. Two helpers
abstract the query and install commands:

```bash
pkg_query_installed pkgname  # echoes 1 / 0
pkg_install pkg1 pkg2 ...    # uses the right command for the PM
```

No clever name-mangling — every distro gets its own explicit list.
This makes the matrix grep-able: a maintainer who wants to know
"does this distro install zenity?" can `grep -A 20 'apt)' install.sh`
and read the answer in five seconds.

**2. Vendored binaries.** When `PKG_MGR` is `apt` or `dnf`, the
script declares `VENDORED=( whisper-cli piper-tts )` and runs two
small downloaders after package install:

- `piper`: fetch the official x86_64 tarball from
  `rhasspy/piper` GitHub releases, extract to `vendor/piper/`.
- `whisper.cpp`: shallow-clone, build with cmake (or fall back to
  make), install the `whisper-cli` binary to `vendor/whisper.cpp/`.

Both are skipped when the binary is already present (idempotent).
Versions are pinned via `${PIPER_VER}`/`${WHISPER_VER}` env vars
with sensible defaults. The script then writes `.voice-env`:

```bash
export PIPER_BIN="$ROOT/vendor/piper/piper"
export WHISPER_BIN="$ROOT/vendor/whisper.cpp/whisper-cli"
```

The wrapper script (`./voice`) sources `.voice-env` if it exists,
which is exactly the mechanism the Python backends already honour.
Vendored content is `.gitignore`d (with `vendor/` and `.voice-env`)
so it never ends up in source control.

**3. Keybind backend.** A new `IS_XFCE` detector mirrors `IS_KDE`
(matches `XFCE/xfce/Xfce` in `XDG_CURRENT_DESKTOP` /
`XDG_SESSION_DESKTOP`). On XFCE the script calls `xfconf-query` on
the `xfce4-keyboard-shortcuts` channel:

```bash
xfconf-query -c xfce4-keyboard-shortcuts \
    -p /commands/custom/F9 --create -t string -s "$ROOT/voice toggle"
xfconf-query -c xfce4-keyboard-shortcuts \
    -p /commands/custom/<Super>F9 --create -t string -s "$ROOT/voice reset"
```

XFCE has no press/release keyboard events at the shortcut layer
(only single-press triggers), so F9 falls back to **toggle**
semantics on XFCE — exactly the contract `cli.toggle` was written
for and that ADR-0001 anticipated as the non-Hyprland behaviour.

A separate XDG-autostart `.desktop` is dropped into
`~/.config/autostart/` on every non-Hyprland host so the tray
launches with the session without needing DE-specific glue. Hyprland
keeps using its native `exec-once` in `~/.config/hypr/conf.d/`.

The dialog-backend choice (`kdialog` vs `zenity`) is also slightly
relaxed: a single `_dialog_choice()` helper picks `kdialog` if the
host is KDE, Hyprland, or already has `kdialog` installed; otherwise
`zenity`. This avoids needlessly pulling GTK onto a Wayland host
that already has Qt anyway.

### Alternatives considered

1. **One installer per distro** (`install-arch.sh`,
   `install-debian.sh`, …). Rejected: duplicates 80 % of the script
   for the sake of avoiding one `case` block.
2. **Generate the package list from a YAML file** parsed by Python.
   Rejected: would force users to run Python *before* installing
   Python in the venv, and parsing in bash via `yq` adds another
   distro-specific dependency.
3. **Ship a Snap / Flatpak / AppImage**. Rejected: the project's
   value proposition is "your local box, your binaries, your audio
   stack". A sandboxed bundle breaks PipeWire access, `/dev/uinput`
   access, `xfconf-query`, and the whole point of being a tray app
   that integrates with the user's session.
4. **Cargo-style build whisper.cpp from source on every distro**
   instead of vendoring upstream piper binaries. Considered for
   parity; we do build whisper.cpp from source (no upstream Linux
   binary release) but we fetch piper as a binary because their
   releases include both the executable and the espeak-ng runtime,
   which is non-trivial to build from scratch.
5. **Use `pipx`/`uvx` to install the Python side, system packages
   for system deps**. Rejected: editable install in a project venv
   is what the dev workflow has always used; switching deployment
   strategies isn't a portability problem.

### Consequences

Positive:

- One installer, three distros. The `case "$PKG_MGR"` block is the
  *only* per-distro branch in the entire script. Adding `zypper` or
  `pkg` later is a 30-line copy/paste.
- The Linux Lite/XFCE target works end-to-end with no manual steps
  beyond `git clone && ./install.sh`. Verified on the dev box
  (Arch/Hyprland) for the regression direction; the new branches
  for apt/dnf/XFCE will get a live test on the Linux Lite machine.
- `vendor/` keeps the repo source-only while letting end users
  reproduce the binary install with `rm -rf vendor/ .voice-env &&
  ./install.sh`.
- `xfconf-query` is the same tool every XFCE-bind GUI uses under
  the hood, so the bindings appear in
  *Settings → Keyboard → Application Shortcuts* and the user can
  edit them through the GUI if they want.

Negative / mitigations:

- Building whisper.cpp from source on a first install on Debian
  takes 3–8 minutes. Acceptable — it happens once. A future ADR may
  swap to a self-built GitHub release tarball if upstream starts
  publishing one.
- `${PIPER_VER}`/`${WHISPER_VER}` are pinned. They will need bumping
  periodically; bump → commit → run `install.sh` again. The script
  is idempotent for unchanged versions and re-fetches when the
  vendored binary is missing.
- Three package matrices in the script. Yes — the alternative is
  worse (see alternative 1). The matrices are explicit, grep-able,
  and only the maintainer touches them.
- XFCE toggle semantics differ from Hyprland push-to-talk. Already
  documented in ADR-0001 and surfaced to the user in the README and
  in `install.sh`'s final warnings.

Rules going forward:

- Adding a new distro = new branch in `case "$PKG_MGR"`. No other
  file changes.
- Adding a new system dependency = list it in *every* per-PM array
  with the correct distro name. Reviewer enforces this by reading
  the matrix.
- Vendored binaries live under `vendor/<project>/`. Use them via
  `PIPER_BIN`/`WHISPER_BIN` (or analogous env vars for future
  projects). Never `cp` into `/usr/local/bin` — keep installs
  user-scoped to the repo so uninstall is `rm -rf`.

---

## ADR-0021 — `platform_info()`: structured host snapshot in one pure function

Date: 2026-05-18

### Context

Platform-aware decisions in this repo were spread across three places:

1. `voice_opencode.platform.detect_platform(env)` — pure, returns one
   of the `PLATFORM_*` strings from env + `sys.platform`.
2. The `shutil.which(...)` guards inside every backend's `__init__`
   (`HyprlandWindowManager` needs `hyprctl`, `XclipClipboardBackend`
   needs `xclip`, etc.). These raise `BackendError` if the tool is
   missing — fail-loud at construction.
3. The parallel bash logic in `install.sh:75-92` (`DISPLAY_KIND`,
   `WM_HINT`, `IS_HYPRLAND`, `IS_KDE`) that picks pacman packages
   and which dialog backend to install.

The result: any caller that wanted a richer answer than "what's the
platform string" — does this host have `kdialog`? is it Wayland or
X11? what XDG vars are actually set? — had to either re-implement
the detection or try-and-fail by constructing a backend. The MCP
`platform_info` tool returned only `{platform, capabilities,
capacity_mode}`, which is enough to decide capability presence but
not enough for the agent to plan ahead ("should I ask via kdialog
or fall back to typing in the focused window?").

### Decision

Add `voice_opencode.platform.platform_info(env=None, *, which=None)`
that returns a frozen `PlatformInfo` dataclass with:

- `platform` (same string `detect_platform` returns; included so
  callers don't need two function calls),
- `session_type` (`"wayland"`/`"x11"`/`""`, with `WAYLAND_DISPLAY` and
  `DISPLAY` promoting an empty `XDG_SESSION_TYPE` — matches
  `install.sh:80-81`),
- `desktop` (lowercased `XDG_CURRENT_DESKTOP`),
- `tools` (frozenset of probed binaries actually present on PATH,
  drawn from `_PROBED_TOOLS` which lists every DE-implying tool any
  current backend keys off — `hyprctl`, `grim`, `wlr-randr`, `wtype`,
  `ydotool`, `wl-copy`, `wl-paste`, `xclip`, `kdialog`, `zenity`,
  `notify-send`, `gtk-launch`, `wpctl`, `playerctl`, `tesseract`),
- `env` (dict of the XDG/display env keys that were present),
- convenience predicates `is_hyprland`, `is_kde`, `is_wayland`,
  `is_x11` (the same names `install.sh` uses).

Both `env` and `which` are injectable for tests — the function is
pure (no calls to `os.environ` or `shutil.which` after the
arguments are resolved). Output `to_dict()` is JSON-serialisable
with sorted `tools` for stable diff-friendly output.

`detect_platform()` keeps its existing signature and behaviour;
`platform_info()` calls it internally rather than duplicating the
sniffing. The MCP `platform_info` tool now returns the full
`to_dict()` blob plus the existing `override`, `capabilities`,
`capacity_mode` fields. `voice platform info` mirrors the same
shape.

### Alternatives

- **Reuse `detect_platform` everywhere.** Already the case for the
  single-string question; doesn't address the structured fields.
- **Make every backend expose a static `probe_available() -> bool`
  classmethod.** Considered. Rejected because (a) the question we
  actually want to answer is "what's on this host", not "would each
  of these 14 backends construct"; (b) the call site would still
  need to know the class name. The tool-set probe in
  `platform_info` answers the host-shape question once.
- **Replace the per-backend `shutil.which` guards with a check
  against `platform_info().tools`.** Rejected: the guards have to
  stay because they run *at construction*, and we want fail-loud
  there. `platform_info` is for callers that want to look before
  they leap.
- **Cache `platform_info()` result the way `_state` caches backend
  wiring.** Rejected for v1. The function is cheap (one env-dict
  copy + ~16 `shutil.which` calls, all PATH-cached by the OS) and
  the agent benefits from re-probing if the user installs something
  mid-session. Reconsider if it ever shows up in a profile.

### Consequences

- Tool count unchanged (we extended an existing read-only tool;
  the agent now sees a richer payload but it's the same name).
- The agent has a single read-only call to orient itself before
  attempting any DE-coupled action ("is kdialog installed before I
  ask the user?", "is this even Hyprland before I call workspace
  ops?").
- The duplication between Python and `install.sh` is now a
  pinch-point: if we ever rewrite `install.sh` in Python it can
  call `platform_info()` directly and the two stop drifting.
- 9 new tests under `TestPlatformInfo` in `tests/test_platform.py`
  cover Hyprland / KDE-Wayland / X11 snapshots, the
  `WAYLAND_DISPLAY` and `DISPLAY` session promotion, env-key
  filtering, empty tool set, JSON round-trip, and frozen-dataclass
  enforcement.

---

## ADR-0020 — Memory mutation: `delete(ts)` + `edit(ts, body)` gated to `full`

Date: 2026-05-18

### Context

Phase G (ADR-0019) shipped append-only memory: agents could write and
read, never modify. In practice the agent occasionally records wrong
information (misheard date, transient context that aged out, duplicate
of a previous note). With no mutation path the user has two bad
options: ignore the noise (search results get noisier over time) or
hand-edit `.md` files (fine for humans, invisible to the agent's
own audit trail). Both undermine memory as a feature.

### Decision

Add two operations to `voice_opencode.memory`:

- `delete(ts) -> MemoryEntry` — remove the entry identified by its
  ISO timestamp (`YYYY-MM-DD HH:MM:SS`).
- `edit(ts, new_text) -> MemoryEntry` — replace **only the body** of
  an existing entry. The `ts` and `tags` are preserved.

Both surface as MCP tools (`memory_delete`, `memory_edit`) and CLI
subcommands (`voice memory delete`, `voice memory edit`). Both are
classified in `TIER_BY_TOOL` as `"full"` — they don't appear in
`assist` or `read-only` mode.

Identification is by ISO timestamp because:

- It's already returned by every read tool (`recent`, `search`,
  `append`), so the agent has it in context.
- It's unique within a day file (we write `datetime.now()` at
  second resolution; the chance of two `append` calls in the same
  second is real but the loud failure is fine — we error).
- It avoids inventing an opaque ID (hash, UUID) that the agent
  would have to remember to look up.

Persistence uses an atomic rewrite: parse the day file in full,
mutate the entry list in memory, serialize the result to a sibling
tempfile (`.YYYY-MM-DD.md.XXXX.tmp`), `os.replace()`. A crash
mid-write leaves the original file untouched. If the file becomes
empty (we deleted its last entry), it is removed entirely so
`list_days()` stays honest.

### Alternatives

- **Edit ts/tags too.** Rejected: lets the agent retroactively
  rewrite its own timeline, which destroys the audit value of the
  per-entry timestamp. If you need a different ts or tag, delete +
  re-append.
- **Tombstone instead of physical delete.** Considered (`status:
  deleted` field). Rejected because the whole point of delete is
  "this should stop showing up in search"; we'd then need to teach
  every reader to filter, doubling the surface area. The audit log
  already records the deletion as an event — that's the immutable
  trace.
- **In-place file edit (no tempfile).** Simpler but unsafe: a Ctrl-C
  or process kill mid-write would leave a half-written `.md` that
  the next `_parse_file` would silently truncate at the bad line.
  Tempfile + `os.replace()` is one extra syscall for crash safety;
  cheap.
- **Soft-tier (assist).** Rejected: delete is irreversible (we don't
  trash, we unlink). The capacity tiering rule (ADR-0014) is "if
  the worst-case outcome of a misfire is data loss the user has to
  manually recover from, it's full". This qualifies.

### Consequences

- Tool counts: read-only 20, assist 45, full **50** (was 48).
- Memory mutations are auditable: every call goes through
  `agent.audit("memory_delete", …)` / `"memory_edit"`, so even after
  the entry is gone the log preserves what changed and when.
- The tray's memory viewer (also new in Tanda 2) stays **read-only**
  — surfacing destructive buttons there would create a confusing
  asymmetry where the user could mutate from a click while a
  `read-only` or `assist` agent can't.
- `append`'s "no `## ` at start of line" body rule is reused by
  `edit` (same `_BAD_BODY_RE`), so the validation stays in one
  place.

---

## ADR-0019 — Agent memory: plain Markdown, one file per day, stdlib search

Date: 2026-05-18

### Context

Phase G gives the agent a way to remember things across opencode
sessions. Today every session starts blank — opencode forgets
that yesterday we fixed an audio bug, that Tesseract needs
``--region`` to be fast, that the user prefers Spanish-first
language order. Three axes had to be decided:

1. **Storage format.** Plain Markdown vs. JSON Lines vs. SQLite
   FTS5 vs. a vector store.
2. **Layout on disk.** One big file vs. one file per day vs. one
   file per topic.
3. **Search strategy.** stdlib substring vs. ripgrep subprocess
   vs. a real index.

### Decision

**Plain Markdown, one file per day, stdlib search.** Memory
lives at ``<repo>/memory/YYYY-MM-DD.md``. Each entry is an H2
header with timestamp and optional inline tag list:

```markdown
## 2026-05-18 14:32:11  [phase-g, memory]
Free-form Markdown body until the next ``## `` header.
```

Four MCP tools — three read-only, one assist:

- ``memory_search(query, limit=20)`` — case-insensitive
  substring scan over body + tags, newest first.
- ``memory_recent(n=10)`` — last N entries across all days.
- ``memory_list_days()`` — every ``YYYY-MM-DD`` that has
  entries, newest first.
- ``memory_append(text, tags?)`` — append a single entry to
  today's file.

The module is **stdlib-only** and never imports from
``platform/`` — memory is text on the project's own disk,
not a desktop capability. There is no capability constant; we
gate purely by capacity tier.

### Alternatives rejected

- **JSON Lines.** Faster to parse, easier to add fields later,
  but loses the "cat the file and read it" affordance that
  motivated the choice in the first place. The user explicitly
  asked for Markdown plano.
- **YAML frontmatter blocks per entry.** Cleaner machine
  parsing but uglier when reading raw. The H2-header form is
  good enough for both humans and a one-page regex.
- **SQLite FTS5 index.** Real ranking, fast at scale. Overkill
  until the corpus is thousands of entries; today it would
  bring a maintenance burden (schema, migrations, index
  rebuilds, .index.sqlite bloating git) for no perceptible
  speedup. Re-evaluate if memory grows past ~10 MB total.
- **ripgrep subprocess for search.** Faster and supports regex.
  But it adds a runtime dep, a subprocess hop per query, and a
  different result shape (we'd have to parse line offsets to
  reassemble entries). At our scale (one user, sub-MB corpus),
  Python's ``in`` is fine. Easy to swap later — the public API
  ``mem.search(query, limit)`` would stay identical.
- **One big ``MEMORY.md``.** Simpler to grep with eyes, but
  any append rewrites and grows the parse cost monotonically.
  Per-day files cap each parse at one day's worth of entries
  and naturally rotate.
- **One file per topic / tag.** Requires deciding topics up
  front, encourages duplicate notes ("does this go in
  ``audio.md`` or ``bugs.md``?"), and breaks chronological
  reading. Tags solve the cross-cutting case without forcing a
  filesystem decision.

### Consequences

- The ``memory/`` directory is gitignored. Each user's memory
  is local; we don't want to commit it accidentally nor force
  a sync mechanism on day one.
- Body text **cannot** contain a line starting with ``## ``.
  ``append`` validates this and raises ``ValueError`` rather
  than escape on parse, keeping the parser trivial. Workaround:
  indent the offending line, or use ``###`` / a different
  prefix.
- Tags **cannot** contain ``,`` or ``]``. Same reason —
  header is one-line greppable.
- ``memory_search`` returns ``[]`` for empty/whitespace query
  (no "give me everything" footgun in a tool exposed to the
  LLM).
- Tool counts: read-only **20** (+3), assist **45** (+4),
  full **48** (+4). 283 tests verde (+31).

---

## ADR-0018 — OCR backend: pure `find_text(path, …)` + composed MCP helper

Date: 2026-05-18

### Context

Phase F wires Tesseract so the agent can answer "where does the
text *X* appear on screen?". Two design axes had to be settled:

1. **Where does screen capture live?** Either the OCR backend
   takes a screenshot internally (high-level helper:
   ``find_text_on_screen(needle)``), or it stays pure
   (image-path in, matches out) and the MCP layer composes
   ``screen.capture_*`` + ``ocr.find_text``.
2. **What granularity is a "match"?** Either Tesseract's line
   rows (single bbox per matched line, simple) or word-level
   rows (each word has its own bbox; multi-word needles join
   consecutive same-line words).

### Decision

**Option C — pure backend + composed helper.** The
``OCRBackend`` Protocol exposes only ``find_text(path, needle, …)``
and ``dump_text(path, …)``. The MCP tool ``screen_find_text``
captures the focused monitor (or a ``--region``) to a temp
PNG, calls the backend, shifts bboxes back to absolute screen
coordinates, and deletes the temp file. A second MCP tool
``ocr_find_text_in_file(path, needle)`` exposes the pure
backend directly so the agent can OCR any image already on
disk (a screenshot the user took, a downloaded PNG, etc.)
without re-capture.

**Word-level matching.** We parse Tesseract's TSV with
``--psm 6`` and filter ``level == 5`` (word rows). Multi-word
needles are matched by walking consecutive words on the same
``(block, line)``, lower-casing both sides, dropping
non-matching prefix/suffix tokens, and taking the bounding-box
union of the matched words via a tiny ``_union_rects`` helper.

**OCR languages are configurable.** ``Settings`` gains
``ocr_languages: tuple[str, ...] = ("spa", "eng")`` and
``ocr_min_confidence: float = 50.0``. Languages are joined with
``+`` and passed to Tesseract's ``-l`` flag in declaration order
(first language wins ties).

### Alternatives rejected

- **High-level helper inside the backend**
  (``find_text_on_screen(needle)``). Would have coupled OCR
  backends to ``platform.screen``, breaking the rule that
  backends never import each other. Would also have prevented
  the agent from OCR-ing an arbitrary file without taking a
  fresh screenshot. Composing at the MCP layer keeps each
  backend single-purpose and lets us add more compositions
  later (e.g. OCR a clipboard image) without touching backends.
- **Line-level matches only.** Simpler parser, single bbox
  per match, but loses positional precision when the needle is
  one word inside a long line — the agent would then have to
  click the entire line. Word-level keeps the bbox tight; the
  TSV row count cost is negligible.
- **Hard-coded English only.** Tesseract is per-language;
  loading both ``spa`` and ``eng`` costs ~50 ms extra at startup
  and a few MB of RAM, but a Spanish-speaking user would
  otherwise see garbled output on Spanish text. The default
  pair covers our environment; the tuple is overridable per
  project via ``config.json``.

### Consequences

- The MCP exposes **two** OCR tools, both ``read-only`` (no
  side effects — they only read pixels):
  - ``screen_find_text(needle, region?)`` — captures + OCRs;
    shifts bboxes to absolute coords when ``region`` is given.
  - ``ocr_find_text_in_file(path, needle)`` — OCRs an
    existing image; bboxes are in the image's own coordinate
    space.
- The backend has a hard 60-second ``subprocess.run`` timeout
  and runs Tesseract with ``OMP_THREAD_LIMIT=1`` to avoid a
  CPU storm when the agent fires several OCR calls back-to-back.
- ``_match_needle`` has a termination bound
  ``len(joined) > len(needle) * 4`` so a pathological line
  with many false-positive prefixes stays O(n) per line.
- The capability constants ``OCR_FIND_TEXT`` and
  ``OCR_DUMP_TEXT`` are now part of the stable contract —
  renaming = breaking change for every backend.
- Live tool counts: read-only **17** (+2), assist **41**
  (+2), full **44** (+2). 252 tests verde (+33).

---

## ADR-0017 — `shell_run` policy: default-deny regex allowlist + no shell

Date: 2026-05-18

### Context

Phase E adds ``shell_run`` so the agent can execute arbitrary
commands. This is the single most dangerous capability in the
project — once it exists, every other safety rail (capacity tiers,
dialog confirms, per-call lock) is only as good as the
restrictions wrapping this tool.

The naive implementations all fail:

- ``subprocess.run(cmd, shell=True)`` is a remote-code-execution
  vector handed to a probabilistic agent. ``rm -rf ~`` is two
  hallucinated tokens away.
- A *denylist* (block ``rm``, ``dd``, ``shutdown``, …) is
  impossible to exhaust: ``python -c 'import os; os.remove(...)'``,
  ``find . -delete``, ``mv * /dev/null``, ``> ~/.bashrc``, …
- Even an allowlist of "safe" binaries breaks the moment the
  caller can sneak in ``|``, ``;``, ``&&``, ``$(...)``, ``` ` ` ```,
  or redirection — every one of those is a way to call an
  unallowed program from inside an allowed one.

### Decision

Three layered rails, all enforced by the backend (not by the MCP
glue, not by the CLI — by the only place that actually spawns the
process):

1. **No shell, ever.** ``subprocess.run(argv, shell=False)`` only.
   String input is tokenised with ``shlex.split``; list input is
   passed through.
2. **Shell metacharacter scan.** After tokenisation, every argv
   element is scanned for any of ``;|&`` `` ` ``$<>``. Match → reject.
   This catches both ``"ls | wc"`` (shlex would happily produce
   ``["ls", "|", "wc"]``) and list-form attempts like
   ``["echo", "a;b"]``.
3. **Default-deny basename allowlist.** ``argv[0]`` is reduced to
   its basename (``/usr/bin/ls`` → ``ls``) and matched with
   ``re.fullmatch`` against each pattern in
   ``settings.shell_allowlist``. No match → reject. Empty
   allowlist → everything rejected (safest possible default —
   the user opts in by editing ``config.json``).

Additional rails:

- ``dry_run=True`` is the default. Callers (and the model) must
  explicitly pass ``dry_run=False`` to actually spawn.
- Timeout is hard-capped at 60 s regardless of caller request.
- ``stdout`` / ``stderr`` are captured and truncated to 64 KB
  each before returning.
- Timeout returns ``rc=-1`` plus partial output and an appended
  ``[timeout after Ns]`` note — never raises.
- The MCP tool ``shell_run`` lives in the ``full`` tier only
  (ADR-0014). It is NOT exposed in ``assist`` or ``read-only``.

The default ``shell_allowlist`` ships with read-mostly tools:
``ls cat head tail wc rg grep find file stat jq yq git hg echo
true false date pwd whoami python3? node``. Notably absent:
``rm mv cp chmod chown sudo systemctl pacman pkill kill sh bash
sleep``. The user can add patterns per-project in ``config.json``.

### Alternatives considered

- **Denylist.** Rejected — fundamentally unbounded; cf. ``find
  -delete``, ``python -c``, redirection.
- **``shell=True`` + sanitisation.** Rejected — no parser short
  of a full POSIX shell handles all the corner cases (quoting,
  parameter expansion, history substitution); even then the LLM
  can craft escapes.
- **Allowlist of full command strings.** Rejected — defeats the
  purpose; agent can't compose arguments.
- **Per-call user confirmation dialog.** Considered for
  ``assist`` tier — rejected as primary defence (dialog fatigue;
  user clicks yes), but the user can opt in by leaving the
  allowlist empty and the tool out of ``full``. Future ADR if we
  add it.
- **``dry_run=False`` as default.** Rejected — symmetry with the
  rest of the project (dialogs default to blocking, type/click
  default to actual, sure — but those are scoped to keyboard/
  mouse, not arbitrary process spawn).

### Consequences

- The model sees ``shell_run`` only when ``capacity_mode=full``
  AND the platform actually has a shell backend.
- A power user who wants ``sed -i`` must add ``sed`` to
  ``shell_allowlist`` explicitly. We accept this friction.
- The backend rejects ``ls | wc`` — the agent must call
  ``shell_run("ls")`` and pipe in its own head. We accept this.
- A timeout never crashes the MCP server; the model can recover.
- Audit log records full argv, cwd, rc, dry_run flag, and
  stdout/stderr *lengths* (not contents — keeps logs small and
  avoids leaking secrets like environment dumps).

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
