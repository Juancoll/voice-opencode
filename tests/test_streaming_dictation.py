"""Tests for ``voice_opencode.streaming_dictation``.

Focus on the logic that doesn't need real audio / real models:

* capability probe (``is_available`` / ``missing_dependencies``)
* child-process lifecycle (``spawn`` / ``stop`` / ``is_running``)
* VAD loop bookkeeping (utterance flush, tail drain on shutdown)
* transcribe loop (prompt accumulation, leading-space rule)

The actual silero-vad / faster-whisper inference is mocked — those
have their own test suites and dragging real models into CI would
make every test run minutes long.
"""

from __future__ import annotations

import os
import signal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from voice_opencode import streaming_dictation


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------
def test_missing_dependencies_returns_empty_when_all_present():
    # The deps are actually installed in this env (we installed them
    # before running the tests). If someone runs this in a slim CI
    # the test should be skipped, not failed.
    missing = streaming_dictation.missing_dependencies()
    if missing:
        pytest.skip(f"deps missing in this env: {missing}")
    assert missing == []


def test_missing_dependencies_lists_each_missing(monkeypatch):
    # Force find_spec to return None for one module: faster_whisper.
    import importlib.util
    real = importlib.util.find_spec

    def fake(name):
        if name == "faster_whisper":
            return None
        return real(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    assert "faster_whisper" in streaming_dictation.missing_dependencies()


def test_is_available_false_when_flag_off(monkeypatch):
    monkeypatch.setattr(
        streaming_dictation, "settings",
        SimpleNamespace(streaming_dictation_enabled=False),
        raising=False,
    )
    assert streaming_dictation.is_available() is False


def test_is_available_false_when_deps_missing(monkeypatch):
    monkeypatch.setattr(
        streaming_dictation, "settings",
        SimpleNamespace(streaming_dictation_enabled=True),
        raising=False,
    )
    monkeypatch.setattr(
        streaming_dictation, "missing_dependencies",
        MagicMock(return_value=["faster_whisper"]),
    )
    assert streaming_dictation.is_available() is False


# ---------------------------------------------------------------------------
# is_running / spawn / stop
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_pidfile(tmp_path, monkeypatch):
    pid_file = tmp_path / "stream.pid"
    monkeypatch.setattr(streaming_dictation, "STREAMING_DICTATION_PID_FILE", pid_file)
    monkeypatch.setattr(streaming_dictation, "log", MagicMock())
    return pid_file


def test_is_running_false_when_no_pidfile(isolated_pidfile):
    assert streaming_dictation.is_running() is False


def test_is_running_false_and_cleans_up_when_pid_dead(isolated_pidfile, monkeypatch):
    # PID 1 exists; pick a definitely-dead PID instead. Use a very
    # high PID that the kernel almost certainly doesn't have.
    isolated_pidfile.write_text("999999")
    # os.kill(0) on a missing pid raises ProcessLookupError → False.
    assert streaming_dictation.is_running() is False
    # And the stale file is cleaned up.
    assert not isolated_pidfile.exists()


def test_is_running_true_for_live_process(isolated_pidfile):
    isolated_pidfile.write_text(str(os.getpid()))
    assert streaming_dictation.is_running() is True


def test_is_running_false_for_garbage_in_file(isolated_pidfile):
    isolated_pidfile.write_text("not-a-pid")
    assert streaming_dictation.is_running() is False


def test_spawn_idempotent_when_child_already_running(isolated_pidfile, monkeypatch):
    isolated_pidfile.write_text(str(os.getpid()))
    fake_popen = MagicMock()
    monkeypatch.setattr(streaming_dictation.subprocess, "Popen", fake_popen)
    pid = streaming_dictation.spawn()
    assert pid == os.getpid()
    fake_popen.assert_not_called()


def test_spawn_writes_pid_file(isolated_pidfile, monkeypatch):
    fake_proc = MagicMock(pid=12345)
    monkeypatch.setattr(
        streaming_dictation.subprocess, "Popen",
        MagicMock(return_value=fake_proc),
    )
    pid = streaming_dictation.spawn()
    assert pid == 12345
    assert isolated_pidfile.read_text().strip() == "12345"


def test_spawn_returns_none_on_failure(isolated_pidfile, monkeypatch):
    monkeypatch.setattr(
        streaming_dictation.subprocess, "Popen",
        MagicMock(side_effect=OSError("no python")),
    )
    pid = streaming_dictation.spawn()
    assert pid is None
    assert not isolated_pidfile.exists()


def test_stop_returns_true_when_no_pidfile(isolated_pidfile):
    assert streaming_dictation.stop() is True


def test_stop_sends_sigterm_and_polls(isolated_pidfile, monkeypatch):
    isolated_pidfile.write_text("4242")
    kills = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        # Pretend the process died after the first poll.
        if sig == 0 and len(kills) > 2:
            raise ProcessLookupError()

    monkeypatch.setattr(streaming_dictation.os, "kill", fake_kill)
    monkeypatch.setattr(streaming_dictation.time, "sleep", lambda _: None)
    ok = streaming_dictation.stop(timeout=1.0)
    assert ok is True
    # First call is the SIGTERM, then SIGNAL 0 polling.
    assert kills[0] == (4242, signal.SIGTERM)
    assert all(s == 0 for _pid, s in kills[1:])
    assert not isolated_pidfile.exists()


def test_stop_escalates_to_sigkill_after_timeout(isolated_pidfile, monkeypatch):
    isolated_pidfile.write_text("4242")
    kills = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        # The signal-0 polls never report death → forces SIGKILL path.

    monkeypatch.setattr(streaming_dictation.os, "kill", fake_kill)
    monkeypatch.setattr(streaming_dictation.time, "sleep", lambda _: None)
    # Move the clock past the deadline immediately.
    times = iter([0.0, 999.0, 999.1])
    monkeypatch.setattr(streaming_dictation.time, "monotonic", lambda: next(times))
    ok = streaming_dictation.stop(timeout=0.5)
    assert ok is False
    signals_sent = [s for _pid, s in kills]
    assert signal.SIGTERM in signals_sent
    assert signal.SIGKILL in signals_sent
    assert not isolated_pidfile.exists()


def test_stop_handles_already_dead_pid(isolated_pidfile, monkeypatch):
    isolated_pidfile.write_text("4242")
    monkeypatch.setattr(
        streaming_dictation.os, "kill",
        MagicMock(side_effect=ProcessLookupError()),
    )
    assert streaming_dictation.stop() is True
    assert not isolated_pidfile.exists()


# ---------------------------------------------------------------------------
# VAD loop: utterance flush and tail-drain semantics
# ---------------------------------------------------------------------------
class _FakeNumpy:
    """Minimal numpy stand-in for ``concatenate``.

    The VAD loop only needs ``np.concatenate`` to combine frames.
    Using a fake avoids paying the import cost in tests that don't
    touch real audio.
    """

    @staticmethod
    def concatenate(arrays):
        # Just flatten the list-of-lists.
        out = []
        for a in arrays:
            out.extend(a)
        return out


def test_vad_loop_emits_utterance_on_end_event():
    state = streaming_dictation._StreamState()
    state.running = True

    # Three frames: voice starts, voice continues, voice ends.
    state.audio_q.put([1, 2])
    state.audio_q.put([3, 4])
    state.audio_q.put([5, 6])
    state.audio_q.put(None)  # sentinel

    events = iter([
        {"start": 0},
        None,
        {"end": 100},
    ])
    fake_vad = MagicMock(side_effect=lambda _f, **_kw: next(events))

    streaming_dictation._vad_loop(state, fake_vad, _FakeNumpy)

    utt = state.utterance_q.get_nowait()
    assert utt == [1, 2, 3, 4, 5, 6]


def test_vad_loop_drains_partial_utterance_on_shutdown():
    """If shutdown arrives mid-sentence, the loop must flush the
    accumulated frames so the user doesn't lose their last words."""
    state = streaming_dictation._StreamState()
    state.running = True
    state.audio_q.put([1])
    state.audio_q.put([2])
    state.audio_q.put(None)  # shutdown sentinel before any "end" event

    events = iter([
        {"start": 0},  # voice begins
        None,          # voice continues
        None,          # shutdown sentinel handled before this call
    ])
    fake_vad = MagicMock(side_effect=lambda _f, **_kw: next(events))

    streaming_dictation._vad_loop(state, fake_vad, _FakeNumpy)

    # The partial sentence still made it onto the queue.
    utt = state.utterance_q.get_nowait()
    assert utt == [1, 2]


# ---------------------------------------------------------------------------
# Transcribe loop: prompt accumulation, leading-space rule
# ---------------------------------------------------------------------------
def test_transcribe_loop_types_first_utterance_without_leading_space(monkeypatch):
    state = streaming_dictation._StreamState()
    state.running = False  # so loop exits when queue empties

    seg = SimpleNamespace(text="hola mundo")
    info = SimpleNamespace()
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=(iter([seg]), info))

    typed: list[str] = []
    monkeypatch.setattr(
        streaming_dictation, "_type_text",
        lambda t: typed.append(t),
    )

    state.utterance_q.put([0.0, 0.1])
    state.utterance_q.put(None)  # sentinel
    streaming_dictation._transcribe_loop(state, fake_model, _FakeNumpy)

    assert typed == ["hola mundo"]
    assert state.have_typed is True
    assert "hola mundo" in state.prompt


