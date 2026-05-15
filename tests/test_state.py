"""Tests for state.py — pause sentinel and pipeline phase."""
from __future__ import annotations


def test_default_state_is_idle(tmp_state):
    from voice_opencode.state import get_state
    assert get_state() == "idle"


def test_set_and_get_state(tmp_state):
    from voice_opencode.state import get_state, set_state
    set_state("recording")
    assert get_state() == "recording"
    set_state("thinking")
    assert get_state() == "thinking"


def test_invalid_state_ignored(tmp_state):
    from voice_opencode.state import get_state, set_state
    set_state("idle")
    set_state("nonsense")
    assert get_state() == "idle"


def test_pause_toggle(tmp_state):
    from voice_opencode.state import is_paused, set_paused
    assert is_paused() is False
    set_paused(True)
    assert is_paused() is True
    set_paused(False)
    assert is_paused() is False
    # Idempotent
    set_paused(False)
    assert is_paused() is False
