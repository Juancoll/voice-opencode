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
    """Redirect lockfile into tmp_path; mute log/state/HUD."""
    lock_file = tmp_path / "pipeline.lock"
    monkeypatch.setattr(pipeline, "PIPELINE_LOCK_FILE", lock_file)
    monkeypatch.setattr(pipeline, "ensure_dirs", lambda: None)
    monkeypatch.setattr(pipeline, "log", MagicMock())
    monkeypatch.setattr(pipeline, "set_state", MagicMock())
    monkeypatch.setattr(pipeline, "turn_start", MagicMock())
    monkeypatch.setattr(pipeline, "turn_update", MagicMock())
    monkeypatch.setattr(pipeline, "turn_end", MagicMock())
    # Default to 'idle' so the cancel-on-busy branch in stop_and_run
    # never fires unless a test explicitly opts in.
    monkeypatch.setattr(
        pipeline.state_mod, "get_state",
        MagicMock(return_value="idle"),
    )
    # Neutralise the out-of-process dictation watchdog: it forks a
    # real subprocess that would (a) noisy-warn about fork-in-threaded,
    # (b) try to open /dev/input on the test runner. Tests that care
    # about the watchdog live in test_dictation_watchdog.py.
    monkeypatch.setattr(pipeline.dictation_watchdog, "spawn", MagicMock())
    monkeypatch.setattr(pipeline.dictation_watchdog, "stop", MagicMock())
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
    # ADR-0027: the contention path is always silent (HUD already
    # shows the running turn's state). The log line still appears.
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


def test_start_recording_dropped_is_silent(isolated_lock):
    """F9 key-repeat / double-tap must NOT do anything visible.

    ADR-0027: there's no libnotify path left in pipeline at all,
    so the only thing to assert is that audio.start wasn't called.
    """
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start") as audio_start:
        pipeline.start_recording()
        audio_start.assert_not_called()


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
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline.tts, "speak") as speak:
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(["respuesta"])
        pipeline.stop_and_run()
        speak.assert_called_once_with("respuesta")
    assert not isolated_lock.exists()


def test_stop_and_run_streams_partial_text_to_hud(isolated_lock):
    """Phase 1 streaming: each delta should refresh the HUD subtitle
    *before* the full reply is available, and the final speak() gets
    the full concatenation."""
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=None), \
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline, "turn_update") as upd, \
         patch.object(pipeline.tts, "speak") as speak, \
         patch.object(pipeline.time, "monotonic", side_effect=[i * 1.0 for i in range(100)]):
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(
            ["Hola, ", "¿cómo ", "estás?"]
        )
        pipeline.stop_and_run()
    # Speak got the joined reply, stripped.
    speak.assert_called_once_with("Hola, ¿cómo estás?")
    # HUD saw at least one "Pensando…" with cumulative text.
    pensando_calls = [
        c for c in upd.call_args_list
        if c.args and c.args[0] == "🧠 Pensando…" and "Hola" in (c.args[1] if len(c.args) > 1 else "")
    ]
    assert pensando_calls, f"no streaming HUD update saw 'Hola' in {upd.call_args_list}"


def test_stop_and_run_passes_extra_context_when_screenshot_present(isolated_lock):
    """When a screenshot is captured AND attach_monitor_layout is on,
    pipeline must pass the composed context block to ask_stream so the
    model gets coordinates + system info without extra round trips."""
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=Path("/tmp/shot.png")), \
         patch.object(pipeline, "build_extra_context",
                      return_value="Monitor layout: DP-1 1920x1080 @ (0,0) [FOCUSED]\n\nSystem: Linux 6.0"), \
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline.tts, "speak"):
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(["ok"])
        pipeline.stop_and_run()
        call = get_backend.return_value.ask_stream.call_args
        ctx = call.kwargs.get("extra_context", "")
        assert "Monitor layout:" in ctx
        assert "System: Linux 6.0" in ctx


def test_stop_and_run_skips_extra_context_when_disabled(isolated_lock, monkeypatch):
    """When attach_monitor_layout=False, build_extra_context() must
    not be called and extra_context stays empty."""
    from voice_opencode import config as _cfg

    monkeypatch.setattr(
        _cfg, "settings", _cfg.Settings(attach_monitor_layout=False)
    )
    monkeypatch.setattr(pipeline, "settings", _cfg.settings, raising=False)
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=Path("/tmp/shot.png")), \
         patch.object(pipeline, "build_extra_context",
                      return_value="SHOULD NOT APPEAR") as bec, \
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline.tts, "speak"):
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(["ok"])
        pipeline.stop_and_run()
        bec.assert_not_called()
        call = get_backend.return_value.ask_stream.call_args
        assert call.kwargs.get("extra_context", "") == ""


