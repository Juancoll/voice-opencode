"""Streaming dictation backend (faster-whisper + silero-vad).

This module replaces the legacy "record full WAV → transcribe → inject"
flow with an online pipeline that emits text as soon as each sentence
closes by silence:

    mic ─► capture_loop ─► audio_q ─► vad_loop ─► utterance_q ─► transcribe_loop ─► type

It is designed to run as a **detached child process** spawned by
``voice dictate stream-start`` and terminated by ``voice dictate
stream-stop`` (which sends SIGTERM). The signal handler flushes any
audio still in flight and waits for the transcription thread to drain
before exiting — so the final words of the last sentence aren't lost
when the user releases Ctrl+F9.

Why a child process and not a thread inside the tray:

- ``faster_whisper.WhisperModel`` allocates ~500 MB and never frees it.
  Keeping it inside the tray would balloon RAM permanently; using a
  child means the cost is only paid while dictating.
- The tray is PyQt single-threaded; running blocking inference inside
  it would freeze the HUD.
- Each Hyprland ``bind = CTRL, F9, exec, voice dictate …`` runs in its
  own short-lived process, so the dictation lifetime is naturally a
  separate process anyway.

Append-only by design: this writes forward only. No backspaces, no
diff'ing, no in-place revisions. That matches the constraint of
``ydotool type`` (the only universal Wayland injection method) and
also matches what users expect from a dictation tool — once a word
shows up, it stays.

Fallback policy: if any of ``faster_whisper``, ``silero_vad``,
``sounddevice`` is unavailable, or the model can't load, the caller
must fall back to the batch flow. ``is_available()`` answers that
question without importing the heavyweights eagerly.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings
from .logging import log
from .paths import (
    DICTATION_FOCUS_FILE,
    FASTER_WHISPER_DIR,
    STREAMING_DICTATION_PID_FILE,
)

__all__ = [
    "is_available",
    "missing_dependencies",
    "spawn",
    "stop",
    "is_running",
    "run_streaming_loop",
]

SR = 16000              # silero-vad and whisper both expect 16 kHz mono
VAD_FRAME_SAMPLES = 512  # silero-vad requires exactly this @ 16 kHz
SHUTDOWN_TIMEOUT_S = 8.0  # max wait for graceful drain after SIGTERM

# Snapshot the in-progress utterance every N seconds while voice is
# active, transcribe it cheaply (beam=1) and push it to the HUD as a
# "transcribiendo: ..." subtitle. Lower = more responsive but more
# CPU; 1.0 s is the same cadence whisper_streaming/Macháček uses for
# its default re-transcribe step.
_PARTIAL_INTERVAL_S = 1.0
# Cap partial-snapshot length so very long monologues don't blow up
# the partial transcribe time (which would lag the HUD updates).
# After this many seconds of speech the partial is computed on the
# tail only; the next VAD-confirmed boundary still emits the full
# utterance to ydotool, so no text is lost.
_PARTIAL_MAX_S = 12.0


# ---------------------------------------------------------------------------
# Capability probes (cheap — never load the model)
# ---------------------------------------------------------------------------
def missing_dependencies() -> list[str]:
    """Return the list of missing Python packages, empty if all present.

    Cheap: only does ``importlib.util.find_spec``. The actual modules
    are imported lazily inside ``run_streaming_loop`` so a tray that
    never dictates pays nothing.
    """
    import importlib.util

    missing = []
    for mod in ("faster_whisper", "silero_vad", "sounddevice", "numpy"):
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
    return missing


def is_available() -> bool:
    """True iff streaming mode can plausibly run on this host.

    Checks: required python deps installed, and the config flag is set.
    Does NOT check microphone availability (sounddevice will fail at
    capture time if there's none — we let it, so the user gets a real
    error message instead of a silent fallback).
    """
    if not getattr(settings, "streaming_dictation_enabled", False):
        return False
    return not missing_dependencies()


# ---------------------------------------------------------------------------
# Child-process lifecycle (spawn / stop / is_running)
# ---------------------------------------------------------------------------
def is_running() -> bool:
    """True iff a streaming-dictation child is alive per the PID file."""
    if not STREAMING_DICTATION_PID_FILE.exists():
        return False
    try:
        pid = int(STREAMING_DICTATION_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False
    if not _process_alive(pid):
        # Stale (or zombie) PID file — clean it up so callers don't
        # keep tripping.
        STREAMING_DICTATION_PID_FILE.unlink(missing_ok=True)
        return False
    return True


def spawn() -> int | None:
    """Fork a detached child that runs the streaming loop.

    Returns the child PID, or None if spawn failed. Idempotent: if a
    child is already alive, returns its PID without spawning a second.

    The child re-execs ``python -m voice_opencode dictate _stream_loop``
    so it gets a clean import context (no inherited PyQt event loop,
    no audio device handles from the parent).
    """
    if is_running():
        try:
            return int(STREAMING_DICTATION_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    # Use the CLI re-exec pattern so the child looks exactly like a
    # normal ``voice`` invocation in logs / ps output.
    cmd = [sys.executable, "-m", "voice_opencode", "dictate", "_stream_loop"]
    try:
        # detach: new session, new pgid, redirect stdio to /dev/null so
        # the parent can exit immediately without holding pipes open.
        devnull = open(os.devnull, "rb+")  # noqa: SIM115 — child owns it
        proc = subprocess.Popen(
            cmd,
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:
        log(f"streaming-dictation: spawn failed ({e}).")
        return None
    STREAMING_DICTATION_PID_FILE.write_text(str(proc.pid))
    log(f"streaming-dictation: child spawned (pid={proc.pid}).")
    return proc.pid


def _process_alive(pid: int) -> bool:
    """True iff ``pid`` is a live, non-zombie process.

    ``os.kill(pid, 0)`` returns True even for zombies (state Z) because
    they still occupy a PID until their parent reaps them. When our
    child is a session leader detached from us (start_new_session=True
    in spawn) and our parent script exits without ``wait()``ing, it
    becomes a zombie and confuses the polling loop into waiting the
    full timeout. ``/proc/{pid}/status`` ``State:`` lets us tell.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else — treat as alive.
        return True
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    # "State:\tR (running)" / "Z (zombie)" / etc.
                    return "Z" not in line
    except OSError:
        # /proc not available (non-Linux) or proc disappeared between
        # the kill check and the read — assume gone.
        return False
    return True


