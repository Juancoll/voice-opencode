# Session handoff — for the next opencode instance

> Drop this file into context (or just `cat _ai/SESSION.md`) at the
> start of a new session on any machine. It captures everything an
> incoming agent needs to continue the project without re-reading the
> entire repo.
>
> Also read **AGENTS.md** for repo-wide conventions and **`_ai/CHANGELOG.md`**
> for the full granular history. This file is the *current cursor*.

Last updated: 2026-05-18 — end of Phase G.

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
| C     | Notify / ask_user / confirm (kdialog→zenity) | ✅ done |
| D     | Capacity modes (read-only/assist/full) filtering MCP tools | ✅ done |
| H     | Audio / media (wpctl + playerctl backends) | ✅ done       |
| I     | Apps (XDG launcher: list/running/launch/kill) | ✅ done    |
| J     | Audit viewer + capacity kill-switch en tray | ✅ done    |
| E     | run_shell with safety rails        | ✅ done       |
| F     | OCR find_text (Tesseract)          | ✅ done       |
| G     | Memory (Markdown plano)            | ✅ done       |
| K     | OS-agnostic detect_*: replace UA strings with ``platform_info`` | ⏭ next (optional) |

## What just shipped (Phase G, this commit)

- New ``memory.py`` — stdlib-only, no ``platform/`` imports.
  Public API: ``append(text, tags=(), when=None)``,
  ``search(query, limit=20)``, ``recent(n=10)``,
  ``list_days()``, plus ``MemoryEntry`` frozen dataclass.
  Storage: ``<repo>/memory/YYYY-MM-DD.md``, one file per
  day, H2 header per entry. Strict ``YYYY-MM-DD.md`` stem
  validation (also calendar-validates via
  ``datetime.strptime``).
- ``append`` rejects: empty text, tags with ``,`` or ``]``,
  body lines starting with ``## `` (would split entry on
  re-parse — fail loud, no escape on parse).
- ``search`` is case-insensitive substring over body + tags,
  newest-first. Empty query → ``[]``.
- ``paths.py``: new ``MEMORY_DIR``.
- ``cli.py``: new ``voice memory {append|search|recent|days}``
  group. ``append`` takes repeatable ``--tag T``; ``search``
  takes ``--limit N``.
- ``mcp_server.py``: ``_register_memory(mcp)`` exposes four
  tools — three read-only (search/recent/list_days) and one
  assist (append). No capability gate (text on our own disk);
  only capacity tier filters. Audit records ``{query,
  matches}`` / ``{ts, tags, chars, file}`` — never the body
  contents.
- ``capacity.py``: tier mapping added. Live counts: read-only
  20 (eran 17), assist 45 (eran 41), full 48 (eran 44).
- ``.gitignore``: ``memory/`` added.
- New **ADR-0019** — plain Markdown, one file per day,
  stdlib search. Alternatives rejected: JSON Lines, YAML
  frontmatter, SQLite FTS5, ripgrep subprocess, one big
  ``MEMORY.md``, one file per topic.
- 283 tests verde (eran 252): +31 in
  ``tests/test_memory.py`` (append validation, parse
  round-trip, search filtering/ordering, recent
  cross-file, list_days date validation).
- Smoke live OK end-to-end: CLI append → days/recent/search
  return the entry; MCP ``memory_search`` over in-process
  server returns identical shape.
- No new runtime dep (stdlib only).
- ruff + mypy verde (60 source files, +1: ``memory.py``).

## Previously shipped (Phase F)

- ``backends/linux_ocr_tesseract/tesseract_backend.py``:
  ``TesseractOCRBackend``, pure (image-path → matches).
  Invokes ``tesseract <png> - -l <langs> --psm 6 tsv`` with
  ``OMP_THREAD_LIMIT=1`` and a 60 s hard timeout. Word-level
  TSV parser (``level=5`` rows only) feeds ``_match_needle``
  which joins consecutive same-line words, trims unmatched
  prefix/suffix, unions bboxes, and bounds per-line work at
  ``len(joined) > len(needle)*4`` to stay O(n) per line.