def test_transcribe_loop_prefixes_space_after_first_utterance(monkeypatch):
    state = streaming_dictation._StreamState()
    state.running = False
    state.have_typed = True
    state.prompt = "anterior"

    seg = SimpleNamespace(text="continuación")
    info = SimpleNamespace()
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=(iter([seg]), info))

    typed: list[str] = []
    monkeypatch.setattr(
        streaming_dictation, "_type_text",
        lambda t: typed.append(t),
    )

    state.utterance_q.put([0.0])
    state.utterance_q.put(None)
    streaming_dictation._transcribe_loop(state, fake_model, _FakeNumpy)

    assert typed == [" continuación"]


def test_transcribe_loop_skips_empty_text(monkeypatch):
    state = streaming_dictation._StreamState()
    state.running = False

    seg = SimpleNamespace(text="   ")  # only whitespace
    info = SimpleNamespace()
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=(iter([seg]), info))

    typed: list[str] = []
    monkeypatch.setattr(
        streaming_dictation, "_type_text",
        lambda t: typed.append(t),
    )

    state.utterance_q.put([0.0])
    state.utterance_q.put(None)
    streaming_dictation._transcribe_loop(state, fake_model, _FakeNumpy)

    assert typed == []
    assert state.have_typed is False


def test_transcribe_loop_continues_after_transcribe_exception(monkeypatch):
    state = streaming_dictation._StreamState()
    state.running = False

    seg = SimpleNamespace(text="después")
    info = SimpleNamespace()
    fake_model = MagicMock()
    # First call raises, second works → loop must not die on the first.
    fake_model.transcribe = MagicMock(
        side_effect=[RuntimeError("CUDA OOM"), (iter([seg]), info)]
    )

    typed: list[str] = []
    monkeypatch.setattr(
        streaming_dictation, "_type_text",
        lambda t: typed.append(t),
    )

    state.utterance_q.put([0.0])
    state.utterance_q.put([0.1])
    state.utterance_q.put(None)
    streaming_dictation._transcribe_loop(state, fake_model, _FakeNumpy)

    assert typed == ["después"]


def test_transcribe_loop_caps_prompt_at_200_chars(monkeypatch):
    state = streaming_dictation._StreamState()
    state.running = False
    # Pre-fill prompt with something long-ish.
    state.prompt = "x" * 300

    seg = SimpleNamespace(text="palabras nuevas")
    info = SimpleNamespace()
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=(iter([seg]), info))

    monkeypatch.setattr(streaming_dictation, "_type_text", lambda _: None)

    state.utterance_q.put([0.0])
    state.utterance_q.put(None)
    streaming_dictation._transcribe_loop(state, fake_model, _FakeNumpy)

    assert len(state.prompt) <= 200
    # The newest text wins the tail.
    assert state.prompt.endswith("palabras nuevas")


# ---------------------------------------------------------------------------
# _type_text uses ydotool
# ---------------------------------------------------------------------------
def test_type_text_invokes_ydotool_with_double_dash(monkeypatch):
    runs = []

    def fake_run(argv, **_kw):
        runs.append(argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(streaming_dictation.subprocess, "run", fake_run)
    streaming_dictation._type_text("hola --raro")
    assert runs == [["ydotool", "type", "--", "hola --raro"]]
