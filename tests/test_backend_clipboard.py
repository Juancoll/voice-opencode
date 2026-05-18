"""Tests for clipboard backends (wl-clipboard + xclip).

We mock ``shutil.which`` so the backend believes the binary exists, and
``subprocess.run`` so we never actually shell out.
"""

from __future__ import annotations

import pytest

from voice_opencode.platform.base import BackendError
from voice_opencode.platform.capabilities import (
    CLIPBOARD_READ,
    CLIPBOARD_READ_PRIMARY,
    CLIPBOARD_WRITE,
    CLIPBOARD_WRITE_PRIMARY,
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
# wl-clipboard backend
# ---------------------------------------------------------------------------
def test_wl_init_requires_both_binaries(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(BackendError):
        mod.WlClipboardBackend()


def test_wl_capabilities_full(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    _patch(monkeypatch, mod, which="/usr/bin/wl-copy", run=lambda *a, **k: _FakeCompleted())
    b = mod.WlClipboardBackend()
    assert b.capabilities() == frozenset(
        {CLIPBOARD_READ, CLIPBOARD_WRITE, CLIPBOARD_READ_PRIMARY, CLIPBOARD_WRITE_PRIMARY}
    )


def test_wl_read_passes_n_and_strips_primary(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "hello")

    _patch(monkeypatch, mod, which="/usr/bin/wl-paste", run=fake_run)
    b = mod.WlClipboardBackend()
    assert b.read() == "hello"
    assert seen["cmd"] == ["wl-paste", "-n"]


def test_wl_read_primary_adds_flag(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "primtxt")

    _patch(monkeypatch, mod, which="/usr/bin/wl-paste", run=fake_run)
    b = mod.WlClipboardBackend()
    assert b.read_primary() == "primtxt"
    assert seen["cmd"] == ["wl-paste", "-n", "--primary"]


def test_wl_read_empty_selection_returns_empty_string(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    # wl-paste exits non-zero when selection is empty.
    _patch(
        monkeypatch, mod, which="/usr/bin/wl-paste",
        run=lambda *a, **k: _FakeCompleted(1, "", "Nothing copied"),
    )
    b = mod.WlClipboardBackend()
    assert b.read() == ""


def test_wl_write_passes_input(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    seen = {}

    def fake_run(cmd, input, stdout, stderr, text, timeout):
        seen["cmd"] = list(cmd)
        seen["input"] = input
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/wl-copy", run=fake_run)
    b = mod.WlClipboardBackend()
    b.write("hola")
    assert seen["cmd"] == ["wl-copy"]
    assert seen["input"] == "hola"


def test_wl_write_primary_adds_flag(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    seen = {}

    def fake_run(cmd, input, stdout, stderr, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/wl-copy", run=fake_run)
    b = mod.WlClipboardBackend()
    b.write_primary("p")
    assert seen["cmd"] == ["wl-copy", "--primary"]


def test_wl_write_failure_raises(monkeypatch):
    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    _patch(
        monkeypatch, mod, which="/usr/bin/wl-copy",
        run=lambda *a, **k: _FakeCompleted(2),
    )
    b = mod.WlClipboardBackend()
    with pytest.raises(BackendError, match="rc=2"):
        b.write("x")


def test_wl_read_timeout_raises(monkeypatch):
    import subprocess as sp

    from voice_opencode.backends.linux_clipboard_wayland import wlclip_backend as mod

    def raise_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd="wl-paste", timeout=1)

    _patch(monkeypatch, mod, which="/usr/bin/wl-paste", run=raise_timeout)
    b = mod.WlClipboardBackend()
    with pytest.raises(BackendError, match="timeout"):
        b.read()


# ---------------------------------------------------------------------------
# xclip backend
# ---------------------------------------------------------------------------
def test_xclip_init_requires_binary(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(BackendError):
        mod.XclipClipboardBackend()


def test_xclip_capabilities_full(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=lambda *a, **k: _FakeCompleted())
    b = mod.XclipClipboardBackend()
    assert b.capabilities() == frozenset(
        {CLIPBOARD_READ, CLIPBOARD_WRITE, CLIPBOARD_READ_PRIMARY, CLIPBOARD_WRITE_PRIMARY}
    )


def test_xclip_read_clipboard_argv(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "abc")

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=fake_run)
    b = mod.XclipClipboardBackend()
    assert b.read() == "abc"
    assert seen["cmd"] == ["xclip", "-selection", "clipboard", "-out"]


def test_xclip_read_primary_argv(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    seen = {}

    def fake_run(cmd, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, "p")

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=fake_run)
    b = mod.XclipClipboardBackend()
    assert b.read_primary() == "p"
    assert seen["cmd"] == ["xclip", "-selection", "primary", "-out"]


def test_xclip_read_empty_selection_returns_empty_string(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    _patch(
        monkeypatch, mod, which="/usr/bin/xclip",
        run=lambda *a, **k: _FakeCompleted(1, "", "no selection"),
    )
    b = mod.XclipClipboardBackend()
    assert b.read() == ""


def test_xclip_write_argv_and_input(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    seen = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        seen["input"] = input
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=fake_run)
    b = mod.XclipClipboardBackend()
    b.write("hola")
    assert seen["cmd"] == ["xclip", "-selection", "clipboard", "-in"]
    assert seen["input"] == "hola"


def test_xclip_write_primary_argv(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    seen = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0)

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=fake_run)
    b = mod.XclipClipboardBackend()
    b.write_primary("p")
    assert seen["cmd"] == ["xclip", "-selection", "primary", "-in"]


def test_xclip_write_failure_raises(monkeypatch):
    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    _patch(
        monkeypatch, mod, which="/usr/bin/xclip",
        run=lambda *a, **k: _FakeCompleted(2, "", "boom"),
    )
    b = mod.XclipClipboardBackend()
    with pytest.raises(BackendError, match="boom"):
        b.write("x")


def test_xclip_write_timeout_raises(monkeypatch):
    import subprocess as sp

    from voice_opencode.backends.linux_clipboard_x11 import xclip_backend as mod

    def raise_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd="xclip", timeout=1)

    _patch(monkeypatch, mod, which="/usr/bin/xclip", run=raise_timeout)
    b = mod.XclipClipboardBackend()
    with pytest.raises(BackendError, match="timeout"):
        b.write("x")