def stop(timeout: float = SHUTDOWN_TIMEOUT_S) -> bool:
    """Ask the child to flush and exit. Returns True if it terminated.

    Sends SIGTERM (the loop's signal handler triggers the drain),
    then polls for up to ``timeout`` seconds. If it doesn't exit in
    time we escalate to SIGKILL so a wedged transcription can't hold
    the next dictation hostage.
    """
    if not STREAMING_DICTATION_PID_FILE.exists():
        return True
    try:
        pid = int(STREAMING_DICTATION_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        STREAMING_DICTATION_PID_FILE.unlink(missing_ok=True)
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        STREAMING_DICTATION_PID_FILE.unlink(missing_ok=True)
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            STREAMING_DICTATION_PID_FILE.unlink(missing_ok=True)
            return True
        time.sleep(0.05)
    # Escalate.
    log(f"streaming-dictation: pid {pid} did not exit in {timeout}s; SIGKILL.")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    STREAMING_DICTATION_PID_FILE.unlink(missing_ok=True)
    return False


# ---------------------------------------------------------------------------
# Child entry point: the actual streaming loop
# ---------------------------------------------------------------------------
class _StreamState:
    """Mutable shared state across the loop threads.

    Plain object (no dataclass) so the signal handler can flip flags
    without needing a lock — every consumer reads through the queues
    which already provide their own synchronisation.
    """

    def __init__(self) -> None:
        self.running = True              # cleared by SIGTERM
        self.audio_q: queue.Queue[Any] = queue.Queue(maxsize=200)
        self.utterance_q: queue.Queue[Any] = queue.Queue(maxsize=20)
        # Partials queue: snapshots of the in-progress utterance sent
        # to the partial transcribe thread every ~1s while the user
        # is still speaking, so the HUD can show live feedback
        # ("transcribiendo: ..."). Bounded small because we only care
        # about the latest snapshot — drops are fine.
        self.partial_q: queue.Queue[Any] = queue.Queue(maxsize=2)
        # tail of the current utterance being accumulated
        self.current_utt: list[Any] = []
        # whitespace-joined history for the initial_prompt window
        self.prompt: str = ""
        # whether ANYTHING has been typed yet, used to decide if the
        # next sentence should be prefixed with a space
        self.have_typed: bool = False
        self.shutdown_event = threading.Event()


def run_streaming_loop() -> int:
    """Child-side entry point. Returns the exit code.

    Layout:

        T1 ─ capture_loop  : pulls frames from sounddevice → audio_q
        T2 ─ vad_loop      : silero-vad on each frame → utterance_q
                             (on sentence-close)
        T3 ─ transcribe_loop : faster-whisper on each utterance → type
        main thread        : wait for shutdown_event, then drain.

    SIGTERM handler clears ``state.running`` and sets the shutdown
    event. The capture loop notices, stops the input stream, the VAD
    loop flushes any in-flight utterance, and the transcribe loop
    drains the queue before main returns.
    """
    # Lazy imports — the module-level capability probe doesn't want
    # to pay the ~300 ms cost of these on every CLI invocation.
    try:
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel
        from silero_vad import VADIterator, load_silero_vad
    except Exception as e:
        log(f"streaming-dictation: dep import failed ({e}); exiting.")
        return 2

    log(
        "streaming-dictation: loop starting "
        f"(model={settings.streaming_dictation_model} "
        f"device={settings.streaming_dictation_device} "
        f"compute={settings.streaming_dictation_compute_type})"
    )

    # Resolve the model directory. faster_whisper accepts either an HF
    # ID ("small") or a local path. We point at our own cache so the
    # download is visible in the install footprint.
    model_dir_setting = (
        settings.streaming_dictation_model_dir
        or str(FASTER_WHISPER_DIR / settings.streaming_dictation_model)
    )
    model_path = Path(model_dir_setting)
    model_arg: str = (
        str(model_path) if model_path.exists()
        else settings.streaming_dictation_model
    )

    try:
        model = WhisperModel(
            model_arg,
            device=settings.streaming_dictation_device,
            compute_type=settings.streaming_dictation_compute_type,
            download_root=str(FASTER_WHISPER_DIR),
        )
    except Exception as e:
        log(f"streaming-dictation: model load failed ({e}); exiting.")
        return 3

    vad_model = load_silero_vad()
    vad = VADIterator(
        vad_model,
        sampling_rate=SR,
        threshold=settings.streaming_dictation_vad_threshold,
        min_silence_duration_ms=settings.streaming_dictation_vad_silence_ms,
        speech_pad_ms=settings.streaming_dictation_vad_pad_ms,
    )

    state = _StreamState()

    def _on_sig(_signum: int, _frame: Any) -> None:
        try:
            active = _hypr_activewindow()
            try:
                wid = DICTATION_FOCUS_FILE.read_text().strip()
            except OSError:
                wid = "<missing>"
            log(
                f"streaming-dictation: SIGTERM received; draining. "
                f"active={active} focus_file_wid={wid}"
            )
        except Exception:
            log("streaming-dictation: SIGTERM received; draining.")
        state.running = False
        state.shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_sig)
    signal.signal(signal.SIGINT, _on_sig)

    # Warmup: first invocation is 3-5× slower because models JIT-compile
    # their kernels. Pay it now so the user's first sentence isn't laggy.
    try:
        _warm = model.transcribe(
            np.zeros(SR, dtype=np.float32),
            language=settings.streaming_dictation_language,
        )
        for _ in _warm[0]:
            pass
        log("streaming-dictation: warmup ok.")
    except Exception as e:
        log(f"streaming-dictation: warmup failed ({e}); continuing.")

    threads = [
        threading.Thread(
            target=_capture_loop,
            args=(state, sd, np),
            daemon=True,
            name="dictation-capture",
        ),
        threading.Thread(
            target=_vad_loop,
            args=(state, vad, np),
            daemon=True,
            name="dictation-vad",
        ),
        threading.Thread(
            target=_transcribe_loop,
            args=(state, model, np),
            daemon=True,
            name="dictation-stt",
        ),
        # Partial transcribe thread: shares the model with the final
        # transcribe loop. faster-whisper's WhisperModel.transcribe()
        # serialises internally on its own state, so concurrent calls
        # from this thread and the final thread are safe (one will
        # block briefly while the other runs).
        threading.Thread(
            target=_partial_loop,
            args=(state, model, np),
            daemon=True,
            name="dictation-partial",
        ),
    ]
    for t in threads:
        t.start()

    # Wait for SIGTERM. The shutdown handler clears state.running which
    # cascades through the queues (audio sends a None sentinel, the VAD
    # loop flushes any partial utterance, the transcribe loop drains).
    state.shutdown_event.wait()
    log("streaming-dictation: drain start.")

    # Send sentinels and join the threads in order.
    try:
        state.audio_q.put_nowait(None)
    except queue.Full:
        pass
    threads[0].join(timeout=2.0)
    threads[1].join(timeout=2.0)
    # Stop the partial-transcribe thread — it polls state.running so
    # the sentinel is just a wakeup to skip the get() timeout.
    try:
        state.partial_q.put_nowait(None)
    except queue.Full:
        pass
    threads[3].join(timeout=2.0)
    # Flush any utterance the VAD loop left mid-air.
    try:
        state.utterance_q.put_nowait(None)
    except queue.Full:
        pass
    threads[2].join(timeout=SHUTDOWN_TIMEOUT_S)
    # Clear the HUD partial subtitle on exit (the pipeline's own
    # notify code will paint the final 'idle' state right after).
    try:
        from . import hud as _hud
        _hud.send("update", icon="🎙", title="Dictando", subtitle="")
    except Exception:
        pass
    log("streaming-dictation: loop exit (calling os._exit).")
    # Force-exit: faster-whisper / sounddevice / torch leave non-daemon
    # worker threads alive that otherwise block the interpreter from
    # shutting down for many seconds. Since we're a short-lived child
    # process with nothing to clean up, ``os._exit`` is correct here.
    os._exit(0)


