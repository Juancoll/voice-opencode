# Architecture Decision Records

Lightweight ADRs. Newest at the top. Each entry: Context → Decision →
Alternatives → Consequences. Date format: YYYY-MM-DD.

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
