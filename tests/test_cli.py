"""Tests for the CLI dispatcher (legacy aliases + grouped commands)."""
from __future__ import annotations

from unittest.mock import patch


def test_legacy_start_dispatches_to_rec_start(tmp_state):
    from voice_opencode import cli
    with patch.object(cli.pipeline, "start_recording") as start:
        rc = cli.main(["start"])
    assert rc == 0
    start.assert_called_once()


def test_legacy_voices_dispatches_to_tts_voices(tmp_state):
    from voice_opencode import cli
    with patch.object(cli, "_print_voices") as p:
        rc = cli.main(["voices"])
    assert rc == 0
    p.assert_called_once()


def test_grouped_session_reset(tmp_state):
    from voice_opencode import cli
    with patch.object(cli.Session, "forget") as forget:
        rc = cli.main(["session", "reset"])
    assert rc == 0
    forget.assert_called_once()


def test_state_outputs_json(tmp_state, capsys):
    from voice_opencode import cli
    with patch.object(cli, "health", return_value=False):
        rc = cli.main(["state"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    import json
    data = json.loads(out)
    assert data["state"] == "idle"
    assert data["server"] is False


def test_unknown_command_returns_nonzero(tmp_state):
    from voice_opencode import cli
    rc = cli.main(["frobnicate"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Phase A: windows / workspaces / platform groups
# ---------------------------------------------------------------------------
def test_platform_info_dumps_json(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    monkeypatch.setattr(cli.plat, "active_platform", "linux-hyprland", raising=False)
    monkeypatch.setattr(cli.plat, "all_capabilities", lambda: frozenset({"wm.list_windows"}))
    rc = cli.main(["platform", "info"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["platform"] == "linux-hyprland"
    assert "wm.list_windows" in data["capabilities"]


def test_windows_list_calls_backend(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.types import Rect, Window
    fake = Window(
        id="0xAA", pid=1, app_id="kitty", title="t",
        rect=Rect(0, 0, 1, 1), monitor_id=0, workspace_id=1, focused=True,
    )

    class FakeWM:
        def list_windows(self):
            return [fake]

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "list"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "0xAA"
    assert data[0]["focused"] is True


def test_windows_focus_invokes_backend(tmp_state, monkeypatch):
    from voice_opencode import cli
    calls: list[str] = []

    class FakeWM:
        def focus_window(self, t):
            calls.append(t)

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "focus", "kitty"])
    assert rc == 0
    assert calls == ["kitty"]


def test_workspaces_switch_invokes_backend(tmp_state, monkeypatch):
    from voice_opencode import cli
    calls: list[str] = []

    class FakeWM:
        def switch_workspace(self, t):
            calls.append(t)

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["workspaces", "switch", "5"])
    assert rc == 0
    assert calls == ["5"]


def test_windows_propagates_backend_error(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.base import BackendError

    class FakeWM:
        def focus_window(self, _t):
            raise BackendError("nope")

    monkeypatch.setattr(cli.plat, "wm", FakeWM(), raising=False)
    rc = cli.main(["windows", "focus", "ghost"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err


# ---------------------------------------------------------------------------
# Phase B: clipboard group
# ---------------------------------------------------------------------------
class _FakeClipboard:
    def __init__(self):
        self.clip = ""
        self.prim = ""
        self.writes: list[tuple[str, str]] = []

    def read(self) -> str:
        return self.clip

    def read_primary(self) -> str:
        return self.prim

    def write(self, text: str) -> None:
        self.writes.append(("clip", text))
        self.clip = text

    def write_primary(self, text: str) -> None:
        self.writes.append(("prim", text))
        self.prim = text


def test_clipboard_read_prints_selection(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    fake = _FakeClipboard()
    fake.clip = "hello"
    monkeypatch.setattr(cli.plat, "clipboard", fake, raising=False)
    rc = cli.main(["clipboard", "read"])
    assert rc == 0
    assert capsys.readouterr().out == "hello"


def test_clipboard_read_primary_prints_primary(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    fake = _FakeClipboard()
    fake.prim = "primtxt"
    monkeypatch.setattr(cli.plat, "clipboard", fake, raising=False)
    rc = cli.main(["clipboard", "read-primary"])
    assert rc == 0
    assert capsys.readouterr().out == "primtxt"


def test_clipboard_write_inline_args(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    fake = _FakeClipboard()
    monkeypatch.setattr(cli.plat, "clipboard", fake, raising=False)
    rc = cli.main(["clipboard", "write", "hola", "mundo"])
    assert rc == 0
    assert fake.writes == [("clip", "hola mundo")]
    assert "wrote 10 chars" in capsys.readouterr().err


def test_clipboard_write_primary_inline_args(tmp_state, monkeypatch):
    from voice_opencode import cli
    fake = _FakeClipboard()
    monkeypatch.setattr(cli.plat, "clipboard", fake, raising=False)
    rc = cli.main(["clipboard", "write-primary", "p"])
    assert rc == 0
    assert fake.writes == [("prim", "p")]


def test_clipboard_write_from_stdin(tmp_state, monkeypatch):
    import io

    from voice_opencode import cli
    fake = _FakeClipboard()
    monkeypatch.setattr(cli.plat, "clipboard", fake, raising=False)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("from stdin"))
    rc = cli.main(["clipboard", "write"])
    assert rc == 0
    assert fake.writes == [("clip", "from stdin")]


def test_clipboard_propagates_backend_error(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.base import BackendError

    class Broken:
        def read(self): raise BackendError("xclip missing")

    monkeypatch.setattr(cli.plat, "clipboard", Broken(), raising=False)
    rc = cli.main(["clipboard", "read"])
    assert rc == 1
    assert "xclip missing" in capsys.readouterr().err


def test_clipboard_unknown_subcommand_returns_1(tmp_state, monkeypatch):
    from voice_opencode import cli
    monkeypatch.setattr(cli.plat, "clipboard", _FakeClipboard(), raising=False)
    rc = cli.main(["clipboard", "frobnicate"])
    assert rc == 1


# ---------------------------------------------------------------------------
# Phase C: dialog group
# ---------------------------------------------------------------------------
class _FakeNotify:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def show(self, title: str, body: str = "", urgency: str = "normal") -> None:
        self.calls.append((title, body, urgency))


class _FakeDialog:
    def __init__(
        self,
        *,
        confirm_result: bool = True,
        ask_text_result: str | None = "answer",
        ask_choice_result: str | None = "b",
    ):
        self.confirm_result = confirm_result
        self.ask_text_result = ask_text_result
        self.ask_choice_result = ask_choice_result
        self.confirm_calls: list[tuple[str, str]] = []
        self.ask_text_calls: list[tuple[str, str, str]] = []
        self.ask_choice_calls: list[tuple[str, list[str], str]] = []

    def confirm(self, message, title="Confirm"):
        self.confirm_calls.append((message, title))
        return self.confirm_result

    def ask_text(self, prompt, default="", title="Input"):
        self.ask_text_calls.append((prompt, default, title))
        return self.ask_text_result

    def ask_choice(self, prompt, choices, title="Choose"):
        self.ask_choice_calls.append((prompt, list(choices), title))
        return self.ask_choice_result


def test_dialog_notify_calls_backend(tmp_state, monkeypatch):
    from voice_opencode import cli
    n = _FakeNotify()
    monkeypatch.setattr(cli.plat, "notify", n, raising=False)
    rc = cli.main(["dialog", "notify", "Hola", "body text", "critical"])
    assert rc == 0
    assert n.calls == [("Hola", "body text", "critical")]


def test_dialog_notify_defaults(tmp_state, monkeypatch):
    from voice_opencode import cli
    n = _FakeNotify()
    monkeypatch.setattr(cli.plat, "notify", n, raising=False)
    rc = cli.main(["dialog", "notify", "Hola"])
    assert rc == 0
    assert n.calls == [("Hola", "", "normal")]


def test_dialog_confirm_yes_returns_0(tmp_state, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog(confirm_result=True)
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "confirm", "are you sure?", "Title"])
    assert rc == 0
    assert d.confirm_calls == [("are you sure?", "Title")]


def test_dialog_confirm_no_returns_2(tmp_state, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog(confirm_result=False)
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "confirm", "really?"])
    assert rc == 2


def test_dialog_ask_prints_answer(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog(ask_text_result="hello world")
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "ask", "name?", "def", "Title"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "hello world"
    assert d.ask_text_calls == [("name?", "def", "Title")]


def test_dialog_ask_cancel_prints_nothing(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog(ask_text_result=None)
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "ask", "name?"])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_dialog_choose_prints_choice(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog(ask_choice_result="green")
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "choose", "color?", "red", "green", "blue"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "green"
    assert d.ask_choice_calls == [("color?", ["red", "green", "blue"], "Choose")]


def test_dialog_choose_requires_options(tmp_state, monkeypatch):
    from voice_opencode import cli
    d = _FakeDialog()
    monkeypatch.setattr(cli.plat, "dialog", d, raising=False)
    rc = cli.main(["dialog", "choose", "color?"])
    assert rc == 1


def test_dialog_propagates_backend_error(tmp_state, capsys, monkeypatch):
    from voice_opencode import cli
    from voice_opencode.platform.base import BackendError

    class Broken:
        def ask_text(self, *a, **k):
            raise BackendError("kdialog gone")

    monkeypatch.setattr(cli.plat, "dialog", Broken(), raising=False)
    rc = cli.main(["dialog", "ask", "x"])
    assert rc == 1
    assert "kdialog gone" in capsys.readouterr().err


def test_dialog_unknown_subcommand_returns_1(tmp_state, monkeypatch):
    from voice_opencode import cli
    monkeypatch.setattr(cli.plat, "dialog", _FakeDialog(), raising=False)
    rc = cli.main(["dialog", "frobnicate"])
    assert rc == 1