# ---------------------------------------------------------------------------
# Loop bodies (separate functions so they're individually testable)
# ---------------------------------------------------------------------------
def _capture_loop(state: _StreamState, sd: Any, np: Any) -> None:
    """Pull 512-sample frames from the mic into ``state.audio_q``.

    Uses a blocking ``InputStream`` rather than a callback because
    callbacks run in a sounddevice-managed thread that doesn't play
    well with our shutdown_event — easier to reason about pulls.
    """
    try:
        with sd.InputStream(
            samplerate=SR,
            channels=1,
            dtype="float32",
            blocksize=VAD_FRAME_SAMPLES,
        ) as stream:
            try:
                wid0 = DICTATION_FOCUS_FILE.read_text().strip()
            except OSError:
                wid0 = "<missing>"
            log(
                f"streaming-dictation: mic open. "
                f"focus_file_wid={wid0} active={_hypr_activewindow()}"
            )
            while state.running:
                data, overflowed = stream.read(VAD_FRAME_SAMPLES)
                if overflowed:
                    log("streaming-dictation: mic overflow.")
                # Flatten mono channel and copy (the buffer rotates).
                frame = data[:, 0].copy()
                try:
                    state.audio_q.put(frame, timeout=0.1)
                except queue.Full:
                    log("streaming-dictation: audio_q full; dropping frame.")
    except Exception as e:
        log(f"streaming-dictation: capture loop crashed ({e}).")
        state.running = False
        state.shutdown_event.set()


