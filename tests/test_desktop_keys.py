"""Tests for desktop.press_key wtype-arg construction."""
from __future__ import annotations

from unittest.mock import patch


def test_simple_key(monkeypatch):
    from voice_opencode import desktop
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/wtype" if x == "wtype" else None)
    with patch("subprocess.run") as run:
        desktop.press_key("Tab")
    args = run.call_args.args[0]
    assert args[0] == "wtype"
    assert "-k" in args
    assert args[args.index("-k") + 1] == "Tab"


def test_modifier_combo(monkeypatch):
    from voice_opencode import desktop
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/wtype" if x == "wtype" else None)
    with patch("subprocess.run") as run:
        desktop.press_key("ctrl+a")
    args = run.call_args.args[0]
    assert "-M" in args and "ctrl" in args
    assert args[-2:] == ["-m", "ctrl"]   # released last
    assert args[args.index("-k") + 1] == "a"


def test_super_alias_maps_to_logo(monkeypatch):
    from voice_opencode import desktop
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/wtype" if x == "wtype" else None)
    with patch("subprocess.run") as run:
        desktop.press_key("super+l")
    args = run.call_args.args[0]
    assert "logo" in args
    assert args[args.index("-k") + 1] == "l"
