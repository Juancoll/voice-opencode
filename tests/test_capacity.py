"""Tests for ``voice_opencode.capacity``."""

from __future__ import annotations

from dataclasses import replace

import pytest

from voice_opencode import capacity, config


@pytest.fixture
def with_mode(monkeypatch):
    """Yield a callable that swaps settings.capacity_mode for the test."""
    original = config.settings

    def _set(mode: str) -> None:
        monkeypatch.setattr(config, "settings", replace(original, capacity_mode=mode))

    yield _set


def test_tier_mapping_is_total_and_known():
    """Every tool in the mapping must reference a known tier."""
    valid = {"read-only", "assist", "full"}
    for tool, tier in capacity.TIER_BY_TOOL.items():
        assert tier in valid, f"{tool} → {tier}"


def test_read_only_mode_only_exposes_safe_tools(with_mode):
    with_mode("read-only")
    exposed = capacity.tools_for()
    # Spot-check a few that must be present.
    for must_have in ("list_windows", "capture_screen", "clipboard_read",
                       "notify", "platform_info"):
        assert must_have in exposed
    # And several that must NOT be present.
    for forbidden in ("type_text", "click_mouse", "clipboard_write",
                       "ask_user", "close_window", "focus_window"):
        assert forbidden not in exposed, forbidden


def test_assist_mode_adds_reversible_actions(with_mode):
    with_mode("assist")
    exposed = capacity.tools_for()
    for must_have in ("type_text", "click_mouse", "focus_window",
                       "clipboard_write", "ask_user", "ask_confirm"):
        assert must_have in exposed
    # close_window stays out of assist.
    assert "close_window" not in exposed


def test_full_mode_exposes_everything(with_mode):
    with_mode("full")
    exposed = capacity.tools_for()
    assert exposed == frozenset(capacity.TIER_BY_TOOL)


def test_allows_unknown_tool_defaults_to_full(with_mode):
    with_mode("assist")
    # Unknown tool name → treated as 'full', so assist denies it.
    assert capacity.allows("undefined_future_tool") is False
    with_mode("full")
    assert capacity.allows("undefined_future_tool") is True


def test_unknown_mode_falls_back_to_assist(with_mode, caplog):
    with_mode("paranoid")  # not a known tier
    assert capacity.current_mode() == "assist"
    # Behaviour should match assist exactly.
    assert capacity.tools_for() == capacity.tools_for("assist")


def test_allows_respects_explicit_mode_arg():
    # Argument overrides settings regardless of monkeypatching.
    assert capacity.allows("close_window", "full") is True
    assert capacity.allows("close_window", "assist") is False
    assert capacity.allows("clipboard_read", "read-only") is True


def test_tiers_are_monotonic():
    """If a tool is allowed in read-only it must be allowed in assist+full."""
    for tool in capacity.TIER_BY_TOOL:
        if capacity.allows(tool, "read-only"):
            assert capacity.allows(tool, "assist")
            assert capacity.allows(tool, "full")
        if capacity.allows(tool, "assist"):
            assert capacity.allows(tool, "full")
