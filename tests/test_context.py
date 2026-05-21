"""Tests for the per-turn context module (monitor layout injection)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice_opencode import context


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Drop the cache between tests so each starts from scratch."""
    context.reset_cache()
    yield
    context.reset_cache()


def _fake_monitor(name, x, y, w, h, *, focused=False):
    """Build a stand-in monitor object exposing ``.to_dict()``."""
    payload = {
        "id": 0,
        "name": name,
        "rect": {"x": x, "y": y, "w": w, "h": h},
        "scale": 1.0,
        "focused": focused,
        "primary": False,
        "refresh_hz": 60.0,
    }
    return SimpleNamespace(to_dict=lambda: payload)


def _patch_screen(monkeypatch, monitors):
    """Replace ``platform.screen`` with a stub whose list_monitors returns ``monitors``."""
    stub = SimpleNamespace(list_monitors=lambda: monitors)
    monkeypatch.setattr(context._platform, "screen", stub, raising=False)


def test_single_monitor_renders_compact_block(monkeypatch):
    _patch_screen(monkeypatch, [_fake_monitor("DP-1", 0, 0, 1920, 1080, focused=True)])
    text = context.monitor_layout_text()
    assert "DP-1 1920x1080 @ (0,0) [FOCUSED]" in text
    assert "Monitor layout" in text
    # Cue the model to call list_monitors for fresher data.
    assert "list_monitors" in text


def test_multi_monitor_sorted_left_to_right(monkeypatch):
    _patch_screen(
        monkeypatch,
        [
            _fake_monitor("DP-4", 2560, 0, 2560, 1440),
            _fake_monitor("DP-3", 0, 0, 2560, 1440, focused=True),
        ],
    )
    text = context.monitor_layout_text()
    # DP-3 (x=0) must appear before DP-4 (x=2560).
    assert text.index("DP-3") < text.index("DP-4")
    # Only the focused monitor carries the tag.
    assert "DP-3 2560x1440 @ (0,0) [FOCUSED]" in text
    assert "DP-4 2560x1440 @ (2560,0)" in text
    assert "DP-4 2560x1440 @ (2560,0) [FOCUSED]" not in text


def test_empty_monitor_list_returns_empty_string(monkeypatch):
    _patch_screen(monkeypatch, [])
    assert context.monitor_layout_text() == ""


def test_backend_failure_degrades_silently(monkeypatch):
    def _boom():
        raise RuntimeError("hyprctl not on PATH")

    stub = SimpleNamespace(list_monitors=_boom)
    monkeypatch.setattr(context._platform, "screen", stub, raising=False)
    # Should not raise; returns empty so the pipeline simply skips
    # the extra context part.
    assert context.monitor_layout_text() == ""


def test_result_is_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return [_fake_monitor("DP-1", 0, 0, 1920, 1080, focused=True)]

    monkeypatch.setattr(
        context._platform,
        "screen",
        SimpleNamespace(list_monitors=_count),
        raising=False,
    )
    a = context.monitor_layout_text()
    b = context.monitor_layout_text()
    assert a == b
    assert calls["n"] == 1, "second call within TTL must hit the cache"


def test_force_refresh_bypasses_cache(monkeypatch):
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return [_fake_monitor("DP-1", 0, 0, 1920, 1080, focused=True)]

    monkeypatch.setattr(
        context._platform,
        "screen",
        SimpleNamespace(list_monitors=_count),
        raising=False,
    )
    context.monitor_layout_text()
    context.monitor_layout_text(force_refresh=True)
    assert calls["n"] == 2


def test_reset_cache_clears_state(monkeypatch):
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return [_fake_monitor("DP-1", 0, 0, 1920, 1080, focused=True)]

    monkeypatch.setattr(
        context._platform,
        "screen",
        SimpleNamespace(list_monitors=_count),
        raising=False,
    )
    context.monitor_layout_text()
    context.reset_cache()
    context.monitor_layout_text()
    assert calls["n"] == 2
