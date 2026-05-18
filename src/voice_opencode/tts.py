"""
Text-to-speech orchestration.

Splits cleanly into three responsibilities:

1. **Voice metadata** (``VoiceInfo``, ``voice_info``, ``resolve_voice``,
   ``list_voices``, ``current_voice``) — pure-Python parsing of the
   ``<voice>.onnx.json`` sidecar. Lives here because both the CLI
   (``voice voices``) and the tray (voice picker) want it as a tiny
   Python API, not as a backend method.

2. **Markdown cleaning** (``clean_for_tts``) — domain logic that runs
   regardless of which TTS engine produces the audio. Has its own
   extensive test suite.

3. **Speaking** (``speak``) — composes ``platform.tts.synthesize(...)``
   with ``platform.player.play_wav(...)``. The intermediate WAV lives
   in ``STATE_DIR`` so a wedged player can be diagnosed offline.

The actual piper-tts invocation lives in
``backends/common_piper/tts.py``; the actual paplay invocation lives
in ``backends/linux_audio_paplay/player.py``. Swapping either is a
matter of wiring a different backend in ``platform/__init__.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import platform as _plat
from .config import settings
from .logging import log
from .paths import STATE_DIR, VOICES_DIR


# ---------------------------------------------------------------------------
# Voice metadata
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceInfo:
    path: Path
    dataset: str
    num_speakers: int
    speakers: list[str]
    sample_rate: int
    language: str

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def is_multispeaker(self) -> bool:
        return self.num_speakers > 1


def voice_info(voice: Path) -> VoiceInfo:
    """Parse the ``.onnx.json`` sidecar; fall back to safe defaults."""
    cfg = voice.with_suffix(voice.suffix + ".json")
    try:
        data = json.loads(cfg.read_text())
        return VoiceInfo(
            path=voice,
            dataset=data.get("dataset", voice.stem),
            num_speakers=int(data.get("num_speakers", 1)),
            speakers=list(data.get("speaker_id_map", {}).keys()),
            sample_rate=int(data["audio"]["sample_rate"]),
            language=data.get("language", {}).get("name_native", "?"),
        )
    except Exception as e:
        log(f"Could not parse {cfg}: {e}; using defaults.")
        return VoiceInfo(
            path=voice, dataset=voice.stem, num_speakers=1,
            speakers=[], sample_rate=22050, language="?",
        )


def resolve_voice(name: str) -> Path:
    """Find a voice .onnx in ``voices/`` by stem or substring."""
    direct = VOICES_DIR / f"{name}.onnx"
    if direct.exists():
        return direct
    matches = sorted(VOICES_DIR.glob(f"*{name}*.onnx"))
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"Voice '{name}' not found in {VOICES_DIR}. "
        f"Available: {[p.stem for p in VOICES_DIR.glob('*.onnx')]}"
    )


def list_voices() -> list[VoiceInfo]:
    """Return metadata for every voice in ``voices/``, sorted by stem."""
    return [voice_info(v) for v in sorted(VOICES_DIR.glob("*.onnx"))]


def current_voice() -> VoiceInfo:
    """The voice currently selected via config."""
    return voice_info(resolve_voice(settings.voice))


# ---------------------------------------------------------------------------
# Markdown cleaning (see test_tts_cleaning.py for the contract)
# ---------------------------------------------------------------------------
# Order matters: fences before inline code, images before links (image
# syntax is a superset of link), emphasis before generic punctuation
# cleanup. Anything reachable by the LLM that would otherwise be read
# out as "asterisco asterisco" goes here.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_LINK_REF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
_BARE_URL_RE = re.compile(r"https?://\S+")
_BOLD_STAR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDER_RE = re.compile(r"__(.+?)__", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_MD_PREFIX_RE = re.compile(r"^\s*(?:[#>]+|[-*+]|\d{1,3}[.)])\s+", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$", re.MULTILINE)
_TABLE_PIPE_RE = re.compile(r"\s*\|\s*")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_for_tts(text: str) -> str:
    """Strip markdown/code so the spoken version sounds natural.

    Steps (order is significant):

    1. Drop fenced code blocks (announce as omitted).
    2. Drop reference-style link definitions.
    3. Strip images/links keeping their visible text.
    4. Replace bare URLs by the word "enlace".
    5. Unwrap bold (``**``, ``__``) before italic so ``**x**`` is not eaten.
    6. Unwrap italic (``*``, ``_``) using look-around to avoid bullets.
    7. Unwrap strikethrough (``~~``).
    8. Unwrap inline code (``` ` ```) keeping content.
    9. Strip leading list/heading/blockquote markers per line.
    10. Strip markdown table separators; turn remaining ``|`` into commas.
    11. Strip HTML tags.
    12. Collapse whitespace.
    """
    text = _FENCE_RE.sub(" (bloque de código omitido) ", text)
    text = _LINK_REF_RE.sub("", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _BARE_URL_RE.sub("enlace", text)
    text = _BOLD_STAR_RE.sub(r"\1", text)
    text = _BOLD_UNDER_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _INLINE_RE.sub(r"\1", text)
    text = _MD_PREFIX_RE.sub("", text)
    text = _TABLE_SEP_RE.sub("", text)
    text = _TABLE_PIPE_RE.sub(", ", text)
    text = _HTML_TAG_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Speaking — orchestrates platform.tts + platform.player
# ---------------------------------------------------------------------------
_TTS_WAV: Path = STATE_DIR / "tts.wav"


def speak(text: str) -> None:
    """Clean ``text``, synthesise via the wired TTS backend, play via
    the wired player backend. Bounded by a 60 s play-timeout so a
    wedged sink cannot freeze the pipeline at ``speaking`` forever.
    """
    text = clean_for_tts(text)
    if not text:
        log("Nothing to speak.")
        return

    voice = current_voice()
    log(f"TTS ← {text[:80]!r}{'…' if len(text) > 80 else ''}")

    speaker = settings.speaker_id if voice.is_multispeaker else None
    try:
        _plat.tts.synthesize(text, voice.stem, _TTS_WAV, speaker_id=speaker)
    except Exception as e:
        log(f"TTS synthesis failed: {e}")
        return
    try:
        _plat.player.play_wav(_TTS_WAV, timeout_s=60.0)
    except Exception as e:
        log(f"TTS playback failed: {e}")
