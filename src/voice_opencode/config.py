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
    # LLM backend selection (ADR-0029). Known values:
    #   "opencode" — local ``opencode serve`` HTTP API (default).
    # Future backends (claude code CLI, ollama, hermes, …) plug in via
    # ``voice_opencode.llm.get_backend``. Selection is config-only:
    # changing this requires restarting the tray + ``opencode-serve``.
    llm_backend: str = "opencode"
    # Force a specific platform/backend wiring. Empty = auto-detect.
    # See voice_opencode.platform for accepted values
    # (e.g. "linux-hyprland", "linux-x11", "linux-kde-wayland").
    platform_override: str = ""
    # Capacity mode for the MCP agent: "read-only" | "assist" | "full".
    # Used by Phase D (capacity_modes) to filter exposed tools.
    capacity_mode: str = "assist"
    # Shell allowlist (Phase E): default-deny + explicit regex match on
    # argv[0]. Each entry is a Python regex that the *basename* of the
    # command must fully match (re.fullmatch). Empty tuple = no command
    # is ever allowed (which is the safest possible default — opt in by
    # editing config.json). The shell tool also lives in `full` tier
    # only, so even with this list populated the model can't reach it
    # in read-only or assist.
    shell_allowlist: tuple[str, ...] = (
        r"ls", r"cat", r"head", r"tail", r"wc",
        r"rg", r"grep", r"find", r"file", r"stat",
        r"jq", r"yq",
        r"git", r"hg",
        r"echo", r"true", r"false", r"date", r"pwd", r"whoami",
        r"python3?", r"node",
    )
    shell_timeout_s: float = 10.0
    # OCR (Phase F): Tesseract languages tried together via "spa+eng"
    # syntax; install matching `tesseract-data-*` packages first. The
    # min-confidence filter (0-100 Tesseract scale) drops noisy words
    # before substring matching so search results stay precise.
    ocr_languages: tuple[str, ...] = ("spa", "eng")
    ocr_min_confidence: float = 50.0
    # HUD placement on the active monitor. ``hud_corner`` ∈
    # {"top-left", "top-right", "bottom-left", "bottom-right"}.
    # ``hud_margin`` is the gap in pixels from the chosen corner.
    # The default (7) matches Hyprland's ``gaps_out=5`` + ``border_size=2``
    # so the HUD aligns visually with neighbouring tiled windows.
    hud_corner: str = "bottom-left"
    hud_margin: int = 7
    # Inject auto-discovered host context (monitor layout, OS +
    # binary versions, default audio devices) into every voice turn
    # that carries a screenshot. Lets the model position windows and
    # phrase responses with real values instead of guessing from
    # pixels. Each section degrades silently when its probe fails
    # (e.g. headless test, no wpctl). Disable to test bare-bones
    # behaviour. Name kept as ``attach_monitor_layout`` for backward
    # compatibility with existing ``config.json`` files.
    attach_monitor_layout: bool = True
    # System prompt prepended to every voice turn. The default biases
    # the assistant toward short spoken answers — the reply goes
    # through Piper TTS, and long paragraphs become unbearable to
    # listen to. Override in config.json to taste; set to empty
    # string to disable.
    voice_system_prompt: str = (
        "Estás respondiendo por voz al usuario: tu respuesta de texto "
        "se sintetiza con TTS y se reproduce. Reglas de estilo: "
        "(1) sé breve por defecto, una o dos frases; "
        "(2) sin markdown, sin listas, sin bloques de código a menos "
        "que sean cortos e imprescindibles; "
        "(3) habla en español neutro. "
        "Importante: estas reglas son SOLO sobre cómo redactas el "
        "mensaje final. NO te impiden usar las herramientas MCP "
        "disponibles (control de escritorio, ventanas, apps, etc.). "
        "Si el usuario te pide hacer algo, hazlo con las herramientas "
        "y después confirma brevemente con voz."
    )
    # Dictation mode (Ctrl+F9 by default): the recording is transcribed
    # by whisper and injected at the cursor without going through the
    # LLM, TTS or opencode session. Two injection strategies:
    #   "paste" — write to the clipboard then send ctrl+v. Instant for
    #             any length but clobbers the clipboard. DEFAULT because
    #             ``type`` drops characters on long inputs (ydotool
    #             throughput is ~60 chars/s and the receiving app may
    #             miss events under load) — see 2026-05-21 incident
    #             where a 1180-char transcript took 20s and lost
    #             characters mid-way.
    #   "type"  — synthesise key events via the input backend
    #             (ydotool/wtype on Linux). Survives clipboard contents
    #             but is slow and unreliable for long texts.
    # Anything else is treated as "paste".
    dictation_inject_method: str = "paste"

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
    "llm_backend": "VOICE_LLM_BACKEND",
    "platform_override": "VOICE_PLATFORM",
    "capacity_mode": "VOICE_CAPACITY_MODE",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _coerce(raw: Any, default: Any, key: str = "") -> Any:
    """Cast a value (str|bool|int|float|tuple) to the type of ``default``."""
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Config key {key!r} must be an integer (got {raw!r})"
            ) from e
    if isinstance(default, float):
        try:
            return float(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Config key {key!r} must be a number (got {raw!r})"
            ) from e
    if isinstance(default, tuple):
        # Accept JSON list, or comma-separated string for env/CLI.
        if isinstance(raw, list | tuple):
            return tuple(str(x) for x in raw)
        if isinstance(raw, str):
            return tuple(s.strip() for s in raw.split(",") if s.strip())
        raise ValueError(
            f"Config key {key!r} must be a list or comma string (got {raw!r})"
        )
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
    # Drop the cached LLM backend so a config change (e.g. switching
    # from opencode to a future backend) takes effect on the next
    # ``get_backend()`` call. Import is local to avoid a cycle:
    # llm.py imports config at module load.
    try:
        from . import llm as _llm

        _llm.reset_backend_cache()
    except ImportError:  # pragma: no cover — llm module always present
        pass
    try:
        from . import context as _ctx

        _ctx.reset_cache()
    except ImportError:  # pragma: no cover — context module always present
        pass
    try:
        from . import host_info as _hi

        _hi.reset()
    except ImportError:  # pragma: no cover — host_info module always present
        pass
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
