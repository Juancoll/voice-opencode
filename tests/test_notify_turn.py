"""Tests for turn_start / turn_update / turn_end (ADR-0026: HUD via socket).

The HUD lives in the tray process; the pipeline + MCP server are
clients that send JSON over a Unix socket. These tests intercept
``hud.send`` and assert the protocol — no Qt, no real socket.
"""

from __future__ import annotations

from typing import Any

from voice_opencode import hud as hud_mod
from voice_opencode import notify as notify_mod


class _SendRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_value = True

    def __call__(self, op: str, *, icon: str = "", title: str = "",
                 subtitle: str = "", timeout: float = 0.05) -> bool:
        self.calls.append({
            "op": op, "icon": icon, "title": title, "subtitle": subtitle,
        })
        return self.return_value


class _FakeSettings:
    def __init__(self, notify: bool = True) -> None:
        self.notify = notify


def _wire(monkeypatch, *, notify_enabled: bool = True) -> _SendRecorder:
    rec = _SendRecorder()
    # Patch the symbol the notify module imported at module load time.
    monkeypatch.setattr(notify_mod.hud, "send", rec)
    monkeypatch.setattr(notify_mod, "settings", _FakeSettings(notify_enabled))
    return rec


# ---------------------------------------------------------------------------
# emoji splitter
# ---------------------------------------------------------------------------
def test_split_emoji_extracts_leading_emoji():
    assert notify_mod._split_emoji("🎙 Grabando…") == ("🎙", "Grabando…")


def test_split_emoji_falls_back_to_bullet_when_no_emoji():
    assert notify_mod._split_emoji("Plain title") == ("•", "Plain title")


def test_split_emoji_empty_string():
    assert notify_mod._split_emoji("") == ("•", "")


def test_split_emoji_handles_multi_word_after_icon():
    assert notify_mod._split_emoji("🧠 Pensando en voz alta") == (
        "🧠", "Pensando en voz alta",
    )


# ---------------------------------------------------------------------------
# turn_start / update / end
# ---------------------------------------------------------------------------
def test_turn_start_sends_show_with_icon_and_title(monkeypatch):
    rec = _wire(monkeypatch)
    notify_mod.turn_start("🎙 Grabando…", "Suelta F9")
    assert rec.calls == [
        {"op": "show", "icon": "🎙", "title": "Grabando…", "subtitle": "Suelta F9"},
    ]


def test_turn_update_sends_update(monkeypatch):
    rec = _wire(monkeypatch)
    notify_mod.turn_update("⚙️ Agente actuando", "click_mouse(left,1483,845)")
    assert rec.calls == [
        {
            "op": "update", "icon": "⚙️", "title": "Agente actuando",
            "subtitle": "click_mouse(left,1483,845)",
        },
    ]


def test_turn_end_sends_hide(monkeypatch):
    rec = _wire(monkeypatch)
    notify_mod.turn_end()
    assert rec.calls == [
        {"op": "hide", "icon": "", "title": "", "subtitle": ""},
    ]


def test_turn_end_always_runs_even_with_notify_disabled(monkeypatch):
    # turn_end has to fire so a stale HUD never survives muting.
    rec = _wire(monkeypatch, notify_enabled=False)
    notify_mod.turn_end()
    assert rec.calls == [
        {"op": "hide", "icon": "", "title": "", "subtitle": ""},
    ]


def test_turn_helpers_noop_when_notify_disabled(monkeypatch):
    rec = _wire(monkeypatch, notify_enabled=False)
    notify_mod.turn_start("🎙 hi", "")
    notify_mod.turn_update("🧠 mid", "")
    # turn_end is intentionally NOT muted (see above) so we only check
    # that show/update are skipped.
    assert all(c["op"] == "hide" for c in rec.calls)


def test_send_returns_false_when_socket_missing(monkeypatch, tmp_path):
    # Force the socket path to somewhere that doesn't exist.
    missing = tmp_path / "no-such.sock"
    monkeypatch.setattr(hud_mod, "HUD_SOCKET", missing)
    assert hud_mod.send("show", title="hi") is False
