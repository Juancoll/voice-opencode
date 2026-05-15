"""
System-tray icon (PyQt6 QSystemTrayIcon).

PyQt's QSystemTrayIcon implements StatusNotifierItem natively, so it
integrates with DankMaterialShell, waybar's tray, KDE, etc., without
extra glue.

The tray is a single instance that polls ``voice state`` once per second
and shells out to the ``voice`` wrapper for actions. We use the wrapper
(not direct Python calls) so behaviour stays identical to the keybinds
and so the tray survives if the package is reinstalled mid-session.

Run with::

    python -m voice_opencode.tray

or via the wrapper::

    voice tray
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import paths

# Path to the bash wrapper (so we shell out the same way the user does).
VOICE_BIN = paths.PROJECT_ROOT / "voice"

ICON_FOR_STATE = {
    "idle":      paths.ICONS_DIR / "voice-idle.svg",
    "recording": paths.ICONS_DIR / "voice-recording.svg",
    "thinking":  paths.ICONS_DIR / "voice-thinking.svg",
    "speaking":  paths.ICONS_DIR / "voice-speaking.svg",
    "error":     paths.ICONS_DIR / "voice-error.svg",
    "paused":    paths.ICONS_DIR / "voice-paused.svg",
}

POLL_MS = 1000


# ---------------------------------------------------------------------------
def voice_cmd(*args: str, capture: bool = False) -> str | None:
    """Invoke the ``voice`` wrapper. Returns stdout if ``capture`` else None."""
    cmd = [str(VOICE_BIN), *args]
    try:
        if capture:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return r.stdout.strip()
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    except Exception as e:
        print(f"[tray] voice {' '.join(args)} failed: {e}", file=sys.stderr)
        return None


def fetch_state() -> dict:
    out = voice_cmd("state", capture=True) or "{}"
    try:
        return json.loads(out)
    except Exception:
        return {}


def list_voice_stems() -> list[str]:
    return sorted(p.stem for p in paths.VOICES_DIR.glob("*.onnx"))


# ---------------------------------------------------------------------------
class VoiceTray(QSystemTrayIcon):
    """Tray icon + context menu wired to the ``voice`` CLI."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self._icon_cache: dict[str, QIcon] = {}
        self._last_state: str | None = None

        self.setToolTip("voice-opencode")
        self._build_menu()
        self.activated.connect(self._on_activated)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)

        self.refresh()
        self.show()

    # -- menu construction --------------------------------------------------
    def _build_menu(self) -> None:
        m = QMenu()
        m.setTitle("voice-opencode")

        self.status_action = self._info(m, "…")
        self.session_action = self._info(m, "session: …")
        self.voice_action = self._info(m, "voice: …")
        m.addSeparator()

        self._action(m, "Toggle grabación", lambda: voice_cmd("rec", "toggle"))
        self._action(m, "Reset sesión",     lambda: voice_cmd("session", "reset"))

        self.pause_action = QAction("Pausar (ignora F9)", m, checkable=True)
        self.pause_action.toggled.connect(self._on_pause_toggled)
        m.addAction(self.pause_action)

        m.addSeparator()
        self.agent_status = self._info(m, "agente: —")
        self._action(m, "Detener agente (MCP)", lambda: voice_cmd("mcp", "stop"))

        m.addSeparator()

        self.voice_menu = QMenu("Cambiar voz", m)
        self.voice_group = QActionGroup(self.voice_menu)
        self.voice_group.setExclusive(True)
        m.addMenu(self.voice_menu)

        self.shot_action = QAction("Adjuntar captura", m, checkable=True)
        self.shot_action.toggled.connect(
            lambda v: voice_cmd("config", "set", "screenshot", "true" if v else "false")
        )
        m.addAction(self.shot_action)

        self.ctx_action = QAction("Mantener contexto", m, checkable=True)
        self.ctx_action.toggled.connect(
            lambda v: voice_cmd("config", "set", "keep_context", "true" if v else "false")
        )
        m.addAction(self.ctx_action)

        m.addSeparator()
        self._action(m, "Ver logs", self._open_logs)
        m.addSeparator()
        self._action(m, "Salir", self._quit)

        self.setContextMenu(m)

    @staticmethod
    def _info(menu: QMenu, text: str) -> QAction:
        a = QAction(text, menu)
        a.setEnabled(False)
        menu.addAction(a)
        return a

    @staticmethod
    def _action(menu: QMenu, text: str, slot) -> QAction:
        a = QAction(text, menu)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    # -- voice submenu (lazy populated, voices don't change at runtime) -----
    def _populate_voices(self, current: str) -> None:
        if self.voice_menu.actions():
            for act in self.voice_group.actions():
                act.setChecked(act.data() == current)
            return
        for v in list_voice_stems():
            act = QAction(v, self.voice_menu, checkable=True)
            act.setData(v)
            act.setChecked(v == current)
            act.triggered.connect(
                lambda _checked, name=v: voice_cmd("config", "set", "voice", name)
            )
            self.voice_group.addAction(act)
            self.voice_menu.addAction(act)

    # -- slots --------------------------------------------------------------
    def _on_pause_toggled(self, checked: bool) -> None:
        voice_cmd("pause" if checked else "resume")

    def _open_logs(self) -> None:
        log_file = paths.LOGS_DIR / "voice.log"
        for term in ("foot", "kitty", "alacritty", "xterm"):
            if subprocess.run(["which", term], capture_output=True).returncode == 0:
                subprocess.Popen([term, "-e", "tail", "-f", str(log_file)])
                return
        subprocess.Popen(["xdg-open", str(log_file)])

    def _quit(self) -> None:
        self.hide()
        self.app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left/middle click pops the menu (DMS already handles right-click).
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            menu = self.contextMenu()
            if menu is not None:
                menu.popup(self.geometry().center())

    # -- icon + state -------------------------------------------------------
    def _icon(self, name: str) -> QIcon:
        if name not in self._icon_cache:
            path: Path = ICON_FOR_STATE.get(name, ICON_FOR_STATE["idle"])
            self._icon_cache[name] = QIcon(str(path))
        return self._icon_cache[name]

    def _set_icon_state(self, name: str) -> None:
        if name == self._last_state:
            return
        self._last_state = name
        self.setIcon(self._icon(name))

    def refresh(self) -> None:
        st = fetch_state()
        if not st:
            self._set_icon_state("error")
            self.status_action.setText("estado: ❌ no disponible")
            return

        paused  = bool(st.get("paused"))
        agent_on = bool(st.get("agent"))
        phase   = st.get("state", "idle")
        server  = bool(st.get("server"))
        session = st.get("session")
        voice   = st.get("voice", "?")
        shot_on = bool(st.get("screenshot"))
        ctx_on  = bool(st.get("context"))

        # Effective icon: error > thinking (agent acts) > paused > phase.
        if not server:
            effective = "error"
        elif agent_on:
            # Agent in control: surface as 'thinking' so the user sees activity.
            effective = "thinking"
        elif paused and phase == "idle":
            effective = "paused"
        else:
            effective = phase
        self._set_icon_state(effective)

        labels = {
            "idle":      "🟢 inactivo",
            "recording": "🎙 grabando",
            "thinking":  "🧠 pensando",
            "speaking":  "🔊 hablando",
            "error":     "❌ error",
            "paused":    "⏸  en pausa",
        }
        suffix = "" if server else "  (servidor caído)"
        self.status_action.setText(f"estado: {labels.get(effective, effective)}{suffix}")
        self.session_action.setText(
            f"session: {session[:20]+'…' if session else '—'}"
        )
        self.voice_action.setText(f"voice: {voice}")
        self.agent_status.setText("agente: 🤖 activo" if agent_on else "agente: —")

        for action, value in (
            (self.pause_action, paused),
            (self.shot_action,  shot_on),
            (self.ctx_action,   ctx_on),
        ):
            if action.isChecked() != value:
                action.blockSignals(True)
                action.setChecked(value)
                action.blockSignals(False)

        self._populate_voices(voice)

        tooltips = {
            "recording": "voice-opencode — grabando…",
            "thinking":  "voice-opencode — pensando…",
            "speaking":  "voice-opencode — hablando…",
            "paused":    "voice-opencode — en pausa",
        }
        self.setToolTip(tooltips.get(effective, f"voice-opencode — {voice}"))


# ---------------------------------------------------------------------------
def main() -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("voice-opencode")
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[tray] No system tray available on this session.", file=sys.stderr)
        return 1

    _ = VoiceTray(app)  # keep ref alive
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