def _vad_loop(state: _StreamState, vad: Any, np: Any) -> None:
    """Run silero-vad on each 512-sample frame; emit utterances.

    Buffers frames into ``state.current_utt`` once VAD says voice
    started. Closes the utterance and pushes it onto ``utterance_q``
    when silence is detected. On shutdown, flushes whatever is in
    ``current_utt`` as one final utterance so the last sentence isn't
    lost mid-air.

    Also emits a snapshot of the in-progress utterance to
    ``partial_q`` roughly every ``_PARTIAL_INTERVAL_S`` while voice
    is still active, so the partial-transcribe thread can update
    the HUD with live feedback (Google/VSCode style).
    """
    speaking = False
    last_partial_t = 0.0
    try:
        while True:
            try:
                frame = state.audio_q.get(timeout=0.1)
            except queue.Empty:
                if not state.running:
                    break
                continue
            if frame is None:  # sentinel from shutdown
                break
            event = vad(frame, return_seconds=False)
            if event is not None:
                if "start" in event:
                    speaking = True
                    state.current_utt = [frame]
                    last_partial_t = time.monotonic()
                    continue
                if "end" in event:
                    state.current_utt.append(frame)
                    if state.current_utt:
                        utt = np.concatenate(state.current_utt)
                        state.current_utt = []
                        try:
                            state.utterance_q.put(utt, timeout=0.5)
                        except queue.Full:
                            log("streaming-dictation: utterance_q full; dropping.")
                    speaking = False
                    continue
            if speaking:
                state.current_utt.append(frame)
                # Snapshot every PARTIAL_INTERVAL_S so the HUD shows
                # live transcription. Drain the queue first so we
                # never queue more than one stale snapshot — the
                # partial thread always works on the freshest audio.
                now = time.monotonic()
                if now - last_partial_t >= _PARTIAL_INTERVAL_S and state.current_utt:
                    snapshot = np.concatenate(state.current_utt)
                    last_partial_t = now
                    # Drop stale snapshot, push fresh one.
                    while True:
                        try:
                            state.partial_q.get_nowait()
                        except queue.Empty:
                            break
                    try:
                        state.partial_q.put_nowait(snapshot)
                    except queue.Full:
                        pass
        # Drain: flush a half-spoken sentence so the tail isn't lost.
        if state.current_utt:
            utt = np.concatenate(state.current_utt)
            state.current_utt = []
            try:
                state.utterance_q.put(utt, timeout=1.0)
                log("streaming-dictation: flushed tail utterance.")
            except queue.Full:
                log("streaming-dictation: drop tail (q full).")
    except Exception as e:
        log(f"streaming-dictation: vad loop crashed ({e}).")
        state.running = False
        state.shutdown_event.set()


