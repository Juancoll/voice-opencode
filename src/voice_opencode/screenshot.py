"""
Screenshot capture — backwards-compatible shim over ``platform.screen``.

The pipeline and CLI still import ``screenshot.capture()`` and
``screenshot.capture_to(path, scope=...)``. This module preserves
those signatures while delegating to the active screen backend.

HUD avoidance (ADR-0028): the per-turn HUD widget is a floating,
pinned overlay that is *always on top* during ``thinking`` and
``speaking``. If we let grim fire while the HUD is on screen it
ends up in the PNG, the model OCRs "Pensando…" and either reads
it back to the user or treats it as part of the user's context.
``capture()`` therefore moves the HUD off-screen for the duration
of the grim call and restores it immediately afterwards. The move
is best-effort via ``hyprctl dispatch`` — on X11 / other compositors
this is a no-op and we accept that the HUD may appear in the shot.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from . import platform as _plat
from .config import settings
from .logging import log
from .paths import SCREENSHOT_FILE
from .platform.base import BackendError, NotSupportedError

# Single source of truth for the HUD window title — must match
# ``hud.TurnHUD``'s ``setWindowTitle("voice-opencode-hud")``.
_HUD_TITLE_SEL = "title:voice-opencode-hud"
# Far-off coords; -99999 is plenty for any sane multi-monitor setup
# and keeps the window away from any visible region.
_HUD_PARK_X, _HUD_PARK_Y = -99999, -99999


def _hyprctl_dispatch(cmd: str, arg: str) -> bool:
    """Best-effort ``hyprctl dispatch`` returning True on rc=0."""
    if not shutil.which("hyprctl"):
        return False
    try:
        r = subprocess.run(
            ["hyprctl", "dispatch", cmd, arg],
            check=False, timeout=1.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@contextmanager
def _hud_offscreen():
    """Move the HUD off-screen for the body of the ``with`` block.

    Pure side-effect helper — yields nothing. We do NOT restore the
    HUD's original geometry here: the pipeline always issues another
    ``turn_update(...)`` immediately after ``capture()`` (either
    "🔊 Respondiendo" on success or "❌ opencode" on failure), and
    every ``turn_update`` re-runs ``TurnHUD._apply_hyprland_rules``
    which re-pins the widget into the bottom-right of the active
    monitor. Restoring twice would just race that mechanism.

    Best-effort. On any compositor without ``hyprctl`` this is a
    silent no-op — the captured shot will include the HUD pixels,
    same as it did before this helper existed.
    """
    moved = _hyprctl_dispatch(
        "movewindowpixel",
        f"exact {_HUD_PARK_X} {_HUD_PARK_Y},{_HUD_TITLE_SEL}",
    )
    if moved:
        # 30ms is enough for Hyprland to commit the new position
        # before grim sweeps the framebuffer; tested visually.
        time.sleep(0.03)
    yield


def capture_to(out_path: Path, scope: str = "monitor") -> Path | None:
    """
    Take a screenshot to ``out_path``.

    scope ∈ {"monitor", "window", "all"}. Returns the path on success,
    ``None`` on failure (logged).
    """
    try:
        if scope == "monitor":
            return _plat.screen.capture_monitor(out_path)
        if scope == "window":
            return _plat.screen.capture_window(out_path)
        if scope == "all":
            return _plat.screen.capture_all(out_path)
        log(f"unknown screenshot scope: {scope!r}")
        return None
    except (BackendError, NotSupportedError) as e:
        log(f"screenshot: {e}")
        return None


def capture() -> Path | None:
    """Pipeline entry-point. Honours ``settings.screenshot``."""
    if not settings.screenshot:
        return None
    with _hud_offscreen():
        path = capture_to(SCREENSHOT_FILE, scope=settings.screenshot_scope)
    if path:
        log(f"Screenshot captured ({path.stat().st_size} bytes, "
            f"scope={settings.screenshot_scope}).")
    return path


def to_data_url(path: Path) -> str:
    """Encode a PNG as a ``data:`` URL for the opencode JSON payload."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"
