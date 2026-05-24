"""Top-level settings dialog.

Surfaces:

* TMDB API key (mandatory for the engine).
* OMDB API key (optional; powers the IMDb fallback).
* "Enable source cleanup" toggle (OFF by default, persisted).
* "Auto-accept top hit" toggle (OFF by default, persisted).
* Button to open the library roots picker.

Settings are persisted via :class:`Settings`; the dialog reads the
existing values on open and writes back on Save.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.config.settings import Settings
from plex_renamer.gui.library_roots_dialog import LibraryRootsDialog


class SettingsDialog(QDialog):
    """Top-level settings dialog."""

    settings_saved = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setAccessibleName("settings-dialog")
        self._settings = settings

        self._tmdb_key = QLineEdit(settings.tmdb_api_key or "")
        self._tmdb_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._omdb_key = QLineEdit(settings.omdb_api_key or "")
        self._omdb_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._cleanup_toggle = QCheckBox("Enable source cleanup after verified copy")
        self._cleanup_toggle.setChecked(settings.cleanup_enabled)
        self._auto_accept_toggle = QCheckBox("Auto-accept TMDB top hit (power user)")
        self._auto_accept_toggle.setChecked(settings.auto_accept_top_hit)
        self._roots_btn = QPushButton("Library roots...")
        self._roots_btn.clicked.connect(self._open_roots)

        form = QFormLayout()
        form.addRow("TMDB API key:", self._tmdb_key)
        form.addRow("OMDB API key (optional):", self._omdb_key)
        form.addRow(self._cleanup_toggle)
        form.addRow(self._auto_accept_toggle)
        form.addRow(self._roots_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _open_roots(self) -> None:
        dlg = LibraryRootsDialog(self._settings, parent=self)
        dlg.exec()

    def _save(self) -> None:
        # Use setters so the file is persisted on each mutation; we then
        # toggle the in-memory flags and call ``save`` for the rest.
        self._settings.set_tmdb_api_key(self._tmdb_key.text().strip() or None)
        self._settings.set_omdb_api_key(self._omdb_key.text().strip() or None)
        self._settings.cleanup_enabled = bool(self._cleanup_toggle.isChecked())
        self._settings.auto_accept_top_hit = bool(self._auto_accept_toggle.isChecked())
        self._settings.save()
        self.settings_saved.emit()
        self.accept()

    # ----- Test helpers ---------------------------------------------------

    def set_cleanup_enabled(self, enabled: bool) -> None:
        self._cleanup_toggle.setChecked(enabled)

    def set_tmdb_key(self, key: str) -> None:
        self._tmdb_key.setText(key)


__all__ = ["SettingsDialog"]