def test_stop_and_run_dropped_when_lock_held_live_and_state_idle(isolated_lock):
    """When held by a live holder but state is 'idle' (e.g. holder
    is still in the brief setup window), no cancel: just drop."""
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.state_mod, "get_state", return_value="idle"), \
         patch.object(pipeline.audio, "stop") as audio_stop, \
         patch.object(pipeline.stt, "transcribe") as transcribe, \
         patch.object(pipeline.tts, "speak") as speak, \
         patch.object(pipeline, "_cancel_active_turn") as cancel:
        pipeline.stop_and_run()
        audio_stop.assert_not_called()
        transcribe.assert_not_called()
        speak.assert_not_called()
        cancel.assert_not_called()


@pytest.mark.parametrize("phase", ["thinking", "speaking"])
def test_stop_and_run_cancels_when_busy_in_cancellable_phase(isolated_lock, phase):
    """F9 mid-turn during thinking/speaking aborts the held turn."""
    _write_lockfile(isolated_lock, os.getpid(), int(time.time() * 1000))
    with patch.object(pipeline.state_mod, "get_state", return_value=phase), \
         patch.object(pipeline, "_cancel_active_turn") as cancel, \
         patch.object(pipeline.audio, "stop") as audio_stop:
        pipeline.stop_and_run()
        cancel.assert_called_once_with(os.getpid())
        # Holder is signalled; we do NOT proceed into a new pipeline.
        audio_stop.assert_not_called()
    # Lock file still owned by the (simulated) holder — canceller does
    # not unlink, the dying holder's finally clause does.
    assert isolated_lock.exists()


def test_stop_and_run_does_not_cancel_when_holder_pid_dead(isolated_lock):
    """If the lockholder is dead, we steal and run, not cancel."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    _write_lockfile(isolated_lock, pid, int(time.time() * 1000))
    with patch.object(pipeline.state_mod, "get_state", return_value="thinking"), \
         patch.object(pipeline, "_cancel_active_turn") as cancel, \
         patch.object(pipeline.audio, "stop", return_value=None):
        pipeline.stop_and_run()
        cancel.assert_not_called()


def test_cancel_active_turn_signals_pid_and_calls_abort(isolated_lock):
    """_cancel_active_turn aborts session, SIGINTs holder, closes HUD."""
    with patch.object(pipeline, "get_backend") as get_backend, \
         patch("voice_opencode.pipeline.os.kill") as kill, \
         patch("voice_opencode.pipeline.subprocess.run") as run, \
         patch("voice_opencode.pipeline.set_state") as set_st, \
         patch("voice_opencode.pipeline.turn_update") as upd, \
         patch("voice_opencode.pipeline.turn_end") as end:
        get_backend.return_value.name = "opencode"
        get_backend.return_value.session_id.return_value = "sess-1"
        pipeline._cancel_active_turn(12345)
        get_backend.return_value.abort.assert_called_once()
        kill.assert_called_once()
        # SIGINT (not SIGTERM) so the holder's finally clause runs.
        import signal as _sig
        assert kill.call_args.args == (12345, _sig.SIGINT)
        # pkill paplay was attempted.
        assert run.call_args.args[0][:2] == ["pkill", "-x"]
        set_st.assert_called_with("idle")
        upd.assert_called_once()
        end.assert_called_once()


def test_cancel_active_turn_survives_dead_pid(isolated_lock):
    """Signalling a dead pid is a no-op, not a crash."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    with patch.object(pipeline, "get_backend") as get_backend, \
         patch("voice_opencode.pipeline.subprocess.run"), \
         patch("voice_opencode.pipeline.set_state"), \
         patch("voice_opencode.pipeline.turn_update"), \
         patch("voice_opencode.pipeline.turn_end"):
        get_backend.return_value.session_id.return_value = None
        # Must not raise.
        pipeline._cancel_active_turn(pid)


def test_stop_and_run_releases_lock_on_tts_exception(isolated_lock):
    """Critical regression: this is the case that polluted the real log."""
    with patch.object(pipeline.audio, "stop", return_value=MagicMock(spec=Path)), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline, "capture", return_value=None), \
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline.tts, "speak", side_effect=RuntimeError("piper died")):
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(["respuesta"])
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
         patch.object(pipeline, "get_backend") as get_backend, \
         patch.object(pipeline.tts, "speak", side_effect=slow_speak):
        get_backend.return_value.name = "opencode"
        get_backend.return_value.ask_stream.return_value = iter(["respuesta"])

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


