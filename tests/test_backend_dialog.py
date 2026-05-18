"""Tests for dialog backends (kdialog + zenity).

We mock ``shutil.which`` and ``subprocess.run`` so the tests don't pop
real dialog windows and don't need a Display.

Exit-code contract under test (same for both backends):

* rc 0 → user confirmed / picked → returns answer (or True)
* rc 1 → user cancelled / closed → returns None (or False for confirm)
* other → BackendError with stderr context
"""

from __future__ import annotations

import pytest

from voice_opencode.platform.base import BackendError
from voice_opencode.platform.capabilities import (
    DIALOG_ASK_CHOICE,
    DIALOG_ASK_TEXT,
    DIALOG_CONFIRM,
)


class _FakeCompleted:
    def __init__(self, rc: int = 0, out: str = "", err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _patch(monkeypatch, mod, *, which: str | None, run):
    monkeypatch.setattr(mod.shutil, "which", lambda _: which)
    monkeypatch.setattr(mod.subprocess, "run", run)


# ---------------------------------------------------------------------------
# kdialog
# ---------------------------------------------------------------------------
def test_kdialog_init_requires_binary(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(BackendError):
        mod.KdialogBackend()


def test_kdialog_capabilities(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(0))
    b = mod.KdialogBackend()
    assert b.capabilities() == frozenset(
        {DIALOG_CONFIRM, DIALOG_ASK_TEXT, DIALOG_ASK_CHOICE}
    )


def test_kdialog_confirm_yes_argv(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/kdialog", run=fake_run)
    assert mod.KdialogBackend().confirm("ok?", title="T") is True
    assert seen["cmd"] == ["kdialog", "--title", "T", "--yesno", "ok?"]


def test_kdialog_confirm_no(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(1))
    assert mod.KdialogBackend().confirm("ok?") is False


def test_kdialog_confirm_error(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(2, "", "boom"))
    with pytest.raises(BackendError, match="boom"):
        mod.KdialogBackend().confirm("ok?")


def test_kdialog_ask_text_argv_and_strip_newline(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "hola\n")

    _patch(monkeypatch, mod, which="/usr/bin/kdialog", run=fake_run)
    assert mod.KdialogBackend().ask_text("name?", default="d", title="T") == "hola"
    assert seen["cmd"] == ["kdialog", "--title", "T", "--inputbox", "name?", "d"]


def test_kdialog_ask_text_cancel_returns_none(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(1, ""))
    assert mod.KdialogBackend().ask_text("name?") is None


def test_kdialog_ask_choice_argv(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "b\n")

    _patch(monkeypatch, mod, which="/usr/bin/kdialog", run=fake_run)
    assert mod.KdialogBackend().ask_choice("pick", ["a", "b", "c"]) == "b"
    # each choice is passed twice (tag + description)
    assert seen["cmd"] == [
        "kdialog", "--title", "Choose", "--menu", "pick",
        "a", "a", "b", "b", "c", "c",
    ]


def test_kdialog_ask_choice_empty_raises(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(0))
    with pytest.raises(BackendError, match="at least one"):
        mod.KdialogBackend().ask_choice("pick", [])


def test_kdialog_ask_choice_cancel(monkeypatch):
    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/kdialog",
           run=lambda *a, **k: _FakeCompleted(1))
    assert mod.KdialogBackend().ask_choice("pick", ["a"]) is None


def test_kdialog_timeout_raises(monkeypatch):
    import subprocess as sp

    from voice_opencode.backends.linux_dialog_kde import kdialog_backend as mod

    def raise_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd="kdialog", timeout=1)

    _patch(monkeypatch, mod, which="/usr/bin/kdialog", run=raise_timeout)
    with pytest.raises(BackendError, match="timeout"):
        mod.KdialogBackend().confirm("ok?")


# ---------------------------------------------------------------------------
# zenity
# ---------------------------------------------------------------------------
def test_zenity_init_requires_binary(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(BackendError):
        mod.ZenityBackend()


def test_zenity_capabilities(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/zenity",
           run=lambda *a, **k: _FakeCompleted(0))
    b = mod.ZenityBackend()
    assert b.capabilities() == frozenset(
        {DIALOG_CONFIRM, DIALOG_ASK_TEXT, DIALOG_ASK_CHOICE}
    )


def test_zenity_confirm_argv_yes(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/zenity", run=fake_run)
    assert mod.ZenityBackend().confirm("ok?", title="T") is True
    assert seen["cmd"] == ["zenity", "--question", "--title", "T", "--text", "ok?"]


def test_zenity_confirm_no(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/zenity",
           run=lambda *a, **k: _FakeCompleted(1))
    assert mod.ZenityBackend().confirm("ok?") is False


def test_zenity_ask_text_argv_and_strip(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "hola\n")

    _patch(monkeypatch, mod, which="/usr/bin/zenity", run=fake_run)
    assert mod.ZenityBackend().ask_text("name?", default="d", title="T") == "hola"
    assert seen["cmd"] == [
        "zenity", "--entry",
        "--title", "T",
        "--text", "name?",
        "--entry-text", "d",
    ]


def test_zenity_ask_text_cancel(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/zenity",
           run=lambda *a, **k: _FakeCompleted(1, ""))
    assert mod.ZenityBackend().ask_text("name?") is None


def test_zenity_ask_choice_argv(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "b\n")

    _patch(monkeypatch, mod, which="/usr/bin/zenity", run=fake_run)
    assert mod.ZenityBackend().ask_choice("pick", ["a", "b", "c"]) == "b"
    assert seen["cmd"] == [
        "zenity", "--list",
        "--title", "Choose",
        "--text", "pick",
        "--column", "Option",
        "a", "b", "c",
    ]


def test_zenity_ask_choice_empty_raises(monkeypatch):
    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod
    _patch(monkeypatch, mod, which="/usr/bin/zenity",
           run=lambda *a, **k: _FakeCompleted(0))
    with pytest.raises(BackendError, match="at least one"):
        mod.ZenityBackend().ask_choice("pick", [])


def test_zenity_timeout_raises(monkeypatch):
    import subprocess as sp

    from voice_opencode.backends.linux_dialog_gtk import zenity_backend as mod

    def raise_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd="zenity", timeout=1)

    _patch(monkeypatch, mod, which="/usr/bin/zenity", run=raise_timeout)
    with pytest.raises(BackendError, match="timeout"):
        mod.ZenityBackend().confirm("ok?")
