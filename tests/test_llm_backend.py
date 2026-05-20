"""Tests for the LLM backend abstraction (ADR-0029)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from voice_opencode import config, llm


def _Backend():
    """Import OpencodeBackend lazily; ``conftest.tmp_state`` reloads
    the module, so a top-level import would refer to a stale class."""
    from voice_opencode.opencode_client import OpencodeBackend
    return OpencodeBackend


def test_opencode_backend_satisfies_protocol():
    """The default adapter must structurally implement LLMBackend."""
    b = _Backend()()
    assert isinstance(b, llm.LLMBackend)
    assert b.name == "opencode"


def test_get_backend_returns_opencode_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE_LLM_BACKEND", raising=False)
    monkeypatch.setattr(config, "settings", config.Settings(llm_backend="opencode"))
    llm.reset_backend_cache()
    b = llm.get_backend()
    assert b.name == "opencode"
    assert isinstance(b, _Backend())


def test_get_backend_is_cached(monkeypatch):
    monkeypatch.setattr(config, "settings", config.Settings(llm_backend="opencode"))
    llm.reset_backend_cache()
    b1 = llm.get_backend()
    b2 = llm.get_backend()
    assert b1 is b2


def test_get_backend_unknown_name_raises(monkeypatch):
    monkeypatch.setattr(
        config, "settings", config.Settings(llm_backend="bogus-xyz")
    )
    llm.reset_backend_cache()
    with pytest.raises(RuntimeError, match="Unknown llm_backend"):
        llm.get_backend()
    llm.reset_backend_cache()  # don't leak failure to other tests


def test_reset_backend_cache_drops_singleton(monkeypatch):
    monkeypatch.setattr(config, "settings", config.Settings(llm_backend="opencode"))
    llm.reset_backend_cache()
    b1 = llm.get_backend()
    llm.reset_backend_cache()
    b2 = llm.get_backend()
    assert b1 is not b2


def test_config_reload_invalidates_backend_cache(monkeypatch):
    monkeypatch.setattr(config, "settings", config.Settings(llm_backend="opencode"))
    llm.reset_backend_cache()
    b1 = llm.get_backend()
    with patch.object(config, "_load_user_json", return_value={}):
        config.reload()
    b2 = llm.get_backend()
    assert b1 is not b2


def test_env_var_selects_backend(monkeypatch):
    monkeypatch.setenv("VOICE_LLM_BACKEND", "opencode")
    fresh = config.load()
    assert fresh.llm_backend == "opencode"


def test_opencode_backend_session_id_reads_file(tmp_state):
    from voice_opencode.paths import SESSION_FILE
    SESSION_FILE.write_text("sess-abc")
    b = _Backend()()
    assert b.session_id() == "sess-abc"
    b.forget()
    assert b.session_id() is None


def test_opencode_backend_abort_no_session_returns_false(tmp_state):
    b = _Backend()()
    b.forget()
    assert b.abort() is False


def test_opencode_backend_abort_with_session_posts(tmp_state):
    from voice_opencode.paths import SESSION_FILE
    SESSION_FILE.write_text("sess-1")
    b = _Backend()()
    with patch("voice_opencode.opencode_client.requests.post") as post:
        post.return_value.ok = True
        post.return_value.status_code = 200
        assert b.abort() is True
        assert "/session/sess-1/abort" in post.call_args.args[0]