- New capability constants ``OCR_FIND_TEXT`` + ``OCR_DUMP_TEXT``.
  New ``OcrMatch(text, rect, confidence, line, word_index)``
  dataclass in ``platform/types.py``. ``OCRBackend`` Protocol
  + ``NullOCRBackend`` added. ``platform/__init__.py`` wires
  the backend into the ``ocr`` slot and re-exports ``OcrMatch``.
- ``config.Settings``: ``ocr_languages: tuple[str, ...] =
  ("spa", "eng")`` and ``ocr_min_confidence: float = 50.0``.
- ``cli.py``: new ``voice ocr {find|dump|file}`` group.
  ``find`` accepts ``--region X Y W H`` and shifts bboxes back
  to absolute screen coords.
- ``mcp_server.py``: new ``_register_ocr(mcp)`` with two
  tools — ``screen_find_text(needle, region?)`` (composed:
  capture → temp PNG → OCR → shift → delete) and
  ``ocr_find_text_in_file(path, needle)`` (pure passthrough).
  Both gated via ``_expose(cap.OCR_FIND_TEXT, …)``. Audit
  records ``{needle, matches: N}`` — never text contents.
- ``capacity.py``: both tools mapped to ``read-only`` (no
  side effects). Live counts: read-only 17, assist 41, full 44.
- New **ADR-0018** — pure backend + composed MCP helper,
  word-level granularity, configurable language tuple.
- 252 tests verde (eran 219): +30+ in
  ``tests/test_backend_ocr.py`` (init/caps, TSV parser,
  union helper, single/multi-word match, mocked subprocess);
  +1 in ``tests/test_mcp_server.py``
  (``test_ocr_tools_in_read_only``).
- Smoke live OK: ``./voice ocr find Chrome --region 0 0 800 200``
  returned ``(170, 8, 41, 30)`` conf 92.12; MCP
  ``screen_find_text`` returned the same match shape.
- New runtime deps (already in ``install.sh`` and STATE.md
  since installer commit 957fb79): ``tesseract``,
  ``tesseract-data-eng``, ``tesseract-data-spa``.
- ruff + mypy verde (59 source files, +2).

## Previously shipped (Phase E)

