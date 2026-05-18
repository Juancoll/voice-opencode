"""
Platform detection and backend wiring.

Public API::

    from voice_opencode.platform import (
        wm, input, screen, clipboard, notify, dialog,
        audio, media, apps, shell,
        active_platform, supported,
    )

* ``active_platform`` is a string like ``"linux-hyprland"`` for logs / UI.
* ``supported(cap)`` returns True if any backend declares ``cap``.
* The module-level singletons (``wm``, ``input``, etc.) are always
  *something* — a real backend if available, a Null implementation
  otherwise. Consumers should call ``supported(...)`` before using a
  method to avoid ``NotSupportedError`` at runtime.

Detection order:

1. Explicit override via ``settings.platform_override`` (e.g.
   ``"linux-hyprland"``, ``"linux-x11"``, ``"linux-kde-wayland"``).
2. Auto-detect from environment:
   * ``HYPRLAND_INSTANCE_SIGNATURE`` ⇒ Hyprland.
   * ``XDG_SESSION_TYPE=wayland`` + ``XDG_CURRENT_DESKTOP~=KDE`` ⇒ KDE Wayland.
   * ``XDG_SESSION_TYPE=wayland`` ⇒ generic wlroots.
   * ``XDG_SESSION_TYPE=x11`` or ``DISPLAY`` set ⇒ X11.
   * ``sys.platform`` ``"darwin"`` / ``"win32"`` ⇒ stub for now.
3. Fallback to all-Null.

The wiring is intentionally lazy: the first import detects, builds the
backends, and caches them. Tests can call ``_reset_for_tests()`` to
force re-detection.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import capabilities as _cap_module
from . import null
from .base import (
    AppLauncher,
    AudioBackend,
    ClipboardBackend,
    DialogBackend,
    InputBackend,
    MediaBackend,
    NotifyBackend,
    OCRBackend,
    ScreenBackend,
    ShellBackend,
    WindowManager,
)
from .capabilities import *  # noqa: F401,F403  re-export capability constants
from .types import Monitor, OcrMatch, Rect, Window, Workspace  # noqa: F401  re-export

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
PLATFORM_LINUX_HYPRLAND     = "linux-hyprland"
PLATFORM_LINUX_KDE_WAYLAND  = "linux-kde-wayland"
PLATFORM_LINUX_WLROOTS      = "linux-wlroots"
PLATFORM_LINUX_X11          = "linux-x11"
PLATFORM_LINUX_GENERIC      = "linux-generic"
PLATFORM_MACOS              = "macos"
PLATFORM_WINDOWS            = "windows"
PLATFORM_UNKNOWN            = "unknown"

_KNOWN_PLATFORMS = frozenset(
    {
        PLATFORM_LINUX_HYPRLAND, PLATFORM_LINUX_KDE_WAYLAND,
        PLATFORM_LINUX_WLROOTS, PLATFORM_LINUX_X11, PLATFORM_LINUX_GENERIC,
        PLATFORM_MACOS, PLATFORM_WINDOWS,
    }
)


def detect_platform(env: dict[str, str] | None = None) -> str:
    """Return one of the ``PLATFORM_*`` constants based on env vars.

    ``env`` defaults to ``os.environ``; tests inject a dict.
    """
    e = dict(env) if env is not None else dict(os.environ)
    if sys.platform == "darwin":
        return PLATFORM_MACOS
    if sys.platform.startswith("win"):
        return PLATFORM_WINDOWS
    if e.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return PLATFORM_LINUX_HYPRLAND
    session = e.get("XDG_SESSION_TYPE", "").lower()
    desktop = e.get("XDG_CURRENT_DESKTOP", "").lower()
    if session == "wayland":
        if "kde" in desktop or "plasma" in desktop:
            return PLATFORM_LINUX_KDE_WAYLAND
        return PLATFORM_LINUX_WLROOTS
    if session == "x11" or e.get("DISPLAY"):
        return PLATFORM_LINUX_X11
    if sys.platform.startswith("linux"):
        return PLATFORM_LINUX_GENERIC
    return PLATFORM_UNKNOWN


# ---------------------------------------------------------------------------
# Backend factories — lazy imports so missing optional deps don't crash
# ---------------------------------------------------------------------------
def _try(factory: Any, name: str) -> Any | None:
    """Run ``factory()``; on any exception, log once at debug volume.

    A backend being absent is normal (e.g. kdialog on a GTK box). We
    only log when ``VOICE_DEBUG_BACKENDS=1`` so CLI output stays clean.
    """
    try:
        return factory()
    except Exception as exc:  # pragma: no cover — defensive
        if os.environ.get("VOICE_DEBUG_BACKENDS") == "1":
            from ..logging import log
            log(f"backend {name} unavailable: {exc}")
        return None


def _build(plat: str) -> dict[str, Any]:
    """Build the concrete backend wiring dict for ``plat``."""
    out: dict[str, Any] = {
        "wm":        null.NullWindowManager(),
        "input":     null.NullInputBackend(),
        "screen":    null.NullScreenBackend(),
        "clipboard": null.NullClipboardBackend(),
        "notify":    null.NullNotifyBackend(),
        "dialog":    null.NullDialogBackend(),
        "audio":     null.NullAudioBackend(),
        "media":     null.NullMediaBackend(),
        "apps":      null.NullAppLauncher(),
        "shell":     null.NullShellBackend(),
        "ocr":       null.NullOCRBackend(),
    }
    if plat == PLATFORM_LINUX_HYPRLAND:
        from ..backends.linux_hyprland import wm as hypr_wm
        if (b := _try(hypr_wm.HyprlandWindowManager, "linux_hyprland.wm")):
            out["wm"] = b
        from ..backends.linux_wlroots import screen as wlr_screen
        if (b := _try(wlr_screen.WlrootsScreenBackend, "linux_wlroots.screen")):
            out["screen"] = b
        _wire_common_linux(out)
    elif plat == PLATFORM_LINUX_KDE_WAYLAND:
        from ..backends.linux_kde_wayland import wm as kde_wm
        if (b := _try(kde_wm.KdeWaylandWindowManager, "linux_kde_wayland.wm")):
            out["wm"] = b
        from ..backends.linux_wlroots import screen as wlr_screen
        if (b := _try(wlr_screen.WlrootsScreenBackend, "linux_wlroots.screen")):
            out["screen"] = b
        _wire_common_linux(out)
    elif plat == PLATFORM_LINUX_WLROOTS:
        from ..backends.linux_wlroots import screen as wlr_screen
        if (b := _try(wlr_screen.WlrootsScreenBackend, "linux_wlroots.screen")):
            out["screen"] = b
        _wire_common_linux(out)
    elif plat == PLATFORM_LINUX_X11:
        from ..backends.linux_x11 import wm as x11_wm
        if (b := _try(x11_wm.X11WindowManager, "linux_x11.wm")):
            out["wm"] = b
        from ..backends.linux_x11 import screen as x11_screen
        if (b := _try(x11_screen.X11ScreenBackend, "linux_x11.screen")):
            out["screen"] = b
        from ..backends.linux_clipboard_x11 import xclip_backend
        if (b := _try(xclip_backend.XclipClipboardBackend, "linux_clipboard_x11")):
            out["clipboard"] = b
        _wire_common_linux(out, prefer_wayland_clipboard=False)
    elif plat == PLATFORM_LINUX_GENERIC:
        _wire_common_linux(out)
    elif plat == PLATFORM_MACOS:
        from ..backends.macos_stub import all as macos_all
        macos_all.wire(out)
    elif plat == PLATFORM_WINDOWS:
        from ..backends.windows_stub import all as windows_all
        windows_all.wire(out)
    return out


def _wire_common_linux(
    out: dict[str, Any], prefer_wayland_clipboard: bool = True,
) -> None:
    """Backends that work on any modern Linux desktop."""
    from ..backends.linux_input import ydotool_backend
    if (b := _try(ydotool_backend.YdotoolInputBackend, "linux_input.ydotool")):
        out["input"] = b
    if prefer_wayland_clipboard:
        from ..backends.linux_clipboard_wayland import wlclip_backend
        if (b := _try(wlclip_backend.WlClipboardBackend, "linux_clipboard_wayland")):
            out["clipboard"] = b
    from ..backends.linux_audio_pipewire import playerctl_backend, wpctl_backend
    if (b := _try(wpctl_backend.WpctlAudioBackend, "linux_audio_pipewire.wpctl")):
        out["audio"] = b
    if (b := _try(playerctl_backend.PlayerctlMediaBackend,
                  "linux_audio_pipewire.playerctl")):
        out["media"] = b
    from ..backends.linux_apps_xdg import xdg_backend
    if (b := _try(xdg_backend.XdgAppLauncher, "linux_apps_xdg")):
        out["apps"] = b
    from ..backends.linux_shell_posix import shell_backend
    if (b := _try(shell_backend.PosixShellBackend, "linux_shell_posix")):
        out["shell"] = b
    from ..backends.linux_ocr_tesseract import tesseract_backend
    if (b := _try(tesseract_backend.TesseractOCRBackend, "linux_ocr_tesseract")):
        out["ocr"] = b
    # Notify: works everywhere with libnotify.
    from ..backends.linux_dialog_kde import knotify_backend
    if (b := _try(knotify_backend.LibnotifyBackend, "linux_dialog_kde.notify")):
        out["notify"] = b
    # Dialog: kdialog by default, fall back to zenity.
    from ..backends.linux_dialog_kde import kdialog_backend
    if (b := _try(kdialog_backend.KdialogBackend, "linux_dialog_kde")):
        out["dialog"] = b
    else:
        from ..backends.linux_dialog_gtk import zenity_backend
        if (b := _try(zenity_backend.ZenityBackend, "linux_dialog_gtk")):
            out["dialog"] = b


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {"platform": "", "backends": {}}


def _ensure_loaded() -> None:
    if _state["backends"]:
        return
    # Lazy import of settings to avoid bootstrap cycles.
    try:
        from ..config import settings as _settings
        override = (_settings.platform_override or "").strip()
    except Exception:
        override = ""
    plat = override if override in _KNOWN_PLATFORMS else detect_platform()
    _state["platform"] = plat
    _state["backends"] = _build(plat)


def __getattr__(name: str) -> Any:
    """Module-level dynamic attribute resolution.

    Lets us do ``from voice_opencode.platform import wm`` and have
    detection happen on first access instead of at import time.
    """
    if name in {
        "wm", "input", "screen", "clipboard", "notify", "dialog",
        "audio", "media", "apps", "shell", "ocr",
    }:
        _ensure_loaded()
        return _state["backends"][name]
    if name == "active_platform":
        _ensure_loaded()
        return _state["platform"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def supported(cap: str) -> bool:
    """True if any wired backend declares ``cap`` in its capabilities."""
    _ensure_loaded()
    return any(cap in b.capabilities() for b in _state["backends"].values())


def all_capabilities() -> frozenset[str]:
    """Union of capabilities across every wired backend. Useful for diagnostics."""
    _ensure_loaded()
    out: set[str] = set()
    for b in _state["backends"].values():
        out |= set(b.capabilities())
    return frozenset(out)


def _reset_for_tests() -> None:
    """Drop cached detection so the next access re-runs ``_build``."""
    _state["platform"] = ""
    _state["backends"] = {}


# Re-export Protocols so ``from voice_opencode.platform import WindowManager`` works.
__all__ = [
    "WindowManager", "InputBackend", "ScreenBackend", "ClipboardBackend",
    "NotifyBackend", "DialogBackend", "AudioBackend", "MediaBackend",
    "AppLauncher", "ShellBackend", "OCRBackend",
    "Window", "Workspace", "Monitor", "Rect", "OcrMatch",
    "active_platform", "supported", "all_capabilities", "detect_platform",  # noqa: F405
    "PLATFORM_LINUX_HYPRLAND", "PLATFORM_LINUX_KDE_WAYLAND",
    "PLATFORM_LINUX_WLROOTS", "PLATFORM_LINUX_X11",
    "PLATFORM_LINUX_GENERIC", "PLATFORM_MACOS", "PLATFORM_WINDOWS",
    "PLATFORM_UNKNOWN",
] + [n for n in dir(_cap_module) if n.isupper()]
