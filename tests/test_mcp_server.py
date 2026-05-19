"""Tests for the MCP server's safety rails and tool registration."""

from __future__ import annotations

from unittest.mock import patch

from voice_opencode import mcp_server


# ---------------------------------------------------------------------------
# Safety rails
# ---------------------------------------------------------------------------
def test_dangerous_keys_blocked():
    assert mcp_server._is_dangerous_key("ctrl+alt+backspace")
    assert mcp_server._is_dangerous_key("CTRL+Alt+BackSpace")
    assert mcp_server._is_dangerous_key("ctrl+alt+f1")
    assert mcp_server._is_dangerous_key("ctrl+alt+f12")
    assert not mcp_server._is_dangerous_key("ctrl+a")
    assert not mcp_server._is_dangerous_key("Tab")
    assert not mcp_server._is_dangerous_key("super+Return")


def test_rate_limit_eventually_throttles(monkeypatch):
    mcp_server._calls.clear()
    monkeypatch.setattr(mcp_server, "_RATE_LIMIT", 5)
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


def test_guard_returns_error_when_throttled(monkeypatch):
    mcp_server._calls.clear()
    monkeypatch.setattr(mcp_server, "_RATE_LIMIT", 1)
    with patch("voice_opencode.mcp_server.agent.audit"):
        assert mcp_server._guard("foo", {}) is None
        err = mcp_server._guard("foo", {})
        assert err is not None and "rate limit" in err


# ---------------------------------------------------------------------------
# Tool registration is capability-driven
# ---------------------------------------------------------------------------
def _registered(srv) -> set[str]:
    return {t.name for t in srv._tool_manager.list_tools()}


def test_build_server_always_registers_misc():
    """Regardless of platform, sleep_ms and platform_info exist."""
    with patch("voice_opencode.mcp_server.agent.audit"):
        srv = mcp_server.build_server()
    names = _registered(srv)
    assert {"sleep_ms", "platform_info"} <= names


def test_build_server_no_caps_means_no_tools(monkeypatch):
    """If every capability returns False, only the unconditional ones survive."""
    monkeypatch.setattr(mcp_server.plat, "supported", lambda _cap: False)
    with patch("voice_opencode.mcp_server.agent.audit"):
        srv = mcp_server.build_server()
    names = _registered(srv)
    # Acting tools should be filtered out.
    assert "type_text" not in names
    assert "click_mouse" not in names
    assert "list_windows" not in names
    assert "capture_screen" not in names
    # But the unconditional ones remain.
    assert "sleep_ms" in names
    assert "platform_info" in names


def test_build_server_full_caps_registers_everything(monkeypatch):
    """When every capability is True, the full tool surface is exposed."""
    monkeypatch.setattr(mcp_server.plat, "supported", lambda _cap: True)
    with patch("voice_opencode.mcp_server.agent.audit"):
        srv = mcp_server.build_server()
    names = _registered(srv)
    # Spot-check one tool from each group.
    assert "type_text" in names              # input
    assert "click_mouse" in names
    assert "capture_screen" in names         # screen
    assert "list_monitors" in names
    assert "list_windows" in names           # windows
    assert "focus_window" in names
    assert "switch_workspace" in names
    assert "clipboard_read" in names         # clipboard
    assert "clipboard_write" in names
    assert "sleep_ms" in names               # misc
    assert "platform_info" in names


# ---------------------------------------------------------------------------
# Capacity-mode filtering (Phase D)
# ---------------------------------------------------------------------------
def _build_with(monkeypatch, mode: str) -> set[str]:
    from dataclasses import replace

    from voice_opencode import config
    monkeypatch.setattr(mcp_server.plat, "supported", lambda _cap: True)
    monkeypatch.setattr(
        config, "settings", replace(config.settings, capacity_mode=mode)
    )
    with patch("voice_opencode.mcp_server.agent.audit"):
        srv = mcp_server.build_server()
    return _registered(srv)


def test_capacity_read_only_hides_acting_tools(monkeypatch):
    names = _build_with(monkeypatch, "read-only")
    # Read-only tools visible.
    for must in ("list_windows", "capture_screen", "clipboard_read",
                 "notify", "platform_info"):
        assert must in names
    # Acting tools hidden.
    for hidden in ("type_text", "click_mouse", "clipboard_write",
                   "focus_window", "ask_user", "close_window"):
        assert hidden not in names, hidden


def test_capacity_assist_hides_destructive_only(monkeypatch):
    names = _build_with(monkeypatch, "assist")
    for must in ("type_text", "click_mouse", "focus_window",
                 "clipboard_write", "ask_user"):
        assert must in names
    # close_window is reserved for 'full'.
    assert "close_window" not in names


def test_capacity_full_exposes_everything(monkeypatch):
    names = _build_with(monkeypatch, "full")
    assert "close_window" in names
    assert "type_text" in names
    assert "list_windows" in names


def test_audio_media_tools_registered(monkeypatch):
    """Phase H tools land in the right tiers."""
    ro = _build_with(monkeypatch, "read-only")
    assert "audio_get_volume" in ro
    assert "media_status" in ro
    assert "audio_set_volume" not in ro
    assert "media_play_pause" not in ro

    a = _build_with(monkeypatch, "assist")
    for must in ("audio_set_volume", "audio_mute_toggle",
                 "audio_mic_mute_toggle", "media_play_pause",
                 "media_next", "media_prev"):
        assert must in a


