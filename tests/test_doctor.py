"""
Tests for ``voice_opencode.doctor``.

Doctor's collectors are pure-ish — they shell out, parse, and return a
``Check``. We stub every external thing (``shutil.which``, ``requests.get``,
``host_info.get``, ``Path.exists``…) so a CI host without opencode-serve,
without piper-tts, on macOS, still passes.
"""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode import doctor, host_info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hi(**overrides: str) -> host_info.HostInfo:
    """Build a ``HostInfo`` with sensible defaults; override per test."""
    base = host_info.HostInfo(
        os_name="Linux", os_release="6.0", distro="Test", arch="x86_64",
        python_version="3.11", hyprland_version="v0.55.2",
        whisper_version="1.6", piper_version="1.2.0", opencode_version="1.15.4",
        locale="en_US.UTF-8", audio_sink="Speakers", audio_source="Mic",
        notes={},
    )
    return replace(base, **overrides)


# ---------------------------------------------------------------------------
# check_binaries
# ---------------------------------------------------------------------------
class TestCheckBinaries:
    def test_all_present_with_versions(self) -> None:
        with patch.object(host_info, "get", return_value=_hi()), \
             patch("voice_opencode.doctor.shutil.which", return_value="/usr/bin/x"):
            results = list(doctor.check_binaries())
        # required (3) + optional (6) = 9
        assert len(results) == 9
        assert all(c.severity == "ok" for c in results)

    def test_required_missing_fails(self) -> None:
        # piper-tts → None, everything else → /usr/bin/x
        def which(name: str) -> str | None:
            return None if name == "piper-tts" else "/usr/bin/x"

        with patch.object(host_info, "get", return_value=_hi(piper_version="")), \
             patch("voice_opencode.doctor.shutil.which", side_effect=which):
            results = list(doctor.check_binaries())
        piper = next(c for c in results if c.name == "binary: piper-tts")
        assert piper.severity == "fail"
        assert "not on PATH" in piper.detail
        assert piper.advice

    def test_required_present_but_no_version_warns(self) -> None:
        with patch.object(host_info, "get", return_value=_hi(whisper_version="")), \
             patch("voice_opencode.doctor.shutil.which", return_value="/usr/bin/x"):
            results = list(doctor.check_binaries())
        whisper = next(c for c in results if c.name == "binary: whisper-cli")
        assert whisper.severity == "warn"
        assert "version probe" in whisper.detail

    def test_optional_missing_warns_not_fails(self) -> None:
        def which(name: str) -> str | None:
            return None if name == "grim" else "/usr/bin/x"

        with patch.object(host_info, "get", return_value=_hi()), \
             patch("voice_opencode.doctor.shutil.which", side_effect=which):
            results = list(doctor.check_binaries())
        grim = next(c for c in results if c.name == "binary: grim")
        assert grim.severity == "warn"


# ---------------------------------------------------------------------------
# check_opencode_health
# ---------------------------------------------------------------------------
class TestCheckOpencodeHealth:
    def test_ok(self) -> None:
        resp = MagicMock(ok=True, status_code=200)
        with patch("voice_opencode.doctor.requests.get", return_value=resp):
            c = doctor.check_opencode_health()
        assert c.severity == "ok"
        assert "200" in c.detail

    def test_connection_refused(self) -> None:
        import requests
        with patch("voice_opencode.doctor.requests.get",
                   side_effect=requests.ConnectionError("nope")):
            c = doctor.check_opencode_health()
        assert c.severity == "fail"
        assert "cannot connect" in c.detail
        assert "systemctl" in c.advice

    def test_timeout(self) -> None:
        import requests
        with patch("voice_opencode.doctor.requests.get",
                   side_effect=requests.Timeout("slow")):
            c = doctor.check_opencode_health()
        assert c.severity == "fail"
        assert "timed out" in c.detail

    def test_non_2xx(self) -> None:
        resp = MagicMock(ok=False, status_code=503)
        with patch("voice_opencode.doctor.requests.get", return_value=resp):
            c = doctor.check_opencode_health()
        assert c.severity == "fail"
        assert "503" in c.detail


