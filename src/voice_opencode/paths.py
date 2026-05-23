"""
Filesystem paths used everywhere in the project.

Single source of truth so other modules don't litter ``Path(...)``
calls.

Three categories:

1. **Repo paths** (``PROJECT_ROOT``, ``MODELS_DIR``, ``VOICES_DIR``,
   ``ICONS_DIR``, ``MEMORY_DIR``, ``CONFIG_FILE``, ``LOGS_DIR``) —
   versioned content shipped with the source tree. Resolved relative
   to this file (``src/voice_opencode/`` is two levels under the
   repo root) and identical on every OS.

2. **Runtime state** (``STATE_DIR`` and its derived files) —
   ephemeral per session: PIDs, current pipeline state, sentinels,
   last screenshot, lockfiles. Lives in OS-appropriate locations:

   - Linux: ``$XDG_RUNTIME_DIR/voice-opencode`` (tmpfs, cleared on
     logout). Falls back to ``/tmp/voice-opencode`` if the variable
     is unset (e.g. running outside a desktop session).
   - Windows: ``%LOCALAPPDATA%\\voice-opencode\\runtime`` (machine-
     local, never roams). No tmpfs on Windows; this is the closest
     equivalent.

3. **User config** (``CONFIG_DIR``, used by ``CONFIG_FILE``) —
   the repo's own ``config.json`` *today*, but the cross-platform
   helper ``config_dir()`` is exposed for future Phase B/C work
   that will move user settings out of the repo to follow XDG /
   Windows conventions. Until then ``CONFIG_FILE`` is kept where
   it is to avoid breaking existing installs.

The three ``*_dir()`` helpers (``runtime_dir``, ``state_dir``,
``config_dir``) are the public Phase A.6 surface; module-level
constants stay as compatibility shims so the 79+ existing call
sites need no edits.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "voice-opencode"


# ---------------------------------------------------------------------------
# Cross-platform directory helpers
# ---------------------------------------------------------------------------
def _windows_localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))


def _windows_appdata() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))


def runtime_dir() -> Path:
    """Ephemeral, per-session state. Cleared on logout where the OS
    cooperates. Linux: tmpfs (``$XDG_RUNTIME_DIR``); Windows:
    ``%LOCALAPPDATA%\\voice-opencode\\runtime``.
    """
    if sys.platform == "win32":
        return _windows_localappdata() / _APP_NAME / "runtime"
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / _APP_NAME


def state_dir() -> Path:
    """Persistent state that survives logout but doesn't belong in
    config. Currently equal to ``runtime_dir()`` on both platforms —
    callers that conceptually want "persistent" should use this name
    so we can split them later without an API break.
    """
    if sys.platform == "win32":
        return _windows_localappdata() / _APP_NAME / "state"
    # Linux: $XDG_STATE_HOME/voice-opencode, default ~/.local/state.
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return base / _APP_NAME


def config_dir() -> Path:
    """User-editable configuration directory. Not yet used by the
    code (``CONFIG_FILE`` still points at the repo for backward
    compatibility) but exposed now so Phase B/C migrations can move
    user settings out of the source tree without an API churn.
    """
    if sys.platform == "win32":
        return _windows_appdata() / _APP_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / _APP_NAME


# ---------------------------------------------------------------------------
# Repo paths — versioned, identical on every OS
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

LOGS_DIR: Path = PROJECT_ROOT / "logs"
MODELS_DIR: Path = PROJECT_ROOT / "models"
VOICES_DIR: Path = PROJECT_ROOT / "voices"
ICONS_DIR: Path = PROJECT_ROOT / "icons"
MEMORY_DIR: Path = PROJECT_ROOT / "memory"
CONFIG_FILE: Path = PROJECT_ROOT / "config.json"


# ---------------------------------------------------------------------------
# Runtime state — derived from runtime_dir() so OS switch happens once
# ---------------------------------------------------------------------------
STATE_DIR: Path = runtime_dir()
REC_PID_FILE: Path = STATE_DIR / "rec.pid"
REC_WAV_FILE: Path = STATE_DIR / "rec.wav"
SESSION_FILE: Path = STATE_DIR / "session.id"
SERVER_FILE: Path = STATE_DIR / "server.url"
PAUSE_FILE: Path = STATE_DIR / "paused"
STATE_FILE: Path = STATE_DIR / "state"
SCREENSHOT_FILE: Path = STATE_DIR / "screen.png"
AGENT_FILE: Path = STATE_DIR / "agent"          # presence = MCP agent has control
AGENT_LOG_FILE: Path = PROJECT_ROOT / "logs" / "agent.log"
# Cross-process lock for the F9 pipeline. Each Hyprland bind invocation
# is its own Python process so a threading.Lock would be useless; we
# fcntl.flock() this file instead (Windows: msvcrt.locking, Phase C).
# Kernel releases on process death either way.
PIPELINE_LOCK_FILE: Path = STATE_DIR / "pipeline.lock"
DICTATION_FOCUS_FILE: Path = STATE_DIR / "dictation.focus"
DICTATION_WATCHDOG_PID_FILE: Path = STATE_DIR / "dictation.watchdog.pid"


def ensure_dirs() -> None:
    """Create runtime directories. Idempotent. Called from CLI startup."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
