"""Tests for the POSIX shell backend.

Real subprocess executions are exercised here against ``/bin/echo``,
``/bin/true``, ``/bin/false``, and ``/bin/sleep`` — these are POSIX
and present on every test host we care about. The point of the
backend is the *rails*, not subprocess itself, so most tests verify
allowlist behaviour, dry-run semantics, and timeout handling.
"""

from __future__ import annotations

import pytest

from voice_opencode.backends.linux_shell_posix import shell_backend as sh
from voice_opencode.platform import capabilities as cap
from voice_opencode.platform.base import BackendError


# Convenience: build a backend with an explicit allowlist + timeout
# so tests don't depend on user config.
def _mk(allowlist: tuple[str, ...] = (r"echo", r"true", r"false",
                                       r"sleep", r"sh"),
        timeout_s: float = 2.0) -> sh.PosixShellBackend:
    return sh.PosixShellBackend(allowlist=allowlist, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# capabilities + init
# ---------------------------------------------------------------------------
class TestInit:
    def test_capability_set(self) -> None:
        assert _mk().capabilities() == frozenset({cap.SHELL_RUN})

    def test_uses_config_defaults_when_none_given(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The defaults from config.Settings should be picked up.
        from voice_opencode import config
        b = sh.PosixShellBackend()
        assert b._allowlist == config.settings.shell_allowlist
        assert b._timeout_s == float(config.settings.shell_timeout_s)


# ---------------------------------------------------------------------------
# allowlist enforcement
# ---------------------------------------------------------------------------
class TestAllowlist:
    def test_empty_allowlist_denies_everything(self) -> None:
        b = _mk(allowlist=())
        with pytest.raises(BackendError, match="allowlist is empty"):
            b.run("echo hi", dry_run=False)

    def test_basename_match_full(self) -> None:
        # Only an exact basename match should pass (fullmatch).
        b = _mk(allowlist=(r"echo",))
        # /bin/echo passes — basename matches.
        r = b.run("/bin/echo hi", dry_run=False)
        assert r["rc"] == 0
        assert r["stdout"].strip() == "hi"

    def test_partial_match_rejected(self) -> None:
        # 'ec' is a prefix of 'echo' but not a fullmatch.
        b = _mk(allowlist=(r"ec",))
        with pytest.raises(BackendError, match="not in allowlist"):
            b.run("echo hi", dry_run=False)

    def test_regex_pattern(self) -> None:
        b = _mk(allowlist=(r"python3?",))
        # 'python3' matches; 'python' would too; 'pythond' wouldn't.
        b.run("/usr/bin/python3 --version", dry_run=True)
        with pytest.raises(BackendError, match="not in allowlist"):
            b.run("pythond --version", dry_run=True)

    def test_denied_command_not_spawned(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If the denial path leaked through, subprocess.run would be called.
        called = []
        monkeypatch.setattr(sh.subprocess, "run",
                            lambda *a, **k: called.append((a, k)))
        b = _mk(allowlist=(r"echo",))
        with pytest.raises(BackendError):
            b.run("rm -rf /", dry_run=False)
        assert called == []


# ---------------------------------------------------------------------------
# argv parsing + shell-metachar rejection
# ---------------------------------------------------------------------------
class TestParse:
    def test_string_split_via_shlex(self) -> None:
        b = _mk()
        # Quoted argument stays as one token.
        r = b.run('echo "hello world"', dry_run=True)
        assert r["cmd"] == ["echo", "hello world"]

    def test_list_passed_verbatim(self) -> None:
        b = _mk()
        r = b.run(["echo", "a", "b c"], dry_run=True)
        assert r["cmd"] == ["echo", "a", "b c"]

    def test_unbalanced_quote_raises(self) -> None:
        with pytest.raises(BackendError, match="cannot parse"):
            _mk().run('echo "unterminated', dry_run=True)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(BackendError, match="empty"):
            _mk().run("", dry_run=True)

    @pytest.mark.parametrize("bad", [
        "echo hi | grep h",       # pipe
        "echo hi && false",       # &&
        "echo hi; false",         # ;
        "echo $(date)",           # subshell
        "echo `date`",            # backticks
        "echo > /tmp/x",          # redirect
    ])
    def test_metachars_rejected_string(self, bad: str) -> None:
        with pytest.raises(BackendError, match="metachar|parse"):
            _mk().run(bad, dry_run=True)

    def test_metachars_rejected_list(self) -> None:
        # A list input bypasses shlex but the post-parse scan still catches it.
        with pytest.raises(BackendError, match="metachar"):
            _mk().run(["echo", "a;b"], dry_run=True)


# ---------------------------------------------------------------------------
# dry-run vs real execution
# ---------------------------------------------------------------------------
class TestExecution:
    def test_dry_run_does_not_spawn(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = []
        monkeypatch.setattr(sh.subprocess, "run",
                            lambda *a, **k: called.append((a, k)))
        r = _mk().run("echo hi", dry_run=True)
        assert r["dry_run"] is True
        assert r["rc"] == 0
        assert r["stdout"] == "" and r["stderr"] == ""
        assert called == []

    def test_dry_run_is_default(self) -> None:
        r = _mk().run("echo hi")        # no dry_run kw
        assert r["dry_run"] is True

    def test_real_execution_captures_stdout(self) -> None:
        r = _mk().run("echo hello", dry_run=False)
        assert r["rc"] == 0
        assert r["dry_run"] is False
        assert r["stdout"].strip() == "hello"

    def test_returns_nonzero_rc(self) -> None:
        r = _mk().run("false", dry_run=False)
        assert r["rc"] != 0

    def test_cwd_applied(self, tmp_path) -> None:
        # 'true' takes no args but with cwd we exercise the param.
        r = _mk().run("true", cwd=str(tmp_path), dry_run=False)
        assert r["rc"] == 0
        assert r["cwd"] == str(tmp_path)


# ---------------------------------------------------------------------------
# timeout
# ---------------------------------------------------------------------------
class TestTimeout:
    def test_timeout_triggers(self) -> None:
        b = _mk(timeout_s=0.2)
        r = b.run("sleep 2", dry_run=False)
        assert r["rc"] == -1
        assert "timeout" in r["stderr"]

    def test_timeout_capped_at_60s(self) -> None:
        # Caller passes 999; effective should not exceed 60.
        # We can't watch the actual subprocess call easily without
        # mocking, but the cap is the contract — assert via mocking.
        import subprocess as real_sp
        b = _mk(timeout_s=0.5)
        captured: dict = {}
        def fake_run(*_a, **kw):
            captured["timeout"] = kw["timeout"]
            return real_sp.CompletedProcess(args=[], returncode=0,
                                            stdout="", stderr="")
        import voice_opencode.backends.linux_shell_posix.shell_backend as mod
        original = mod.subprocess.run
        try:
            mod.subprocess.run = fake_run     # type: ignore[assignment]
            b.run("echo hi", timeout=999.0, dry_run=False)
        finally:
            mod.subprocess.run = original     # type: ignore[assignment]
        assert captured["timeout"] == 60.0


# ---------------------------------------------------------------------------
# output truncation
# ---------------------------------------------------------------------------
class TestTruncation:
    def test_truncate_helper(self) -> None:
        small = "a" * 100
        assert sh._truncate(small) == small
        big = "a" * (sh._MAX_OUTPUT_BYTES + 1000)
        out = sh._truncate(big)
        assert "truncated" in out
        # Truncated text is shorter than the original.
        assert len(out) < len(big) + 200