# ---------------------------------------------------------------------------
# check_opencode_mcp_config
# ---------------------------------------------------------------------------
class TestCheckMcpConfig:
    def test_missing_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "warn"
        assert "does not exist" in c.detail

    def test_invalid_json(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg = tmp_path / ".config/opencode"
        cfg.mkdir(parents=True)
        (cfg / "opencode.json").write_text("{not json")
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "fail"
        assert "not valid JSON" in c.detail

    def test_mcp_block_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg = tmp_path / ".config/opencode"
        cfg.mkdir(parents=True)
        (cfg / "opencode.json").write_text(json.dumps({"model": "x"}))
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "warn"
        assert "voice_desktop MCP not declared" in c.detail

    def test_mcp_disabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg = tmp_path / ".config/opencode"
        cfg.mkdir(parents=True)
        (cfg / "opencode.json").write_text(json.dumps(
            {"mcp": {"voice_desktop": {"enabled": False}}}
        ))
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "warn"

    def test_stale_config(self, tmp_path, monkeypatch) -> None:
        """Config edited AFTER opencode-serve started ⇒ fail."""
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg_dir = tmp_path / ".config/opencode"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "opencode.json"
        cfg_path.write_text(json.dumps(
            {"mcp": {"voice_desktop": {"command": ["/x"]}}}
        ))
        cfg_mtime = cfg_path.stat().st_mtime
        # Pretend server started 1 hour before config was written.
        monkeypatch.setattr(
            "voice_opencode.doctor._opencode_serve_started_at",
            lambda: cfg_mtime - 3600,
        )
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "fail"
        assert "stale config" in c.detail
        assert "restart" in c.advice.lower()

    def test_fresh_config(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg_dir = tmp_path / ".config/opencode"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "opencode.json"
        cfg_path.write_text(json.dumps(
            {"mcp": {"voice_desktop": {"command": ["/x"]}}}
        ))
        # Server "started" AFTER the config was last touched.
        monkeypatch.setattr(
            "voice_opencode.doctor._opencode_serve_started_at",
            lambda: cfg_path.stat().st_mtime + 3600,
        )
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "ok"

    def test_pid_unknown_does_not_block_ok(self, tmp_path, monkeypatch) -> None:
        """When we can't find the server PID, we trust the config."""
        monkeypatch.setattr("voice_opencode.doctor.Path.home", lambda: tmp_path)
        cfg_dir = tmp_path / ".config/opencode"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "opencode.json").write_text(json.dumps(
            {"mcp": {"voice_desktop": {"command": ["/x"]}}}
        ))
        monkeypatch.setattr(
            "voice_opencode.doctor._opencode_serve_started_at",
            lambda: None,
        )
        c = doctor.check_opencode_mcp_config()
        assert c.severity == "ok"


# ---------------------------------------------------------------------------
# check_session_file
# ---------------------------------------------------------------------------
class TestCheckSessionFile:
    def test_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.SESSION_FILE", tmp_path / "nope")
        c = doctor.check_session_file()
        assert c.severity == "ok"
        assert "no cached session" in c.detail

    def test_empty(self, tmp_path, monkeypatch) -> None:
        sf = tmp_path / "session.id"
        sf.write_text("   \n")
        monkeypatch.setattr("voice_opencode.doctor.SESSION_FILE", sf)
        c = doctor.check_session_file()
        assert c.severity == "warn"
        assert "empty" in c.detail

    def test_valid(self, tmp_path, monkeypatch) -> None:
        sf = tmp_path / "session.id"
        sf.write_text("ses_abc")
        monkeypatch.setattr("voice_opencode.doctor.SESSION_FILE", sf)
        resp = MagicMock(ok=True, status_code=200)
        with patch("voice_opencode.doctor.requests.get", return_value=resp):
            c = doctor.check_session_file()
        assert c.severity == "ok"
        assert "ses_abc" in c.detail

    def test_stale_session_id(self, tmp_path, monkeypatch) -> None:
        sf = tmp_path / "session.id"
        sf.write_text("ses_old")
        monkeypatch.setattr("voice_opencode.doctor.SESSION_FILE", sf)
        resp = MagicMock(ok=False, status_code=404)
        with patch("voice_opencode.doctor.requests.get", return_value=resp):
            c = doctor.check_session_file()
        assert c.severity == "warn"
        assert "404" in c.detail

    def test_network_error_warns(self, tmp_path, monkeypatch) -> None:
        sf = tmp_path / "session.id"
        sf.write_text("ses_x")
        monkeypatch.setattr("voice_opencode.doctor.SESSION_FILE", sf)
        import requests
        with patch("voice_opencode.doctor.requests.get",
                   side_effect=requests.ConnectionError("nope")):
            c = doctor.check_session_file()
        assert c.severity == "warn"


# ---------------------------------------------------------------------------
# check_platform_backends
# ---------------------------------------------------------------------------
class TestCheckPlatformBackends:
    def test_all_real_backends(self) -> None:
        # Live platform — on dev hosts this is linux-hyprland with all 6
        # backends wired. We don't mock here because the platform module
        # is itself the system under test in spirit.
        c = doctor.check_platform_backends()
        assert c.severity in ("ok", "warn")  # ok on Hyprland; warn on bare Linux

    def test_mostly_null(self) -> None:
        # Replace each capability attribute on the platform package with a
        # NullBackend-named MagicMock to simulate an unsupported host.
        nulls = {}
        for cap in ("wm", "input", "screen", "clipboard", "tts", "stt"):
            m = MagicMock()
            type(m).__name__ = "NullBackend"
            nulls[cap] = m
        with patch.multiple("voice_opencode.platform", **nulls):
            c = doctor.check_platform_backends()
        assert c.severity == "fail"
        assert "fell through" in c.detail


# ---------------------------------------------------------------------------
# check_config_file
# ---------------------------------------------------------------------------
class TestCheckConfigFile:
    def test_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("voice_opencode.doctor.CONFIG_FILE", tmp_path / "nope.json")
        c = doctor.check_config_file()
        assert c.severity == "ok"

    def test_invalid_json(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text("{not json")
        monkeypatch.setattr("voice_opencode.doctor.CONFIG_FILE", cfg)
        c = doctor.check_config_file()
        assert c.severity == "fail"

    def test_valid(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text("{}")
        monkeypatch.setattr("voice_opencode.doctor.CONFIG_FILE", cfg)
        c = doctor.check_config_file()
        assert c.severity == "ok"


# ---------------------------------------------------------------------------
# run_all
# ---------------------------------------------------------------------------
class TestRunAll:
    def test_returns_a_list_of_checks(self) -> None:
        # Patch everything that does I/O so this is hermetic.
        with patch.object(doctor, "check_config_file",
                          return_value=doctor.Check("config.json", "ok", "x")), \
             patch.object(doctor, "check_platform_backends",
                          return_value=doctor.Check("platform backends", "ok", "x")), \
             patch.object(doctor, "check_binaries", return_value=iter([
                 doctor.Check("binary: opencode", "ok", "x"),
             ])), \
             patch.object(doctor, "check_opencode_health",
                          return_value=doctor.Check("opencode-serve", "ok", "x")), \
             patch.object(doctor, "check_opencode_mcp_config",
                          return_value=doctor.Check("opencode mcp config", "ok", "x")), \
             patch.object(doctor, "check_session_file",
                          return_value=doctor.Check("opencode session", "ok", "x")):
            out = doctor.run_all()
        assert len(out) == 6
        assert all(isinstance(c, doctor.Check) for c in out)


# ---------------------------------------------------------------------------
# restart_opencode_serve
# ---------------------------------------------------------------------------
class TestRestart:
    def test_success(self) -> None:
        r = MagicMock(returncode=0, stderr="")
        with patch("voice_opencode.doctor.subprocess.run", return_value=r):
            c = doctor.restart_opencode_serve()
        assert c.severity == "ok"

    def test_failure_propagates(self) -> None:
        r = MagicMock(returncode=5, stderr="unit not found")
        with patch("voice_opencode.doctor.subprocess.run", return_value=r):
            c = doctor.restart_opencode_serve()
        assert c.severity == "fail"
        assert "5" in c.detail

    def test_systemctl_missing(self) -> None:
        with patch("voice_opencode.doctor.subprocess.run",
                   side_effect=FileNotFoundError("no systemctl")):
            c = doctor.restart_opencode_serve()
        assert c.severity == "fail"


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
class TestCmdDoctor:
    def test_returns_0_when_no_fail(self, capsys) -> None:
        from voice_opencode import cli
        with patch.object(doctor, "run_all", return_value=[
            doctor.Check("a", "ok", "x"),
            doctor.Check("b", "warn", "y", advice="z"),
        ]):
            rc = cli.cmd_doctor([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[ok]" in out and "[warn]" in out
        assert "→ z" in out

    def test_returns_1_when_any_fail(self, capsys) -> None:
        from voice_opencode import cli
        with patch.object(doctor, "run_all", return_value=[
            doctor.Check("a", "fail", "boom", advice="fix it"),
        ]):
            rc = cli.cmd_doctor([])
        assert rc == 1
        assert "[FAIL]" in capsys.readouterr().out

    def test_json_mode(self, capsys) -> None:
        from voice_opencode import cli
        with patch.object(doctor, "run_all", return_value=[
            doctor.Check("a", "ok", "x"),
        ]):
            rc = cli.cmd_doctor(["--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == [{"name": "a", "severity": "ok", "detail": "x", "advice": ""}]

    def test_fix_invokes_restart_when_mcp_stale(self) -> None:
        from voice_opencode import cli
        with patch.object(doctor, "run_all", return_value=[
            doctor.Check("opencode mcp config", "fail", "stale"),
        ]), \
             patch.object(doctor, "restart_opencode_serve",
                          return_value=doctor.Check("fix", "ok", "done")) as restart, \
             patch.object(doctor, "check_opencode_health",
                          return_value=doctor.Check("opencode-serve", "ok", "x")), \
             patch.object(doctor, "check_opencode_mcp_config",
                          return_value=doctor.Check("opencode mcp config", "ok", "x")):
            cli.cmd_doctor(["--fix"])
        restart.assert_called_once()

    def test_fix_noop_when_mcp_ok(self) -> None:
        from voice_opencode import cli
        with patch.object(doctor, "run_all", return_value=[
            doctor.Check("opencode mcp config", "ok", "x"),
        ]), \
             patch.object(doctor, "restart_opencode_serve") as restart:
            cli.cmd_doctor(["--fix"])
        restart.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
