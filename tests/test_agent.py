"""Tests for the agent-mode lock and audit log."""

from __future__ import annotations

import json

from voice_opencode import agent, paths


def test_acquire_release_roundtrip(tmp_state):
    assert not agent.is_active()
    agent.acquire()
    assert agent.is_active()
    assert paths.AGENT_FILE.exists()
    agent.release()
    assert not agent.is_active()
    assert not paths.AGENT_FILE.exists()


def test_stale_lock_is_cleaned(tmp_state):
    # PID 1 is init; we don't own it → kill(0) raises PermissionError on
    # most systems, but if the file holds a clearly dead PID we should
    # treat the lock as released.
    paths.AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.AGENT_FILE.write_text("99999999")  # very unlikely to exist
    assert agent.is_active() is False
    assert not paths.AGENT_FILE.exists()


def test_audit_appends_jsonl(tmp_state, tmp_path, monkeypatch):
    log = tmp_path / "agent.log"
    monkeypatch.setattr(agent, "AGENT_LOG_FILE", log)
    agent.audit("type_text", {"len": 5})
    agent.audit("press_key", {"combo": "Tab"}, result="ok")
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["tool"] == "type_text"
    assert parsed[1]["args"]["combo"] == "Tab"


def test_is_blocking_combines_pause_and_agent(tmp_state):
    from voice_opencode import state

    assert not agent.is_blocking()
    state.set_paused(True)
    assert agent.is_blocking()
    state.set_paused(False)
    agent.acquire()
    assert agent.is_blocking()
    agent.release()
    assert not agent.is_blocking()