# ---------------------------------------------------------------------------
# Dictation flow (Ctrl+F9 by default): record → STT → inject. No LLM, no TTS.
# ---------------------------------------------------------------------------
def test_start_dictation_acquires_lock_and_starts_audio(isolated_lock):
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start") as a_start:
        pipeline.start_dictation()
        a_start.assert_called_once()
    pipeline.set_state.assert_any_call("recording")
    pipeline.turn_start.assert_called_once()


def test_start_dictation_dropped_when_paused(isolated_lock):
    with patch.object(pipeline.agent, "is_blocking", return_value=True), \
         patch.object(pipeline.agent, "is_active", return_value=False), \
         patch.object(pipeline.audio, "start") as a_start:
        pipeline.start_dictation()
        a_start.assert_not_called()


def test_start_dictation_dropped_when_already_recording(isolated_lock):
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline.audio, "start") as a_start:
        pipeline.start_dictation()
        a_start.assert_not_called()


def test_stop_dictation_injects_via_type_by_default(isolated_lock, monkeypatch):
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "type"})(),
        raising=False,
    )
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola mundo"), \
         patch.object(pipeline.desktop, "type_text") as type_text, \
         patch.object(pipeline.desktop, "press_key") as press_key:
        pipeline.stop_dictation_and_inject()
    type_text.assert_called_once_with("hola mundo")
    press_key.assert_not_called()
    pipeline.set_state.assert_any_call("idle")


def test_stop_dictation_injects_via_paste_when_configured(isolated_lock, monkeypatch):
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "paste"})(),
        raising=False,
    )
    cb = MagicMock()
    monkeypatch.setattr(pipeline._plat, "clipboard", cb, raising=False)
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline.desktop, "type_text") as type_text, \
         patch.object(pipeline.desktop, "press_key") as press_key:
        pipeline.stop_dictation_and_inject()
    cb.write.assert_called_once_with("hola")
    press_key.assert_called_once_with("ctrl+v")
    type_text.assert_not_called()


def test_stop_dictation_paste_falls_back_to_type_on_clipboard_failure(
    isolated_lock, monkeypatch,
):
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "paste"})(),
        raising=False,
    )
    cb = MagicMock()
    cb.write.side_effect = RuntimeError("no clipboard backend")
    monkeypatch.setattr(pipeline._plat, "clipboard", cb, raising=False)
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline.desktop, "type_text") as type_text:
        pipeline.stop_dictation_and_inject()
    type_text.assert_called_once_with("hola")


def test_stop_dictation_handles_no_audio(isolated_lock):
    with patch.object(pipeline.audio, "stop", return_value=None), \
         patch.object(pipeline.stt, "transcribe") as transcribe, \
         patch.object(pipeline.desktop, "type_text") as type_text:
        pipeline.stop_dictation_and_inject()
    transcribe.assert_not_called()
    type_text.assert_not_called()
    pipeline.set_state.assert_any_call("idle")


def test_stop_dictation_handles_empty_transcript(isolated_lock):
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value=""), \
         patch.object(pipeline.desktop, "type_text") as type_text:
        pipeline.stop_dictation_and_inject()
    type_text.assert_not_called()
    pipeline.set_state.assert_any_call("idle")


def test_stop_dictation_handles_stt_failure(isolated_lock):
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", side_effect=RuntimeError("boom")), \
         patch.object(pipeline.desktop, "type_text") as type_text:
        pipeline.stop_dictation_and_inject()
    type_text.assert_not_called()
    pipeline.set_state.assert_any_call("error")


def test_stop_dictation_handles_inject_failure(isolated_lock, monkeypatch):
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "type"})(),
        raising=False,
    )
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline.desktop, "type_text", side_effect=RuntimeError("no ydotool")):
        pipeline.stop_dictation_and_inject()
    pipeline.set_state.assert_any_call("error")


def test_toggle_dictation_when_recording_stops(isolated_lock):
    with patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline, "stop_dictation_and_inject") as stop_dict, \
         patch.object(pipeline, "start_dictation") as start_dict:
        pipeline.toggle_dictation()
        stop_dict.assert_called_once()
        start_dict.assert_not_called()


def test_toggle_dictation_when_idle_starts(isolated_lock):
    with patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline, "stop_dictation_and_inject") as stop_dict, \
         patch.object(pipeline, "start_dictation") as start_dict:
        pipeline.toggle_dictation()
        start_dict.assert_called_once()
        stop_dict.assert_not_called()


