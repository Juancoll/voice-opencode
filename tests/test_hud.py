"""Tests for the in-process TurnHUD widget + Unix-socket server (ADR-0026).

Uses ``QT_QPA_PLATFORM=offscreen`` so the widget can be constructed
without a display. Sockets are real (in a tmp dir) so we exercise
the full JSON-per-line protocol.
"""

from __future__ import annotations

import json
import os
import socket
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Skip the whole module if PyQt6 isn't importable in this env.
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from voice_opencode import hud as hud_mod  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def server(monkeypatch, tmp_path, qapp):
    sock_path = tmp_path / "hud.sock"
    monkeypatch.setattr(hud_mod, "HUD_SOCKET", sock_path)
    widget = hud_mod.TurnHUD()
    srv = hud_mod.HudServer(widget)
    srv.start()
    yield srv, widget, sock_path
    srv.stop()
    widget.deleteLater()


def _send_line(sock_path, payload: dict) -> None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(str(sock_path))
    s.sendall(json.dumps(payload).encode() + b"\n")
    s.close()


def _drain(qapp, ms: int = 100) -> None:
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def test_server_creates_socket_file(server):
    _srv, _widget, sock_path = server
    assert sock_path.exists()
    # 0600 permissions (owner-only).
    mode = sock_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_show_updates_widget_state(server, qapp):
    srv, widget, sock_path = server
    _send_line(sock_path, {
        "op": "show", "icon": "🎙", "title": "Grabando…", "subtitle": "Suelta F9",
    })
    _drain(qapp)
    assert widget.icon_label.text() == "🎙"
    assert widget.title_label.text() == "Grabando…"
    assert widget.subtitle_label.text() == "Suelta F9"


def test_update_replaces_content(server, qapp):
    srv, widget, sock_path = server
    _send_line(sock_path, {"op": "show", "icon": "🎙", "title": "a", "subtitle": "1"})
    _drain(qapp)
    _send_line(sock_path, {"op": "update", "icon": "🧠", "title": "b", "subtitle": "2"})
    _drain(qapp)
    assert widget.icon_label.text() == "🧠"
    assert widget.title_label.text() == "b"
    assert widget.subtitle_label.text() == "2"


def test_hide_does_not_crash_when_not_visible(server, qapp):
    srv, _widget, sock_path = server
    # Widget is not visible yet — hide must be a clean no-op.
    _send_line(sock_path, {"op": "hide"})
    _drain(qapp)


def test_unknown_op_is_ignored(server, qapp):
    srv, widget, sock_path = server
    _send_line(sock_path, {"op": "nope"})
    _drain(qapp)
    # Still default; nothing crashed.
    assert widget.title_label.text() == ""


def test_subtitle_is_elided_at_110_chars(server, qapp):
    srv, widget, sock_path = server
    long_text = "x" * 200
    _send_line(sock_path, {
        "op": "show", "icon": "i", "title": "t", "subtitle": long_text,
    })
    _drain(qapp)
    rendered = widget.subtitle_label.text()
    assert len(rendered) <= 110
    assert rendered.endswith("…")


def test_send_client_round_trip(server, qapp):
    srv, widget, sock_path = server
    ok = hud_mod.send("show", icon="✅", title="ok", subtitle="all good")
    _drain(qapp)
    assert ok is True
    assert widget.title_label.text() == "ok"


def test_send_silent_when_socket_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_mod, "HUD_SOCKET", tmp_path / "nope.sock")
    assert hud_mod.send("show", title="x") is False


# ---------------------------------------------------------------------------
# Focus restore helpers (HUD must not steal focus from the editor)
# ---------------------------------------------------------------------------
def test_current_focus_address_returns_none_without_hyprctl(monkeypatch):
    monkeypatch.setattr(hud_mod.shutil, "which", lambda _: None)
    assert hud_mod._current_focus_address() is None


def test_current_focus_address_skips_self(monkeypatch):
    monkeypatch.setattr(hud_mod.shutil, "which", lambda _: "/usr/bin/hyprctl")

    class _R:
        returncode = 0
        stdout = json.dumps({"address": "0xabc", "title": "voice-opencode-hud"})
        stderr = ""

    monkeypatch.setattr(hud_mod.subprocess, "run", lambda *a, **kw: _R())
    # If active window IS the HUD, return None — restoring focus to
    # ourselves would just bounce.
    assert hud_mod._current_focus_address() is None


def test_current_focus_address_returns_addr(monkeypatch):
    monkeypatch.setattr(hud_mod.shutil, "which", lambda _: "/usr/bin/hyprctl")

    class _R:
        returncode = 0
        stdout = json.dumps({"address": "0xdeadbeef", "title": "kwrite"})
        stderr = ""

    monkeypatch.setattr(hud_mod.subprocess, "run", lambda *a, **kw: _R())
    assert hud_mod._current_focus_address() == "0xdeadbeef"


def test_focus_window_address_invokes_hyprctl(monkeypatch):
    calls = []

    def fake_run(argv, **_kw):
        calls.append(argv)

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(hud_mod.subprocess, "run", fake_run)
    hud_mod._focus_window_address("0x123")
    assert calls == [
        ["hyprctl", "dispatch", "focuswindow", "address:0x123"]
    ]


def test_focus_window_address_silent_on_oserror(monkeypatch):
    def fake_run(*_a, **_kw):
        raise OSError("hyprctl missing")
    monkeypatch.setattr(hud_mod.subprocess, "run", fake_run)
    # Must not raise.
    hud_mod._focus_window_address("0x456")
