# Skill: add a backend for a new OS or window manager

> When to use: bringing macOS, Windows, sway, GNOME-Wayland, or any
> other platform online — partially or fully. Read ADR-0031 first
> (multi-OS scope + capability matrix).

## Pre-flight (read once before touching code)

1. **Read these in order.** `_ai/ARCHITECTURE.md` (module map),
   `_ai/DECISIONS.md` ADR-0031 (multi-OS roadmap),
   `_ai/CAPABILITY_MATRIX.md` (what's required / optional on your
   target). If any of those disagree with what you're about to do,
   stop and ask the user — don't paper over the disagreement in code.

2. **You will never have to refactor existing modules.** The platform
   layer is already capability-routed: `platform/__init__.py::_build`
   seeds every slot with a `NullBackend` and then **overwrites** the
   ones your platform supports. Pipeline modules (`pipeline.py`,
   `mcp_server.py`, `desktop.py`, …) consult `platform.<capability>`
   without knowing or caring which OS is underneath.

3. **No `if sys.platform == ...` outside `platform/`**. That's the
   line. The only files allowed to branch on OS identity are:
   - `platform/__init__.py` (detect + wire)
   - `paths.py` (XDG vs Library vs %APPDATA%)
   - `install.sh` / future per-OS installers
   Everything else routes through Protocols.

## What "adding a backend" means in this codebase

A backend is a class that satisfies one Protocol from
`platform/base.py` (e.g. `ScreenBackend`, `InputBackend`,
`ClipboardBackend`). You ship:

- One subpackage under `src/voice_opencode/backends/<os>_<capability>/`
  with `__init__.py` and one `*_backend.py` file.
- A registration line inside `platform/__init__.py::_build` (or in
  the wrapper `wire(out)` for macOS/Windows — see below).
- A unit test under `tests/test_backend_<name>.py`.

That's it. Three files. No edits to `pipeline.py`, `cli.py`,
`mcp_server.py`, or any other consumer.

## Steps (one capability at a time)

1. **Pick the smallest capability that gives the user something.**
   Order to bring up a new OS (proven in Linux work):
   `clipboard → notify → dialog → apps → screen → wm → input
    → audio → recorder → player → tts → stt → media → shell
    → ocr → logview`.
   Don't try to land them all in one PR. Each one is a green test
   before the next.

2. **Create the package.**
   ```
   src/voice_opencode/backends/<os>_<capability>/
     __init__.py        # empty
     <impl>_backend.py  # the class
   ```
   Naming convention examples:
   - `linux_clipboard_x11/xclip_backend.py`
   - `macos_clipboard/pbcopy_backend.py`
   - `windows_clipboard/winclip_backend.py`

3. **Implement the Protocol.** Copy the closest existing backend as a
   skeleton (look in `backends/linux_*` first). The Protocol the class
   must satisfy is in `platform/base.py`; the capability key it must
   declare in `capabilities()` is one of the constants in
   `platform/capabilities.py`. Tests will fail loudly if you mismatch.

4. **Probe dependencies in `__init__`.** If the backend shells out to
   a binary, `shutil.which(...)` it and raise
   `BackendError("<tool> not installed")` early. This is what the
   `_try(...)` helper in `platform/__init__.py` swallows so absence is
   silent in production but visible with `VOICE_DEBUG_BACKENDS=1`.

5. **Wire it.**
   - **Linux WMs:** edit `_build(plat)` in `platform/__init__.py`,
     adding an `elif plat == PLATFORM_LINUX_<your_wm>:` branch.
     Reuse `_wire_common_linux(out)` for the cross-WM Linux bits.
   - **macOS:** edit `backends/macos_stub/all.py::wire(out)` (the stub
     already exists and is currently a no-op). Add
     `out["<capability>"] = <YourBackend>()` guarded by `_try`.
     When macOS has > 4 backends, rename the package to
     `backends/macos/` and split `all.py` per capability.
   - **Windows:** same pattern in `backends/windows_stub/all.py`.

