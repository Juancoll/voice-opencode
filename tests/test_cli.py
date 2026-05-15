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
