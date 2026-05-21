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


# ---------------------------------------------------------------------------
# system_info_text()
# ---------------------------------------------------------------------------
def test_system_info_text_renders_os_and_versions(monkeypatch):
    from voice_opencode import host_info

    fake = host_info.HostInfo(
        os_name="Linux",
        os_release="6.13.4-zen1",
        distro="CachyOS",
        arch="x86_64",
        python_version="3.13.1",
        hyprland_version="v0.55.2",
        whisper_version="1.5.4",
        piper_version="1.2.0",
        opencode_version="0.3.2",
        locale="es_AR.UTF-8",
        audio_sink="",
        audio_source="",
    )
    monkeypatch.setattr(host_info, "_cache", fake)
    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: fake)

    out = context.system_info_text()
    assert "Linux 6.13.4-zen1" in out
    assert "CachyOS" in out
    assert "x86_64" in out
    assert "es_AR.UTF-8" in out
    assert "hyprland v0.55.2" in out
    assert "whisper.cpp 1.5.4" in out
    assert "piper 1.2.0" in out


def test_system_info_text_empty_when_nothing_detected(monkeypatch):
    from voice_opencode import host_info

    fake = host_info.HostInfo()  # all empty strings
    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: fake)
    assert context.system_info_text() == ""


# ---------------------------------------------------------------------------
# audio_info_text()
# ---------------------------------------------------------------------------
def test_audio_info_text_renders_both_devices(monkeypatch):
    from voice_opencode import host_info

    fake = host_info.HostInfo(audio_sink="Built-in Output", audio_source="USB Mic")
    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: fake)
    out = context.audio_info_text()
    assert "output=Built-in Output" in out
    assert "input=USB Mic" in out


def test_audio_info_text_empty_when_unknown(monkeypatch):
    from voice_opencode import host_info

    fake = host_info.HostInfo()
    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: fake)
    assert context.audio_info_text() == ""


# ---------------------------------------------------------------------------
# build_extra_context()
# ---------------------------------------------------------------------------
def test_build_extra_context_joins_non_empty_sections(monkeypatch):
    """All three sections present ⇒ all three in the output."""
    from voice_opencode import host_info

    fake = host_info.HostInfo(
        os_name="Linux",
        python_version="3.13.1",
        audio_sink="Built-in",
    )
    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: fake)
    monkeypatch.setattr(
        context._platform,
        "screen",
        SimpleNamespace(list_monitors=lambda: [
            SimpleNamespace(to_dict=lambda: {
                "name": "DP-1",
                "rect": {"x": 0, "y": 0, "w": 1920, "h": 1080},
                "focused": True,
            })
        ]),
        raising=False,
    )
    out = context.build_extra_context()
    assert "Monitor layout" in out
    assert "System: Linux" in out
    assert "Audio: output=Built-in" in out
    # Blank line between sections.
    assert "\n\n" in out


def test_build_extra_context_skips_empty_sections(monkeypatch):
    """Empty section ⇒ no orphan header, no double blank lines."""
    from voice_opencode import host_info

    monkeypatch.setattr(host_info, "get", lambda *, force_refresh=False: host_info.HostInfo())
    monkeypatch.setattr(
        context._platform,
        "screen",
        SimpleNamespace(list_monitors=lambda: []),
        raising=False,
    )
    assert context.build_extra_context() == ""
