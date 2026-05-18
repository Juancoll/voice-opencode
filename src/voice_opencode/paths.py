"""
Filesystem paths used everywhere in the project.

Single source of truth so other modules don't litter `Path(...)` calls.
Two roots:

* ``PROJECT_ROOT`` — the repo. Holds models, voices, icons, config.json.
* ``STATE_DIR``    — runtime state under ``$XDG_RUNTIME_DIR/voice-opencode``.
                     Ephemeral per session: PIDs, current pipeline state,
                     pause sentinel, last screenshot, opencode session id.

We resolve ``PROJECT_ROOT`` from this file's location: ``src/voice_opencode``
sits two levels under the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root: .../voice-opencode/
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# Repo subdirs (read-mostly, versioned)
LOGS_DIR: Path = PROJECT_ROOT / "logs"
MODELS_DIR: Path = PROJECT_ROOT / "models"
VOICES_DIR: Path = PROJECT_ROOT / "voices"
ICONS_DIR: Path = PROJECT_ROOT / "icons"
MEMORY_DIR: Path = PROJECT_ROOT / "memory"
CONFIG_FILE: Path = PROJECT_ROOT / "config.json"

# Runtime state (ephemeral, per user/session)
STATE_DIR: Path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "voice-opencode"
REC_PID_FILE: Path = STATE_DIR / "rec.pid"
REC_WAV_FILE: Path = STATE_DIR / "rec.wav"
SESSION_FILE: Path = STATE_DIR / "session.id"
SERVER_FILE: Path = STATE_DIR / "server.url"
PAUSE_FILE: Path = STATE_DIR / "paused"
STATE_FILE: Path = STATE_DIR / "state"
SCREENSHOT_FILE: Path = STATE_DIR / "screen.png"
AGENT_FILE: Path = STATE_DIR / "agent"          # presence = MCP agent has control
AGENT_LOG_FILE: Path = PROJECT_ROOT / "logs" / "agent.log"


def ensure_dirs() -> None:
    """Create runtime directories. Idempotent. Called from CLI startup."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
