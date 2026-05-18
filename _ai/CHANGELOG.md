# Changelog (assistant view)

What I (the assistant) actually did, when, and why. Newest first.
This is intentionally more granular than `_ai/DECISIONS.md`.

## 2026-05-19 — A.7: LogViewerBackend; tray._open_logs goes through platform

- Phase A.7: the tray's "Ver logs" action used to probe
  ``foot``/``kitty``/``alacritty``/``xterm`` inline via
  ``subprocess.run(['which', …])`` and fall back to ``xdg-open``.
  That probe lives in a new ``LogViewerBackend`` protocol with
  one method ``tail_file(path)``; the Linux implementation
  (``backends/linux_logview_terminal/logview_backend.py``)
  preserves the exact preference order using ``shutil.which`` +
  ``subprocess.Popen``. Wired as slot ``logview`` in
  ``platform/__init__.py``.
- ``tray._open_logs`` is now five lines: get the backend, call
  ``tail_file``, log+swallow any ``BackendError`` so the tray
  doesn't crash if no terminal exists on a minimal host.
- New capability ``LOGVIEW_TAIL_FILE``. New Null backend
  ``NullLogViewerBackend``. Re-export added to
  ``platform.__all__`` and ``platform/__getattr__``'s slot set.
- Tests (``tests/test_backend_logview.py``, 6 cases):
  capabilities; first-installed terminal wins (probe order
  ``foot > kitty > alacritty > xterm``); ``xdg-open`` fallback
  when no terminal; ``BackendError`` when nothing at all;
  ``str`` path accepted alongside ``Path``.
- Verified: 412 tests (+6), ruff + mypy clean. The pattern
  generalises cleanly to Windows (Phase C): swap in a
  ``WindowsLogViewerBackend`` that runs
  ``powershell -NoExit Get-Content -Wait`` and the tray code
  stays unchanged.

## 2026-05-19 — A.6: cross-platform runtime / state / config dirs in paths.py

- Phase A.6: introduces three OS-aware helpers in ``paths.py`` —
  ``runtime_dir()``, ``state_dir()`` and ``config_dir()`` — that
  branch on ``sys.platform``. Linux honours XDG (``XDG_RUNTIME_DIR``,
  ``XDG_STATE_HOME``, ``XDG_CONFIG_HOME`` with sensible fallbacks).
  Windows uses ``%LOCALAPPDATA%\\voice-opencode\\runtime`` for
  runtime/state and ``%APPDATA%\\voice-opencode`` for config (the
  Windows equivalents — there is no tmpfs on Windows).
- ``STATE_DIR`` now derives from ``runtime_dir()`` so the OS branch
  happens in exactly one place. The 79+ existing call sites that
  import module-level constants (``STATE_DIR``, ``MODELS_DIR``,
  ``REC_PID_FILE``, etc.) stay untouched — those constants are
  kept as compatibility shims around the new helpers.
- ``CONFIG_FILE`` deliberately still points at the repo (not
  ``config_dir()``) to avoid breaking existing installs. The
  helper is exposed now so Phase B/C migrations can move user
  config out of the source tree without an API churn.
- New ``tests/test_paths.py`` (16 cases): Linux branch reads
  XDG vars and falls back correctly; Windows branch simulated
  by patching ``sys.platform`` reads ``LOCALAPPDATA`` /
  ``APPDATA`` (with ``Path.home()`` fallback); module constants
  retain the expected shape; ``ensure_dirs`` is idempotent and
  creates missing dirs.
- Verified: 406 tests (+16), ruff + mypy clean. No behaviour
  change on Linux — every constant resolves to the exact same
  path it did before this commit on this host.

## 2026-05-19 — A.5: STTBackend + WhisperCppSTTBackend; stt.py is a shim

- Phase A.5: moves the ``whisper-cli`` invocation out of
  ``src/voice_opencode/stt.py`` into
  ``backends/common_whisper_cpp/stt.py``. ``stt.py`` becomes a
  20-line shim that delegates to ``platform.stt.transcribe(wav)``
  so ``pipeline.py`` keeps doing ``from . import stt`` unchanged.
- Backend lives under ``common_`` (not ``linux_``) for the same
  reason as Piper: identical CLI on Linux and Windows, only the
  binary path changes via ``WHISPER_BIN`` env override. Defaults
  to ``whisper-cli`` (Arch package, GitHub release, Windows .exe
  once Phase C lands).
- Model file and language stay sourced from ``config.settings``
  inside the backend itself, so callers never pass them. Tag
  markers like ``[BLANK_AUDIO]`` are stripped as before.
  Subprocess is timeout-bounded (120 s) and reports failures
  as ``BackendError`` (rc≠0, timeout, missing model).
- New ``tests/test_backend_whisper_cpp.py`` (10 cases):
  construction with/without whisper-cli on PATH, ``WHISPER_BIN``
  env override, capability set, missing model → ``BackendError``,
  argv shape (binary, ``-m``, ``-l``, ``-nt``, ``-np``, ``-f``,
  WAV path), bracket-tag stripping (single + multiple), blank
  audio → empty string, rc≠0 → ``BackendError``, timeout →
  ``BackendError``, ``settings.whisper_lang`` honoured.
- Verified: 390 tests (+10), ruff + mypy clean. Live end-to-end
  smoke ran the just-produced ``tts.wav`` through ``transcribe()``
  and got back recognisable Spanish text, closing the
  Piper→WAV→whisper loop with the new architecture.

## 2026-05-19 — A.4: TTSBackend + PiperTTSBackend; tts.speak rewired

- Phase A.4: cuts the streaming ``piper-tts | paplay`` pipe in
  ``tts.speak`` into two independent steps: ``platform.tts.synthesize
  (...)`` writes a WAV to ``$XDG_RUNTIME_DIR/voice-opencode/tts.wav``,
  then ``platform.player.play_wav(...)`` reproduces it. Same audible
  result, but TTS engine and audio sink are now independently
  swappable — that's the unlock Windows needs (Piper binary works
  cross-platform; WASAPI replaces paplay).
- The Piper backend lives at ``backends/common_piper/tts.py`` with
  a ``common_`` prefix (not ``linux_``) because the CLI is identical
  on Linux and Windows; only the binary path differs (``PIPER_BIN``
  env override; default ``piper-tts``). This is the first cross-OS
  backend in the repo and sets the pattern for whisper.cpp next.
- ``tts.py`` keeps three domain responsibilities: voice metadata
  (``VoiceInfo`` + helpers, used by ``cli voices`` and tray voice
  picker), markdown cleaning (``clean_for_tts`` and its 14-pattern
  regex set), and the orchestration ``speak`` itself. Subprocess
  calls live exclusively in the backend now.
