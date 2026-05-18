"""
Capacity modes: filter which MCP tools the agent can see.

Three tiers:

* ``read-only``: only tools that read system state (plus ``notify`` and
  ``sleep_ms``). The model can describe and observe but cannot act.
* ``assist`` (default): read-only + reversible / confirmable actions.
  No destructive tools.
* ``full``: assist + destructive tools (``close_window`` today,
  ``run_shell`` once Phase E lands).

The tier is read from ``settings.capacity_mode`` (in turn overridable
via ``$VOICE_CAPACITY_MODE``). Anything outside the three known names
falls back to ``assist`` with a warning — fail-open on the side of the
existing default, not surprise-locked.

The mapping is **the contract**. Adding a new MCP tool means updating
``TIER_BY_TOOL`` here in the same change, or the registrar in
``mcp_server.py`` will refuse to expose it (defensive default:
unknown → ``full``, so a forgotten entry hides the tool from the two
restricted modes rather than silently leaking it everywhere).
"""

from __future__ import annotations

from typing import Final, Literal

from . import config
from .logging import log

Tier = Literal["read-only", "assist", "full"]

_TIERS: Final[tuple[Tier, ...]] = ("read-only", "assist", "full")
_TIER_RANK: Final[dict[str, int]] = {t: i for i, t in enumerate(_TIERS)}

# ---------------------------------------------------------------------------
# Tool → minimum tier required to expose it
# ---------------------------------------------------------------------------
# When you add a new MCP tool, add it here. Unknown tools default to
# ``full`` (see ``allows()``), which is safe-by-default for the two
# restricted modes.
TIER_BY_TOOL: Final[dict[str, Tier]] = {
    # --- read-only ---
    "list_monitors":     "read-only",
    "list_windows":      "read-only",
    "find_windows":      "read-only",
    "focused_window":    "read-only",
    "list_workspaces":   "read-only",
    "active_workspace":  "read-only",
    "capture_screen":    "read-only",
    "clipboard_read":    "read-only",
    "notify":            "read-only",  # output-only, not destructive
    "sleep_ms":          "read-only",
    "platform_info":     "read-only",
    "audio_get_volume":  "read-only",
    "media_status":      "read-only",
    "apps_list_installed": "read-only",
    "apps_list_running":   "read-only",
    "screen_find_text":    "read-only",
    "ocr_find_text_in_file": "read-only",
    "memory_search":       "read-only",
    "memory_recent":       "read-only",
    "memory_list_days":    "read-only",
    # --- assist (reversible / interactive) ---
    "type_text":                 "assist",
    "press_key":                 "assist",
    "move_mouse":                "assist",
    "click_mouse":               "assist",
    "scroll_mouse":              "assist",
    "focus_window":              "assist",
    "move_window":               "assist",
    "resize_window":             "assist",
    "toggle_floating":           "assist",
    "toggle_fullscreen":         "assist",
    "switch_workspace":          "assist",
    "move_window_to_workspace":  "assist",
    "send_workspace_to_monitor": "assist",
    "clipboard_write":           "assist",
    "ask_confirm":               "assist",
    "ask_user":                  "assist",
    "ask_choice":                "assist",
    "audio_set_volume":          "assist",
    "audio_mute_toggle":         "assist",
    "audio_mic_mute_toggle":     "assist",
    "media_play_pause":          "assist",
    "media_next":                "assist",
    "media_prev":                "assist",
    "apps_launch":               "assist",
    "memory_append":             "assist",
    # --- full (irreversible) ---
    "close_window":              "full",
    "apps_kill":                 "full",
    "shell_run":                 "full",
    "memory_delete":             "full",
    "memory_edit":               "full",
}


def _normalise(raw: str) -> Tier:
    """Coerce a capacity_mode string to a known tier (warn on unknown)."""
    if raw in _TIER_RANK:
        return raw  # type: ignore[return-value]
    log(f"capacity: unknown mode {raw!r}, falling back to 'assist'.")
    return "assist"


def current_mode() -> Tier:
    """Return the active capacity tier from ``settings``."""
    return _normalise(config.settings.capacity_mode)


def allows(tool: str, mode: Tier | None = None) -> bool:
    """Return True iff ``tool`` is exposed under ``mode`` (or current)."""
    active = mode if mode is not None else current_mode()
    required: Tier = TIER_BY_TOOL.get(tool, "full")  # unknown ⇒ most restrictive
    return _TIER_RANK[active] >= _TIER_RANK[required]


def tools_for(mode: Tier | None = None) -> frozenset[str]:
    """Return the set of tool names exposed under ``mode`` (or current)."""
    active = mode if mode is not None else current_mode()
    return frozenset(t for t in TIER_BY_TOOL if allows(t, active))
