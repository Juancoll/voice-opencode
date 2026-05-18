"""MPRIS media transport control via ``playerctl``.

Drives whichever MPRIS-compatible player is currently active (Chromium,
Firefox, mpv, Spotify, …). When several players are running, playerctl
picks the most recently active one — same behaviour we want.

``play_pause`` is a single toggle (matches the media key on most
keyboards). ``next`` / ``prev`` map directly to the MPRIS verbs.

``status()`` returns a small dict:

    {"player": "chromium", "status": "Playing",
     "title": "...", "artist": "..."}

If no player is running, every method raises ``BackendError`` with
playerctl's stderr message. The CLI and MCP layer turn that into a
user-friendly "no active player".
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Final

from ...platform import capabilities as cap
from ...platform.base import BackendError

_TIMEOUT_S: Final[float] = 3.0
# Use ASCII Unit Separator (0x1F) — vanishingly unlikely in real metadata,
# unlike '|' which appears in YouTube titles.
_SEP: Final[str] = "\x1f"
_META_FMT: Final[str] = f"{{{{playerName}}}}{_SEP}{{{{status}}}}{_SEP}{{{{title}}}}{_SEP}{{{{artist}}}}"


class PlayerctlMediaBackend:
    """Media backend wired to ``playerctl`` (MPRIS CLI)."""

    def __init__(self) -> None:
        if shutil.which("playerctl") is None:
            raise BackendError("playerctl not installed (pacman: playerctl)")

    def capabilities(self) -> frozenset[str]:
        return frozenset({
            cap.MEDIA_PLAY_PAUSE,
            cap.MEDIA_NEXT,
            cap.MEDIA_PREV,
            cap.MEDIA_STATUS,
        })

    # -- transport -----------------------------------------------------
    def play_pause(self) -> None:
        self._run(["playerctl", "play-pause"])

    def next(self) -> None:
        self._run(["playerctl", "next"])

    def prev(self) -> None:
        self._run(["playerctl", "previous"])

    # -- read ----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        proc = self._run(["playerctl", "metadata", "--format", _META_FMT])
        line = proc.stdout.strip()
        parts = line.split(_SEP, 3)
        # Pad to 4 fields so we don't IndexError on partial metadata.
        while len(parts) < 4:
            parts.append("")
        player, status, title, artist = parts
        return {
            "player": player,
            "status": status,
            "title": title,
            "artist": artist,
        }

    # -- internals -----------------------------------------------------
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"playerctl timed out: {' '.join(argv)}") from e
        if proc.returncode != 0:
            # "No players found" is the common case — surface verbatim.
            msg = (proc.stderr.strip() or proc.stdout.strip()
                   or "playerctl returned non-zero")
            raise BackendError(f"playerctl failed: {msg}")
        return proc
