"""Tests for the MCP server's safety rails (rate limit, blocklist)."""

from __future__ import annotations

from unittest.mock import patch

from voice_opencode import mcp_server


def test_dangerous_keys_blocked():
    assert mcp_server._is_dangerous_key("ctrl+alt+backspace")
    assert mcp_server._is_dangerous_key("CTRL+Alt+BackSpace")
    assert mcp_server._is_dangerous_key("ctrl+alt+f1")
    assert mcp_server._is_dangerous_key("ctrl+alt+f12")
    assert not mcp_server._is_dangerous_key("ctrl+a")
    assert not mcp_server._is_dangerous_key("Tab")
    assert not mcp_server._is_dangerous_key("super+Return")


def test_rate_limit_eventually_throttles(monkeypatch):
    # Reset the deque
    mcp_server._calls.clear()
    monkeypatch.setattr(mcp_server, "_RATE_LIMIT", 5)
    # 5 calls allowed, 6th throttled (within window).
    for _ in range(5):
        assert mcp_server._rate_check() is True
    assert mcp_server._rate_check() is False


def test_rate_limit_window_expires(monkeypatch):
    mcp_server._calls.clear()
    monkeypatch.setattr(mcp_server, "_RATE_LIMIT", 2)
    monkeypatch.setattr(mcp_server, "_RATE_WINDOW_S", 0.01)
    assert mcp_server._rate_check()
    assert mcp_server._rate_check()
    assert mcp_server._rate_check() is False
    import time as _t
    _t.sleep(0.02)
    assert mcp_server._rate_check() is True


def test_build_server_registers_tools():
    """Smoke: server constructs and the MCP machinery sees our tools."""
    with patch("voice_opencode.mcp_server.agent.audit"):
        srv = mcp_server.build_server()
    # FastMCP exposes registered tools via _tool_manager.
    names = {t.name for t in srv._tool_manager.list_tools()}
    expected = {
        "type_text", "press_key", "move_mouse", "click_mouse",
        "focused_window", "capture_screen", "list_monitors", "sleep_ms",
    }
    assert expected <= names


def test_guard_returns_error_when_throttled(monkeypatch):
    mcp_server._calls.clear()
    monkeypatch.setattr(mcp_server, "_RATE_LIMIT", 1)
    with patch("voice_opencode.mcp_server.agent.audit"):
        assert mcp_server._guard("foo", {}) is None  # 1st ok
        err = mcp_server._guard("foo", {})
        assert err is not None and "rate limit" in err
