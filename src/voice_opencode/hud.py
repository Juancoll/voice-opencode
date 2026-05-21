"""
Turn HUD — frameless PyQt6 overlay that shows live agent progress.

Replaces the libnotify-based persistent notification (ADR-0025) with
a widget we own end-to-end, fixing the "burbuja se queda enganchada"
bug from KDE/Plasma notification daemons that ignore ``-r <id>`` once
the bubble has auto-expired.

Architecture
------------

* ``TurnHUD(QWidget)``: frameless, always-on-top, no focus, no
  taskbar entry. Bottom-right of the screen under the cursor. Icon
  + title + subtitle, fade in/out 180ms.

* ``HudServer``: Unix-socket listener bound to
  ``$XDG_RUNTIME_DIR/voice-opencode/hud.sock``. Reads one JSON object
  per line. Protocol::

      {"op":"show",   "icon":"🎙", "title":"Grabando…", "subtitle":"Suelta F9"}
      {"op":"update", "icon":"⚙️", "title":"Agente actuando", "subtitle":"click_mouse(...)"}
      {"op":"hide"}

  Driven by ``QSocketNotifier`` so it shares the tray's Qt event loop
  (no thread, no second QApplication).

* Mounted from ``tray.main()`` so the tray process owns it. Pipeline
  and MCP server are *clients* of the socket via ``notify.turn_*``.

The HUD is best-effort: if the socket isn't there (tray off, fresh
boot before tray started), the client silently no-ops. Pipeline never
blocks on it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import (
    QPoint,
    QPropertyAnimation,
    QSocketNotifier,
    Qt,
    QTimer,
    pyqtSlot,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import paths
from .config import settings
from .logging import log

HUD_SOCKET: Path = paths.STATE_DIR / "hud.sock"

# Widget geometry / look.
_HUD_W = 520
_HUD_H = 96
_BG_COLOR = QColor(28, 28, 32, 235)
_BORDER_RADIUS = 14


def _corner_xy(geo, corner: str, margin: int) -> tuple[int, int]:
    """Map a corner name + margin to absolute (x, y) for a HUD-sized
    widget inside the rectangle ``geo``. Unknown corners fall back to
    bottom-left (the user's default).

    ``geo`` is a Qt ``QRect``-like with ``left()``, ``top()``,
    ``right()``, ``bottom()`` returning inclusive pixel coordinates.
    """
    if corner == "top-left":
        return geo.left() + margin, geo.top() + margin
    if corner == "top-right":
        return geo.right() - _HUD_W - margin + 1, geo.top() + margin
    if corner == "bottom-right":
        return (
            geo.right() - _HUD_W - margin + 1,
            geo.bottom() - _HUD_H - margin + 1,
        )
    # bottom-left and anything unknown
    return geo.left() + margin, geo.bottom() - _HUD_H - margin + 1


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------
class TurnHUD(QWidget):
    """Floating overlay used to narrate the current turn.

    Position is configurable via ``settings.hud_corner`` and
    ``settings.hud_margin`` (see config.py).
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        # Identify the window so Hyprland window rules can match it:
        # see install.sh and ~/.config/hypr/conf.d/voice.conf — we
        # rely on ``class:^(voice-opencode-hud)$`` to force float +
        # pin + exact size/position. setObjectName drives the
        # Wayland app_id and X11 WM_CLASS via Qt.
        self.setObjectName("voice-opencode-hud")
        self.setWindowTitle("voice-opencode-hud")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # NOTE: BypassWindowManagerHint was tempting (skips the WM
        # entirely) but on Hyprland it makes the window unmanaged —
        # we lose ``windowrulev2`` and end up at (0,0) with the
        # default layout. Better to be a managed window with strict
        # rules.
        # setFixedSize (not just resize) so the widget can't grow as
        # long subtitles arrive — otherwise Qt expands the QLabel and
        # the window with it, which on a bottom-left anchor visually
        # grows leftward off-screen.
        self.setFixedSize(_HUD_W, _HUD_H)

        # --- layout ---
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(14)

        self.icon_label = QLabel("•")
        icon_font = QFont()
        icon_font.setPointSize(26)
        self.icon_label.setFont(icon_font)
        self.icon_label.setFixedWidth(48)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("color: #e0e0e0;")
        root.addWidget(self.icon_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("")
        t_font = QFont()
        t_font.setPointSize(13)
        t_font.setBold(True)
        self.title_label.setFont(t_font)
        self.title_label.setStyleSheet("color: #ffffff;")
        text_box.addWidget(self.title_label)

        self.subtitle_label = QLabel("")
        s_font = QFont()
        s_font.setPointSize(10)
        self.subtitle_label.setFont(s_font)
        self.subtitle_label.setStyleSheet("color: #b8b8c0;")
        self.subtitle_label.setWordWrap(False)
        text_box.addWidget(self.subtitle_label)
        text_box.addStretch(1)

        root.addLayout(text_box, 1)

        # Soft drop shadow.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # Fade animation.
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(180)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _ev: object) -> None:  # noqa: N802 (Qt signature)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(_BG_COLOR)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), _BORDER_RADIUS, _BORDER_RADIUS)

    # -- public API (called via Qt slots from HudServer) --------------------
    @pyqtSlot(str, str, str)
    def show_msg(self, icon: str, title: str, subtitle: str) -> None:
        self.icon_label.setText(icon or "•")
        self.title_label.setText(title or "")
        self.subtitle_label.setText(self._elide(subtitle))
        self._reposition()
        if not self.isVisible():
            self.show()
            # Hyprland tiles new windows by default; force float + pin
            # + exact geometry via hyprctl now that the window exists.
            # Done in a single-shot timer so Hyprland has a tick to
            # register the new window before we address it by title.
            QTimer.singleShot(80, self._apply_hyprland_rules)
            self._fade(0.0, 1.0)
        else:
            # Already visible: cancel any pending fade-out and snap to opaque.
            self._anim.stop()
            self.setWindowOpacity(1.0)

    @pyqtSlot(str, str, str)
    def update_msg(self, icon: str, title: str, subtitle: str) -> None:
        # Same UX as show: idempotent so callers don't have to track state.
        self.show_msg(icon, title, subtitle)

    @pyqtSlot()
    def hide_msg(self) -> None:
        if not self.isVisible():
            return
        self._fade(self.windowOpacity(), 0.0, then_hide=True)

    # -- helpers ------------------------------------------------------------
    def _elide(self, text: str) -> str:
        # Hard cap; chosen so the rendered string fits inside _HUD_W
        # at the current subtitle font. Qt also clips via setFixedSize
        # but eliding gives a cleaner '…' instead of a chopped char.
        if len(text) > 90:
            return text[:87] + "…"
        return text

    def _reposition(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x, y = _corner_xy(geo, settings.hud_corner, settings.hud_margin)
        self.move(QPoint(x, y))

    def _apply_hyprland_rules(self) -> None:
        """Force Hyprland to treat the HUD as a small floating pinned
        overlay in the configured corner of the active monitor.

        Corner + margin come from ``settings.hud_corner`` and
        ``settings.hud_margin``. Done at runtime via ``hyprctl dispatch``
        (no edits to the user's ``hypr/conf.d``) because:

        * Hyprland tiles new windows by default — without these calls
          the widget shows up at full workspace size.
        * ``windowrulev2`` syntax differs between Hyprland versions
          (renamed to ``windowrule`` in 0.55, new ``= value``
          grammar) and we don't want to silently break the user's
          config.

        Best-effort: if hyprctl is missing (X11, other compositor)
        or any dispatch fails we just leave the window wherever Qt
        put it. The widget is still visible, just not pinned.
        """
        if not shutil.which("hyprctl"):
            return
        sel = "title:voice-opencode-hud"
        # Position from settings (hud_corner + hud_margin).
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()  # excludes reserved panels (waybar, etc.)
        x, y = _corner_xy(geo, settings.hud_corner, settings.hud_margin)
        for cmd in (
            ("setfloating",   sel),
            ("pin",           sel),
            ("resizewindowpixel", f"exact {_HUD_W} {_HUD_H},{sel}"),
            ("movewindowpixel",   f"exact {x} {y},{sel}"),
        ):
            try:
                subprocess.run(
                    ["hyprctl", "dispatch", *cmd],
                    check=False, timeout=1.5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.TimeoutExpired, OSError):
                # Compositor not responsive; skip silently — the HUD
                # is non-essential UX.
                continue

    def _fade(self, start: float, end: float, *, then_hide: bool = False) -> None:
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        if then_hide:
            self._anim.finished.connect(self.hide)
        self._anim.start()


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------
class HudServer:
    """Unix-socket listener that drives a ``TurnHUD`` via the Qt event loop.

    Lives in the tray process. Binds ``HUD_SOCKET`` (mode 0600) and
    spawns one ``QSocketNotifier`` per accepted client. Each client
    sends one or more JSON objects, newline-separated; the server
    routes ``op`` to the matching widget method.
    """

    def __init__(self, hud: TurnHUD) -> None:
        self.hud = hud
        self._notifiers: list[QSocketNotifier] = []
        self._clients: dict[int, bytearray] = {}
        self._sock: socket.socket | None = None

    def start(self) -> None:
        paths.ensure_dirs()
        # Always start clean: stale socket files from a previous tray
        # process would otherwise make bind() fail with EADDRINUSE.
        try:
            HUD_SOCKET.unlink()
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.setblocking(False)
        s.bind(str(HUD_SOCKET))
        os.chmod(HUD_SOCKET, 0o600)
        s.listen(8)
        self._sock = s
        n = QSocketNotifier(s.fileno(), QSocketNotifier.Type.Read)  # type: ignore[call-overload]
        n.activated.connect(self._on_accept)
        self._notifiers.append(n)
        log(f"HUD server listening on {HUD_SOCKET}")

    def stop(self) -> None:
        for n in self._notifiers:
            n.setEnabled(False)
        self._notifiers.clear()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            HUD_SOCKET.unlink()
        except FileNotFoundError:
            pass

    # -- internals ----------------------------------------------------------
    def _on_accept(self, _fd: int) -> None:
        if self._sock is None:
            return
        try:
            client, _ = self._sock.accept()
        except OSError:
            return
        client.setblocking(False)
        fd = client.fileno()
        self._clients[fd] = bytearray()
        notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read)  # type: ignore[call-overload]
        notifier.activated.connect(lambda _f, c=client: self._on_client_data(c))
        self._notifiers.append(notifier)

    def _on_client_data(self, client: socket.socket) -> None:
        fd = client.fileno()
        buf = self._clients.get(fd)
        if buf is None:
            return
        try:
            chunk = client.recv(4096)
        except OSError:
            chunk = b""
        if not chunk:
            self._close_client(client)
            return
        buf.extend(chunk)
        while b"\n" in buf:
            line, _, rest = buf.partition(b"\n")
            del buf[:]
            buf.extend(rest)
            self._dispatch(bytes(line))

    def _close_client(self, client: socket.socket) -> None:
        fd = client.fileno()
        self._clients.pop(fd, None)
        # Detach notifier(s) for this fd.
        self._notifiers = [n for n in self._notifiers if n.socket() != fd]
        try:
            client.close()
        except OSError:
            pass

    def _dispatch(self, line: bytes) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            log(f"HUD: ignoring non-JSON line: {line!r}")
            return
        op = msg.get("op")
        icon = str(msg.get("icon", ""))
        title = str(msg.get("title", ""))
        subtitle = str(msg.get("subtitle", ""))
        if op == "show":
            self.hud.show_msg(icon, title, subtitle)
        elif op == "update":
            self.hud.update_msg(icon, title, subtitle)
        elif op == "hide":
            self.hud.hide_msg()
        else:
            log(f"HUD: unknown op {op!r}")


# ---------------------------------------------------------------------------
# Client (used by notify.turn_*)
# ---------------------------------------------------------------------------
def send(op: str, *, icon: str = "", title: str = "", subtitle: str = "",
         timeout: float = 0.05) -> bool:
    """Send a single HUD command. Returns True on success.

    Best-effort: silently returns False if the socket isn't there (tray
    not running) or the send times out. Never raises.
    """
    if sys.platform == "win32":
        # No Unix sockets on Windows. Phase B will add a named-pipe
        # backend; for now the HUD is Linux-only.
        return False
    if not HUD_SOCKET.exists():
        return False
    payload = json.dumps({
        "op": op, "icon": icon, "title": title, "subtitle": subtitle,
    }).encode("utf-8") + b"\n"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(HUD_SOCKET))
        s.sendall(payload)
        s.close()
        return True
    except (TimeoutError, OSError):
        return False