- New ``tests/test_backend_piper.py`` (16 cases): construction
  with/without piper on PATH, ``PIPER_BIN`` env override is
  consulted by ``shutil.which``, capability set, ``list_voices``
  (empty + sorted), ``_resolve`` (stem, substring, absolute, missing
  → ``BackendError``), ``synthesize`` (writes WAV via
  ``--output_file``, adds ``--speaker`` only for multispeaker
  models, omits it for single-speaker even when caller passes one,
  treats rc≠0 / empty file / timeout / unknown voice as failures).
- Verified: 380 tests (+16), ruff + mypy clean. Live smoke
  (``python -c "from voice_opencode.tts import speak;
  speak('Prueba A.4...')"``) reproduced audio; intermediate
  ``tts.wav`` is a valid 22050 Hz mono S16_LE WAV.

## 2026-05-19 — A.3: PlayerBackend + PaplayPlayerBackend

- Phase A.3: add a WAV-file PlayerBackend so TTS engine and audio
  sink become independently swappable. ``platform.player`` slot was
  reserved in A.2; this commit fills it on Linux with
  ``PaplayPlayerBackend`` (subprocess to ``paplay <wav>``, blocks
  on ``wait(timeout=…)``, kills + cleans up if the deadline is hit).
- Deliberately split from A.4 (TTS): in production today ``tts.py``
  pipes piper-tts raw stdout straight into paplay's stdin so they
  share fds. Introducing a WAV intermediate decouples them at the
  cost of one tmp file per reply (imperceptible on SSD) and is
  precisely what allows Windows to pair the same Piper backend with
  a WASAPI player. ``tts.py`` itself is **not** rewired in A.3 —
  A.4 will do the cut to ``synthesize()`` + ``player.play_wav()``
  in one commit so the behaviour change is atomic.
- New ``tests/test_backend_player.py`` (6 cases): missing paplay
  → BackendError, capability set, missing WAV → BackendError,
  Popen called with correct argv, timeout kills the process,
  ``timeout_s`` propagates to ``proc.wait``. All mocked subprocess,
  no audio played.
- Verified: 364 tests passed (+6), ruff + mypy clean. Backend
  exists and is wired but is not yet called from any consumer —
  intentional, A.4 makes the cut.

## 2026-05-19 — A.2: RecorderBackend + ArecordRecorderBackend

- Phase A.2: extract microphone capture into the platform surface.
  Added ``RecorderBackend`` Protocol (``base.py``), capabilities
  ``RECORDER_START``/``RECORDER_STOP``/``RECORDER_IS_RECORDING``,
  ``NullRecorderBackend`` (with ``is_recording() -> False`` instead
  of raising, so the lock/audio guards in the pipeline still work
  on hosts without a wired backend), and the concrete
  ``ArecordRecorderBackend`` at
  ``backends/linux_audio_arecord/recorder.py`` — bit-for-bit the
  same logic that lived in ``audio.py`` (S16_LE @ 16 kHz mono,
  SIGINT for WAV finalisation, 2.5 s wait, 4 KB min-usable
  threshold).
- ``platform.__init__`` wires ``recorder``, ``player``, ``tts`` and
  ``stt`` slots at once (the latter three default to their Null
  implementations; they'll be filled in A.3-A.5). Doing the slot
  reservation in a single commit keeps ``_build`` from being touched
  four times in a row.
- ``audio.py`` becomes a 28-line shim: ``start``/``stop``/
  ``is_recording`` delegate to ``_plat.recorder`` using the canonical
  ``REC_WAV_FILE`` so ``pipeline.py``, ``stt.py`` and the tests don't
  change. The shim deliberately keeps the public surface narrow —
  callers that want a custom output path should use
  ``platform.recorder`` directly.
- New ``tests/test_backend_recorder.py`` (13 cases): construction
  guard (``arecord`` not on PATH → ``BackendError``), capability
  declaration, ``is_recording`` paths (no PID file / live PID / stale
  PID / garbage), ``start`` (Popen args incl. the whisper-mandated
  format, no-op when already recording, unlinks previous WAV), and
  ``stop`` (not-recording / file-too-small / usable / SIGINT-before-
  poll). All via mocked ``subprocess.Popen`` + ``os.kill``;
  ``tmp_path`` isolation so the live ``$XDG_RUNTIME_DIR`` never
  leaks into the suite.
- Verified: 358 passed (+13), ruff + mypy clean (63 source files,
  +2 backend package), live smoke (0.6 s record → 12 KB WAV at
  ``rec.wav``). No consumer changed.

## 2026-05-19 — A.1: notify.py shim over platform.notify

- Phase A.1 of the multiplatform plan. Auditor (read-only) confirmed
  ``notify.py`` was the cheapest leak to plug: bypassed the existing
  ``platform.notify`` surface entirely, kept its own ``subprocess.run
  (["notify-send", ...])`` 4 months after ``LibnotifyBackend`` was
  wired. Rewrote it as a 12-line shim that respects ``settings.notify``
  (gate that lives only here, not in the backend), delegates to
  ``plat.notify.show(title, body, urgency)``, and swallows any
  exception (best-effort contract preserved). Two call sites
  (``pipeline.py``, ``cli.py``) keep their import unchanged.
- Decided to **not** propagate the old ``-t 2500`` visualisation
  timeout into ``LibnotifyBackend``: a daemon-default lifetime is
  fine, and changing the backend belongs to a backend commit, not a
  shim commit. If toasts feel sticky we revisit.
- Verified: 346 tests still green, ruff + mypy clean, live smoke
  (``python -c "from voice_opencode.notify import notify;
  notify('voice-opencode', 'A.1 shim funcionando', 'low')"``)
  produced the toast.
- First commit of Phase A. Sets the pattern for the remaining steps:
  one shim/backend per commit, suite green at each one, no behaviour
  change observable from the consumer layer.

## 2026-05-18 — Pipeline lockfile + silent F9 drop

- Smoke-test forensics: `logs/voice.log` showed two `paplay timed out`
  + `piper exit code -9` events 60s apart between 17:14:22 and
  17:15:22 with **no recording happening between them** — meaning two
  ``stop_and_run()`` invocations had been racing and both reached
  ``tts.speak()``. User caught this live ("se han reproducido varias
  veces las voces"). Earlier in the same session, lines 487-495 also
  showed five "Recording started" events in 13s caused by Hyprland's
  key-repeat re-dispatching the press bind while F9 was held.
- **First attempt was wrong**: I used ``threading.Lock``. That only
  serialises within one process; F9 dispatches every press as a fresh
  ``voice`` subprocess so the lock had no effect on the real bug. The
  attempt was caught the next session — production log filled with
  "TTS error: piper died" from a test that didn't mock ``log()``,
  exposing both the wrong abstraction and the test contamination at
  once. Reverted.
- **Correct fix**: cross-process **timestamped lockfile** at
  ``PIPELINE_LOCK_FILE`` (``$XDG_RUNTIME_DIR/voice-opencode/pipeline.lock``).
  Acquire via ``os.open(O_CREAT|O_EXCL)`` — atomic at the kernel
  level. Contents: ``"<pid> <unix_ms>\n"`` so the file is human
  inspectable (``cat pipeline.lock``) and manually removable
  (``rm pipeline.lock`` recovers the system). Stale recovery: on
  contention we read the file, and if the holder PID is dead OR the
  timestamp is older than ``_LOCK_TTL_S`` (120s — 2x the paplay
  watchdog), we steal the lock + retry once. Release: ``unlink()``.
- ``start_recording`` calls the lock with ``notify_on_busy=False``:
  a held F9 (key-repeat), accidental double-tap, or true concurrent
  press is dropped **silently** — no toast spam, just a single log
  line ``F9 descartado: ya hay un turno en curso (grabación o
  respuesta).`` That same message is emitted whether the duplicate
  was caught by ``audio.is_recording()`` (fast path) or by the
  lockfile (true race), so grepping ``logs/voice.log`` for one
  marker covers every "ignored because busy" case. ``audio.start()``
  itself became silent (no more ``Already recording.``) — the user-
  facing message belongs to the caller's layer.
- ``stop_and_run`` keeps the ``⏳ Ocupado`` toast: if you release F9
  mid-reply and nothing happens, you deserve feedback. Different
  semantics, deliberate.
- ``tests/test_pipeline.py`` rewritten (21 cases): isolated lockfile
  via ``tmp_path``, monkeypatched ``log``/``notify``/``set_state``
  so production logs stay clean; live-holder drop, dead-pid steal
  (using real ``os.fork``/``waitpid``), TTL-expired steal,
  corrupted-file steal, exception release, two-thread race, fast
  path via ``audio.is_recording``, message uniformity across both
  guards, silent-drop assertion. Real cross-process smoke also run
  with two ``voice rec start`` invocations back-to-back: second
  logged ``F9 descartado…`` and emitted no toast.
- Suite: 346 passed (+21), ruff clean, mypy clean. No new ADR — this
  is hardening of an implicit single-runner assumption, not a new
  policy.

## 2026-05-18 — TTS markdown sanitizer hardening

- Bug surfaced live: piper was reading every Markdown delimiter
  literally ("doble asterisco voice-opencode doble asterisco"),
  making long opencode answers unintelligible. The original
  ``clean_for_tts()`` only handled fenced code, inline code, a
  narrow set of line prefixes, and whitespace collapse.
- Rewrote ``src/voice_opencode/tts.py`` regex set keeping the
  existing order (fences → inline code → prefixes → whitespace)
  but inserting the missing layers: bold ``**``/``__``, italic
  ``*``/``_`` with look-arounds so list bullets are not eaten,
  strikethrough ``~~``, inline links ``[text](url)`` and images
  ``![alt](url)`` (alt-text kept), reference-style link
  definitions (whole line dropped), bare URLs (replaced by the
  word ``enlace``), ordered list bullets (``1.`` and ``12)``),
  blockquote markers anywhere on the line, ``+`` bullets,
  Markdown table separator rows, table pipes (turned into
  commas), and stray HTML tags. Order is documented in the
  docstring because it is load-bearing — bold MUST run before
  italic so ``**x**`` is not consumed by the italic regex.
- Extended ``tests/test_tts_cleaning.py`` from 5 to 26 cases:
  one per new category plus a regression test using the exact
  payload captured in ``logs/voice.log`` during the smoke test
  ("Veo tu sesión OpenCode en Ghostty…**voice-opencode**…
  `tts.py`…[el commit](https://…)").
- Final suite: 325 passed (+21), ruff clean, mypy clean. No
  ADR — this is a bugfix that strengthens an existing decision,
  not a new policy.

## 2026-05-18 — Repository move + env-var indirection

- Moved repo from ``~/gitr/voice-opencode`` → ``~/git/voice-opencode``
  to consolidate under the user's canonical ``~/git/`` workspace.
  The ``./voice`` wrapper is location-independent
  (``DIR="$(cd "$(dirname "$0")" && pwd)"``) so the package itself
  needed no change.
- Recreated ``venv/`` from scratch with Python 3.14.4 — every
  venv carries hardcoded shebangs and ``pyvenv.cfg`` absolute
  paths, so any ``mv`` of the project root invalidates it. This
  is a kernel-level constraint (shebangs are interpreted before
  env vars), not something the installer can paper over.
- Introduced ``VOICE_OPENCODE_HOME`` env var via
  ``~/.config/environment.d/voice-opencode.conf``
  (``VOICE_OPENCODE_HOME=%h/git/voice-opencode``). systemd's
  ``environment.d`` mechanism exposes it to the whole graphical
  session at login. Updated ``~/.config/hypr/conf.d/voice.conf``
  (F9 binds) and ``~/.config/hypr/conf.d/autostart.conf`` (tray
  exec-once, deduplicated — there were two identical lines from
  an older install) to use ``$VOICE_OPENCODE_HOME``. Hyprland
  expands env vars at bind dispatch time, confirmed via
  ``hyprctl binds -j`` showing the absolute resolved path.
- ``~/.config/opencode/opencode.json`` keeps the literal absolute
  path because the opencode docs do not document env-var
  expansion in ``mcp.<server>.command`` (only in ``headers`` and
  oauth fields). Conservative: literal path + manifest entry.
- Wrote ``~/.config/voice-opencode/install.manifest.json``: JSON
  inventory of every file the install touched (path, owner,
  action, purpose) so a future ``uninstall.sh`` can revert
  exactly what was installed without guesswork.
- Verified end-to-end smoke from the new location: 304 tests
  green, ``./voice platform info`` returns 12 detected tools,
  opencode daemon serving on ``127.0.0.1:4096``, MCP server
  boots reporting ``capacity=assist tools=45``, tray icon
  visible in DankMaterialShell, F9 push-to-talk pipeline ran
  twice through STT → screenshot → opencode → TTS (logs in
  ``logs/voice.log``). One pre-existing bug surfaced during the
  E2E run: ``paplay`` 60s watchdog kills long TTS replies
  (``piper exit code -9``); tracked separately, not a move
  regression.
- Killed the stale MCP server (pid 71633) that was still bound
  to the old ``~/gitr`` venv path. opencode respawns it lazily
  from the updated ``opencode.json`` command on next MCP call.

## 2026-05-18 — Tanda 3 / Phase K: `platform_info()` structured host snapshot

- New ``voice_opencode.platform.platform_info(env=None, *,
  which=None) -> PlatformInfo``. Pure function: takes injectable
  env dict and ``shutil.which`` callable so tests don't need to
  monkey-patch anything. Returns a frozen dataclass with
  ``platform`` (same string ``detect_platform`` returns),
  ``session_type``, ``desktop``, ``tools`` (frozenset of probed
  binaries actually on PATH from ``_PROBED_TOOLS``), captured
  ``env`` dict, and convenience predicates ``is_hyprland``,
  ``is_kde``, ``is_wayland``, ``is_x11``.
- ``_PROBED_TOOLS`` lists every DE-implying binary any current
  backend keys off: ``hyprctl``, ``wlr-randr``, ``grim``,
  ``wtype``, ``ydotool``, ``wl-copy``, ``wl-paste``, ``xclip``,
  ``kdialog``, ``zenity``, ``notify-send``, ``gtk-launch``,
  ``wpctl``, ``playerctl``, ``tesseract``. Extending the set is
  one-line.
- ``session_type`` is promoted from ``WAYLAND_DISPLAY`` /
  ``DISPLAY`` when ``XDG_SESSION_TYPE`` is empty, matching
  ``install.sh:80-81`` so install-time and runtime detection agree.
- ``PlatformInfo.to_dict()`` returns a JSON-friendly snapshot
  with sorted ``tools`` for stable diff-friendly output.
- ``detect_platform()`` kept as-is (single-string answer);
  ``platform_info`` composes it internally. The per-backend
  ``shutil.which`` guards stay too — they're for fail-loud
  construction, ``platform_info`` is for look-before-you-leap.
- MCP ``platform_info`` tool now returns the richer payload
  (``platform``, ``session_type``, ``desktop``, ``tools``,
  ``env``, the four ``is_*`` predicates) plus the existing
  ``override`` / ``capabilities`` / ``capacity_mode`` fields.
  Tool count unchanged — same name, richer body.
- ``voice platform info`` CLI mirrors the same JSON shape.
- 9 new tests in ``tests/test_platform.py`` (``TestPlatformInfo``)
  covering Hyprland / KDE-Wayland / X11 snapshots, env-driven
  session-type promotion, env-key filtering, empty tool set,
  JSON round-trip, and frozen-dataclass enforcement. Suite is
  now 303 tests.
- See **ADR-0021** for the consolidation rationale and the
  alternatives rejected (per-backend ``probe_available()``,
  replacing the construction-time guards, caching the result).

## 2026-05-18 — Tanda 2: memory delete/edit + memory viewer + v0.1.0

- Released **v0.1.0** to GitHub
  (https://github.com/Juancoll/voice-opencode/releases/tag/v0.1.0).
  CI run ``26033407692`` green on Python 3.11/3.12/3.13.
- Fixed the CI-only failure that blocked the release: the two
  ``test_known_app_uses_gtk_launch`` and
  ``test_known_app_falls_back_when_gtk_launch_fails`` tests left
  ``shutil.which("gtk-launch")`` unpatched. On the dev host
  ``gtk-launch`` is installed so the branch ran; on the GitHub
  runner ``which`` returned ``None`` and the test silently went
  down the ``_spawn`` path, returning a real pid instead of the
  mocked 12345. Patched ``shutil.which`` inside the test ``with``
  blocks (commit ``1bbe837``).
- Bumped ``pyproject.toml`` version 0.3.0 → 0.1.0 to match the
  public tag (decision: align pyproject with what users see on
  GitHub now that we're publishing real releases).
- Added ``memory.delete(ts) -> MemoryEntry`` and
  ``memory.edit(ts, new_text) -> MemoryEntry``. Identification by
  ISO timestamp (the same string already returned by every read
  tool); atomic rewrite via tempfile + ``os.replace``; empty day
  files are unlinked so ``list_days()`` stays honest. ``edit``
  preserves ts + tags and reuses ``_BAD_BODY_RE`` from ``append``
  so the validation rules can't drift. See ADR-0020.
- 11 new tests in ``tests/test_memory.py`` (``TestDelete`` +
  ``TestEdit``) covering: single-entry removal keeping siblings,
  empty-file cleanup, missing entry / missing file / malformed ts
  errors, body preservation of ts+tags on edit, empty-body and
  ``## ``-in-body rejection on edit. Suite is now 294 tests.
- Exposed both as MCP tools (``memory_delete``, ``memory_edit``)
  in ``mcp_server._register_memory``. Both audited via
  ``agent.audit`` even on rejection; both return ``{"error": str}``
  shape on ``ValueError`` so the model can self-correct.
- ``capacity.TIER_BY_TOOL`` adds both as ``"full"``. New tool
  counts: read-only **20**, assist **45**, full **50** (was 48).
- ``cli.cmd_memory`` adds ``delete`` and ``edit`` subcommands.
  Timestamp passes as two tokens (``YYYY-MM-DD HH:MM:SS``) to keep
  argv parsing trivial. Docstring updated.
- New module ``memory_viewer.py``: PyQt6 ``QDialog`` mirroring
  ``audit_viewer`` (count spinbox + Refrescar button + 2 s
  auto-poll + monospace table + status bar). Adds a client-side
  substring filter (``QLineEdit``) that matches against body and
  tags simultaneously. Read-only by design — see ADR-0020 for the
  capability-asymmetry rationale.
- ``tray.py`` exposes "Ver memoria…" under the Agente submenu and
  caches the dialog ref alongside the existing audit viewer, with
  the same lazy import + ``RuntimeError``-on-C++-gone fallback.

## 2026-05-18 — Tanda 1: CI + README rewrite + v0.1.0 prep

- New ``.github/workflows/ci.yml``: pytest + ruff + mypy on
  ``ubuntu-latest`` × Python 3.11/3.12/3.13. PyQt6 wheels are
  self-contained on Linux so no system Qt deps; tests never
  build a ``QApplication``. README gets a CI badge.
- README rewritten for humans: quickstart, configuration table,
  48-tool overview by tier, phase table, troubleshooting
  pointers. Replaces the AI-agent-oriented stub.

## 2026-05-18 — Phase G: agent memory (plain Markdown)

- New ``memory.py`` module — stdlib-only, no ``platform/``
  imports. Public API: ``append(text, tags=(), when=None)``,
  ``search(query, limit=20)``, ``recent(n=10)``,
  ``list_days()``, plus ``MemoryEntry`` frozen dataclass
  (``ts, tags, body, file``). Storage: ``<repo>/memory/YYYY-MM-DD.md``,
  one file per day, H2 header per entry
  (``## YYYY-MM-DD HH:MM:SS  [tag, tag]``), free-form Markdown
  body until next header. Strict ``YYYY-MM-DD.md`` stem
  validation (also checks calendar validity via
  ``datetime.strptime``) so stray notes are ignored.
- ``append`` rejects empty text, tags with ``,`` or ``]``, and
  body lines starting with ``## `` (would split the entry on
  re-parse — fail loud rather than escape on parse).
- ``search`` is case-insensitive substring over body + tags,
  newest-first within and across files. Empty/whitespace
  query returns ``[]`` (no "give me everything" footgun).
- ``recent`` walks files newest-first; ``list_days()`` returns
  ISO strings of every ``.md`` whose stem is a real calendar
  date.
- ``paths.py``: new ``MEMORY_DIR = PROJECT_ROOT / "memory"``.
- ``cli.py``: new ``voice memory {append|search|recent|days}``
  group. ``append`` accepts ``--tag T`` (repeatable);
  ``search`` accepts ``--limit N``; ``recent`` takes an
  optional N (default 10). JSON output for search/recent.
- ``mcp_server.py``: new ``_register_memory(mcp)`` exposing
  four tools — ``memory_search``, ``memory_recent``,
  ``memory_list_days`` (all read-only) and ``memory_append``
  (assist). No capability gate (it's just text on our own
  disk); only the capacity tier filters. Audit records
  ``{query, matches}`` / ``{ts, tags, chars, file}`` —
  never the body contents.
- ``capacity.py``: tier mapping added for all four tools.
  Live counts: read-only 20 (eran 17), assist 45 (eran 41),
  full 48 (eran 44).
- ``.gitignore``: ``memory/`` added — each user's memory is
  local, not committed.
- New **ADR-0019** documenting the storage choice (plain
  Markdown, one file per day), the rejection of JSON Lines /
  FTS5 / ripgrep / single big file / one-file-per-topic, and
  the validation rules (no ``## `` in body, no ``,`` or ``]``
  in tags, empty query returns ``[]``).
- 283 tests verde (eran 252): +31 in ``tests/test_memory.py``
  covering append (file creation, tag rendering, atomic
  append, strip, empty/invalid-tag/header-like-body
  rejection, default-now microsecond stripping), parse
  round-trip (single/multi entry, multi-line body, missing
  file, stray pre-header lines, malformed timestamp drop,
  empty body), search (body/tag match, case insensitivity,
  newest-first, empty query, no matches, limit, missing
  dir), recent (newest-first cross-file, oversize n,
  zero/negative, no files), list_days (newest-first, ignores
  non-ISO files, no files).
- Smoke live OK: ``voice memory append "Phase G smoke test from CLI" --tag phase-g --tag test``
  wrote ``memory/2026-05-18.md``; ``voice memory days``
  returned ``2026-05-18``; ``voice memory recent 3`` and
  ``voice memory search phase-g`` both returned the entry;
  MCP ``memory_search`` over the in-process server returned
  the same shape.
- No new runtime dep (stdlib only).
- ruff + mypy verde (60 source files, +1: ``memory.py``).

## 2026-05-18 — Phase F: OCR find_text (Tesseract)

- New ``backends/linux_ocr_tesseract/tesseract_backend.py`` —
  ``TesseractOCRBackend`` implementing the new ``OCRBackend``
  Protocol. Pure: image-path in, ``list[OcrMatch]`` out.
  Invokes ``tesseract <png> - -l <langs> --psm 6 tsv`` with
  ``OMP_THREAD_LIMIT=1`` and a 60 s hard timeout. ``_parse_tsv``
  drops non-word rows (``level != 5``), negative-conf rows,
  malformed rows, and empty text cells. ``_match_needle``
  walks consecutive words on the same ``(block, line)``,
  lower-cases both sides, trims unmatched prefix/suffix
  tokens, unions matched-word rects, dedups, sorts in reading
  order, and bounds the search per-line at
  ``len(joined) > len(needle) * 4`` to keep termination O(n).
- New capability constants ``OCR_FIND_TEXT`` and
  ``OCR_DUMP_TEXT`` in ``platform/capabilities.py``. New
  frozen dataclass ``OcrMatch(text, rect, confidence, line,
  word_index)`` in ``platform/types.py`` (``line`` encoded as
  ``block_num * 1000 + line_num`` so it sorts naturally).
  ``OCRBackend`` Protocol added to ``platform/base.py``;
  ``NullOCRBackend`` added to ``platform/null.py``;
  ``platform/__init__.py`` wires the Tesseract backend into
  the ``ocr`` slot and re-exports ``OcrMatch``.
- ``config.Settings``: new ``ocr_languages: tuple[str, ...] =
  ("spa", "eng")`` and ``ocr_min_confidence: float = 50.0``.
  ``_coerce`` already covered both types from Phase E.
- ``cli.py``: new ``voice ocr {find|dump|file}`` group. ``find``
  captures the focused monitor (or ``--region X Y W H``) and
  prints word-level matches; bboxes are shifted back to
  absolute screen coords when ``--region`` is set. ``file``
  OCRs any PNG/JPG on disk. ``dump`` writes all recognised
  text for an image without filtering by needle.
- ``mcp_server.py``: new ``_register_ocr(mcp)`` exposing
  ``screen_find_text(needle, region?)`` (composed:
  capture → temp PNG → OCR → shift → delete) and
  ``ocr_find_text_in_file(path, needle)`` (pure backend
  passthrough). Both gated via ``_expose(cap.OCR_FIND_TEXT,
  …)``. Audit records ``{needle, matches: N}`` — never the
  text contents.
- ``capacity.py``: both new tools mapped to ``read-only``
  (they only read pixels — no side effects). Live tool
  counts: read-only 17 (eran 15), assist 41 (eran 39), full
  44 (eran 42).
- New **ADR-0018** documenting (1) the pure backend +
  composed MCP helper split, (2) word-level granularity,
  (3) configurable language tuple. Alternatives rejected:
  high-level helper inside the backend, line-level matches,
  hard-coded English.
- 252 tests verde (eran 219): +30+ in
  ``tests/test_backend_ocr.py`` covering init + capability
  declaration, TSV parser (header skip, malformed rows,
  level filter, conf filter, empty text), ``_union_rects``,
  single-word ``_match_needle`` (hit/miss, case folding,
  confidence floor), multi-word ``_match_needle`` (in-order
  match, trimmed prefix/suffix, pruning bound), mocked
  subprocess invocation (argv shape, env, timeout); +1 in
  ``tests/test_mcp_server.py`` (``test_ocr_tools_in_read_only``).
- Smoke live OK: ``./voice ocr find Chrome --region 0 0 800 200``
  returned a single bbox at ``(170, 8, 41, 30)`` with conf
  92.12; ``screen_find_text`` over MCP returned the same
  match shape.
- New runtime deps (already installed locally and shipped in
  ``install.sh``'s 957fb79 commit): ``tesseract``,
  ``tesseract-data-eng``, ``tesseract-data-spa``. STATE.md
  already lists them.
- ruff + mypy verde (59 source files, +2:
  ``backends/linux_ocr_tesseract/{__init__,tesseract_backend}.py``).

## 2026-05-18 — Phase E: ``shell_run`` with safety rails

- New ``backends/linux_shell_posix/shell_backend.py`` —
  ``PosixShellBackend`` implementing the long-stubbed
  ``ShellBackend`` Protocol. Three layered rails (ADR-0017):
  (1) ``shell=False`` always; string input goes through
  ``shlex.split``; (2) post-parse shell-metachar scan rejects
  ``;|&`` `` ` `` ``$<>`` even on list input (catches
  ``["echo", "a;b"]``); (3) default-deny ``re.fullmatch`` on
  ``Path(argv[0]).name`` against ``settings.shell_allowlist``.
  Empty allowlist → everything rejected. ``dry_run=True``
  default. Timeout hard-capped at 60 s. Output truncated to
  64 KB per stream. Timeout returns ``rc=-1`` + partial output
  + ``[timeout after Ns]`` note, never raises. ``OSError`` →
  ``BackendError``.
- ``config.Settings``: new ``shell_allowlist: tuple[str, ...]``
  (default ~20 read-mostly tools — ``ls cat head tail wc rg
  grep find file stat jq yq git hg echo true false date pwd
  whoami python3? node``; notably no ``rm mv cp sudo systemctl
  pacman sh bash``) and ``shell_timeout_s: float = 10.0``.
  ``_coerce`` extended for ``float`` and tuple types.
- ``platform/__init__.py``: wires ``PosixShellBackend`` into the
  ``shell`` slot for the linux variants block.
- ``capacity.py``: ``"shell_run": "full"``. Verified live —
  ``shell_run`` does NOT appear in ``read-only`` (15 tools) or
  ``assist`` (39 tools); only in ``full`` (42 tools, +1 vs
  Phase J's 41).
- ``cli.py``: new ``voice shell {run|allowlist}`` group.
  ``run`` is dry-run by default; ``--exec`` flag actually
  spawns. ``allowlist`` prints the active regex patterns one
  per line.
- ``mcp_server.py``: new ``_register_shell(mcp)`` exposing
  ``shell_run(cmd, cwd?, timeout?, dry_run=True)``, gated via
  ``_expose(cap.SHELL_RUN, "shell_run")``. Audit log records
  ``{cmd, cwd, rc, dry_run, stdout_len, stderr_len}`` — never
  contents (privacy + log size). Denied calls audit
  ``{cmd, denied: True}`` with the error message.
- New **ADR-0017** documenting the allowlist + no-shell +
  metachar-reject policy and alternatives rejected (denylist;
  ``shell=True`` + sanitisation; per-call confirm dialog;
  full-string allowlist).
- 219 tests verde (eran 192): +26 in ``tests/test_backend_shell.py``
  (allowlist match/miss, regex boundary, metachar rejection in
  str and list, unbalanced quote, dry-run no-Popen, real exec
  captures stdout, non-zero rc, cwd applied, timeout returns
  rc=-1, timeout hard-cap, truncation helper, capability set,
  defaults from config); +1 in ``tests/test_mcp_server.py`` —
  ``test_shell_run_only_in_full`` verifies the tier gate.
- Smoke live OK: ``./voice shell allowlist`` lists the default
  patterns; ``./voice shell run echo hola`` returns
  ``dry_run=true`` with parsed argv; ``--exec`` echoes the
  string; ``run --exec rm …`` rejected with "not in allowlist";
  ``run "echo a | grep b"`` rejected with "shell metachar".
  MCP tool counts: read-only 15, assist 39, full 42.
- No new runtime dep (stdlib ``subprocess`` + ``shlex`` + ``re``).
- ruff + mypy verde (57 source files, +2: shell backend module
  + ``__init__``).

## 2026-05-18 — Phase J: audit viewer + capacity kill-switch in tray

- New ``audit_tail(n)`` reader on ``agent`` (alongside ``audit``).
  Returns the last ``n`` parsed JSON-line entries, newest last;
  silently skips malformed/non-dict lines; never raises. Lazy
  full-file read — fine for our log sizes (thousands of lines,
  not millions). Future: switch to seek-from-end if logs grow.
- New ``audit_viewer.py`` module: a modeless ``QDialog`` with a
  ``QTableWidget`` (ts/tool/args/result columns), a spinbox for
  line count, manual Refresh, and a 1.5 s polling timer. Imported
  lazily from the tray to keep tray boot fast (PyQt6 QTableWidget
  not loaded until the user actually opens the viewer).
- ``tray.py``: new "Agente" submenu with **Ver auditoría…** (opens
  the viewer; keeps a live ref so it isn't GC'd; raises an
  existing window if reopened) and **Modo de capacidad** (exclusive
  radio group: Solo lectura / Asistir / Completo). Selecting a
  mode runs ``voice config set capacity_mode <x>`` then
  ``voice mcp stop`` so the next opencode tool call spawns a
  fresh MCP with the new tier (ADR-0016). The radio state is
  re-synced on every 1 s refresh from ``voice state``.
- ``cmd_state``: now also returns ``capacity`` so the tray can
  reflect the live setting without parsing config.toml directly.
  Backward-compatible — additive field.
- New **ADR-0016** explaining why we restart the MCP server
  instead of in-process reload (FastMCP has no unregister; the
  call-time gate ADR-0014 rejected reappears; respawn is the
  same primitive already used by "Detener agente (MCP)").
- 192 tests verde (eran 183): +9 in ``tests/test_audit.py``
  covering missing log, n≤0 guard, ordering (newest last),
  tail limit, malformed-line skip, non-dict skip, unicode
  round-trip, write-then-read round-trip, IOError on
  read_text. No tray test (Qt-modal UI; not worth a fake
  X display in CI).
- Smoke live: ``voice state`` now reports ``capacity: assist``;
  ``voice mcp log -n 3`` confirmed real entries from prior
  Phase H smoke (``press_key``, ``list_monitors``); the
  ``audit_viewer`` module imports cleanly (full Qt smoke not
  possible from this shell — needs a logged-in Wayland
  session, the user runs ``voice tray`` for that).
- ruff + mypy verde (55 source files).
- No new runtime deps — PyQt6 was already present for the tray.

---

## 2026-05-18 — Phase I: apps surface (xdg launcher, MCP, CLI)

- New backend ``backends/linux_apps_xdg/xdg_backend.py`` replacing
  the ``NullAppLauncher`` stub. Four capabilities declared:
  ``APP_{LAUNCH,LIST_INSTALLED,LIST_RUNNING,KILL}``.
- ``list_installed`` scans XDG ``applications`` dirs (user dir wins
  on dedup), parses ``.desktop`` files manually to skip i18n
  ``Name[xx]=`` duplicates that would crash ``configparser`` in
  strict mode; returns ``{id, name, exec, icon, no_display}``.
  Filters out ``Type=Link`` (URL bookmarks); keeps ``NoDisplay=true``
  helpers (the model may want to launch them explicitly).
- ``list_running`` scans ``/proc/<pid>/comm`` and joins to the
  installed-app set by the basename of ``Exec=`` (truncated to 15
  chars per Linux comm semantics). Returns ``{pid, comm, app_id?}``.
- ``launch`` prefers ``gtk-launch <id>`` for known ``.desktop``
  entries (handles StartupNotify, DBusActivatable, field codes,
  env). Since ``gtk-launch`` detaches, the child pid is recovered
  by a ``/proc`` probe with a 250 ms grace; returns 0 if the probe
  misses. Falls back to direct ``Popen`` of the stripped ``Exec=``
  line if ``gtk-launch`` is missing or returns non-zero. Raw
  commands ("/bin/sleep 30") bypass the desktop-entry path and
  go straight to ``Popen`` with ``start_new_session=True``.
- ``kill`` accepts int pid, digit-string pid, or app id. App id
  is resolved against ``list_running``; SIGTERM is sent to every
  matching pid (``pkill``-style). Swallows ``ProcessLookupError``
  (race ok); ``PermissionError`` re-raises as ``BackendError``.
- ``cli.py``: new ``voice apps {list|running|launch|kill}`` group.
  ``launch`` joins all remaining argv with spaces so raw commands
  with arguments work without quoting tricks.
- ``mcp_server.py``: new ``_register_apps(mcp)`` with four tools:
  ``apps_list_installed``, ``apps_list_running``, ``apps_launch``,
  ``apps_kill``. Gated with ``_expose``; mutators wrapped in
  ``_acting`` + audit per Phase D contract.
- ``capacity.py``: ``apps_list_installed`` and ``apps_list_running``
  in ``read-only``; ``apps_launch`` in ``assist``; ``apps_kill`` in
  ``full`` (irreversible — SIGTERM destroys unsaved state). Decision
  documented in ADR-0015.
- New ADR-0015 covering the three sub-decisions (gtk-launch vs
  xdg-open; manual parser vs configparser; apps_kill in full).
- 183 tests verde (eran 158): +24 in ``tests/test_backend_apps.py``
  covering init guards, ``.desktop`` parsing + i18n + Type=Link
  filtering + dedup across dirs, field-code stripping,
  ``/proc`` matching, launch paths (known/fallback/raw/error),
  kill paths (int/string/app id/no-match/lookup/perm). +1 in
  ``test_mcp_server.py`` covering Phase I tier placement.
- Smoke live OK on host: ``./voice apps list`` returned 200+ apps
  including Moonlight and my custom ``doc-viewer`` / ``image-viewer``
  helpers; ``./voice apps launch "/bin/sleep 30"`` returned pid
  3270083, verified with ``pgrep``. Real ``/usr/share/applications``
  scan works; ``configparser`` would have choked on Firefox's
  20+ ``Name[xx]=`` lines.
- No new runtime deps: ``gtk-launch`` ships with ``gtk3``,
  already installed. ``glib2``'s ``gio`` not required (kept the
  backend lean).
- ruff + mypy verde (54 source files).

---

## 2026-05-18 — Phase H: audio + media surface (wpctl + playerctl, MCP, CLI)

- Phase 0 had wired stub ``WpctlAudioBackend`` and
  ``PlayerctlMediaBackend`` that raised ``BackendError`` in
  ``__init__``. Phase H replaces both with real implementations.
- ``backends/linux_audio_pipewire/wpctl_backend.py``: real wpctl
  driver. Operates on the default sink/source via the wpctl aliases
  ``@DEFAULT_AUDIO_SINK@`` / ``@DEFAULT_AUDIO_SOURCE@``. Parses
  ``Volume: 0.52`` and ``Volume: 1.00 [MUTED]`` lines. After a
  ``set-mute toggle`` we re-read the volume line just to learn the
  new muted state — wpctl gives no other signal. All four caps
  declared: ``AUDIO_{VOLUME_GET,VOLUME_SET,MUTE_TOGGLE,MIC_MUTE_TOGGLE}``.
  ``volume_set`` clamps to 0.0-1.0 defensively (wpctl would happily
  set 1.5 = 150% otherwise, which can blow speakers).
- ``backends/linux_audio_pipewire/playerctl_backend.py``: real
  playerctl driver. Uses ``--format`` with **ASCII Unit Separator
  (0x1F)** as field delimiter — discovered live that YouTube titles
  contain literal ``|`` chars, which would have stolen tokens from
  the artist field with a pipe-delimited format. The separator is
  vanishingly unlikely in real metadata. All four caps:
  ``MEDIA_{PLAY_PAUSE,NEXT,PREV,STATUS}``. ``status()`` returns
  ``{player, status, title, artist}`` and pads missing fields to ""
  rather than IndexError.
- ``cli.py``: new ``voice audio {get|set|mute|mic-mute}`` and
  ``voice media {play|pause|next|prev|status}`` groups (``play`` /
  ``pause`` / ``toggle`` are aliases — MPRIS exposes a single
  play_pause verb). Registered between ``dialog`` and ``platform``
  in ``COMMANDS``.
- ``mcp_server.py``: new ``_register_audio(mcp)`` and
  ``_register_media(mcp)``. Eight tools total:
  ``audio_get_volume``, ``audio_set_volume``, ``audio_mute_toggle``,
  ``audio_mic_mute_toggle``, ``media_play_pause``, ``media_next``,
  ``media_prev``, ``media_status``. Read-only tools (get_volume,
  status) live in the ``read-only`` tier; the rest in ``assist``.
  Added to ``capacity.TIER_BY_TOOL`` per ADR-0014.
- 158 tests verde (eran 137): +20 in ``test_backend_audio.py``
  covering both backends end-to-end (init guards, capabilities,
  argv shape per op, volume parsing with/without ``[MUTED]``,
  clamp behaviour, mute_toggle returns new state from re-read,
  source vs sink target, missing-fields padding, pipe-tolerance,
  failure paths, timeouts); +1 in ``test_mcp_server.py`` covering
  tier placement (audio_get_volume and media_status visible in
  read-only; setters/transport hidden until assist).
- Smoke live OK on host: volume round-trip 1.00 → 0.50 → 1.00,
  metadata from Chromium parsed correctly including the YouTube
  title with literal ``|`` chars (would have failed silently with
  a pipe delimiter — caught it on the first live probe).
- New runtime dependency: ``playerctl`` (installed via pacman).
  ``wireplumber`` was already present (system audio). STATE.md
  updated.
- ruff + mypy verde (52 source files, no new modules — the
  backends grew, did not multiply).

---

## 2026-05-18 — Phase D: capacity modes (read-only / assist / full filter for MCP tools)

- New module ``capacity.py``: maps every MCP tool to a tier in
  ``TIER_BY_TOOL`` and exposes ``current_mode()`` / ``allows(tool)`` /
  ``tools_for(mode)``. Tiers are monotonic (read-only ⊂ assist ⊂ full).
  Unknown tools default to ``full`` (safe-by-default — a forgotten
  entry hides the tool from restricted modes rather than leaking it).
  Unknown mode strings fall back to ``assist`` with a warning.
  See ADR-0014.
- ``mcp_server.py``: new ``_expose(cap, tool_name)`` helper combines
  the capability check and the capacity check into one call. All 25
  ``if plat.supported(cap.X):`` guards became ``if _expose(cap.X,
  "tool_name"):``. ``sleep_ms`` and ``platform_info`` (previously
  always-on) are now also gated, both belong to ``read-only`` so
  they remain visible in every mode. ``platform_info`` return value
  gained a ``capacity_mode`` field so the model can describe its
  own bounds without calling extra tools. ``serve()`` logs the
  active mode and exposed tool count on startup.
- ``capacity.py`` reads ``config.settings.capacity_mode`` dynamically
  (``from . import config`` then ``config.settings.…``) — caching
  the value at import time silently broke tests, written up in
  ADR-0014 as a footgun warning.
- Smoke live count (with current backend wiring): read-only → 11
  tools, assist (default) → 28, full → 29 (the extra one is
  ``close_window``). Sole destructive tool in this phase; Phase E's
  ``run_shell`` will be the second.
- 137 tests verde (eran 126): +8 in ``test_capacity.py`` covering
  tier mapping totality, per-mode tool sets, unknown-tool default,
  unknown-mode fallback, explicit-mode arg override, monotonicity;
  +3 in ``test_mcp_server.py`` covering read-only / assist / full
  registration shapes with all capabilities mocked-on.
- No CLI change needed — ``voice config set capacity_mode full`` and
  ``$VOICE_CAPACITY_MODE`` already worked via the generic config CLI.
- ruff + mypy verde (52 source files, +1: ``capacity.py``).

---

## 2026-05-18 — Phase C: dialogs + notifications (kdialog, zenity, MCP, CLI)

- Phase 0 had wired `LibnotifyBackend` (notify-send) and stub
  `KDialogBackend` / `ZenityBackend` raising `NotSupportedError`. Phase C
  closes the loop: real kdialog + zenity, MCP tools the agent can call,
  and a CLI group for parity. See ADR-0013 for the shared exit-code
  contract.
- ``backends/linux_dialog_kde/kdialog_backend.py``: real implementation.
  ``confirm`` → ``--yesno`` (rc 0 yes, rc 1 no, other → BackendError),
  ``ask_text`` → ``--inputbox`` (stdout stripped of trailing newline),
  ``ask_choice`` → ``--menu`` with each option passed twice (tag +
  description) so the returned tag matches the choice string exactly.
  Empty ``choices`` raises ``BackendError("ask_choice needs at least one
  option")``. Declares ``DIALOG_{CONFIRM,ASK_TEXT,ASK_CHOICE}``.
  ``_TIMEOUT_S = 300.0`` because dialogs are interactive.
- ``backends/linux_dialog_gtk/zenity_backend.py``: real implementation
  with the same exit-code contract and the same empty-choices guard.
  Uses ``--question``, ``--entry``, ``--list --column=Option`` for the
  three operations. Same caps, same timeout.
- ``platform/__init__.py``: wiring order unchanged (kdialog preferred,
  zenity fallback). Each backend's ``__init__`` already raises
  ``BackendError`` when the binary is missing, so the existing
  try/fallback in ``_wire_common_linux`` just works.
- ``mcp_server.py``: new ``_register_dialogs(mcp)`` between clipboard
  and misc. Four tools: ``notify(title, body, urgency)`` (no lock —
  fire-and-forget), ``ask_confirm(question, title)`` returning
  ``"yes"``/``"no"``, ``ask_user(prompt, default, title)`` returning the
  answer or ``""`` on cancel, ``ask_choice(prompt, choices, title)``
  same. All three blocking ones wrap in ``with _acting():`` so F9
  push-to-talk is silently dropped while the user is in the dialog.
  Audit-logged through ``_guard`` like every other tool.
- ``cli.py``: new ``voice dialog {notify|confirm|ask|choose}`` group.
  ``confirm`` uses rc=0 for Yes and **rc=2 for No** (rc=1 is reserved
  for backend error) so shells can distinguish; ``ask`` / ``choose``
  print the answer to stdout or nothing on cancel. Registered in
  ``COMMANDS`` between ``clipboard`` and ``platform``.
- 126 tests verde (eran 96): +19 in ``test_backend_dialog.py`` covering
  both backends end-to-end (init guards, capabilities, argv shape per
  op, rc 0 / rc 1 / rc other, stdout stripping, empty-choices guard,
  timeouts); +11 in ``test_cli.py`` for the new dialog group
  (notify with default & critical urgency, confirm rc=0/2/1, ask
  inline + cancel, choose, unknown subcommand → rc=1, BackendError
  propagation).
- Smoke live OK: ``./voice dialog notify "Phase C" "ok"`` shows a
  toast; ``./voice dialog confirm`` opens a real kdialog window
  (``./voice windows find kdialog`` confirms class ``org.kde.kdialog``,
  title ``Phase C``); ``./voice dialog ask`` round-trips text;
  ``./voice dialog choose red green blue`` returns the picked tag.
- ruff + mypy verde (51 source files, no new top-level modules — the
  two new backend files live under the existing backend packages).
- New skill: ``_ai/SKILLS/build-dialog-backend.md`` so the next
  rofi/wofi/yad/AppleScript backend is a copy-paste away.

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
