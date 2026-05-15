"""Tests for the CLI dispatcher (legacy aliases + grouped commands)."""
from __future__ import annotations

from unittest.mock import patch


def test_legacy_start_dispatches_to_rec_start(tmp_state):
    from voice_opencode import cli
    with patch.object(cli.pipeline, "start_recording") as start:
        rc = cli.main(["start"])
    assert rc == 0
    start.assert_called_once()


def test_legacy_voices_dispatches_to_tts_voices(tmp_state):
    from voice_opencode import cli
    with patch.object(cli, "_print_voices") as p:
        rc = cli.main(["voices"])
    assert rc == 0
    p.assert_called_once()


def test_grouped_session_reset(tmp_state):
    from voice_opencode import cli
    with patch.object(cli.Session, "forget") as forget:
        rc = cli.main(["session", "reset"])
    assert rc == 0
    forget.assert_called_once()


def test_state_outputs_json(tmp_state, capsys):
    from voice_opencode import cli
    with patch.object(cli, "health", return_value=False):
        rc = cli.main(["state"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    import json
    data = json.loads(out)
    assert data["state"] == "idle"
    assert data["server"] is False


def test_unknown_command_returns_nonzero(tmp_state):
    from voice_opencode import cli
    rc = cli.main(["frobnicate"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Phase A: windows / workspaces / platform groups
# ---------------------------------------------------------------------------
def test_platform_info_dumps_json(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    monkeypatch.setattr(cli.plat, "active_platform", "linux-hyprland", raising=False)
    monkeypatch.setattr(cli.plat, "all_capabilities", lambda: frozenset({"wm.list_windows"}))
    rc = cli.main(["platform", "info"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["platform"] == "linux-hyprland"
    assert "wm.list_windows" in data["capabilities"]


def test_windows_list_calls_backend(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.types import Rect, Window
    fake = Window(
        id="0xAA", pid=1, app_id="kitty", title="t",
        rect=Rect(0, 0, 1, 1), monitor_id=0, workspace_id=1, focused=True,
    )

    class FakeWM:
        def list_windows(self):
            return [fake]

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "list"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "0xAA"
    assert data[0]["focused"] is True


def test_windows_focus_invokes_backend(tmp_state, monkeypatch):
    from voice_opencode import cli
    calls: list[str] = []

    class FakeWM:
        def focus_window(self, t):
            calls.append(t)

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "focus", "kitty"])
    assert rc == 0
    assert calls == ["kitty"]


def test_workspaces_switch_invokes_backend(tmp_state, monkeypatch):
    from voice_opencode import cli
    calls: list[str] = []

    class FakeWM:
        def switch_workspace(self, t):
            calls.append(t)

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["workspaces", "switch", "5"])
    assert rc == 0
    assert calls == ["5"]


def test_windows_propagates_backend_error(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.base import BackendError

    class FakeWM:
        def focus_window(self, _t):
            raise BackendError("nope")

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "focus", "ghost"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err
