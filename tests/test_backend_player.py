"""Tests for the paplay player backend.

Exercises the backend with mocked ``subprocess.Popen`` so no audio
is ever played in the test suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.linux_audio_paplay import player as plr
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> plr.PaplayPlayerBackend:
    monkeypatch.setattr(plr.shutil, "which", lambda _: "/usr/bin/paplay")
    return plr.PaplayPlayerBackend()


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    p = tmp_path / "out.wav"
    # 44 bytes minimal WAV-ish header is enough for path-exists checks.
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 40)
    return p


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_missing_paplay_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(plr.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="paplay"):
            plr.PaplayPlayerBackend()

    def test_capabilities(self, backend: plr.PaplayPlayerBackend) -> None:
        assert backend.capabilities() == frozenset({cap.PLAYER_PLAY_WAV})


# ---------------------------------------------------------------------------
# play_wav
# ---------------------------------------------------------------------------
class TestPlayWav:
    def test_missing_wav_raises(
        self, backend: plr.PaplayPlayerBackend, tmp_path: Path,
    ) -> None:
        with pytest.raises(BackendError, match="WAV not found"):
            backend.play_wav(tmp_path / "missing.wav")

    def test_spawns_paplay_with_path(
        self, backend: plr.PaplayPlayerBackend, wav: Path,
    ) -> None:
        proc = MagicMock()
        proc.wait.return_value = 0
        with patch.object(plr.subprocess, "Popen", return_value=proc) as popen:
            backend.play_wav(wav)
        cmd = popen.call_args.args[0]
        assert cmd[0] == "paplay"
        assert cmd[1] == str(wav)
        proc.wait.assert_called_once()

    def test_timeout_kills_paplay(
        self, backend: plr.PaplayPlayerBackend, wav: Path,
    ) -> None:
        proc = MagicMock()
        # First wait() raises TimeoutExpired; second wait() (post-kill) returns.
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="paplay", timeout=0.1),
            0,
        ]
        with patch.object(plr.subprocess, "Popen", return_value=proc):
            backend.play_wav(wav, timeout_s=0.1)
        proc.kill.assert_called_once()

    def test_timeout_arg_is_propagated(
        self, backend: plr.PaplayPlayerBackend, wav: Path,
    ) -> None:
        proc = MagicMock()
        proc.wait.return_value = 0
        with patch.object(plr.subprocess, "Popen", return_value=proc):
            backend.play_wav(wav, timeout_s=12.5)
        proc.wait.assert_called_once_with(timeout=12.5)