- ``backends/linux_shell_posix/shell_backend.py``: real
  ``PosixShellBackend``. Three layered rails (ADR-0017):
  ``shell=False`` always; post-parse scan rejects shell
  metacharacters (``;|&`` `` ` `` ``$<>``) on both string and list
  input; default-deny ``re.fullmatch`` on ``Path(argv[0]).name``
  against ``settings.shell_allowlist``. Empty allowlist →
  everything rejected. ``dry_run=True`` is the default.
  Timeout hard-capped at 60 s. Output truncated at 64 KB per
  stream. Timeout returns ``rc=-1`` + partial output, never
  raises. ``OSError`` → ``BackendError``.
- ``config.Settings``: ``shell_allowlist`` (default ~20
  read-mostly tools, notably no ``rm/mv/cp/sudo/sh/bash``) and
  ``shell_timeout_s=10.0``.
- ``platform/__init__.py``: wires the new backend into the
  ``shell`` slot for linux.
- ``capacity.py``: ``"shell_run": "full"``. Verified live —
  read-only 15, assist 39, full 42 (+1 vs Phase J).
- ``cli.py``: new ``voice shell {run|allowlist}`` group. ``run``
  is dry-run by default; ``--exec`` actually spawns.
- ``mcp_server.py``: new ``_register_shell`` with the single tool
  ``shell_run``. Audit records argv, cwd, rc, dry_run, and
  ``stdout_len/stderr_len`` (lengths, not contents).
- New **ADR-0017** — allowlist policy and alternatives rejected.
- 219 tests verde (eran 192): +26 in ``test_backend_shell.py``,
  +1 in ``test_mcp_server.py`` (tier gate).
- Smoke live OK: dry-run, exec, denied basename, metachar reject
  all behave as specified.
- ruff + mypy verde (57 source files, +2).
- No new runtime dep.

## Previously shipped (Phase J)

- `agent.audit_tail(n)`: new reader paired with the existing
  `audit` writer. Skips malformed/non-dict JSON lines, never
  raises, returns newest-last. Full-file read (fine for current
  log sizes).
- `audit_viewer.py`: new module — modeless `QDialog` +
  `QTableWidget` (ts/tool/args/result), spinbox for N entries,
  manual Refresh, 1.5 s polling. Imported lazily by the tray.
- `tray.py`: new **Agente** submenu with **Ver auditoría…** and
  **Modo de capacidad** (exclusive radio: read-only/assist/full).
  Selecting a mode persists it and runs `voice mcp stop` so the
  next opencode tool call spawns a fresh MCP at the new tier
  (in-process reload rejected — see ADR-0016).
- `cmd_state` now also returns `capacity` (additive field) so
  the tray's radio reflects the persisted value on each poll.
- **ADR-0016** documents the respawn-over-reload decision and
  the alternatives considered.
- 192 tests verde (eran 183): +9 in `test_audit.py`.
- ruff + mypy verde (55 source files).
- No new runtime deps.

## Previously shipped (Phase I)

- `backends/linux_apps_xdg/xdg_backend.py`: real XDG app launcher.
  Manual `.desktop` parser (i18n-safe — `configparser` chokes on
  duplicate `Name[xx]=` keys), XDG dir scan with user-dir-wins
  dedup, `gtk-launch` for known app ids with `/proc` pid probe
  (best-effort, returns 0 on miss), raw-command fallback via
  detached `Popen`, `kill` via SIGTERM with pid-or-app-id
  resolution (app id = pkill-style: every matching pid).
- `cli.py`: new `voice apps {list|running|launch|kill}` group.
  `launch` joins remaining argv with spaces.
- `mcp_server.py`: new `_register_apps` with 4 tools:
  `apps_list_installed` + `apps_list_running` (read-only),
  `apps_launch` (assist), `apps_kill` (**full** — irreversible,
  rationale in ADR-0015).
- `capacity.py`: tier mapping added.
- New **ADR-0015** documenting (1) gtk-launch over xdg-open,
  (2) manual parser over configparser, (3) `apps_kill` in `full`
  not `assist`.
- 183 tests verde (eran 158): +24 in `test_backend_apps.py`,
  +1 in `test_mcp_server.py`.
- Smoke live OK: `./voice apps list` returned the real
  `/usr/share/applications` set (200+ apps); `./voice apps
  launch "/bin/sleep 30"` returned a usable pid and the
  process was visible via `pgrep`.
- No new runtime dep: `gtk-launch` ships with `gtk3` (already
  present). STATE.md updated to record the dependency.
- ruff + mypy verde (54 source files).
- Live tool counts now: read-only 15 (eran 13), assist 39
  (eran 36), full 41 (eran 37).

## Previously shipped (Phase H)

- `backends/linux_audio_pipewire/wpctl_backend.py`: real wpctl
  driver. Default sink/source aliases. Parses `Volume: 0.52` and
  `Volume: 1.00 [MUTED]`. `set-mute toggle` requires a re-read of
  `get-volume` to learn the new state (wpctl gives no other signal).
  Clamps volume to 0.0-1.0 defensively (wpctl accepts >1.0 = >100%,
  speaker-dangerous).
- `backends/linux_audio_pipewire/playerctl_backend.py`: real
  playerctl driver. Uses ASCII Unit Separator (0x1F) as field
  delimiter in `--format` — discovered live that YouTube titles
  contain literal `|`, which would have stolen tokens from artist.
  `status()` pads missing fields to "" instead of IndexError.
- `cli.py`: new `voice audio {get|set|mute|mic-mute}` and `voice
  media {play|pause|next|prev|status}` groups.
- `mcp_server.py`: new `_register_audio` + `_register_media`. 8
  new tools. `audio_get_volume` and `media_status` placed in
  `read-only` tier; the rest in `assist`.
- 158 tests verde (eran 137): +20 in `test_backend_audio.py`,
  +1 in `test_mcp_server.py`.
- Smoke live OK: volume round-trip, mute toggle, YouTube title
  with literal `|` parsed intact, MCP tool registration confirmed
  (36 tools in assist now, eran 28).
- New runtime dep: `playerctl` (pacman). `wireplumber` already
  present. STATE.md updated.
- ruff + mypy verde (52 source files, no new modules).

## Previously shipped (Phase D)

- New module `capacity.py`: `TIER_BY_TOOL` maps every MCP tool to a
  tier (`read-only` / `assist` / `full`); `allows(tool, mode)`,
  `current_mode()`, `tools_for(mode)`. Tiers are monotonic. Unknown
  tools default to `full` (safe-by-default — forgotten entries hide
  from restricted modes, never leak). Unknown mode strings fall back
  to `assist` with a warning. See ADR-0014.
- `mcp_server.py`: new `_expose(cap, tool_name)` helper. All 25
  `if plat.supported(cap.X):` guards became `if _expose(cap.X,
  "tool_name"):`. `sleep_ms` / `platform_info` also gated (both in
  read-only tier). `platform_info` return adds `capacity_mode` so
  the model can describe its own bounds. `serve()` logs active mode
  and exposed tool count on startup.
- Footgun fix: `capacity.py` reads `config.settings.capacity_mode`
  dynamically via `from . import config`, not at import time —
  caching silently broke monkeypatched tests.
- Tool→tier policy (conservative): `close_window` lives in `full`
  only. Phase E's `run_shell` will join it. Everything else
  (type/click/clipboard_write/dialogs/wm-non-destructive) is `assist`.
- Live counts (current backend wiring on this host): read-only → 11,
  assist (default) → 28, full → 29.
- 137 tests verde (eran 126): +8 in `test_capacity.py`, +3 in
  `test_mcp_server.py`.
- No CLI change — `voice config set capacity_mode <mode>` and
  `$VOICE_CAPACITY_MODE` already worked via the generic config CLI.
- ruff + mypy verde (52 source files, +1: `capacity.py`).

## Previously shipped (Phase C)

- `backends/linux_dialog_kde/kdialog_backend.py`: real implementation
  (was a stub). `--yesno` / `--inputbox` / `--menu` with the shared
  exit-code contract from ADR-0013 (rc 0 → answer/True; rc 1 →
  None/False; other → `BackendError` with stderr). `ask_choice` passes
  each option twice (tag + description) so the returned tag matches the
  choice string. Empty `choices` → fail-fast `BackendError`.
  Capabilities `DIALOG_{CONFIRM,ASK_TEXT,ASK_CHOICE}`. Timeout 300 s
  (interactive).
- `backends/linux_dialog_gtk/zenity_backend.py`: real implementation
  with the same contract, same caps, same timeout. Uses `--question` /
  `--entry` / `--list --column=Option`.
- `platform/__init__.py`: wiring unchanged — kdialog preferred,
  zenity fallback via the existing `_try()` chain.
- `mcp_server.py`: new `_register_dialogs(mcp)` exposing four tools:
  `notify` (fire-and-forget, no lock), `ask_confirm` → `"yes"`/`"no"`,
  `ask_user` / `ask_choice` returning the answer or `""` on cancel.
  Blocking tools wrap `with _acting():` so F9 is dropped while the
  user is in the dialog.
- `cli.py`: new `voice dialog {notify|confirm|ask|choose}` group.
  `confirm` uses rc=0 / rc=2 / rc=1 (yes / no / error) so shells can
  distinguish. `ask` / `choose` print the answer to stdout, nothing on
  cancel.
- 126 tests verde (eran 96): +19 in `test_backend_dialog.py` covering
  both backends; +11 in `test_cli.py` for the new dialog group.
- Smoke live OK: notify shows toast; kdialog window verified visible
  via `./voice windows find kdialog` (class `org.kde.kdialog`).
- ruff + mypy verde (51 source files).
- New skill: `_ai/SKILLS/build-dialog-backend.md`.

## Previously shipped (Phase B)

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

## Next concrete steps for the incoming agent

1. **Phase K (optional)** — OS-agnostic detection helpers:
   reemplazar UA-strings y heurísticas dispersas por
   ``platform_info`` consultable y testeable. No es bloqueante;
   el sistema funciona sin esto.
2. Posibles mejoras orgánicas no planificadas:
   - Memory: agregar ``memory_delete(ts)`` o ``memory_edit(ts, text)``
     si el agente alguna vez pide corregir notas (hoy no se puede
     deshacer una entrada salvo editando el ``.md`` a mano).
   - Memory: si el corpus crece >10 MB, evaluar índice FTS5 o
     ripgrep (ADR-0019 lo prevé).
   - Memory: tray entry "Ver memoria…" similar al audit viewer.

## Critical context to keep in your head

- `gh` autenticado como `Juancoll` (token con scopes
  `gist, read:org, repo, workflow`).
- Repo público: <https://github.com/Juancoll/voice-opencode>, branch
  `main`. Commits previos: `40a41db`, `9d231ac`, `69e82c2`, `63f0755`,
  `f95ad2f`, `30fc0cd` (Phase B), `6c53357` (Phase C), `f38c0dd`
  (Phase D), `f0de0b9` (Phase H), `f4f534d` (Phase I), + el
  commit Phase J que estás creando ahora.
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
- ADR-0013: Dialog backends shared exit-code contract (rc 0 → answer,
  rc 1 → cancel/None, other → BackendError); kdialog preferred,
  zenity fallback; notify as a separate Protocol; MCP tools translate
  Python `None` → `""` and `True`/`False` → `"yes"`/`"no"`.
- ADR-0014: Capacity modes filter MCP tools at *registration time*,
  not at invocation. Three monotonic tiers (read-only ⊂ assist ⊂
  full). `TIER_BY_TOOL` is the contract: unknown tools default to
  `full` (safe-by-default — forgotten entries hide, never leak).
  `_expose(cap, name)` combines capability + capacity into one gate.
  Reading `config.settings` dynamically (not at import) avoids a
  caching footgun.
- ADR-0017: `shell_run` policy — default-deny regex allowlist on
  `argv[0]` basename, `shell=False` always, post-parse rejection
  of shell metacharacters (`;|&` `` ` ``$<>) even on list input,
  `dry_run=True` default, 60 s hard timeout cap, 64 KB output
  truncation, audit records lengths not contents, tool lives in
  `full` tier only. Denylist + sanitisation + per-call dialog all
  rejected with rationale.