6. **Test.** Mirror `tests/test_backend_<existing>.py`. Mock
   subprocesses with `unittest.mock.patch`; never spawn real `osascript`
   / `pbcopy` / `powershell` in tests. CI runs on Linux only — your
   macOS/Windows test must be fully mockable.

7. **Update `_ai/CAPABILITY_MATRIX.md`.** Tick your capability ✓
   for your platform. If it's a partial implementation, mark it ⚠ and
   list the gap below the table.

8. **Update `_ai/STATE.md`** if you added a new system dependency.

## Cross-cutting concerns by OS

These are the things that don't fit the per-capability pattern. Read
them once per new OS:

### macOS
- **Hotkey (F9 PTT):** No Hyprland-style declarative bind. Use
  `pynput.keyboard.GlobalHotKeys` or `MASShortcut` (PyObjC). Both
  need Accessibility permission; surface a clear error if not
  granted, pointing to System Settings → Privacy.
- **Paths:** `XDG_RUNTIME_DIR` doesn't exist. Add a macOS branch in
  `paths.py` returning `~/Library/Application Support/voice-opencode`
  (state) and `~/Library/Caches/voice-opencode` (runtime + lockfile).
- **Audio:** `paplay` / `arecord` absent. Use `afplay` for playback;
  `sox -d` or `ffmpeg -f avfoundation` for capture.
- **Tray:** PyQt6 `QSystemTrayIcon` works native — no change needed.

### Windows
- **Hotkey:** `RegisterHotKey` via `pywin32`, or `keyboard` package
  if a license-clean alternative is acceptable. Investigate first.
- **Paths:** `%LOCALAPPDATA%\voice-opencode` for state,
  `%LOCALAPPDATA%\voice-opencode\runtime` for the lockfile (no /run).
- **Lockfile:** `pipeline._pipeline_lock` uses POSIX `O_CREAT|O_EXCL`
  which works on Windows, but the cancel path uses `SIGINT` to the
  holder PID. Replace with a named `Event` (`win32event`) or move
  to an asyncio cancel token. Decision deferred to ADR-0031.
- **Audio:** `winsound.PlaySound` for the simple cases; PortAudio
  (via `sounddevice`) for capture.
- **Tray:** PyQt6 native.

### New Linux WM (sway, GNOME-Wayland, river, …)
- Add a new `PLATFORM_LINUX_<WM>` constant in `platform/__init__.py`.
- Teach `detect_platform()` to recognise it (env var sniffing or
  `pgrep` of the compositor; copy the Hyprland detection).
- Most new Linux WMs reuse `linux_wlroots.screen`,
  `linux_input.ydotool`, `linux_clipboard_wayland.wlclip`,
  `common_piper`, `common_whisper_cpp`. The only thing typically
  unique per WM is `wm` (window manager IPC) and sometimes `input`
  if ydotool isn't preferred.

## Common mistakes to avoid

- **Importing the OS-specific module unconditionally.** Always
  lazy-import inside `_build` so a missing dep on a wrong OS doesn't
  poison the whole `platform` package.
- **Raising from `__init__` instead of from methods.** Raising in
  `__init__` is correct when the backing tool is missing. Raising
  from a method when the same backend was happy in `__init__` is a
  bug — the `_try` helper only catches `__init__`.
- **Adding `if sys.platform == "darwin"` to `pipeline.py`** or any
  other consumer. If you feel the urge, you're missing a Protocol
  method or a capability constant. Add it instead.
- **Skipping the test.** CI gates merge.

## When you've finished a capability

1. `PYTHONPATH=src venv/bin/pytest tests/test_backend_<name>.py`
2. `venv/bin/ruff check src tests`
3. `venv/bin/mypy src/voice_opencode`
4. Add a one-line entry to `_ai/CHANGELOG.md`.
5. Update `_ai/CAPABILITY_MATRIX.md` (table cell + any caveats).
6. If this brings a new OS to its first usable state, ping the user —
   don't just close the task. Smoke-testing on the target OS is
   manual.
