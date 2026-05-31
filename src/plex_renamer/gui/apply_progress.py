"""Live progress widget rendered during an apply pass.

Shows a determinate :class:`QProgressBar` keyed on the executor's
``op_index`` + ``total_ops``, plus a current-file label that flips to
the source filename on every ``op_started`` and the error string on
every ``op_failed``. The widget is hidden between apply passes; the
:class:`MainWindow` shows it via :meth:`begin` when an apply starts
and :meth:`hide_widget` when the worker finishes (success or failure).

Mirrors the WPF ``ApplyProgress`` user control (PR #21).
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout


class ApplyProgressWidget(QFrame):
    """Determinate progress bar + caption shown during apply.

    The widget is created hidden. Call :meth:`begin` with the op count
    before starting the worker, :meth:`update_for_event` per event off
    the worker's signal, and :meth:`hide_widget` when the worker
    completes.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("apply_progress")
        self.setAccessibleName("Apply progress")
        self.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._headline = QLabel("Applying...")
        self._headline.setStyleSheet("font-weight: 600;")
        top.addWidget(self._headline, stretch=1)
        self._count = QLabel("0 / 0")
        self._count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._count)
        outer.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setMaximum(1)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setAccessibleName("Apply progress bar")
        outer.addWidget(self._bar)

        self._current = QLabel("")
        self._current.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._current.setWordWrap(False)
        self._current.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        outer.addWidget(self._current)

    def begin(self, total_ops: int) -> None:
        """Show the widget at zero progress before the first event arrives."""
        self._headline.setText("Applying...")
        if total_ops > 0:
            self._bar.setMaximum(total_ops)
            self._count.setText(f"0 / {total_ops}")
        else:
            self._bar.setMaximum(1)
            self._count.setText("starting...")
        self._bar.setValue(0)
        self._current.setText("")
        self.setVisible(True)

    def update_for_event(self, event: dict[str, Any]) -> None:
        """Update visible state from one executor event."""
        kind = event.get("event")
        total = event.get("total_ops")
        if isinstance(total, int) and total > 0:
            self._bar.setMaximum(total)
        op_index = event.get("op_index")
        if kind == "op_started" and isinstance(op_index, int):
            self._bar.setValue(op_index)
            if isinstance(total, int):
                self._count.setText(f"{op_index + 1} / {total}")
            else:
                self._count.setText(str(op_index + 1))
            source = event.get("source")
            if isinstance(source, str) and source:
                self._current.setText(f"Copying {os.path.basename(source)}")
            else:
                self._current.setText("")
        elif kind == "op_verified" and isinstance(op_index, int):
            self._bar.setValue(op_index + 1)
            if isinstance(total, int):
                self._count.setText(f"{op_index + 1} / {total}")
            else:
                self._count.setText(str(op_index + 1))
        elif kind == "op_failed" and isinstance(op_index, int):
            self._bar.setValue(op_index + 1)
            if isinstance(total, int):
                self._count.setText(f"{op_index + 1} / {total}")
            error = event.get("error")
            if isinstance(error, str) and error:
                self._current.setText(f"Failed: {error}")

    def hide_widget(self) -> None:
        """Hide the widget. Distinct name avoids clobbering ``QWidget.hide``."""
        self.setVisible(False)


__all__ = ["ApplyProgressWidget"]
