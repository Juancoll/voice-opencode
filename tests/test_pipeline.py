"""Tests for pipeline.py — timestamped lockfile + cross-process semantics.

Motivated by the 2026-05-18 smoke test: a long TTS reply was overlapped
by a second pipeline triggered by a stray F9 release ("voice played
twice"). Hyprland dispatches each F9 bind as a fresh ``voice`` process,
so the lock must be cross-process.

Implementation under test: ``pipeline._pipeline_lock`` writes a text
lockfile ``"<pid> <unix_ms>\\n"`` via O_CREAT|O_EXCL; on contention it
inspects the file and either drops (live, fresh holder) or steals
(dead PID, or older than ``_LOCK_TTL_S``).

Every test isolates state by:

* pointing ``PIPELINE_LOCK_FILE`` at ``tmp_path`` via monkeypatch on
  the ``pipeline`` module's binding (not on ``paths``, because the
  module already imported the name);
* mocking ``pipeline.log`` / ``notify`` / ``set_state`` so noise does
  not bleed into the real ``logs/voice.log`` — an earlier iteration
  polluted production logs because the import-bound ``log`` was real.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_opencode import pipeline


@pytest.fixture
def isolated_lock(tmp_path, monkeypatch):
    """Redirect lockfile into tmp_path; mute log/notify/state."""
    lock_file = tmp_path / "pipeline.lock"
    monkeypatch.setattr(pipeline, "PIPELINE_LOCK_FILE", lock_file)
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "log", MagicMock())
    monkeypatch.setattr(pipeline, "notify", MagicMock())
    monkeypatch.setattr(pipeline, "set_state", MagicMock())
    return lock_file


def _write_lockfile(path: Path, pid: int, ts_ms: int) -> None:
    """Simulate another process holding the lock with a known timestamp."""
    path.write_text(f"{pid} {ts_ms}\n")


# ---------------------------------------------------------------------------
# _pipeline_lock context manager
# ---------------------------------------------------------------------------
def test_lock_acquired_when_free_creates_file(isolated_lock):
    assert not isolated_lock.exists()
    with pipeline._pipeline_lock("test") as acquired:
        assert acquired is True
        # Inside the block the lock file exists with our pid.
        contents = isolated_lock.read_text().strip().split()
        assert int(contents[0]) == os.getpid()
        assert len(contents) == 2  # pid + ts
    # Released → file removed.
    assert not isolated_lock.exists()


def test_lock_dropped_when_live_holder_is_fresh(isolated_lock):
    # Another live process (use our own PID — guaranteed alive) holding
    # the lock with a recent timestamp.
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with pipeline._pipeline_lock("test") as acquired:
        assert acquired is False
    assert isolated_lock.exists()  # holder still owns it
    # Default behaviour: low-urgency toast emitted so the user sees feedback.
    assert pipeline.notify.call_args.kwargs.get("urgency") == "low"


def test_lock_dropped_silently_when_notify_on_busy_false(isolated_lock):
    """Used by start_recording to suppress toasts on F9 key-repeat."""
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with pipeline._pipeline_lock("test", notify_on_busy=False) as acquired:
        assert acquired is False
    pipeline.notify.assert_not_called()
    # The log line still appears so silent drops remain diagnosable.
    logged = " | ".join(call.args[0] for call in pipeline.log.call_args_list)
    assert "Pipeline busy" in logged


def test_lock_stolen_when_holder_pid_is_dead(isolated_lock):
    # PID 1 is reachable (init) but let's use a pid we KNOW is dead by
    # spawning a child and waiting for it to exit.
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    # Now `pid` is reaped; os.kill(pid, 0) raises ProcessLookupError.
    _write_lockfile(isolated_lock, pid, int(time.time() * 1000))
    with pipeline._pipeline_lock("test") as acquired:
        assert acquired is True
        # We took over: file contains our pid now.
        contents = isolated_lock.read_text().strip().split()
        assert int(contents[0]) == os.getpid()
    assert not isolated_lock.exists()


def test_lock_stolen_when_timestamp_is_stale(isolated_lock):
    ancient_ms = int((time.time() - (pipeline._LOCK_TTL_S + 60)) * 1000)
    _write_lockfile(isolated_lock, os.getpid(), ancient_ms)
    with pipeline._pipeline_lock("test") as acquired:
        assert acquired is True
        # Timestamp inside is fresh now.
        new_ms = int(isolated_lock.read_text().strip().split()[1])
        assert (time.time() * 1000) - new_ms < 5000
    assert not isolated_lock.exists()


def test_lock_stolen_when_file_is_garbage(isolated_lock):
    # Corrupted lockfile from a previous crash mid-write.
    isolated_lock.write_text("not a valid lock line at all\n")
    with pipeline._pipeline_lock("test") as acquired:
        assert acquired is True
    assert not isolated_lock.exists()


def test_lock_released_on_exception(isolated_lock):
    with pytest.raises(RuntimeError), pipeline._pipeline_lock("test") as acquired:
        assert acquired is True
        raise RuntimeError("boom")
    assert not isolated_lock.exists()
    # Fresh acquire must succeed.
    with pipeline._pipeline_lock("retry") as acquired:
        assert acquired is True


# ---------------------------------------------------------------------------
# start_recording
# ---------------------------------------------------------------------------
def test_start_recording_acquires_and_calls_audio_start(isolated_lock):
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "start") as audio_start:
        pipeline.start_recording()
        audio_start.assert_called_once()
    assert not isolated_lock.exists()


def test_start_recording_dropped_when_lock_held_live(isolated_lock):
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start") as audio_start:
        pipeline.start_recording()
        audio_start.assert_not_called()


def test_start_recording_dropped_is_silent_no_notify(isolated_lock):
    """F9 key-repeat / double-tap must NOT pop a toast on every press."""
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start"):
        pipeline.start_recording()
        # Zero notify calls — the whole point of the silent drop.
        pipeline.notify.assert_not_called()


def test_start_recording_dropped_logs_specific_message(isolated_lock):
    """Even when silent, the log must explain *why* F9 was ignored."""
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start"):
        pipeline.start_recording()
    # Collect every string passed to log() and search for our marker.
    logged = " | ".join(call.args[0] for call in pipeline.log.call_args_list)
    assert "F9 descartado" in logged
    assert "turno en curso" in logged


def test_start_recording_dropped_when_audio_already_recording(isolated_lock):
    """Fast path: audio.is_recording() catches the duplicate before the lock."""
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline.audio, "start") as audio_start:
        pipeline.start_recording()
        audio_start.assert_not_called()
    # No toast on this path either.
    pipeline.notify.assert_not_called()
    # Lockfile must not have been touched.
    assert not isolated_lock.exists()


def test_start_recording_dropped_message_is_unified_across_paths(isolated_lock):
    """Same log line whether audio.is_recording or the lock catches it.

    Lets the user grep ``logs/voice.log`` for a single marker instead
    of two different ones depending on which guard fired.
    """
    expected = "F9 descartado: ya hay un turno en curso (grabación o respuesta)."

    # Path 1: audio.is_recording() guard.
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=True):
        pipeline.start_recording()
    audio_path_msgs = [c.args[0] for c in pipeline.log.call_args_list]
    assert expected in audio_path_msgs

    # Reset and try path 2: cross-process lock contention.
    pipeline.log.reset_mock()
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start"):
        pipeline.start_recording()
    lock_path_msgs = [c.args[0] for c in pipeline.log.call_args_list]
    assert expected in lock_path_msgs


def test_start_recording_releases_lock_on_audio_exception(isolated_lock):
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "start", side_effect=RuntimeError("boom")), \
         pytest.raises(RuntimeError):
        pipeline.start_recording()
    assert not isolated_lock.exists()


# ---------------------------------------------------------------------------
# stop_and_run
# ---------------------------------------------------------------------------
def test_stop_and_run_happy_path_speaks_once(isolated_lock):
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=None), \
         patch.object(pipeline.Session, "get_or_create") as get_session, \
         patch.object(pipeline.tts, "speak") as speak:
        get_session.return_value.ask.return_value = "respuesta"
        pipeline.stop_and_run()
        speak.assert_called_once_with("respuesta")
    assert not isolated_lock.exists()


def test_stop_and_run_dropped_when_lock_held_live(isolated_lock):
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.audio, "stop") as audio_stop, \
         patch.object(pipeline.stt, "transcribe") as transcribe, \
         patch.object(pipeline.tts, "speak") as speak:
        pipeline.stop_and_run()
        audio_stop.assert_not_called()
        transcribe.assert_not_called()
        speak.assert_not_called()
    # Unlike start_recording, stop_and_run KEEPS the toast — if you
    # release F9 mid-reply and nothing happens, you deserve feedback.
    pipeline.notify.assert_called_once()
    assert pipeline.notify.call_args.kwargs.get("urgency") == "low"


def test_stop_and_run_releases_lock_on_tts_exception(isolated_lock):
    """Critical regression: this is the case that polluted the real log."""
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=None), \
         patch.object(pipeline.Session, "get_or_create") as get_session, \
         patch.object(pipeline.tts, "speak", side_effect=RuntimeError("piper died")):
        get_session.return_value.ask.return_value = "respuesta"
        pipeline.stop_and_run()  # internal try/except catches it
    assert not isolated_lock.exists()


def test_stop_and_run_releases_lock_when_audio_returns_none(isolated_lock):
    with patch.object(pipeline.audio, "stop", return_value=None):
        pipeline.stop_and_run()
    assert not isolated_lock.exists()


# ---------------------------------------------------------------------------
# Concurrency: same-process two-thread test. Cross-process semantics are
# covered by the O_EXCL contract — fresh open() in a child process
# behaves identically to the lockfile state we simulate here.
# ---------------------------------------------------------------------------
def test_concurrent_stop_and_run_only_one_speaks(isolated_lock):
    speak_calls: list[str] = []
    started = threading.Event()
    allow_finish = threading.Event()

    def slow_speak(text: str) -> None:
        speak_calls.append(text)
        started.set()
        allow_finish.wait(timeout=2.0)

    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=None), \
         patch.object(pipeline.Session, "get_or_create") as get_session, \
         patch.object(pipeline.tts, "speak", side_effect=slow_speak):
        get_session.return_value.ask.return_value = "respuesta"

        t1 = threading.Thread(target=pipeline.stop_and_run)
        t1.start()
        assert started.wait(timeout=2.0), "first stop_and_run never reached tts.speak"
        # Second simulated F9 release while t1 still owns the lockfile.
        pipeline.stop_and_run()
        allow_finish.set()
        t1.join(timeout=2.0)

    assert speak_calls == ["respuesta"], (
        f"expected exactly one speak() call, got {speak_calls!r}"
    )


# ---------------------------------------------------------------------------
# toggle delegates.
# ---------------------------------------------------------------------------
def test_toggle_when_recording_calls_stop_and_run(isolated_lock):
    with patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline, "stop_and_run") as stop_run, \
         patch.object(pipeline, "start_recording") as start_rec:
        pipeline.toggle()
        stop_run.assert_called_once()
        start_rec.assert_not_called()


def test_toggle_when_idle_calls_start_recording(isolated_lock):
    with patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline, "stop_and_run") as stop_run, \
         patch.object(pipeline, "start_recording") as start_rec:
        pipeline.toggle()
        start_rec.assert_called_once()
        stop_run.assert_not_called()
