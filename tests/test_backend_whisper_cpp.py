"""Tests for the common_whisper_cpp STT backend.

Exercises with mocked ``subprocess.run`` so no real whisper-cli is
ever invoked. ``MODELS_DIR`` and ``settings`` are redirected per test
so the host's real models/ directory cannot leak into assertions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.common_whisper_cpp import stt as whisper
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def models_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    md = tmp_path / "models"
    md.mkdir()
    monkeypatch.setattr(whisper, "MODELS_DIR", md)
    return md


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    s = MagicMock(whisper_model="ggml-small.bin", whisper_lang="es")
    monkeypatch.setattr(whisper, "settings", s)
    return s


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> whisper.WhisperCppSTTBackend:
    monkeypatch.setattr(whisper.shutil, "which", lambda _: "/usr/bin/whisper-cli")
    return whisper.WhisperCppSTTBackend()


def _wav(tmp_path: Path) -> Path:
    p = tmp_path / "in.wav"
    p.write_bytes(b"RIFF....WAVE")
    return p


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_missing_whisper_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(whisper.shutil, "which", lambda _: None)
        with pytest.raises(BackendError, match="whisper"):
            whisper.WhisperCppSTTBackend()

    def test_honours_whisper_bin_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHISPER_BIN", "/opt/whisper/whisper-cli")
        seen: dict[str, str] = {}
        def fake_which(name: str) -> str | None:
            seen["name"] = name
            return "/opt/whisper/whisper-cli"
        monkeypatch.setattr(whisper.shutil, "which", fake_which)
        whisper.WhisperCppSTTBackend()
        assert seen["name"] == "/opt/whisper/whisper-cli"

    def test_capabilities(self, backend: whisper.WhisperCppSTTBackend) -> None:
        assert backend.capabilities() == frozenset({cap.STT_TRANSCRIBE})


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------
class TestTranscribe:
    def test_missing_model_raises(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(BackendError, match="model not found"):
            backend.transcribe(_wav(tmp_path))

    def test_argv_shape(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        wav = _wav(tmp_path)
        with patch.object(
            whisper.subprocess, "run",
            return_value=MagicMock(returncode=0, stdout=" hola mundo\n", stderr=""),
        ) as run:
            out = backend.transcribe(wav)
        cmd = run.call_args.args[0]
        assert cmd[0] == "whisper-cli"
        assert "-m" in cmd and str(models_dir / "ggml-small.bin") in cmd
        assert "-l" in cmd and "es" in cmd
        assert "-nt" in cmd and "-np" in cmd
        assert "-f" in cmd and str(wav) in cmd
        assert out == "hola mundo"

    def test_strips_bracket_tags(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        with patch.object(
            whisper.subprocess, "run",
            return_value=MagicMock(
                returncode=0,
                stdout="[BLANK_AUDIO] hola [MUSIC] qué tal\n",
                stderr="",
            ),
        ):
            assert backend.transcribe(_wav(tmp_path)) == "hola  qué tal"

    def test_blank_audio_returns_empty(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        with patch.object(
            whisper.subprocess, "run",
            return_value=MagicMock(
                returncode=0, stdout="[BLANK_AUDIO]\n", stderr="",
            ),
        ):
            assert backend.transcribe(_wav(tmp_path)) == ""

    def test_failed_run_raises(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        with (
            patch.object(
                whisper.subprocess, "run",
                return_value=MagicMock(returncode=2, stdout="", stderr="boom"),
            ),
            pytest.raises(BackendError, match="failed"),
        ):
            backend.transcribe(_wav(tmp_path))

    def test_timeout_raises(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        with (
            patch.object(
                whisper.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("whisper-cli", 120),
            ),
            pytest.raises(BackendError, match="timeout"),
        ):
            backend.transcribe(_wav(tmp_path))

    def test_honours_lang_setting(
        self,
        backend: whisper.WhisperCppSTTBackend,
        models_dir: Path,
        fake_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        (models_dir / "ggml-small.bin").write_bytes(b"\x00")
        fake_settings.whisper_lang = "en"
        with patch.object(
            whisper.subprocess, "run",
            return_value=MagicMock(returncode=0, stdout="hi\n", stderr=""),
        ) as run:
            backend.transcribe(_wav(tmp_path))
        cmd = run.call_args.args[0]
        assert cmd[cmd.index("-l") + 1] == "en"
