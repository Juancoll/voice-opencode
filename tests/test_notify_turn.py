"""Tests for turn_start / turn_update / turn_end persistent
notification helpers (ADR-0025)."""

from __future__ import annotations

from voice_opencode import notify as notify_mod
from voice_opencode.platform import capabilities as cap


class _FakeBackend:
    def __init__(self, supports_replace: bool = True):
        self.supports_replace = supports_replace
        self.shown: list[tuple[str, str, str, int]] = []
        self.dismissed: list[int] = []
        self._next = 100

    def show_persistent(self, title, body="", urgency="normal", replace_id=0):
        if not self.supports_replace:
            raise RuntimeError("no replace")
        if replace_id:
            self.shown.append((title, body, urgency, replace_id))
            return replace_id
        nid = self._next
        self._next += 1
        self.shown.append((title, body, urgency, nid))
        return nid

    def dismiss(self, nid):
        self.dismissed.append(nid)

    def show(self, title, body="", urgency="normal"):
        # only hit via fallback when NOTIFY_REPLACE is not supported
        self.shown.append((title, body, urgency, 0))


class _FakeSettings:
    def __init__(self, notify=True):
        self.notify = notify


def _wire(monkeypatch, *, supports_replace=True, notify_enabled=True):
    fake = _FakeBackend(supports_replace=supports_replace)
    monkeypatch.setattr(notify_mod._plat, "notify", fake, raising=False)
    monkeypatch.setattr(
        notify_mod._plat, "supported",
        lambda c: supports_replace and c == cap.NOTIFY_REPLACE,
    )
    monkeypatch.setattr(notify_mod, "settings", _FakeSettings(notify_enabled))
    # Use a tmp file so we don't pollute the runtime dir.
    tmp = notify_mod._TURN_ID_FILE
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    return fake


def test_turn_start_creates_and_persists_id(monkeypatch):
    fake = _wire(monkeypatch)
    notify_mod.turn_start("🎙", "go")
    assert fake.shown == [("🎙", "go", "critical", 100)]
    assert notify_mod._read_turn_id() == 100


def test_turn_update_replaces_same_id(monkeypatch):
    fake = _wire(monkeypatch)
    notify_mod.turn_start("a", "1")
    notify_mod.turn_update("b", "2")
    assert fake.shown == [
        ("a", "1", "critical", 100),
        ("b", "2", "critical", 100),
    ]


def test_turn_update_noop_if_no_active_turn(monkeypatch):
    fake = _wire(monkeypatch)
    notify_mod.turn_update("late", "")
    assert fake.shown == []


def test_turn_end_dismisses_and_clears(monkeypatch):
    fake = _wire(monkeypatch)
    notify_mod.turn_start("x", "")
    notify_mod.turn_end()
    assert fake.dismissed == [100]
    assert notify_mod._read_turn_id() == 0


def test_turn_end_idempotent(monkeypatch):
    fake = _wire(monkeypatch)
    notify_mod.turn_end()
    notify_mod.turn_end()
    assert fake.dismissed == []


def test_turn_start_falls_back_to_show_without_replace_support(monkeypatch):
    fake = _wire(monkeypatch, supports_replace=False)
    notify_mod.turn_start("hi", "body")
    # Falls through to the plain ``notify`` shim, which calls show(...).
    assert fake.shown == [("hi", "body", "critical", 0)]


def test_turn_helpers_noop_when_notify_disabled(monkeypatch):
    fake = _wire(monkeypatch, notify_enabled=False)
    notify_mod.turn_start("x", "")
    notify_mod.turn_update("y", "")
    notify_mod.turn_end()
    assert fake.shown == []
    assert fake.dismissed == []
