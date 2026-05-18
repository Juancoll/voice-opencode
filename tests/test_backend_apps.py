"""Tests for the XDG app launcher backend.

Filesystem and subprocess are both mocked: we never touch real
``/proc``, real ``.desktop`` files, or real launches.
"""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode.backends.linux_apps_xdg import xdg_backend as xdg
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError

# ---------------------------------------------------------------------------
# Fixtures: a fake XDG applications directory
# ---------------------------------------------------------------------------
_FIREFOX = """\
[Desktop Entry]
Type=Application
Name=Firefox
Name[es]=Zorro de Fuego
Exec=/usr/lib/firefox/firefox %u
Icon=firefox
Categories=Network;WebBrowser;
"""

_HIDDEN = """\
[Desktop Entry]
Type=Application
Name=Internal Helper
Exec=/usr/lib/helper
NoDisplay=true
"""

_NOT_APP = """\
[Desktop Entry]
Type=Link
Name=Some Link
URL=https://example.com
"""

_MALFORMED = "this is not a desktop file at all\n"


@pytest.fixture
def fake_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "firefox.desktop").write_text(_FIREFOX)
    (apps / "helper.desktop").write_text(_HIDDEN)
    (apps / "link.desktop").write_text(_NOT_APP)
    (apps / "broken.desktop").write_text(_MALFORMED)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", "")
    return apps


