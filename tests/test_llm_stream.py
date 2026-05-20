"""Tests for streaming LLM backend (ADR-0030)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _sse_lines(events: list[dict]) -> list[str]:
    """Render a list of event dicts as raw SSE 'data:' lines."""
    out: list[str] = []
    for evt in events:
        out.append(f"data: {json.dumps(evt)}")
        out.append("")  # SSE event terminator
    return out


def _mock_sse_response(lines: list[str]) -> MagicMock:
    r = MagicMock()
    r.iter_lines.return_value = iter(lines)
    r.raise_for_status.return_value = None
    r.close.return_value = None
    return r


@pytest.fixture
def session_id_present(tmp_state):
    from voice_opencode.paths import SESSION_FILE
    SESSION_FILE.write_text("sess-stream")
    return "sess-stream"


def test_ask_stream_yields_deltas_in_order(session_id_present):
    """Each message.part.delta with field=text becomes one yield."""
    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [
        {"type": "server.connected", "properties": {}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "Ho"}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "la, "}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "mundo."}},
        {"type": "session.idle", "properties": {"sessionID": sid}},
    ]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        chunks = list(b.ask_stream("test"))
    assert chunks == ["Ho", "la, ", "mundo."]


def test_ask_stream_filters_other_sessions(session_id_present):
    """Deltas from other sessions are ignored."""
    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [
        {"type": "message.part.delta",
         "properties": {"sessionID": "other-session",
                        "field": "text", "delta": "nope"}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "ok"}},
        {"type": "session.idle", "properties": {"sessionID": sid}},
    ]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        chunks = list(b.ask_stream("test"))
    assert chunks == ["ok"]


def test_ask_stream_ignores_non_text_field(session_id_present):
    """Deltas for fields other than 'text' (e.g. tool args) are dropped."""
    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "args", "delta": "{...}"}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "ok"}},
        {"type": "session.idle", "properties": {"sessionID": sid}},
    ]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        chunks = list(b.ask_stream("test"))
    assert chunks == ["ok"]


def test_ask_stream_stops_on_session_idle(session_id_present):
    """Events after session.idle (e.g. for next turn) are not consumed."""
    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "a"}},
        {"type": "session.idle", "properties": {"sessionID": sid}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "leaked"}},
    ]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        chunks = list(b.ask_stream("test"))
    assert chunks == ["a"]


def test_ask_calls_ask_stream_and_joins(session_id_present):
    """Non-streaming ask() is now a wrapper over ask_stream()."""
    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "Hola "}},
        {"type": "message.part.delta",
         "properties": {"sessionID": sid, "field": "text", "delta": "mundo"}},
        {"type": "session.idle", "properties": {"sessionID": sid}},
    ]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        reply = b.ask("test")
    assert reply == "Hola mundo"


def test_ask_stream_aborts_on_sse_connect_error(session_id_present):
    """If GET /event fails, abort() is called and the error re-raises."""
    from voice_opencode.opencode_client import OpencodeBackend
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch.object(b, "abort") as abort, \
         patch("voice_opencode.opencode_client.requests.get",
               side_effect=ConnectionError("boom")), pytest.raises(ConnectionError):
        list(b.ask_stream("test"))
    abort.assert_called_once()


def test_ask_stream_aborts_on_post_failure(session_id_present):
    """If POST fails, ask_stream still drains SSE then surfaces error."""
    import requests as _req

    from voice_opencode.opencode_client import OpencodeBackend
    sid = session_id_present
    events = [{"type": "session.idle", "properties": {"sessionID": sid}}]
    sse_resp = _mock_sse_response(_sse_lines(events))
    b = OpencodeBackend()
    with patch.object(b, "ensure_session"), \
         patch.object(b, "abort") as abort, \
         patch("voice_opencode.opencode_client.requests.get",
               return_value=sse_resp), \
         patch("voice_opencode.opencode_client.requests.post",
               side_effect=_req.HTTPError("500")), pytest.raises(_req.HTTPError):
        list(b.ask_stream("test"))
    abort.assert_called_once()
