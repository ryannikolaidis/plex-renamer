"""Post-run report widget.

Renders the apply result: succeeded / skipped / errored counts plus a
one-click "Undo this batch" button. Undo is wired to call
:func:`plex_renamer.executor.undo_batch` against the journal recorded
on the run.

The widget is a regular :class:`QWidget` (not a modal) so the user can
keep it open in the bottom of the main window while inspecting the
target panel.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.models import RunReport


class RunReportWidget(QWidget):
    """Renders a :class:`RunReport` and offers an undo action."""

    undo_requested = Signal(Path)  # journal_path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("run-report")

        self._succeeded_label = QLabel("0")
        self._skipped_label = QLabel("0")
        self._errored_label = QLabel("0")
        self._journal_label = QLabel("(no run yet)")
        self._errors_list = QListWidget()

        self._undo_btn = QPushButton("Undo this batch")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._on_undo)

        counts = QGridLayout()
        counts.addWidget(QLabel("Succeeded:"), 0, 0)
        counts.addWidget(self._succeeded_label, 0, 1)
        counts.addWidget(QLabel("Skipped:"), 1, 0)
        counts.addWidget(self._skipped_label, 1, 1)
        counts.addWidget(QLabel("Errored:"), 2, 0)
        counts.addWidget(self._errored_label, 2, 1)
        counts.addWidget(QLabel("Journal:"), 3, 0)
        counts.addWidget(self._journal_label, 3, 1)

        errors_box = QGroupBox("Errors")
        errors_layout = QVBoxLayout(errors_box)
        errors_layout.addWidget(self._errors_list)

        layout = QVBoxLayout(self)
        layout.addLayout(counts)
        layout.addWidget(errors_box)
        layout.addWidget(self._undo_btn)

        self._report: RunReport = RunReport()

    # ----- Public API -----------------------------------------------------

    def set_report(self, report: RunReport) -> None:
        self._report = report
        self._succeeded_label.setText(str(report.succeeded))
        self._skipped_label.setText(str(report.skipped))
        self._errored_label.setText(str(report.errored))
        self._journal_label.setText(str(report.journal_path) if report.journal_path else "(none)")
        self._errors_list.clear()
        for msg in report.error_messages:
            self._errors_list.addItem(msg)
        self._undo_btn.setEnabled(report.journal_path is not None)

    def set_resolve_errors(self, errors: list[tuple[Path, str]]) -> None:
        """Replace the Errors list with per-row resolver failures.

        Called from the main window when the orchestrator's
        ``resolve_errors_changed`` signal fires. We render each entry
        as ``<filename>: <message>`` so the user sees which file failed
        to resolve and why; the full path is available on hover via the
        widget's tooltip.

        This is intentionally a SET (not an append): each resolve pass
        is the authoritative list. An empty argument clears prior
        errors so a successful re-resolve doesn't leave stale entries
        showing.
        """
        self._errors_list.clear()
        for path, message in errors:
            self._errors_list.addItem(f"{path.name}: {message}")

    def report(self) -> RunReport:
        return self._report

    # ----- Handlers -------------------------------------------------------

    def _on_undo(self) -> None:
        if self._report.journal_path is not None:
            self.undo_requested.emit(self._report.journal_path)


__all__ = ["RunReportWidget"]
