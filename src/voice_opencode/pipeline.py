"""
Pipeline orchestration: record → STT → opencode → TTS, with state events.

The pipeline coordinates the dumb modules (audio, stt, screenshot,
opencode_client, tts) and emits ``state`` updates so the tray icon can
react. Each phase is wrapped in try/except so a failure in one stage
doesn't leave the state machine stuck.
"""

from __future__ import annotations

from . import agent, audio, stt, tts
from .logging import log
from .notify import notify
from .opencode_client import Session
from .screenshot import capture
from .state import set_state


# ---------------------------------------------------------------------------
def start_recording() -> None:
    """Begin recording unless paused or already running."""
    if agent.is_blocking():
        why = "agent activo" if agent.is_active() else "en pausa"
        log(f"Blocked — ignoring start ({why}).")
        notify(f"⏸  {why.capitalize()}", "F9 ignorado", urgency="low")
        return
    audio.start()
    set_state("recording")
    notify("🎙 Grabando…", "Suelta F9 para enviar")


def stop_and_run() -> None:
    """
    Stop recording and run the full pipeline. The pipeline is linear
    (no early return on partial success) but each step has its own
    error path so the state always lands on ``idle`` or ``error``.
    """
    wav = audio.stop()
    if wav is None:
        set_state("idle")
        return

    set_state("thinking")

    # 1. Transcribe
    try:
        text = stt.transcribe(wav)
    except Exception as e:
        log(f"STT error: {e}")
        notify("❌ Error STT", str(e), urgency="critical")
        set_state("error")
        return
    if not text:
        notify("🤷 Nada que transcribir", "")
        set_state("idle")
        return

    notify("🧠 Pensando…", text[:80])

    # 2. Ask opencode (with optional screenshot)
    shot = capture()
    try:
        reply = Session.get_or_create().ask(text, screenshot=shot)
    except Exception as e:
        log(f"opencode error: {e}")
        notify("❌ opencode", str(e), urgency="critical")
        set_state("error")
        return
    if not reply:
        notify("🤐 Sin respuesta", "")
        set_state("idle")
        return

    notify("🔊 Respondiendo", reply[:80])

    # 3. Speak
    set_state("speaking")
    try:
        tts.speak(reply)
    except Exception as e:
        log(f"TTS error: {e}")
        notify("❌ Error TTS", str(e), urgency="critical")
        set_state("error")
        return

    set_state("idle")


def toggle() -> None:
    """Push-to-talk single-binding helper: stop+run if recording, else start."""
    if audio.is_recording():
        stop_and_run()
    else:
        start_recording()
