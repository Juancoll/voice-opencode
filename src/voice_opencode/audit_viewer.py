"""Audit log viewer (PyQt6 dialog).

A tiny, read-only window that tails ``logs/agent.log`` and shows the
last N JSON-line entries in a monospace table. Polled (not inotified)
because (a) the file is rotated/truncated rarely, (b) the viewer is
opened from the tray on demand and closed after a glance — keeping
the implementation to one timer is worth the 1s lag.

Why a separate module
---------------------
``tray.py`` is already ~360 lines. The viewer pulls in
``QDialog`` + ``QTableWidget`` which we don't want loaded for tray
boot. The tray imports this module lazily, only when the user picks
"Ver auditoría" from the Agente submenu.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import agent

# Columns in the table.
_COLS = ("ts", "tool", "args", "result")

# Polling cadence. Audit log is append-only; we don't need fast refresh.
_POLL_MS = 1500


class AuditViewer(QDialog):
    """Modeless dialog showing the tail of the agent audit log."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("voice-opencode — Auditoría del agente")
        self.setMinimumSize(900, 420)

        layout = QVBoxLayout(self)

        # Top bar: line count + manual refresh.
        top = QHBoxLayout()
        top.addWidget(QLabel("Últimas"))
        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(10, 5000)
        self.count_spin.setValue(100)
        self.count_spin.setSingleStep(10)
        self.count_spin.valueChanged.connect(lambda _: self.refresh())
        top.addWidget(self.count_spin)
        top.addWidget(QLabel("entradas"))
        top.addStretch()
        self.refresh_btn = QPushButton("Refrescar", self)
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)

        # Table.
        self.table = QTableWidget(self)
        self.table.setColumnCount(len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.table.setFont(mono)
        layout.addWidget(self.table)

        # Footer status.
        self.status = QLabel("", self)
        layout.addWidget(self.status)

        # Auto-refresh timer.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(_POLL_MS)

        self.refresh()

    # -- data -----------------------------------------------------------
    def refresh(self) -> None:
        entries = agent.audit_tail(int(self.count_spin.value()))
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for col, key in enumerate(_COLS):
                raw = entry.get(key, "")
                text = raw if isinstance(raw, str) else _short_json(raw)
                item = QTableWidgetItem(text)
                # Align ts left-padded, keep wrap off for table compactness.
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        # Scroll to bottom so the newest is in view.
        if entries:
            self.table.scrollToBottom()
        self.status.setText(
            f"{len(entries)} entradas mostradas — log: {agent.AGENT_LOG_FILE}")


def _short_json(obj: Any) -> str:
    """Compact one-line repr for dict/list values in the table."""
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(obj)


def open_viewer() -> AuditViewer:
    """Construct and show a non-modal viewer. Caller must keep the ref."""
    v = AuditViewer()
    v.show()
    return v