def _transcribe_loop(state: _StreamState, model: Any, np: Any) -> None:
    """Pull utterances, transcribe, type the result via ydotool."""
    while True:
        try:
            utt = state.utterance_q.get(timeout=0.2)
        except queue.Empty:
            if not state.running and state.utterance_q.empty():
                return
            continue
        if utt is None:  # sentinel
            return
        try:
            t0 = time.monotonic()
            segments, _info = model.transcribe(
                utt,
                language=settings.streaming_dictation_language,
                beam_size=settings.streaming_dictation_beam_size,
                initial_prompt=state.prompt or None,
                condition_on_previous_text=False,
                vad_filter=False,
                # Anti-hallucination: on near-silent utterances
                # (low log-prob / high no_speech_prob) faster-whisper
                # defaults emit canned phrases like "Subtítulos
                # realizados por la comunidad de Amara.org". Tightening
                # these thresholds drops those segments instead.
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
            )
            text = "".join(s.text for s in segments).strip()
            dt = time.monotonic() - t0
            n_samples = utt.shape[0] if hasattr(utt, "shape") else len(utt)
            log(
                f"streaming-dictation: utt {n_samples / SR:.2f}s → "
                f"{text!r} in {dt:.2f}s"
            )
        except Exception as e:
            log(f"streaming-dictation: transcribe error ({e}).")
            continue
        if not text:
            continue
        out = (" " + text) if state.have_typed else text
        try:
            _refocus_target()
            _type_text(out)
            state.have_typed = True
            log(f"streaming-dictation: typed {out!r}.")
            # Keep a short rolling prompt so vocabulary stays consistent
            # across sentences without dragging full history into every
            # decode (which would slow it down and risk drift).
            state.prompt = (state.prompt + " " + text).strip()[-200:]
        except Exception as e:
            log(f"streaming-dictation: type error ({e}); text was {out!r}.")


