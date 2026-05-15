"""Tests for the Hyprland WM backend.

We mock subprocess.run so we never actually shell out to hyprctl.
"""

from __future__ import annotations

import json

import pytest

from voice_opencode.platform.base import BackendError


def _hypr_with_pipes(monkeypatch, json_responses, dispatch_returns="ok"):
    """Build a HyprlandWindowManager whose hyprctl calls return canned data.

    json_responses is a dict {("subcommand", ...): obj_or_str}.
    """
    from voice_opencode.backends.linux_hyprland import wm as hwm

    monkeypatch.setattr(hwm.shutil, "which", lambda _: "/usr/bin/hyprctl")

    class FakeCompleted:
        def __init__(self, rc, out, err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[1] == "-j":
            key = tuple(cmd[2:])
            payload = json_responses.get(key, json_responses.get(key[:1]))
            if payload is None:
                return FakeCompleted(1, "", f"unmocked: {key}")
            body = payload if isinstance(payload, str) else json.dumps(payload)
            return FakeCompleted(0, body)
        if cmd[1] == "dispatch":
            return FakeCompleted(0, dispatch_returns)
        return FakeCompleted(1, "", f"unknown cmd: {cmd}")

    monkeypatch.setattr(hwm.subprocess, "run", fake_run)
    return hwm.HyprlandWindowManager()


def test_init_requires_hyprctl(monkeypatch):
    from voice_opencode.backends.linux_hyprland import wm as hwm
    monkeypatch.setattr(hwm.shutil, "which", lambda _: None)
    with pytest.raises(BackendError):
        hwm.HyprlandWindowManager()


def test_list_windows_translates_shapes(monkeypatch):
    raw = [
        {
            "address": "0xAA", "pid": 100, "class": "firefox",
            "title": "GitHub", "at": [10, 20], "size": [800, 600],
            "monitor": 0, "workspace": {"id": 1, "name": "1"},
            "floating": False, "fullscreen": 0, "hidden": False,
        },
        {
            "address": "0xBB", "pid": 200, "class": "kitty",
            "title": "shell", "at": [900, 0], "size": [600, 400],
            "monitor": 1, "workspace": {"id": 2, "name": "code"},
            "floating": True, "fullscreen": 0, "hidden": False,
        },
    ]
    wm = _hypr_with_pipes(monkeypatch, {
        ("version",): {"branch": "main"},
        ("clients",): raw,
        ("activewindow",): raw[0],
    })
    wins = wm.list_windows()
    assert len(wins) == 2
    ff = wins[0]
    assert ff.id == "0xAA"
    assert ff.app_id == "firefox"
    assert ff.rect.x == 10 and ff.rect.w == 800
    assert ff.workspace_id == 1
    assert ff.focused is True            # matches activewindow
    assert wins[1].focused is False
    assert wins[1].floating is True


def test_active_window_none(monkeypatch):
    wm = _hypr_with_pipes(monkeypatch, {
        ("version",): {"branch": "main"},
        ("activewindow",): "",
    })
    assert wm.active_window() is None


def test_find_windows_substring(monkeypatch):
    raw = [
        {"address": "0x1", "pid": 1, "class": "Firefox", "title": "OC | Issues",
         "at": [0, 0], "size": [100, 100], "monitor": 0,
         "workspace": {"id": 1, "name": "1"}},
        {"address": "0x2", "pid": 2, "class": "kitty", "title": "shell",
         "at": [0, 0], "size": [100, 100], "monitor": 0,
         "workspace": {"id": 1, "name": "1"}},
    ]
    wm = _hypr_with_pipes(monkeypatch, {
        ("version",): {"branch": "main"},
        ("clients",): raw,
        ("activewindow",): "",
    })
    assert [w.id for w in wm.find_windows("fire")] == ["0x1"]
    assert [w.id for w in wm.find_windows("OC")] == ["0x1"]
    assert wm.find_windows("nope") == []


def test_resolve_target_substring(monkeypatch):
    raw = [
        {"address": "0xCAFE", "pid": 1, "class": "kitty", "title": "x",
         "at": [0, 0], "size": [1, 1], "monitor": 0,
         "workspace": {"id": 1, "name": "1"}},
    ]
    wm = _hypr_with_pipes(monkeypatch, {
        ("version",): {"branch": "main"},
        ("clients",): raw,
        ("activewindow",): "",
    })
    assert wm._resolve_target("0xCAFE") == "address:0xCAFE"
    assert wm._resolve_target("address:0xDEAD") == "address:0xDEAD"
    assert wm._resolve_target("kitty") == "address:0xCAFE"
    with pytest.raises(BackendError):
        wm._resolve_target("nothing-matches")


def test_capabilities_excludes_minimize(monkeypatch):
    """Hyprland has no minimize; we shouldn't claim that capability."""
    from voice_opencode.platform import capabilities as cap
    wm = _hypr_with_pipes(monkeypatch, {("version",): {"branch": "main"}})
    caps = wm.capabilities()
    assert cap.WM_FOCUS_WINDOW in caps
    assert cap.WM_MINIMIZE_WINDOW not in caps