- ADR-0018: OCR backend stays pure (image-path → matches); MCP
  composes ``capture + ocr`` in ``screen_find_text`` and also
  exposes the pure backend as ``ocr_find_text_in_file``.
  Word-level granularity via Tesseract TSV (``--psm 6``,
  ``level=5`` rows); multi-word needles join consecutive
  same-line words and union their bboxes. Languages
  configurable via ``settings.ocr_languages`` (default
  ``("spa", "eng")``). Both tools live in ``read-only`` tier.
- ADR-0019: Agent memory is plain Markdown, one file per day
  (``memory/YYYY-MM-DD.md``). H2 header per entry
  (``## ts  [tags]``) + free-form body. Stdlib substring
  search (no DB / ripgrep). Four MCP tools: search/recent/
  list_days (read-only), append (assist). No capability gate
  — memory is text on our own disk, not a desktop capability.
  Body rejects lines starting with ``## `` and tags reject
  ``,`` / ``]`` at append time (loud fail > escape on parse).
  ``memory/`` is gitignored.

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
PYTHONPATH=src venv/bin/pytest -q            # should be 283 passing
venv/bin/ruff check src tests                # all clean
venv/bin/mypy src/voice_opencode             # no issues, 60 files
./voice platform info                        # confirm correct backend
./voice state | jq                           # what's the system doing right now
```

If any of those fail, fix them **before** starting Phase K.

## Files to read first when resuming

1. `AGENTS.md` — repo conventions.
2. `_ai/SESSION.md` — this file.
3. `_ai/CHANGELOG.md` — granular history newest first.
4. `_ai/ARCHITECTURE.md` — module map / dependency rules.
5. `_ai/DECISIONS.md` — ADRs.
6. `_ai/STATE.md` — host-specific environment snapshot.
7. `_ai/RUNBOOK.md` — operational recipes.