def test_start_dictation_captures_focused_window_id(isolated_lock, tmp_path, monkeypatch):
    focus_file = tmp_path / "dictation.focus"
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    fake_win = MagicMock(id="0xdead")
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start"), \
         patch.object(pipeline._plat.wm, "active_window", return_value=fake_win):
        pipeline.start_dictation()
    assert focus_file.read_text() == "0xdead"


def test_start_dictation_clears_focus_file_when_no_active_window(
    isolated_lock, tmp_path, monkeypatch,
):
    focus_file = tmp_path / "dictation.focus"
    focus_file.write_text("stale")
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    with patch.object(pipeline.agent, "is_blocking", return_value=False), \
         patch.object(pipeline.audio, "is_recording", return_value=False), \
         patch.object(pipeline.audio, "start"), \
         patch.object(pipeline._plat.wm, "active_window", return_value=None):
        pipeline.start_dictation()
    assert not focus_file.exists()


def test_stop_dictation_restores_focus_before_inject(
    isolated_lock, tmp_path, monkeypatch,
):
    focus_file = tmp_path / "dictation.focus"
    focus_file.write_text("0xbeef")
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "type"})(),
        raising=False,
    )
    call_order: list[str] = []
    focus_mock = MagicMock(side_effect=lambda _: call_order.append("focus"))
    type_mock = MagicMock(side_effect=lambda _: call_order.append("type"))
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline._plat.wm, "focus_window", focus_mock), \
         patch.object(pipeline.desktop, "type_text", type_mock):
        pipeline.stop_dictation_and_inject()
    assert call_order == ["focus", "type"]
    focus_mock.assert_called_once_with("0xbeef")
    # File consumed.
    assert not focus_file.exists()


def test_stop_dictation_handles_missing_focus_file(
    isolated_lock, tmp_path, monkeypatch,
):
    focus_file = tmp_path / "dictation.focus"  # never created
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "type"})(),
        raising=False,
    )
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline._plat.wm, "focus_window") as focus_mock, \
         patch.object(pipeline.desktop, "type_text") as type_mock:
        pipeline.stop_dictation_and_inject()
    # No focus call (no id to restore), but inject still happens.
    focus_mock.assert_not_called()
    type_mock.assert_called_once_with("hola")


def test_stop_dictation_inject_still_runs_when_focus_restore_fails(
    isolated_lock, tmp_path, monkeypatch,
):
    focus_file = tmp_path / "dictation.focus"
    focus_file.write_text("0xbeef")
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    monkeypatch.setattr(
        pipeline, "settings",
        type("S", (), {"dictation_inject_method": "type"})(),
        raising=False,
    )
    with patch.object(pipeline.audio, "stop", return_value="/tmp/x.wav"), \
         patch.object(pipeline.stt, "transcribe", return_value="hola"), \
         patch.object(pipeline._plat.wm, "focus_window",
                      side_effect=RuntimeError("no such window")), \
         patch.object(pipeline.desktop, "type_text") as type_mock:
        pipeline.stop_dictation_and_inject()
    type_mock.assert_called_once_with("hola")
    # Even on failure the file is consumed (avoid retrying on a stale id).
    assert not focus_file.exists()


def test_stop_and_run_delegates_to_dictation_when_sentinel_present(
    isolated_lock, tmp_path, monkeypatch,
):
    """Guards against the F9-release-without-Ctrl bug: if a dictation
    was in progress (sentinel present) and a plain F9 release fires,
    route the audio to the dictation path, not to the LLM."""
    focus_file = tmp_path / "dictation.focus"
    focus_file.write_text("0xbeef")
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    with patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline, "stop_dictation_and_inject") as stop_dict, \
         patch.object(pipeline.stt, "transcribe") as transcribe:
        pipeline.stop_and_run()
    stop_dict.assert_called_once()
    transcribe.assert_not_called()  # never reached the LLM path


def test_stop_and_run_normal_path_when_no_dictation_sentinel(
    isolated_lock, tmp_path, monkeypatch,
):
    focus_file = tmp_path / "dictation.focus"  # never created
    monkeypatch.setattr(pipeline, "DICTATION_FOCUS_FILE", focus_file)
    with patch.object(pipeline.audio, "is_recording", return_value=True), \
         patch.object(pipeline.audio, "stop", return_value=None), \
         patch.object(pipeline, "stop_dictation_and_inject") as stop_dict:
        pipeline.stop_and_run()
    stop_dict.assert_not_called()  # took the normal path