def _partial_loop(state: _StreamState, model: Any, np: Any) -> None:
    """Transcribe in-progress utterance snapshots and push to HUD.

    Runs in parallel with the final ``_transcribe_loop``. Cheap config:
    ``beam_size=1`` (greedy) and a tight ``no_speech_threshold`` so
    half-words at the leading edge don't get printed as hallucinations.
    Never types anything — only updates the HUD subtitle so the user
    sees live feedback while speaking. The VAD-confirmed final pass
    is what actually emits text to ydotool.

    Drops snapshots silently if it can't keep up; we always read the
    freshest one from the queue.
    """
    from . import hud as _hud  # late import: avoid Qt import at module load
    last_text = ""
    while True:
        try:
            snapshot = state.partial_q.get(timeout=0.2)
        except queue.Empty:
            if not state.running:
                return
            continue
        if snapshot is None:  # shutdown sentinel
            return
        # If a fresher snapshot is already queued, skip this one.
        try:
            while True:
                snapshot = state.partial_q.get_nowait()
                if snapshot is None:
                    return
        except queue.Empty:
            pass
        # Trim to the last _PARTIAL_MAX_S to bound transcribe cost.
        n_samples = snapshot.shape[0] if hasattr(snapshot, "shape") else len(snapshot)
        max_samples = int(_PARTIAL_MAX_S * SR)
        if n_samples > max_samples:
            snapshot = snapshot[-max_samples:]
        try:
            t0 = time.monotonic()
            segments, _info = model.transcribe(
                snapshot,
                language=settings.streaming_dictation_language,
                beam_size=1,  # fast/greedy — quality not critical for partials
                initial_prompt=state.prompt or None,
                condition_on_previous_text=False,
                vad_filter=False,
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
            )
            text = "".join(s.text for s in segments).strip()
            dt = time.monotonic() - t0
        except Exception as e:
            log(f"streaming-dictation: partial transcribe error ({e}).")
            continue
        if not text or text == last_text:
            continue
        last_text = text
        log(f"streaming-dictation: partial {dt:.2f}s → {text!r}")
        # Elide for HUD: subtitle area is narrow, show the tail.
        display = text if len(text) <= 80 else "…" + text[-79:]
        try:
            _hud.send(
                "update",
                icon="🎙",
                title="Dictando",
                subtitle=display,
                timeout=0.05,
            )
        except Exception as e:
            log(f"streaming-dictation: hud send failed ({e}).")


