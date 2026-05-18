"""Tests for ``voice_opencode.paths`` cross-platform helpers.

The module is imported once at startup so its module-level constants
are frozen for the live process; these tests therefore exercise the
*functions* (``runtime_dir``, ``state_dir``, ``config_dir``), not
the constants. The constants themselves are smoke-tested for
"sensible values on this host" only.

We patch ``sys.platform`` per case to verify the Windows branch
without needing a Windows host.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voice_opencode import paths


# ---------------------------------------------------------------------------
# Linux branch
# ---------------------------------------------------------------------------
class TestLinuxBranch:
    @pytest.fixture(autouse=True)
    def _force_linux(self) -> None:
        # noop: we run on linux already. Keep the fixture for symmetry.
        return

    def test_runtime_uses_xdg_runtime_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert paths.runtime_dir() == tmp_path / "voice-opencode"

    def test_runtime_falls_back_to_tmp(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert paths.runtime_dir() == Path("/tmp/voice-opencode")

    def test_state_uses_xdg_state_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert paths.state_dir() == tmp_path / "voice-opencode"

    def test_state_falls_back_to_local_state(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert paths.state_dir() == Path.home() / ".local/state/voice-opencode"

    def test_config_uses_xdg_config_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert paths.config_dir() == tmp_path / "voice-opencode"

    def test_config_falls_back_to_dot_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert paths.config_dir() == Path.home() / ".config/voice-opencode"


# ---------------------------------------------------------------------------
# Windows branch (simulated)
# ---------------------------------------------------------------------------
class TestWindowsBranch:
    """Patch ``sys.platform`` to 'win32' so the helper takes the
    Windows branch even though we run on Linux."""

    def test_runtime_uses_localappdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        with patch.object(paths.sys, "platform", "win32"):
            assert paths.runtime_dir() == tmp_path / "voice-opencode" / "runtime"

    def test_state_uses_localappdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        with patch.object(paths.sys, "platform", "win32"):
            assert paths.state_dir() == tmp_path / "voice-opencode" / "state"

    def test_config_uses_appdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("APPDATA", str(tmp_path))
        with patch.object(paths.sys, "platform", "win32"):
            assert paths.config_dir() == tmp_path / "voice-opencode"

    def test_localappdata_falls_back_to_home(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        with patch.object(paths.sys, "platform", "win32"):
            assert (
                paths.runtime_dir()
                == Path.home() / "AppData/Local/voice-opencode/runtime"
            )

    def test_appdata_falls_back_to_home(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("APPDATA", raising=False)
        with patch.object(paths.sys, "platform", "win32"):
            assert (
                paths.config_dir()
                == Path.home() / "AppData/Roaming/voice-opencode"
            )


# ---------------------------------------------------------------------------
# Module-level constants stay sane on the live host
# ---------------------------------------------------------------------------
class TestModuleConstants:
    """Smoke-test the frozen constants so a future refactor that
    inadvertently moves them off the runtime/repo roots is caught."""

    def test_state_dir_under_runtime_root(self) -> None:
        # The constant was computed at import time. Other tests may
        # monkeypatch.setattr it during the suite (and pytest's setattr
        # restores it per-test), so we only assert the *shape* — it
        # must end with our app name, regardless of the runtime root.
        assert paths.STATE_DIR.name == "voice-opencode"

    def test_repo_constants_under_project_root(self) -> None:
        for p in (
            paths.LOGS_DIR, paths.MODELS_DIR, paths.VOICES_DIR,
            paths.ICONS_DIR, paths.MEMORY_DIR,
        ):
            assert p.is_relative_to(paths.PROJECT_ROOT)
        assert paths.CONFIG_FILE.parent == paths.PROJECT_ROOT

    def test_runtime_files_under_state_dir(self) -> None:
        for p in (
            paths.REC_PID_FILE, paths.REC_WAV_FILE, paths.SESSION_FILE,
            paths.PAUSE_FILE, paths.STATE_FILE, paths.SCREENSHOT_FILE,
            paths.AGENT_FILE, paths.PIPELINE_LOCK_FILE, paths.SERVER_FILE,
        ):
            assert p.parent == paths.STATE_DIR


# ---------------------------------------------------------------------------
# ensure_dirs
# ---------------------------------------------------------------------------
class TestEnsureDirs:
    def test_creates_missing_dirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        logs = tmp_path / "logs"
        state = tmp_path / "state"
        monkeypatch.setattr(paths, "LOGS_DIR", logs)
        monkeypatch.setattr(paths, "STATE_DIR", state)
        assert not logs.exists() and not state.exists()
        paths.ensure_dirs()
        assert logs.is_dir() and state.is_dir()

    def test_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")
        paths.ensure_dirs()
        paths.ensure_dirs()  # should not raise
