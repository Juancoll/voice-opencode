"""Tests for the audit log reader (``agent.audit_tail``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_opencode import agent


@pytest.fixture
def fake_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "agent.log"
    monkeypatch.setattr(agent, "AGENT_LOG_FILE", log)
    return log


def test_returns_empty_when_missing(fake_log: Path) -> None:
    assert agent.audit_tail(50) == []


def test_returns_empty_when_n_le_zero(fake_log: Path) -> None:
    fake_log.write_text('{"tool":"x"}\n')
    assert agent.audit_tail(0) == []
    assert agent.audit_tail(-5) == []


def test_returns_newest_last(fake_log: Path) -> None:
    fake_log.write_text(
        '{"tool":"a","ts":"1"}\n'
        '{"tool":"b","ts":"2"}\n'
        '{"tool":"c","ts":"3"}\n'
    )
    out = agent.audit_tail(50)
    assert [e["tool"] for e in out] == ["a", "b", "c"]


def test_tail_limits_n(fake_log: Path) -> None:
    fake_log.write_text("".join(
        f'{{"tool":"t{i}"}}\n' for i in range(20)))
    out = agent.audit_tail(5)
    assert len(out) == 5
    assert [e["tool"] for e in out] == [f"t{i}" for i in range(15, 20)]


def test_skips_malformed_lines(fake_log: Path) -> None:
    fake_log.write_text(
        '{"tool":"ok1"}\n'
        'not-json-at-all\n'
        '{"tool":"ok2"}\n'
        '\n'                     # empty line
        '{"tool":"ok3"}\n'
    )
    out = agent.audit_tail(50)
    assert [e["tool"] for e in out] == ["ok1", "ok2", "ok3"]


def test_skips_non_dict_json(fake_log: Path) -> None:
    fake_log.write_text(
        '{"tool":"ok"}\n'
        '"a string"\n'
        '[1,2,3]\n'
        '42\n'
        '{"tool":"ok2"}\n'
    )
    out = agent.audit_tail(50)
    assert [e["tool"] for e in out] == ["ok", "ok2"]


def test_audit_roundtrip(fake_log: Path) -> None:
    """``audit`` writes lines that ``audit_tail`` reads back faithfully."""
    agent.audit("media_status", {"player": "chromium"}, "ok")
    agent.audit("clipboard_write", {"len": 5}, "ok")
    out = agent.audit_tail(10)
    assert len(out) == 2
    assert out[0]["tool"] == "media_status"
    assert out[0]["args"] == {"player": "chromium"}
    assert out[1]["tool"] == "clipboard_write"
    # ts is present and ISO-8601-ish.
    assert "T" in out[0]["ts"] and out[0]["ts"].endswith("Z")


def test_audit_tail_survives_unreadable_file(
    fake_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_log.write_text('{"tool":"x"}\n')
    def _boom(*_a: object, **_k: object) -> str:
        raise OSError("io")
    monkeypatch.setattr(type(fake_log), "read_text", _boom)
    assert agent.audit_tail(10) == []


def test_audit_handles_unicode(fake_log: Path) -> None:
    agent.audit("notify", {"body": "Sin filtros — más volumen 🎵"}, "ok")
    raw = fake_log.read_text(encoding="utf-8")
    parsed = json.loads(raw.strip())
    # ensure_ascii=False keeps unicode literal in the log.
    assert "más" in raw
    assert parsed["args"]["body"].endswith("🎵")
