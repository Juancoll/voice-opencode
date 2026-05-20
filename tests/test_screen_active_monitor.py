"""Tests for the active-window monitor resolution in the wlroots screen
backend, and for the HUD-offscreen context manager in ``screenshot``.

Motivated by the 2026-05-20 incident: ``capture_monitor()`` defaulted
to ``focused_monitor()`` (the flag from ``hyprctl monitors``), which
follows the cursor / last focus. When the cursor (or a floating
pinned overlay like our own HUD) crossed a monitor boundary, grim
recorded the wrong screen and the model described UI the user could
not see. ADR-0028 fixes this by preferring the monitor that holds
the active *window*, and by parking the HUD off-screen during the
grim call.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Wlroots ScreenBackend — active-window monitor resolution
# ---------------------------------------------------------------------------
@pytest.fixture
def screen_backend(monkeypatch):
    """Build a WlrootsScreenBackend with grim+hyprctl pretending to exist."""
    from voice_opencode.backends.linux_wlroots import screen as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/fake")
    b = mod.WlrootsScreenBackend()
    return b, mod


def _set_run(mod, monkeypatch, responses):
    """Replace ``_run`` with a lookup over the first two argv tokens.

    ``responses`` is {(arg0, arg1, ...): (rc, stdout, stderr)} OR
    a callable cmd -> (rc, out, err).
    """
    def fake_run(cmd, timeout=2):
        if callable(responses):
            return responses(cmd)
        key = tuple(cmd[:3])
        if key in responses:
            return responses[key]
        # Fall back to first two tokens
        key2 = tuple(cmd[:2])
        if key2 in responses:
            return responses[key2]
        return (1, "", f"no canned response for {cmd!r}")

    monkeypatch.setattr(mod, "_run", fake_run)


def test_capture_monitor_uses_active_window_monitor(screen_backend, monkeypatch, tmp_path):
    """ADR-0028: prefer the monitor that holds the active window."""
    b, mod = screen_backend
    monitors = [
        {"id": 0, "name": "DP-3", "x": 0, "y": 0, "width": 2560, "height": 1440,
         "scale": 1.0, "focused": True, "refreshRate": 60.0},
        {"id": 1, "name": "DP-4", "x": 2560, "y": 0, "width": 2560, "height": 1440,
         "scale": 1.0, "focused": False, "refreshRate": 60.0},
    ]
    # Active window lives on monitor id=1 (DP-4) even though DP-3 is "focused".
    active = {"monitor": 1, "at": [3000, 100], "size": [800, 600]}
    captured = {}

    def fake_run(cmd, timeout=2):
        if cmd[:3] == ["hyprctl", "-j", "monitors"]:
            return (0, json.dumps(monitors), "")
        if cmd[:3] == ["hyprctl", "-j", "activewindow"]:
            return (0, json.dumps(active), "")
        if cmd[0] == "grim":
            captured["argv"] = cmd
            Path(cmd[-1]).write_bytes(b"\x89PNG fake")
            return (0, "", "")
        return (1, "", f"unexpected {cmd!r}")

    monkeypatch.setattr(mod, "_run", fake_run)

    out = b.capture_monitor(tmp_path / "out.png")
    assert out.exists()
    # We must have asked grim for DP-4 specifically, NOT DP-3.
    assert "-o" in captured["argv"]
    name_idx = captured["argv"].index("-o") + 1
    assert captured["argv"][name_idx] == "DP-4"


def test_capture_monitor_falls_back_to_focused_when_no_active_window(
    screen_backend, monkeypatch, tmp_path,
):
    """If activewindow is empty (nothing focused), use focused_monitor."""
    b, mod = screen_backend
    monitors = [
        {"id": 0, "name": "DP-3", "x": 0, "y": 0, "width": 2560, "height": 1440,
         "scale": 1.0, "focused": True, "refreshRate": 60.0},
    ]
    captured = {}

    def fake_run(cmd, timeout=2):
        if cmd[:3] == ["hyprctl", "-j", "monitors"]:
            return (0, json.dumps(monitors), "")
        if cmd[:3] == ["hyprctl", "-j", "activewindow"]:
            return (0, "", "")  # empty: no active window
        if cmd[0] == "grim":
            captured["argv"] = cmd
            Path(cmd[-1]).write_bytes(b"\x89PNG fake")
            return (0, "", "")
        return (1, "", "")

    monkeypatch.setattr(mod, "_run", fake_run)

    out = b.capture_monitor(tmp_path / "out.png")
    assert out.exists()
    assert captured["argv"][captured["argv"].index("-o") + 1] == "DP-3"


def test_capture_monitor_explicit_arg_bypasses_resolution(
    screen_backend, monkeypatch, tmp_path,
):
    """A caller passing an explicit monitor name must be respected verbatim."""
    b, mod = screen_backend
    captured = {}

    def fake_run(cmd, timeout=2):
        if cmd[0] == "grim":
            captured["argv"] = cmd
            Path(cmd[-1]).write_bytes(b"\x89PNG fake")
            return (0, "", "")
        # Activewindow / monitors should NOT be called when caller is explicit.
        raise AssertionError(f"unexpected hyprctl probe: {cmd!r}")

    monkeypatch.setattr(mod, "_run", fake_run)
    b.capture_monitor(tmp_path / "out.png", monitor="DP-9")
    assert captured["argv"][captured["argv"].index("-o") + 1] == "DP-9"


def test_active_window_monitor_name_returns_none_when_id_missing(
    screen_backend, monkeypatch,
):
    """Defensive: activewindow without a 'monitor' key shouldn't crash."""
    b, mod = screen_backend

    def fake_run(cmd, timeout=2):
        if cmd[:3] == ["hyprctl", "-j", "activewindow"]:
            return (0, json.dumps({"at": [0, 0]}), "")  # no monitor key
        return (1, "", "")

    monkeypatch.setattr(mod, "_run", fake_run)
    assert b._active_window_monitor_name() is None


# ---------------------------------------------------------------------------
# screenshot.capture — settings gating
# ---------------------------------------------------------------------------
# Note: the older ``_hud_offscreen`` parking helper (ADR-0028) was removed
# after the user reported that the resulting HUD flicker was worse than
# accepting the HUD pixels in the screenshot. We now capture as-is.

def test_capture_returns_none_when_screenshots_disabled(monkeypatch):
    """settings.screenshot=False short-circuits before calling capture_to."""
    from voice_opencode import screenshot as s

    fake_settings = MagicMock(screenshot=False)
    monkeypatch.setattr(s, "settings", fake_settings)
    called = {"capture_to": False}

    def fake_capture_to(path, scope):
        called["capture_to"] = True
        return path

    monkeypatch.setattr(s, "capture_to", fake_capture_to)
    assert s.capture() is None
    assert called["capture_to"] is False
