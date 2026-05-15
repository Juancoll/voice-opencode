"""Tests for the ydotool/wtype input backend's key-combo construction."""
from __future__ import annotations

from unittest.mock import patch


def _make_backend(monkeypatch, *, have_wtype=True, have_ydotool=True):
    """Build a YdotoolInputBackend with mocked tool availability."""
    from voice_opencode.backends.linux_input import ydotool_backend as yb

    def fake_which(name):
        if name == "wtype" and have_wtype:
            return "/usr/bin/wtype"
        if name == "ydotool" and have_ydotool:
            return "/usr/bin/ydotool"
        return None

    monkeypatch.setattr(yb.shutil, "which", fake_which)
    return yb.YdotoolInputBackend()


def test_simple_key(monkeypatch):
    be = _make_backend(monkeypatch)
    with patch("voice_opencode.backends.linux_input.ydotool_backend._run") as run:
        be.press_key("Tab")
    args = run.call_args.args[0]
    assert args[0] == "wtype"
    assert "-k" in args
    assert args[args.index("-k") + 1] == "Tab"


def test_modifier_combo(monkeypatch):
    be = _make_backend(monkeypatch)
    with patch("voice_opencode.backends.linux_input.ydotool_backend._run") as run:
        be.press_key("ctrl+a")
    args = run.call_args.args[0]
    assert "-M" in args and "ctrl" in args
    assert args[-2:] == ["-m", "ctrl"]   # released last
    assert args[args.index("-k") + 1] == "a"


def test_super_alias_maps_to_logo(monkeypatch):
    be = _make_backend(monkeypatch)
    with patch("voice_opencode.backends.linux_input.ydotool_backend._run") as run:
        be.press_key("super+l")
    args = run.call_args.args[0]
    assert "logo" in args
    assert args[args.index("-k") + 1] == "l"


def test_ydotool_only_falls_back_for_text(monkeypatch):
    """Without wtype, type_text should go through ydotool."""
    be = _make_backend(monkeypatch, have_wtype=False)
    with patch("voice_opencode.backends.linux_input.ydotool_backend._run") as run:
        be.type_text("hello", delay_ms=5)
    args = run.call_args.args[0]
    assert args[:2] == ["ydotool", "type"]
    assert args[-1] == "hello"


def test_capabilities_reflect_available_tools(monkeypatch):
    from voice_opencode.platform import capabilities as cap

    full = _make_backend(monkeypatch).capabilities()
    assert cap.INPUT_TYPE_TEXT in full
    assert cap.INPUT_CLICK_MOUSE in full

    keyboard_only = _make_backend(monkeypatch, have_ydotool=False).capabilities()
    assert cap.INPUT_TYPE_TEXT in keyboard_only
    assert cap.INPUT_CLICK_MOUSE not in keyboard_only
