"""Tests for the dictation key watchdog.

Hardware (``/dev/input/event*``) is mocked end-to-end — no real
keyboard is touched. We exercise:

* Keycode lookup (known / unknown names).
* The "wait for press → wait for release" state machine.
* Graceful degradation when no devices are accessible.
* ``spawn`` / ``stop`` PID-file lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_opencode import dictation_watchdog as wd


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the watchdog PID file into a tmp dir."""
    pid_file = tmp_path / "watchdog.pid"
    monkeypatch.setattr(wd, "DICTATION_WATCHDOG_PID_FILE", pid_file)
    return tmp_path


class TestKeycodeMap:
    def test_known_keys_resolve(self) -> None:
        # spot check a few keys we care about
        assert wd.KEY_CODES["F9"] == 67
        assert wd.KEY_CODES["F1"] == 59
        assert wd.KEY_CODES["PAUSE"] == 119

    def test_run_returns_2_on_unknown_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No need to mock devices — we should bail before opening any.
        called = False

        def fake_iter() -> list[Path]:
            nonlocal called
            called = True
            return [Path("/dev/input/event0")]

        monkeypatch.setattr(wd, "_iter_keyboard_devices", fake_iter)
        rc = wd.run_watchdog("NOPE_NOT_A_KEY", 10, max_seconds=0.1)
        assert rc == 2
        assert not called, "must reject unknown key before probing devices"


class TestRunWatchdog:
    def test_returns_1_when_no_devices(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(wd, "_iter_keyboard_devices", lambda: [])
        rc = wd.run_watchdog("F9", 10, max_seconds=0.1)
        assert rc == 1

    def test_returns_1_when_open_fails_on_every_device(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            wd, "_iter_keyboard_devices", lambda: [Path("/dev/input/event0")]
        )

        def bad_open(_path: str, _flags: int) -> int:
            raise PermissionError("not in input group")

        monkeypatch.setattr(wd.os, "open", bad_open)
        rc = wd.run_watchdog("F9", 10, max_seconds=0.1)
        assert rc == 1

    def test_returns_3_when_press_never_observed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If we never see the key pressed, exit with 'timed out' code."""
        monkeypatch.setattr(
            wd, "_iter_keyboard_devices", lambda: [Path("/dev/input/event0")]
        )
        monkeypatch.setattr(wd.os, "open", lambda *_a, **_kw: 999)
        closed: list[int] = []
        monkeypatch.setattr(wd.os, "close", lambda fd: closed.append(fd))
        monkeypatch.setattr(wd, "_key_is_pressed", lambda _fd, _kc: False)

        rc = wd.run_watchdog("F9", 10, max_seconds=0.05)
        assert rc == 3
        assert 999 in closed, "must close every fd it opened"

    def test_triggers_stop_when_press_then_release_observed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full happy path: press → release → spawn `voice dictate stop`."""
        monkeypatch.setattr(
            wd, "_iter_keyboard_devices", lambda: [Path("/dev/input/event0")]
        )
        monkeypatch.setattr(wd.os, "open", lambda *_a, **_kw: 7)
        monkeypatch.setattr(wd.os, "close", lambda _fd: None)

        # State machine: first call pressed, second released.
        states = iter([True, False])
        monkeypatch.setattr(
            wd, "_key_is_pressed", lambda _fd, _kc: next(states)
        )

        triggered = []
        monkeypatch.setattr(wd, "_trigger_stop", lambda: triggered.append(True))

        rc = wd.run_watchdog("F9", 10, max_seconds=1.0)
        assert rc == 0
        assert triggered == [True]

    def test_returns_3_when_deadline_hits_while_still_pressed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User holds the key past the deadline → fall back to arecord -d cap."""
        monkeypatch.setattr(
            wd, "_iter_keyboard_devices", lambda: [Path("/dev/input/event0")]
        )
        monkeypatch.setattr(wd.os, "open", lambda *_a, **_kw: 7)
        monkeypatch.setattr(wd.os, "close", lambda _fd: None)
        monkeypatch.setattr(wd, "_key_is_pressed", lambda _fd, _kc: True)

        triggered = []
        monkeypatch.setattr(wd, "_trigger_stop", lambda: triggered.append(True))

        rc = wd.run_watchdog("F9", 10, max_seconds=0.05)
        assert rc == 3
        assert triggered == [], "must not inject after timeout"


class TestSpawnStop:
    def test_spawn_empty_key_is_noop(self, isolated_state: Path) -> None:
        assert wd.spawn("", 200) is None
        assert not (isolated_state / "watchdog.pid").exists()

    def test_spawn_writes_pid_file_in_parent(
        self, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the fork into the "parent" branch.
        monkeypatch.setattr(wd.os, "fork", lambda: 4242)
        pid = wd.spawn("F9", 200)
        assert pid == 4242
        pid_file = isolated_state / "watchdog.pid"
        assert pid_file.read_text() == "4242"

    def test_spawn_kills_previous_watchdog(
        self, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (isolated_state / "watchdog.pid").write_text("9999")
        killed: list[int] = []
        monkeypatch.setattr(
            wd.os, "kill", lambda pid, _sig: killed.append(pid)
        )
        monkeypatch.setattr(wd.os, "fork", lambda: 555)
        wd.spawn("F9", 200)
        assert 9999 in killed, "stale watchdog must be reaped before respawn"
        assert (isolated_state / "watchdog.pid").read_text() == "555"

    def test_stop_missing_file_is_noop(self, isolated_state: Path) -> None:
        # Must not raise.
        wd.stop()

    def test_stop_sigterms_pid_and_unlinks_file(
        self, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (isolated_state / "watchdog.pid").write_text("7777")
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            wd.os, "kill", lambda pid, sig: killed.append((pid, sig))
        )
        wd.stop()
        assert killed == [(7777, 15)]
        assert not (isolated_state / "watchdog.pid").exists()

    def test_stop_garbage_pid_file_unlinked_without_kill(
        self, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (isolated_state / "watchdog.pid").write_text("not-a-pid")
        killed: list[int] = []
        monkeypatch.setattr(
            wd.os, "kill", lambda pid, _sig: killed.append(pid)
        )
        wd.stop()
        assert killed == [], "garbage pid file must not trigger kill"
        assert not (isolated_state / "watchdog.pid").exists()

    def test_stop_ignores_dead_process(
        self, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (isolated_state / "watchdog.pid").write_text("3333")

        def kill(_pid: int, _sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(wd.os, "kill", kill)
        # Must not raise.
        wd.stop()
        assert not (isolated_state / "watchdog.pid").exists()


class TestTriggerStop:
    def test_uses_project_shim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, list[str]] = {}

        class FakePopen:
            def __init__(self, argv: list[str], **kw: object) -> None:
                captured["argv"] = argv

        monkeypatch.setattr(wd.subprocess, "Popen", FakePopen)
        wd._trigger_stop()
        assert captured["argv"][-2:] == ["dictate", "stop"]
        assert captured["argv"][0].endswith("voice"), (
            "must invoke the project shim, not python -m"
        )
