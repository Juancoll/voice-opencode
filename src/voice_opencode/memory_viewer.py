"""Memory viewer (PyQt6 dialog).

Read-only window that polls the on-disk memory files and shows the
last N entries in a sortable table with a live substring filter.
Mirrors the design of :mod:`audit_viewer` so the two tray dialogs
feel the same.

Why read-only
-------------
``memory.delete`` and ``memory.edit`` exist (Tanda 2 / Phase G+1),
but they're destructive and gated to the ``full`` capacity tier.
Surfacing them as buttons here would let the user mutate memory
from a click while the agent itself can't — a confusing capability
asymmetry. If/when we want UI editing, it gets its own dialog with
explicit confirms; for now the viewer stays a glance tool.

Why polling, not inotify
------------------------
Same rationale as :mod:`audit_viewer`: opened on demand from the
tray, closed after a glance. One QTimer is simpler than wiring
``QFileSystemWatcher`` across day-file rotations.

Why lazy-imported by the tray
-----------------------------
``QTableWidget`` is heavy. The tray boots in ~150 ms today; we
don't want to pay table-widget initialisation cost for users who
never open this dialog.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import memory as mem
from .paths import MEMORY_DIR

# Columns. ``ts`` first for chronological scanning; ``body`` last so
# the long column eats the remaining width.
_COLS = ("ts", "tags", "body")

# Polling cadence. Memory mutates only on agent action; 2 s lag is fine.
_POLL_MS = 2000


class MemoryViewer(QDialog):
    """Modeless dialog showing the most recent memory entries."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("voice-opencode — Memoria del agente")
        self.setMinimumSize(900, 480)

        layout = QVBoxLayout(self)

        # Top bar: count spin + search box + manual refresh.
        top = QHBoxLayout()
        top.addWidget(QLabel("Últimas"))
        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(10, 5000)
        self.count_spin.setValue(100)
        self.count_spin.setSingleStep(10)
        self.count_spin.valueChanged.connect(lambda _: self.refresh())
        top.addWidget(self.count_spin)
        top.addWidget(QLabel("entradas"))

        top.addSpacing(20)
        top.addWidget(QLabel("Filtro:"))
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(
            "substring (busca en cuerpo y tags)")
        self.search_edit.textChanged.connect(lambda _: self._apply_filter())
        top.addWidget(self.search_edit, 1)

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
        self.table.setWordWrap(True)
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

        # Cache of currently loaded entries (pre-filter). We re-pull
        # from disk only on timer / count change / manual refresh;
        # filter typing is applied client-side on this cache.
        self._entries: list[mem.MemoryEntry] = []

        self.refresh()

    # -- data -----------------------------------------------------------
    def refresh(self) -> None:
        """Re-read from disk and reapply the active filter."""
        self._entries = mem.recent(int(self.count_spin.value()))
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self.search_edit.text().strip().lower()
        if needle:
            visible = [
                e for e in self._entries
                if needle in e.body.lower()
                or any(needle in t.lower() for t in e.tags)
            ]
        else:
            visible = self._entries

        self.table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            cells = (
                entry.ts.strftime("%Y-%m-%d %H:%M:%S"),
                ", ".join(entry.tags),
                entry.body,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

        total = len(self._entries)
        shown = len(visible)
        if needle:
            msg = (f"{shown}/{total} entradas (filtro activo) — "
                   f"dir: {MEMORY_DIR}")
        else:
            msg = f"{shown} entradas mostradas — dir: {MEMORY_DIR}"
        self.status.setText(msg)


def open_viewer() -> MemoryViewer:
    """Construct and show a non-modal viewer. Caller must keep the ref."""
    v = MemoryViewer()
    v.show()
    return v
