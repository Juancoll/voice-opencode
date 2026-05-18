"""Tests for the linux_logview_terminal backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.linux_logview_terminal import logview_backend
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


@pytest.fixture
def backend() -> logview_backend.TerminalLogViewerBackend:
    return logview_backend.TerminalLogViewerBackend()


def test_capabilities(backend: logview_backend.TerminalLogViewerBackend) -> None:
    assert backend.capabilities() == frozenset({cap.LOGVIEW_TAIL_FILE})


def test_prefers_first_available_terminal(
    backend: logview_backend.TerminalLogViewerBackend, tmp_path: Path,
) -> None:
    """foot beats kitty beats alacritty beats xterm."""
    seen: dict[str, str] = {}
    def which(name: str) -> str | None:
        # Only kitty installed.
        return f"/usr/bin/{name}" if name == "kitty" else None
    def popen(cmd: list[str]) -> MagicMock:
        seen["cmd"] = " ".join(cmd)
        return MagicMock()
    with (
        patch.object(logview_backend.shutil, "which", side_effect=which),
        patch.object(logview_backend.subprocess, "Popen", side_effect=popen),
    ):
        backend.tail_file(tmp_path / "voice.log")
    assert seen["cmd"] == f"kitty -e tail -f {tmp_path / 'voice.log'}"


def test_first_terminal_wins(
    backend: logview_backend.TerminalLogViewerBackend, tmp_path: Path,
) -> None:
    """When foot and kitty both exist, foot is picked."""
    calls: list[list[str]] = []
    with (
        patch.object(logview_backend.shutil, "which",
                     side_effect=lambda n: f"/usr/bin/{n}"),
        patch.object(logview_backend.subprocess, "Popen",
                     side_effect=lambda cmd: calls.append(cmd) or MagicMock()),
    ):
        backend.tail_file(tmp_path / "f.log")
    assert calls[0][0] == "foot"


def test_falls_back_to_xdg_open(
    backend: logview_backend.TerminalLogViewerBackend, tmp_path: Path,
) -> None:
    """No terminal → xdg-open the file directly."""
    def which(name: str) -> str | None:
        return "/usr/bin/xdg-open" if name == "xdg-open" else None
    calls: list[list[str]] = []
    with (
        patch.object(logview_backend.shutil, "which", side_effect=which),
        patch.object(logview_backend.subprocess, "Popen",
                     side_effect=lambda cmd: calls.append(cmd) or MagicMock()),
    ):
        backend.tail_file(tmp_path / "v.log")
    assert calls[0][0] == "xdg-open"
    assert str(tmp_path / "v.log") in calls[0]


def test_raises_when_nothing_available(
    backend: logview_backend.TerminalLogViewerBackend, tmp_path: Path,
) -> None:
    """No terminal and no xdg-open → BackendError."""
    with (
        patch.object(logview_backend.shutil, "which", return_value=None),
        patch.object(logview_backend.subprocess, "Popen") as popen,
        pytest.raises(BackendError, match="no terminal"),
    ):
        backend.tail_file(tmp_path / "v.log")
    popen.assert_not_called()


def test_accepts_str_path(
    backend: logview_backend.TerminalLogViewerBackend, tmp_path: Path,
) -> None:
    """``tail_file`` accepts a string path even though typed as Path."""
    calls: list[list[str]] = []
    with (
        patch.object(logview_backend.shutil, "which",
                     side_effect=lambda n: "/usr/bin/foot" if n == "foot" else None),
        patch.object(logview_backend.subprocess, "Popen",
                     side_effect=lambda cmd: calls.append(cmd) or MagicMock()),
    ):
        backend.tail_file(str(tmp_path / "v.log"))  # type: ignore[arg-type]
    assert str(tmp_path / "v.log") in calls[0]
