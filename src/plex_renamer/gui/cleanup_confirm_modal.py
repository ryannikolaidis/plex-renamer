"""Deletion confirmation modal.

When source cleanup is enabled and the user clicks Apply, this modal
pops up with EVERY path scheduled for deletion (source files plus the
now-empty parents that would be pruned up the chain). The user must
explicitly check the "I understand" checkbox before the Confirm button
enables.

Closing the modal, clicking Cancel, or unchecking the box all cancel
the deletion entirely. There is no partial-confirm path.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)


class CleanupConfirmModal(QDialog):
    """Modal showing every path that would be deleted by cleanup.

    Construct with the list of source paths; the user must check the
    "I understand" box before Confirm enables.
    """

    confirmed = Signal()

    def __init__(self, paths: Iterable[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm source deletion")
        self.setAccessibleName("cleanup-confirm-modal")
        self.setModal(True)

        warning = QLabel(
            "Cleanup will permanently delete the following source files. "
            "This cannot be undone from this app."
        )
        warning.setWordWrap(True)

        self._paths = list(paths)
        self._list = QListWidget()
        for p in self._paths:
            self._list.addItem(str(p))

        self._consent = QCheckBox("I understand, delete these")
        self._consent.toggled.connect(self._on_consent_toggled)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Delete")
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.accepted.connect(self._confirm)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addWidget(self._list)
        layout.addWidget(self._consent)
        layout.addWidget(self._buttons)

    # ----- Public API -----------------------------------------------------

    def paths(self) -> list[Path]:
        return list(self._paths)

    def consent_checked(self) -> bool:
        return bool(self._consent.isChecked())

    def confirm_button_enabled(self) -> bool:
        return self._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    # ----- Signal handlers ------------------------------------------------

    def _on_consent_toggled(self, checked: bool) -> None:
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(checked)

    def _confirm(self) -> None:
        if self._consent.isChecked():
            self.confirmed.emit()
            self.accept()


__all__ = ["CleanupConfirmModal"]