def test_apps_tools_registered(monkeypatch):
    """Phase I tools land in the right tiers."""
    ro = _build_with(monkeypatch, "read-only")
    assert "apps_list_installed" in ro
    assert "apps_list_running" in ro
    assert "apps_launch" not in ro
    assert "apps_kill" not in ro

    a = _build_with(monkeypatch, "assist")
    assert "apps_launch" in a
    assert "apps_kill" not in a            # kill is full-only

    f = _build_with(monkeypatch, "full")
    assert "apps_kill" in f


def test_shell_run_only_in_full(monkeypatch):
    """Phase E: shell_run must NOT leak into read-only or assist."""
    ro = _build_with(monkeypatch, "read-only")
    assert "shell_run" not in ro
    a = _build_with(monkeypatch, "assist")
    assert "shell_run" not in a
    f = _build_with(monkeypatch, "full")
    assert "shell_run" in f


def test_ocr_tools_in_read_only(monkeypatch):
    """Phase F: OCR tools must be available in every tier (read-only)."""
    ro = _build_with(monkeypatch, "read-only")
    # OCR tools require the tesseract backend to be wired; if absent
    # (CI host without tesseract) the tools simply won't appear.
    # On any host where the capability is present, they MUST be in
    # read-only — they only read pixels.
    from voice_opencode import platform as plat
    if plat.supported("ocr.find_text"):
        assert "screen_find_text" in ro
        assert "ocr_find_text_in_file" in ro


# ---------------------------------------------------------------------------
# Focus guard (ADR-0025): block type/click/press unless a recent
# focus_window matches the current active window.
# ---------------------------------------------------------------------------
def test_focus_guard_blocks_without_recent_focus(monkeypatch):
    """No focus_window in the last N seconds => guard refuses."""
    mcp_server._focus_state["target"] = ""
    mcp_server._focus_state["expected_id"] = ""
    mcp_server._focus_state["ts"] = 0.0
    err = mcp_server._focus_guard("type_text")
    assert err is not None and "focus_window" in err


def test_focus_guard_expires_after_ttl(monkeypatch):
    import time as _t
    mcp_server._record_focus("chrome", "0x1234")
    monkeypatch.setattr(mcp_server, "_FOCUS_GUARD_TTL_S", 0.01)
    _t.sleep(0.02)
    err = mcp_server._focus_guard("click_mouse")
    assert err is not None and "focus_window" in err


def test_focus_guard_passes_when_recent_and_no_active_window_cap(monkeypatch):
    """If the WM can't tell us active window, trust the recent focus."""
    from voice_opencode import platform as plat
    mcp_server._record_focus("chrome", "0x1234")
    monkeypatch.setattr(plat, "supported", lambda c: False)
    assert mcp_server._focus_guard("type_text") is None


def test_focus_guard_blocks_when_active_window_drifted(monkeypatch):
    """User switched window after agent focused => refuse to type."""
    from voice_opencode import platform as plat
    from voice_opencode.platform.types import Rect, Window

    mcp_server._record_focus("chrome", "0xCHROME")

    class FakeWM:
        def active_window(self):
            return Window(id="0xTERMINAL", pid=1, app_id="kitty",
                          title="t", rect=Rect(0, 0, 100, 100),
                          monitor_id=0, workspace_id=1,
                          focused=True, floating=False,
                          fullscreen=False)

    monkeypatch.setattr(plat, "supported", lambda c: True)
    monkeypatch.setattr(plat, "wm", FakeWM())
    err = mcp_server._focus_guard("type_text")
    assert err is not None and "drifted" not in err  # message says "changed"
    assert "0xCHROME" in err and "0xTERMINAL" in err


def test_focus_guard_passes_when_active_matches(monkeypatch):
    from voice_opencode import platform as plat
    from voice_opencode.platform.types import Rect, Window

    mcp_server._record_focus("chrome", "0xCHROME")

    class FakeWM:
        def active_window(self):
            return Window(id="0xCHROME", pid=1, app_id="chrome",
                          title="t", rect=Rect(0, 0, 100, 100),
                          monitor_id=0, workspace_id=1,
                          focused=True, floating=False,
                          fullscreen=False)

    monkeypatch.setattr(plat, "supported", lambda c: True)
    monkeypatch.setattr(plat, "wm", FakeWM())
    assert mcp_server._focus_guard("type_text") is None


# ---------------------------------------------------------------------------
# _fmt_action: one-line label for the per-turn notification
# ---------------------------------------------------------------------------
def test_fmt_action_short():
    assert mcp_server._fmt_action("click_mouse",
                                  {"button": "left", "x": 100, "y": 200}) \
        == "click_mouse(button='left', x=100, y=200)"


def test_fmt_action_truncates_long_string_values():
    s = "a" * 100
    out = mcp_server._fmt_action("type_text", {"text": s})
    # repr adds quotes; value alone gets truncated to 27 + ellipsis
    assert "…" in out
    assert len(out) < 100


def test_fmt_action_truncates_overall_body():
    out = mcp_server._fmt_action("x", {f"k{i}": f"v{i}" for i in range(20)})
    assert len(out) <= 80
    assert out.endswith("…")


# ---------------------------------------------------------------------------
# _audit wrapper: pipes ok-results into turn_update
# ---------------------------------------------------------------------------
def test_audit_calls_turn_update_on_ok(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "turn_update",
                        lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(mcp_server.agent, "audit", lambda *a, **k: None)
    mcp_server._audit("type_text", {"text": "hola"})
    assert len(calls) == 1
    assert "type_text" in calls[0][1]


def test_audit_skips_turn_update_on_error_result(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "turn_update",
                        lambda title, body: calls.append((title, body)))
    monkeypatch.setattr(mcp_server.agent, "audit", lambda *a, **k: None)
    mcp_server._audit("type_text", {"text": "x"}, result="refused: ...")
    assert calls == []
