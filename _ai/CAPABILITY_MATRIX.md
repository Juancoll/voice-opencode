# Capability matrix

Status per platform × capability group. Updated whenever a backend lands.

**Legend:**
- ✅ implemented and tested
- ⚠ partial — see notes below the table
- ⛔ not implemented (Null backend in place — fails loudly)
- ➖ not applicable on this platform

Groups collapse multiple capability constants from
`platform/capabilities.py` for readability. The authoritative list per
backend is `<backend>.capabilities()`.

## Matrix

| Capability group | Linux/Hyprland | Linux/KDE-Wayland | Linux/wlroots (sway, river, …) | Linux/X11 | Linux/Generic | macOS | Windows |
|---|---|---|---|---|---|---|---|
| **wm** (windows, workspaces) | ✅ | ✅ | ⚠ (screen only, no wm IPC) | ✅ | ⛔ | ⛔ | ⛔ |
| **input** (kbd/mouse synth) | ✅ ydotool | ✅ ydotool | ✅ ydotool | ✅ ydotool | ✅ ydotool | ⛔ | ⛔ |
| **screen** (monitors, capture) | ✅ wlroots+grim | ✅ wlroots+grim | ✅ wlroots+grim | ✅ X11 | ⛔ | ⛔ | ⛔ |
| **clipboard** | ✅ wl-clipboard | ✅ wl-clipboard | ✅ wl-clipboard | ✅ xclip | ⛔ | ⛔ | ⛔ |
| **notify** | ✅ libnotify | ✅ libnotify | ✅ libnotify | ✅ libnotify | ✅ libnotify | ⛔ | ⛔ |
| **dialog** (confirm, ask, choice) | ✅ kdialog/gtk | ✅ kdialog | ✅ gtk/zenity | ✅ gtk/zenity | ✅ gtk/zenity | ⛔ | ⛔ |
| **audio** (vol, mute) | ✅ wpctl | ✅ wpctl | ✅ wpctl | ✅ wpctl | ✅ wpctl | ⛔ | ⛔ |
| **media** (MPRIS) | ✅ playerctl | ✅ playerctl | ✅ playerctl | ✅ playerctl | ✅ playerctl | ⛔ | ⛔ |
| **apps** (launch, list) | ✅ xdg | ✅ xdg | ✅ xdg | ✅ xdg | ✅ xdg | ⛔ | ⛔ |
| **shell** | ✅ posix | ✅ posix | ✅ posix | ✅ posix | ✅ posix | ⛔ | ⛔ |
| **ocr** | ✅ tesseract | ✅ tesseract | ✅ tesseract | ✅ tesseract | ✅ tesseract | ⛔ | ⛔ |
| **recorder** | ✅ arecord | ✅ arecord | ✅ arecord | ✅ arecord | ✅ arecord | ⛔ | ⛔ |
| **player** | ✅ paplay | ✅ paplay | ✅ paplay | ✅ paplay | ✅ paplay | ⛔ | ⛔ |
| **tts** | ✅ piper | ✅ piper | ✅ piper | ✅ piper | ✅ piper | ⛔ | ⛔ |
| **stt** | ✅ whisper.cpp | ✅ whisper.cpp | ✅ whisper.cpp | ✅ whisper.cpp | ✅ whisper.cpp | ⛔ | ⛔ |
| **logview** (tray "View logs") | ✅ terminal | ✅ terminal | ✅ terminal | ✅ terminal | ✅ terminal | ⛔ | ⛔ |
| **hotkey (F9 PTT)** ⁽¹⁾ | ✅ hyprland bind | ⚠ user must set up KWin shortcut | ⚠ user must set up sway bind | ⚠ user must set up xbindkeys | ⚠ manual | ⛔ | ⛔ |

⁽¹⁾ Hotkey is **not a `platform/` capability** — it's wired by the
shell/WM that launches `voice toggle`. The "✅" cells mean we ship a
documented config snippet for that WM (`~/.config/hypr/conf.d/voice.conf`).

## Notes

- **Linux/wlroots wm = ⚠.** We can capture monitors and synth input but
  don't enumerate or move windows on plain wlroots (no portable IPC
  across river / sway / dwl). When the active platform is detected as
  `linux-wlroots`, MCP tools that need `wm.list_windows` etc. are
  hidden from the model rather than failing at call time.
- **macOS / Windows = ⛔ across the board** today. Stubs exist at
  `backends/macos_stub/all.py` and `backends/windows_stub/all.py`; the
  pipeline can start on these OSes and emits "not implemented" via
  `NullBackend` for any capability the caller actually touches. See
  ADR-0031 for the rollout plan and `_ai/SKILLS/adding-a-backend.md`
  for the procedure.

## Hard dependencies (cross-OS)

| Dep | Linux | macOS | Windows |
|---|---|---|---|
| Python ≥ 3.11 | ✅ | ✅ Homebrew or python.org | ✅ python.org |
| PyQt6 (tray + HUD) | ✅ pip | ✅ pip | ✅ pip |
| whisper.cpp | ✅ binary | ⚠ build from source (Apple Silicon: Metal) | ⚠ build from source |
| piper | ✅ binary | ⚠ build or rosetta | ⚠ build |
| opencode | ✅ binary | ✅ binary | ⚠ TBD |

⚠ on whisper.cpp / piper means: build is straightforward but we
don't ship installers yet. ADR-0031 tracks who/when.
