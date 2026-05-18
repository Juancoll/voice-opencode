"""Tests for the arecord recorder backend.

The backend is exercised entirely via mocked ``subprocess.Popen`` and
mocked ``os.kill`` so the suite never spawns real arecord. Tests use
``tmp_path`` for the PID file and WAV so concurrent runs / leftover
state from the live desktop cannot pollute them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.linux_audio_arecord import recorder as rec_mod
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect REC_PID_FILE / LOGS_DIR / paths.* to a per-test tmp_path."""
    monkeypatch.setattr(rec_mod, "REC_PID_FILE", tmp_path / "rec.pid")
    monkeypatch.setattr(rec_mod, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> rec_mod.ArecordRecorderBackend:
    """Construct an ArecordRecorderBackend bypassing the PATH check."""
    monkeypatch.setattr(rec_mod.shutil, "which", lambda _: "/usr/bin/arecord")
    return rec_mod.ArecordRecorderBackend()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_missing_arecord_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rec_mod.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="arecord"):
            rec_mod.ArecordRecorderBackend()

    def test_capabilities(self, backend: rec_mod.ArecordRecorderBackend) -> None:
        assert backend.capabilities() == frozenset({
            cap.RECORDER_START, cap.RECORDER_STOP, cap.RECORDER_IS_RECORDING,
        })


# ---------------------------------------------------------------------------
# is_recording
# ---------------------------------------------------------------------------
class TestIsRecording:
    def test_no_pidfile_means_idle(
        self, backend: rec_mod.ArecordRecorderBackend, isolated_state: Path,
    ) -> None:
        assert backend.is_recording() is False

    def test_live_pid_is_recording(
        self, backend: rec_mod.ArecordRecorderBackend, isolated_state: Path,
    ) -> None:
        (isolated_state / "rec.pid").write_text("12345")
        with patch.object(rec_mod.os, "kill") as kill:
            kill.return_value = None
            assert backend.is_recording() is True

    def test_stale_pid_cleans_up(
        self, backend: rec_mod.ArecordRecorderBackend, isolated_state: Path,
    ) -> None:
        pid_file = isolated_state / "rec.pid"
        pid_file.write_text("99999")
        with patch.object(rec_mod.os, "kill", side_effect=ProcessLookupError):
            assert backend.is_recording() is False
        assert not pid_file.exists(), "stale PID file must be cleaned up"

    def test_garbage_pidfile_cleans_up(
        self, backend: rec_mod.ArecordRecorderBackend, isolated_state: Path,
    ) -> None:
        pid_file = isolated_state / "rec.pid"
        pid_file.write_text("not-a-number")
        assert backend.is_recording() is False
        assert not pid_file.exists()


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
class TestStart:
    def test_spawns_arecord_with_expected_args(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wav = isolated_state / "rec.wav"
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        with patch.object(rec_mod.subprocess, "Popen",
                          return_value=fake_proc) as popen:
            backend.start(wav)

        cmd = popen.call_args.args[0]
        assert cmd[0] == "arecord"
        assert "-f" in cmd and "S16_LE" in cmd
        assert "-r" in cmd and "16000" in cmd
        assert "-c" in cmd and "1" in cmd
        assert "-t" in cmd and "wav" in cmd
        assert cmd[-1] == str(wav)
        # PID persisted
        assert (isolated_state / "rec.pid").read_text() == "4242"

    def test_noop_when_already_recording(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
    ) -> None:
        (isolated_state / "rec.pid").write_text("12345")
        with patch.object(rec_mod.os, "kill") as kill, \
             patch.object(rec_mod.subprocess, "Popen") as popen:
            kill.return_value = None
            backend.start(isolated_state / "rec.wav")
            popen.assert_not_called()

    def test_unlinks_previous_wav(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
    ) -> None:
        wav = isolated_state / "rec.wav"
        wav.write_bytes(b"old")
        fake_proc = MagicMock(pid=1)
        with patch.object(rec_mod.subprocess, "Popen", return_value=fake_proc):
            backend.start(wav)
        # Popen is mocked so no new file is created; we only assert
        # the old one was removed before spawn.
        assert not wav.exists()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------
class TestStop:
    def test_returns_none_when_not_recording(
        self, backend: rec_mod.ArecordRecorderBackend, isolated_state: Path,
    ) -> None:
        assert backend.stop(isolated_state / "rec.wav") is None

    def test_returns_none_when_file_too_small(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
    ) -> None:
        pid_file = isolated_state / "rec.pid"
        wav = isolated_state / "rec.wav"
        pid_file.write_text("12345")
        wav.write_bytes(b"x" * 100)  # well under 4 KB
        # First os.kill call (in is_recording) succeeds; subsequent
        # signal + wait loop raises ProcessLookupError immediately.
        calls: list[int] = []
        def fake_kill(pid: int, sig: int) -> None:
            calls.append(sig)
            if len(calls) > 1:
                raise ProcessLookupError
        with patch.object(rec_mod.os, "kill", side_effect=fake_kill):
            assert backend.stop(wav) is None
        assert not pid_file.exists()

    def test_returns_path_when_usable(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
    ) -> None:
        pid_file = isolated_state / "rec.pid"
        wav = isolated_state / "rec.wav"
        pid_file.write_text("12345")
        wav.write_bytes(b"x" * 8192)  # > 4 KB threshold
        calls = {"n": 0}
        def fake_kill(pid: int, sig: int) -> None:
            calls["n"] += 1
            if calls["n"] > 1:
                raise ProcessLookupError
        with patch.object(rec_mod.os, "kill", side_effect=fake_kill):
            result = backend.stop(wav)
        assert result == wav
        assert not pid_file.exists()

    def test_sigint_sent_before_polling(
        self,
        backend: rec_mod.ArecordRecorderBackend,
        isolated_state: Path,
    ) -> None:
        import signal
        pid_file = isolated_state / "rec.pid"
        wav = isolated_state / "rec.wav"
        pid_file.write_text("12345")
        wav.write_bytes(b"x" * 8192)
        sent: list[int] = []
        def fake_kill(pid: int, sig: int) -> None:
            sent.append(sig)
            if len(sent) > 2:  # is_recording check + SIGINT + poll loop
                raise ProcessLookupError
        with patch.object(rec_mod.os, "kill", side_effect=fake_kill):
            backend.stop(wav)
        # sent[0] is the liveness probe (sig 0) from is_recording();
        # sent[1] is the real SIGINT that finalises the WAV header.
        assert sent[0] == 0
        assert sent[1] == signal.SIGINT, \
            "first real signal must be SIGINT so arecord finalises the WAV header"