# ---------------------------------------------------------------------------
# init / capabilities
# ---------------------------------------------------------------------------
class TestInit:
    def test_init_ok(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        assert b.capabilities() == frozenset({
            cap.APP_LAUNCH, cap.APP_LIST_INSTALLED,
            cap.APP_LIST_RUNNING, cap.APP_KILL,
        })

    def test_init_no_xdg_dirs(self) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"), \
             patch.object(xdg, "_xdg_app_dirs", return_value=[]), \
             pytest.raises(BackendError, match="XDG application"):
            xdg.XdgAppLauncher()

    def test_init_no_launcher(
        self, fake_xdg: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch.object(xdg.shutil, "which", return_value=None), \
             pytest.raises(BackendError, match="no launcher"):
            xdg.XdgAppLauncher()


# ---------------------------------------------------------------------------
# list_installed: parsing + filtering
# ---------------------------------------------------------------------------
class TestListInstalled:
    def test_includes_apps_excludes_non_apps(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            apps = xdg.XdgAppLauncher().list_installed()
        ids = [a["id"] for a in apps]
        assert "firefox" in ids
        assert "helper" in ids
        assert "link" not in ids        # Type=Link skipped
        assert "broken" not in ids      # no [Desktop Entry] section

    def test_fields_extracted(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            apps = xdg.XdgAppLauncher().list_installed()
        firefox = next(a for a in apps if a["id"] == "firefox")
        assert firefox["name"] == "Firefox"        # not the Spanish variant
        assert firefox["exec"] == "/usr/lib/firefox/firefox %u"
        assert firefox["icon"] == "firefox"
        assert firefox["no_display"] is False

    def test_no_display_flag(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            apps = xdg.XdgAppLauncher().list_installed()
        helper = next(a for a in apps if a["id"] == "helper")
        assert helper["no_display"] is True

    def test_dedup_across_dirs(
        self, fake_xdg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Add a system dir with an override; XDG_DATA_HOME wins (most specific).
        system = tmp_path / "system"
        sysapps = system / "applications"
        sysapps.mkdir(parents=True)
        (sysapps / "firefox.desktop").write_text(
            _FIREFOX.replace("Name=Firefox", "Name=SystemFirefox"))
        monkeypatch.setenv("XDG_DATA_DIRS", str(system))
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            apps = xdg.XdgAppLauncher().list_installed()
        firefox = [a for a in apps if a["id"] == "firefox"]
        assert len(firefox) == 1
        assert firefox[0]["name"] == "Firefox"     # user dir wins


# ---------------------------------------------------------------------------
# Exec field-code stripping
# ---------------------------------------------------------------------------
class TestStripFieldCodes:
    @pytest.mark.parametrize("line,want", [
        ("/usr/bin/foo",            ["/usr/bin/foo"]),
        ("/usr/bin/foo %u",         ["/usr/bin/foo"]),
        ("/usr/bin/foo %F arg",     ["/usr/bin/foo", "arg"]),
        ("foo -a -b %i %c %k",      ["foo", "-a", "-b"]),
        ("",                        []),
    ])
    def test_strips(self, line: str, want: list[str]) -> None:
        assert xdg._strip_field_codes(line) == want


# ---------------------------------------------------------------------------
# list_running: parse /proc
# ---------------------------------------------------------------------------
class TestListRunning:
    def test_matches_app_id_by_comm(
        self, fake_xdg: Path, tmp_path: Path,
    ) -> None:
        # Build a fake /proc with two pids: one matches firefox, one doesn't.
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "100").mkdir()
        (proc / "100" / "comm").write_text("firefox\n")
        (proc / "200").mkdir()
        (proc / "200" / "comm").write_text("bash\n")
        (proc / "notapid").mkdir()      # should be skipped
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"), \
             patch.object(xdg, "Path", side_effect=lambda p: (
                 proc if str(p) == "/proc" else Path(p))):
            running = xdg.XdgAppLauncher().list_running()
        by_pid = {r["pid"]: r for r in running}
        assert by_pid[100]["app_id"] == "firefox"
        assert "app_id" not in by_pid[200]


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------
class TestLaunch:
    def test_empty_raises(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with pytest.raises(BackendError, match="empty"):
            b.launch("")

    def test_known_app_uses_gtk_launch(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.subprocess, "run") as run, \
             patch.object(b, "_probe_pid", return_value=12345):
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            pid = b.launch("firefox")
        assert pid == 12345
        argv = run.call_args[0][0]
        assert argv == ["gtk-launch", "firefox"]

    def test_known_app_falls_back_when_gtk_launch_fails(
        self, fake_xdg: Path,
    ) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        mock_proc = MagicMock(pid=999)
        with patch.object(xdg.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, ["gtk-launch"])), \
             patch.object(xdg.subprocess, "Popen", return_value=mock_proc) as popen:
            pid = b.launch("firefox")
        assert pid == 999
        # Field codes stripped from Exec line.
        argv = popen.call_args[0][0]
        assert argv == ["/usr/lib/firefox/firefox"]

    def test_raw_command(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        mock_proc = MagicMock(pid=7777)
        with patch.object(xdg.subprocess, "Popen", return_value=mock_proc) as popen:
            pid = b.launch("/bin/sleep 1")
        assert pid == 7777
        assert popen.call_args[0][0] == ["/bin/sleep", "1"]

    def test_spawn_oserror(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.subprocess, "Popen", side_effect=FileNotFoundError("nope")), \
             pytest.raises(BackendError, match="launch"):
            b.launch("/bin/does-not-exist")


# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------
class TestKill:
    def test_kill_by_int_pid(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.os, "kill") as kill:
            b.kill(4242)
        kill.assert_called_once_with(4242, signal.SIGTERM)

    def test_kill_by_digit_string(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.os, "kill") as kill:
            b.kill("4242")
        kill.assert_called_once_with(4242, signal.SIGTERM)

    def test_kill_by_app_id_no_match(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(b, "list_running", return_value=[]), \
             pytest.raises(BackendError, match="no process"):
            b.kill("firefox")

    def test_kill_by_app_id_matches(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(b, "list_running", return_value=[
            {"pid": 11, "comm": "firefox", "app_id": "firefox"},
            {"pid": 22, "comm": "firefox", "app_id": "firefox"},
            {"pid": 33, "comm": "bash"},
        ]), patch.object(xdg.os, "kill") as kill:
            b.kill("firefox")
        assert {c.args for c in kill.call_args_list} == {
            (11, signal.SIGTERM), (22, signal.SIGTERM),
        }

    def test_kill_swallows_process_lookup(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.os, "kill", side_effect=ProcessLookupError):
            b.kill(4242)        # must not raise

    def test_kill_permission_error_raises(self, fake_xdg: Path) -> None:
        with patch.object(xdg.shutil, "which", return_value="/usr/bin/gtk-launch"):
            b = xdg.XdgAppLauncher()
        with patch.object(xdg.os, "kill", side_effect=PermissionError("nope")), \
             pytest.raises(BackendError, match="kill"):
            b.kill(4242)
