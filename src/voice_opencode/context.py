"""
Per-turn contextual hints injected alongside the user prompt.

The voice pipeline already attaches a screenshot of the focused
monitor. The model, however, has no way of knowing *where* that
screenshot lives in the desktop coordinate space, nor that a second
monitor exists at all — pixels alone don't carry geometry. Without
that hint the model invents window coordinates, picks the wrong
monitor for ``move_window``, or refuses to act ("no sé en qué pantalla
estás").

This module produces a compact, deterministic text block describing
the current monitor layout. The pipeline prepends it to the user's
prompt as a separate ``text`` part so the model gets it for free on
every turn, without round-tripping through ``list_monitors`` first.

Design choices:

* **Cached with TTL.** Querying ``hyprctl monitors`` is cheap (~5ms)
  but we call it on every F9 press; a 60s cache covers normal usage
  and still picks up hotplug within a minute. Tests hit the
  underlying ``platform.screen`` directly so the cache is irrelevant.
* **Single-line per monitor.** Keeps the block under ~200 chars on
  typical 1-3 monitor setups, well below any prompt-size concerns.
* **Stable ordering.** Sorted by ``(x, y)`` so the model sees a
  consistent layout across turns even if the backend re-orders.
* **Best-effort.** If the screen backend can't enumerate monitors
  (null backend, hyprctl missing) we return an empty string and the
  pipeline simply skips the extra part. Never raises.
"""

from __future__ import annotations

import time

from . import platform as _platform
from .logging import log

# Cache TTL in seconds. Long enough to cover a burst of F9 presses,
# short enough that hotplugging a monitor (rare) is reflected within
# a minute without a manual reset.
_CACHE_TTL_S: float = 60.0

# Module-level cache. (text, expiry_monotonic).
_cache: tuple[str, float] | None = None


def _format_monitors(monitors: list[dict]) -> str:
    """Render a list of monitor dicts to a single human-readable block.

    Format::

        Monitor layout (desktop coordinates, pixels):
        - DP-3 2560x1440 @ (0,0) [FOCUSED]
        - DP-4 2560x1440 @ (2560,0)

    The screenshot attached this turn shows the FOCUSED monitor. Any
    other monitor listed is reachable via ``move_window`` /
    ``focus_window`` but is not visible in the attached image.
    """
    if not monitors:
        return ""

    # Stable order: left-to-right, top-to-bottom.
    ordered = sorted(
        monitors,
        key=lambda m: (m["rect"]["x"], m["rect"]["y"]),
    )
    lines = ["Monitor layout (desktop coordinates, pixels):"]
    for m in ordered:
        rect = m["rect"]
        tag = " [FOCUSED]" if m.get("focused") else ""
        lines.append(
            f"- {m['name']} {rect['w']}x{rect['h']} "
            f"@ ({rect['x']},{rect['y']}){tag}"
        )
    lines.append(
        "The attached screenshot shows the FOCUSED monitor only. "
        "Use these coordinates when positioning windows; do not invent "
        "values. Call list_monitors if you need fresher data."
    )
    return "\n".join(lines)


def monitor_layout_text(*, force_refresh: bool = False) -> str:
    """Return a cached, formatted description of the monitor layout.

    Returns an empty string if enumeration fails (no backend support,
    headless tests, hyprctl missing). Callers should treat ``""`` as
    "skip the extra context part" — do not inject a placeholder.
    """
    global _cache
    now = time.monotonic()
    if not force_refresh and _cache is not None and _cache[1] > now:
        return _cache[0]

    try:
        screen = _platform.screen
        mons = [m.to_dict() for m in screen.list_monitors()]
    except Exception as e:
        # Null backend raises NotImplementedError; hyprctl missing
        # raises BackendError. Either way we degrade silently — the
        # turn just won't carry layout context.
        log(f"context: monitor layout unavailable ({type(e).__name__}: {e})")
        _cache = ("", now + _CACHE_TTL_S)
        return ""

    text = _format_monitors(mons)
    _cache = (text, now + _CACHE_TTL_S)
    return text


def reset_cache() -> None:
    """Drop the cached layout. Used by tests and by ``config.reload``
    so an admin can force a refresh without restarting the tray."""
    global _cache
    _cache = None
