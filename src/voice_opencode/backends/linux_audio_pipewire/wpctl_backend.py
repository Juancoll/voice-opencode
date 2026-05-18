"""PipeWire / WirePlumber audio control via ``wpctl``.

Operates on the default sink (``@DEFAULT_AUDIO_SINK@``) and source
(``@DEFAULT_AUDIO_SOURCE@``). Per-app routing and device enumeration
are out of scope — this backend handles the four operations the voice
assistant actually needs: read volume, set volume, toggle output mute,
toggle mic mute.

Volume is normalised to ``0.0–1.0`` at the Protocol boundary. wpctl
accepts the same range natively, and clamping is the caller's
responsibility (we do it defensively anyway in ``volume_set``).

Output parsing: ``wpctl get-volume`` prints exactly one line:

    Volume: 0.52
    Volume: 1.00 [MUTED]

The `` [MUTED]`` suffix is the *only* way to read the mute state — we
parse it on every ``mute_toggle`` to know what state we just toggled
*into* and return that.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Final

from ...platform import capabilities as cap
from ...platform.base import BackendError

_SINK: Final[str] = "@DEFAULT_AUDIO_SINK@"
_SOURCE: Final[str] = "@DEFAULT_AUDIO_SOURCE@"
_TIMEOUT_S: Final[float] = 3.0


class WpctlAudioBackend:
    """Audio backend wired to ``wpctl`` (WirePlumber CLI)."""

    def __init__(self) -> None:
        if shutil.which("wpctl") is None:
            raise BackendError("wpctl not installed (pacman: wireplumber)")

    def capabilities(self) -> frozenset[str]:
        return frozenset({
            cap.AUDIO_VOLUME_GET,
            cap.AUDIO_VOLUME_SET,
            cap.AUDIO_MUTE_TOGGLE,
            cap.AUDIO_MIC_MUTE_TOGGLE,
        })

    # -- read ----------------------------------------------------------
    def volume_get(self) -> float:
        """Return the current sink volume in 0.0–1.0."""
        level, _muted = self._read_volume(_SINK)
        return level

    # -- write ---------------------------------------------------------
    def volume_set(self, level: float) -> None:
        """Set the sink volume. Input is clamped to 0.0–1.0."""
        clamped = max(0.0, min(1.0, float(level)))
        self._run(["wpctl", "set-volume", _SINK, f"{clamped:.2f}"])

    def mute_toggle(self) -> bool:
        """Toggle output mute. Returns the new muted state (True if now muted)."""
        self._run(["wpctl", "set-mute", _SINK, "toggle"])
        _level, muted = self._read_volume(_SINK)
        return muted

    def mic_mute_toggle(self) -> bool:
        """Toggle microphone mute. Returns the new muted state."""
        self._run(["wpctl", "set-mute", _SOURCE, "toggle"])
        _level, muted = self._read_volume(_SOURCE)
        return muted

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
            raise BackendError(f"wpctl timed out: {' '.join(argv)}") from e
        if proc.returncode != 0:
            raise BackendError(
                f"wpctl failed (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def _read_volume(self, node: str) -> tuple[float, bool]:
        """Return (volume 0.0-1.0, muted) for the given node id/alias."""
        proc = self._run(["wpctl", "get-volume", node])
        line = proc.stdout.strip()
        # Expected: "Volume: 0.52" or "Volume: 1.00 [MUTED]"
        if not line.startswith("Volume:"):
            raise BackendError(f"wpctl: unexpected output {line!r}")
        rest = line[len("Volume:"):].strip()
        muted = "[MUTED]" in rest
        # Drop the suffix and parse the leading float.
        token = rest.split()[0]
        try:
            level = float(token)
        except ValueError as e:
            raise BackendError(f"wpctl: cannot parse level from {line!r}") from e
        return level, muted
