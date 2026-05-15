"""
Capability flags.

A backend declares which abstract operations it actually supports by
returning a frozenset of these strings from its ``capabilities()``
method. The MCP server consults this set to decide which tools to
register, so the model never sees a tool that would always fail.

Keep the strings lowercase, snake_case, and **stable** — we use them in
config and (eventually) in the audit log, so renaming one is a breaking
change.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Window manager
# ---------------------------------------------------------------------------
WM_LIST_WINDOWS         = "wm.list_windows"
WM_FIND_WINDOWS         = "wm.find_windows"
WM_ACTIVE_WINDOW        = "wm.active_window"
WM_FOCUS_WINDOW         = "wm.focus_window"
WM_CLOSE_WINDOW         = "wm.close_window"
WM_MOVE_WINDOW          = "wm.move_window"
WM_RESIZE_WINDOW        = "wm.resize_window"
WM_TOGGLE_FLOATING      = "wm.toggle_floating"
WM_TOGGLE_FULLSCREEN    = "wm.toggle_fullscreen"
WM_MINIMIZE_WINDOW      = "wm.minimize_window"
WM_LIST_WORKSPACES      = "wm.list_workspaces"
WM_ACTIVE_WORKSPACE     = "wm.active_workspace"
WM_SWITCH_WORKSPACE     = "wm.switch_workspace"
WM_MOVE_TO_WORKSPACE    = "wm.move_window_to_workspace"
WM_SEND_WS_TO_MONITOR   = "wm.send_workspace_to_monitor"

# ---------------------------------------------------------------------------
# Input synthesis
# ---------------------------------------------------------------------------
INPUT_TYPE_TEXT         = "input.type_text"
INPUT_PRESS_KEY         = "input.press_key"
INPUT_MOVE_MOUSE        = "input.move_mouse"
INPUT_CLICK_MOUSE       = "input.click_mouse"
INPUT_SCROLL_MOUSE      = "input.scroll_mouse"
INPUT_DRAG_MOUSE        = "input.drag_mouse"

# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
SCREEN_LIST_MONITORS    = "screen.list_monitors"
SCREEN_CAPTURE_MONITOR  = "screen.capture_monitor"
SCREEN_CAPTURE_WINDOW   = "screen.capture_window"
SCREEN_CAPTURE_REGION   = "screen.capture_region"
SCREEN_CAPTURE_ALL      = "screen.capture_all"

# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------
CLIPBOARD_READ          = "clipboard.read"
CLIPBOARD_WRITE         = "clipboard.write"
CLIPBOARD_READ_PRIMARY  = "clipboard.read_primary"
CLIPBOARD_WRITE_PRIMARY = "clipboard.write_primary"

# ---------------------------------------------------------------------------
# Notifications & dialogs
# ---------------------------------------------------------------------------
NOTIFY_SHOW             = "notify.show"
DIALOG_CONFIRM          = "dialog.confirm"
DIALOG_ASK_TEXT         = "dialog.ask_text"
DIALOG_ASK_CHOICE       = "dialog.ask_choice"

# ---------------------------------------------------------------------------
# Audio / media
# ---------------------------------------------------------------------------
AUDIO_VOLUME_GET        = "audio.volume_get"
AUDIO_VOLUME_SET        = "audio.volume_set"
AUDIO_MUTE_TOGGLE       = "audio.mute_toggle"
AUDIO_MIC_MUTE_TOGGLE   = "audio.mic_mute_toggle"
MEDIA_PLAY_PAUSE        = "media.play_pause"
MEDIA_NEXT              = "media.next"
MEDIA_PREV              = "media.prev"
MEDIA_STATUS            = "media.status"

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
APP_LAUNCH              = "app.launch"
APP_LIST_INSTALLED      = "app.list_installed"
APP_LIST_RUNNING        = "app.list_running"
APP_KILL                = "app.kill"

# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
SHELL_RUN               = "shell.run"
