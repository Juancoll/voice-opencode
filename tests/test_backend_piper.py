"""Tests for the common_piper TTS backend.

Exercises the backend with mocked ``subprocess.run`` so no real
piper-tts is ever invoked. ``VOICES_DIR`` is redirected to a
``tmp_path`` per test so the host's real voices/ directory cannot
leak into assertions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.common_piper import tts as piper
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_voice(
    voices_dir: Path, stem: str, *, num_speakers: int = 1,
) -> Path:
    """Create a fake voice pair (<stem>.onnx + <stem>.onnx.json)."""
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{stem}.onnx"
    onnx.write_bytes(b"\x00")
    cfg = onnx.with_suffix(onnx.suffix + ".json")
    cfg.write_text(json.dumps({
        "dataset": stem,
        "num_speakers": num_speakers,
        "speaker_id_map": {str(i): i for i in range(num_speakers)},
        "audio": {"sample_rate": 22050},
        "language": {"name_native": "español"},
    }))
    return onnx


@pytest.fixture
def voices_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    vd = tmp_path / "voices"
    vd.mkdir()
    monkeypatch.setattr(piper, "VOICES_DIR", vd)
    monkeypatch.setattr(piper, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir(exist_ok=True)
    return vd


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> piper.PiperTTSBackend:
    monkeypatch.setattr(piper.shutil, "which", lambda _: "/usr/bin/piper-tts")
    return piper.PiperTTSBackend()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_missing_piper_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(piper.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="piper"):
            piper.PiperTTSBackend()

    def test_honours_piper_bin_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPER_BIN", "/opt/piper/piper")
        seen: dict[str, str] = {}
        def fake_which(name: str) -> str | None:
            seen["name"] = name
            return "/opt/piper/piper"
        monkeypatch.setattr(piper.shutil, "which", fake_which)
        piper.PiperTTSBackend()
        assert seen["name"] == "/opt/piper/piper"

    def test_capabilities(self, backend: piper.PiperTTSBackend) -> None:
        assert backend.capabilities() == frozenset({
            cap.TTS_SYNTHESIZE, cap.TTS_LIST_VOICES,
        })


# ---------------------------------------------------------------------------
# list_voices
# ---------------------------------------------------------------------------
class TestListVoices:
    def test_empty_dir(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        assert backend.list_voices() == []

    def test_lists_stems_sorted(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        _make_voice(voices_dir, "es_AR-daniela-high")
        _make_voice(voices_dir, "es_ES-sharvard-medium")
        assert backend.list_voices() == [
            "es_AR-daniela-high", "es_ES-sharvard-medium",
        ]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
class TestResolve:
    def test_direct_stem(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        v = _make_voice(voices_dir, "es_AR-daniela-high")
        assert backend._resolve("es_AR-daniela-high") == v

    def test_substring(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        v = _make_voice(voices_dir, "es_AR-daniela-high")
        assert backend._resolve("daniela") == v

    def test_absolute_path(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        v = _make_voice(voices_dir, "es_AR-daniela-high")
        assert backend._resolve(str(v)) == v

    def test_missing_raises(
        self, backend: piper.PiperTTSBackend, voices_dir: Path,
    ) -> None:
        with pytest.raises(BackendError, match="not found"):
            backend._resolve("nope")


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------
class TestSynthesize:
    def test_writes_wav_via_output_file_arg(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "es_AR-daniela-high")
        out = tmp_path / "speech.wav"

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            # Simulate piper writing the file before returning.
            Path(cmd[cmd.index("--output_file") + 1]).write_bytes(b"RIFF...")
            return MagicMock(returncode=0)

        with patch.object(piper.subprocess, "run", side_effect=fake_run) as run:
            backend.synthesize("hola", "es_AR-daniela-high", out)

        cmd = run.call_args.args[0]
        assert cmd[0] == "piper-tts"
        assert "--model" in cmd
        assert "--output_file" in cmd
        assert str(out) in cmd
        assert out.exists()

    def test_multispeaker_adds_speaker_arg(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "multi-voice", num_speakers=4)
        out = tmp_path / "speech.wav"
        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            Path(cmd[cmd.index("--output_file") + 1]).write_bytes(b"RIFF")
            return MagicMock(returncode=0)
        with patch.object(piper.subprocess, "run", side_effect=fake_run) as run:
            backend.synthesize("hi", "multi-voice", out, speaker_id=2)
        cmd = run.call_args.args[0]
        assert "--speaker" in cmd
        assert cmd[cmd.index("--speaker") + 1] == "2"

    def test_singlespeaker_omits_speaker_arg(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "single-voice", num_speakers=1)
        out = tmp_path / "speech.wav"
        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            Path(cmd[cmd.index("--output_file") + 1]).write_bytes(b"RIFF")
            return MagicMock(returncode=0)
        with patch.object(piper.subprocess, "run", side_effect=fake_run) as run:
            backend.synthesize("hi", "single-voice", out, speaker_id=7)
        cmd = run.call_args.args[0]
        assert "--speaker" not in cmd

    def test_failed_synthesis_raises(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "v")
        out = tmp_path / "speech.wav"
        with (
            patch.object(piper.subprocess, "run",
                         return_value=MagicMock(returncode=1)),
            pytest.raises(BackendError, match="failed"),
        ):
            backend.synthesize("hi", "v", out)

    def test_empty_output_treated_as_failure(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "v")
        out = tmp_path / "speech.wav"
        # rc=0 but file empty
        with patch.object(piper.subprocess, "run",
                          return_value=MagicMock(returncode=0)), pytest.raises(BackendError):
            backend.synthesize("hi", "v", out)

    def test_timeout_raises(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        _make_voice(voices_dir, "v")
        out = tmp_path / "speech.wav"
        with (
            patch.object(piper.subprocess, "run",
                         side_effect=subprocess.TimeoutExpired("piper", 60)),
            pytest.raises(BackendError, match="timeout"),
        ):
            backend.synthesize("hi", "v", out)

    def test_unknown_voice_raises(
        self,
        backend: piper.PiperTTSBackend,
        voices_dir: Path,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "speech.wav"
        with pytest.raises(BackendError, match="not found"):
            backend.synthesize("hi", "ghost", out)
