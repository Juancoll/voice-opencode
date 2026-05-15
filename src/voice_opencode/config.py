"""
Configuration: dataclass + JSON file + env-var override.

Resolution order, highest priority first:

    1. Environment variable (handy for one-shot tests)
    2. ``config.json`` next to the project root
    3. Hard-coded defaults in ``DEFAULTS``

Access pattern::

    from voice_opencode.config import settings
    print(settings.voice, settings.opencode_port)

The ``settings`` object is a frozen dataclass instance built once on import.
If you change ``config.json`` at runtime, call ``reload()`` to refresh.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from typing import Any, Final

from .paths import CONFIG_FILE


# ---------------------------------------------------------------------------
# Defaults — also act as the schema (key → type)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    voice: str = "es_AR-daniela-high"
    speaker_id: int = 0
    whisper_model: str = "ggml-small.bin"
    whisper_lang: str = "es"
    keep_context: bool = True
    screenshot: bool = True
    screenshot_scope: str = "monitor"   # "monitor" | "all"
    notify: bool = True
    opencode_host: str = "127.0.0.1"
    opencode_port: int = 4096
    # Force a specific platform/backend wiring. Empty = auto-detect.
    # See voice_opencode.platform for accepted values
    # (e.g. "linux-hyprland", "linux-x11", "linux-kde-wayland").
    platform_override: str = ""
    # Capacity mode for the MCP agent: "read-only" | "assist" | "full".
    # Used by Phase D (capacity_modes) to filter exposed tools.
    capacity_mode: str = "assist"

    @property
    def opencode_url(self) -> str:
        return f"http://{self.opencode_host}:{self.opencode_port}"


DEFAULTS: Final[Settings] = Settings()

# Mapping of setting name → environment variable that overrides it.
ENV_MAP: Final[dict[str, str]] = {
    "voice": "VOICE",
    "speaker_id": "VOICE_SPEAKER",
    "whisper_model": "WHISPER_MODEL",
    "whisper_lang": "WHISPER_LANG",
    "keep_context": "VOICE_KEEP_CONTEXT",
    "screenshot": "VOICE_SCREENSHOT",
    "screenshot_scope": "VOICE_SCREENSHOT_SCOPE",
    "notify": "VOICE_NOTIFY",
    "opencode_host": "OPENCODE_HOST",
    "opencode_port": "OPENCODE_PORT",
    "platform_override": "VOICE_PLATFORM",
    "capacity_mode": "VOICE_CAPACITY_MODE",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _coerce(raw: Any, default: Any, key: str = "") -> Any:
    """Cast a value (str|bool|int) to the type of ``default``."""
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Config key {key!r} must be an integer (got {raw!r})"
            ) from e
    return raw  # str passthrough


def _load_user_json() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"[voice-opencode] Bad config.json, ignoring: {e}", file=sys.stderr)
        return {}


def load() -> Settings:
    """Resolve env > json > defaults, return a frozen ``Settings``."""
    user = _load_user_json()
    values: dict[str, Any] = {}
    for f in fields(Settings):
        default = getattr(DEFAULTS, f.name)
        env_name = ENV_MAP.get(f.name)
        if env_name and env_name in os.environ:
            values[f.name] = _coerce(os.environ[env_name], default, f.name)
        elif f.name in user:
            values[f.name] = _coerce(user[f.name], default, f.name)
        else:
            values[f.name] = default
    return Settings(**values)


# Public singleton — most callers just `from .config import settings`.
settings: Settings = load()


def reload() -> Settings:
    """Re-read config.json and env. Returns the fresh ``Settings``."""
    global settings
    settings = load()
    return settings


# ---------------------------------------------------------------------------
# Mutators (only operation: persist to config.json)
# ---------------------------------------------------------------------------
def write_defaults() -> None:
    """Create ``config.json`` from ``DEFAULTS`` if it doesn't exist."""
    if CONFIG_FILE.exists():
        raise FileExistsError(f"Refusing to overwrite {CONFIG_FILE}")
    CONFIG_FILE.write_text(
        json.dumps(asdict(DEFAULTS), indent=2, ensure_ascii=False) + "\n"
    )


def set_value(key: str, raw_value: str) -> Any:
    """
    Persist ``key=raw_value`` into ``config.json``. Casts ``raw_value`` to
    the type of the default. Returns the parsed value.
    Reloads ``settings`` afterwards.
    """
    if not hasattr(DEFAULTS, key):
        raise KeyError(f"Unknown config key: {key}")
    parsed = _coerce(raw_value, getattr(DEFAULTS, key), key)
    current = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    current[key] = parsed
    CONFIG_FILE.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n"
    )
    reload()
    return parsed


def as_dict() -> dict[str, Any]:
    """Return effective settings as a plain dict (JSON-friendly)."""
    return asdict(settings)