def _hypr_activewindow() -> str:
    """Return a short ``addr|class|title`` describing the focused window.

    Used purely for logging — gives us a snapshot of who hyprland thinks
    has focus at a given point. Never raises; on any failure returns a
    descriptive marker so log lines still align.
    """
    try:
        r = subprocess.run(
            ["hyprctl", "-j", "activewindow"],
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"<hyprctl-error:{e}>"
    if r.returncode != 0:
        return f"<hyprctl-rc={r.returncode}>"
    try:
        import json
        d = json.loads(r.stdout or "{}")
    except Exception as e:
        return f"<json-error:{e}>"
    if not d:
        return "<no-active-window>"
    return f"{d.get('address', '?')}|{d.get('class', '?')}|{d.get('title', '?')[:40]}"


def _refocus_target() -> None:
    """Re-focus the dictation target window before each keystroke burst.

    The HUD steals focus when it appears, and every Ctrl+F9 release
    can also bounce focus around. In the batch flow we only injected
    once, so a single focus restore at start was enough. In streaming
    we inject per utterance — we must refocus before each one or the
    second sentence onwards lands in the HUD / tray / nowhere.

    Reads ``DICTATION_FOCUS_FILE`` non-destructively (the pipeline's
    own cleanup will unlink it at session end). Best-effort: any
    failure just logs and falls through, matching the batch flow's
    'inject on whatever has focus' behaviour as the worst case.

    Logs the full picture so we can actually diagnose focus problems
    instead of guessing: file contents, active window before and after,
    hyprctl rc/stderr, total time spent.
    """
    t0 = time.monotonic()
    try:
        wid = DICTATION_FOCUS_FILE.read_text().strip()
    except (FileNotFoundError, OSError) as e:
        log(f"streaming-dictation: refocus skipped, focus file unreadable ({e}).")
        return
    if not wid:
        log("streaming-dictation: refocus skipped, focus file empty.")
        return
    before = _hypr_activewindow()
    try:
        r = subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{wid}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"streaming-dictation: refocus hyprctl failed ({e}).")
        return
    # 80ms — enough on Hyprland for the focus change to land
    # before ydotool fires. Lower than the 150ms used by the batch
    # flow because we pay this cost per-utterance.
    time.sleep(0.08)
    after = _hypr_activewindow()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log(
        f"streaming-dictation: refocus wid={wid} rc={r.returncode} "
        f"stdout={r.stdout.strip()!r} stderr={r.stderr.strip()!r} "
        f"before={before} after={after} t={elapsed_ms}ms"
    )


def _type_text(text: str) -> None:
    """Synthesise keystrokes via ydotool. Blocking.

    We deliberately go through ``ydotool type`` (not the Python
    ``desktop.type_text`` wrapper) so this module has zero PyQt /
    Qt dependency and the child stays small. Caller-side fallback
    chooses the wrapper if ydotool isn't on PATH.

    Times out after 5 s — a stuck ydotool call must not pin the
    transcribe loop and block the SIGTERM drain.

    Releases Ctrl + Shift + Alt + Super FIRST. While the user is
    holding Ctrl+F9 (the streaming dictation key combo), any letter
    we type becomes a Ctrl+letter shortcut in the target app — the
    text never appears in the editor and we may even trigger
    destructive actions (Ctrl+A select-all, Ctrl+S save, ...).
    Faking key-up events for the modifiers is the only reliable fix
    that works across X11 and Wayland and any compositor.

    Linux input event codes (see /usr/include/linux/input-event-codes.h):
      KEY_LEFTCTRL=29, KEY_RIGHTCTRL=97,
      KEY_LEFTSHIFT=42, KEY_RIGHTSHIFT=54,
      KEY_LEFTALT=56, KEY_RIGHTALT=100,
      KEY_LEFTMETA=125, KEY_RIGHTMETA=126.
    """
    t0 = time.monotonic()
    try:
        rmod = subprocess.run(
            [
                "ydotool", "key",
                "29:0", "97:0", "42:0", "54:0",
                "56:0", "100:0", "125:0", "126:0",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if rmod.returncode != 0:
            log(
                f"streaming-dictation: modifier release rc={rmod.returncode} "
                f"stderr={rmod.stderr.strip()!r}"
            )
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"streaming-dictation: modifier release failed ({e}).")
    active_before_type = _hypr_activewindow()
    try:
        r = subprocess.run(
            ["ydotool", "type", "--", text],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log(
            f"streaming-dictation: ydotool type len={len(text)} "
            f"rc={r.returncode} stderr={r.stderr.strip()!r} "
            f"active={active_before_type} t={elapsed_ms}ms"
        )
    except subprocess.TimeoutExpired:
        log("streaming-dictation: ydotool timeout (5s).")
