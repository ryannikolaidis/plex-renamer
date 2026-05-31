"""Top-level settings dialog.

Surfaces:

* TMDB API key (mandatory for the engine).
* OMDB API key (optional; powers the IMDb fallback).
* "Enable source cleanup" toggle (OFF by default, persisted).
* "Auto-accept top hit" toggle (OFF by default, persisted).
* Movies / TV library-root pickers (inline, with Browse buttons).

Settings are persisted via :class:`Settings`; the dialog reads the
existing values on open and writes back on Save.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.config.settings import Settings


def _section_header(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; padding-top: 6px;")
    return label


def _hr() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setStyleSheet("color: palette(mid);")
    return line


class SettingsDialog(QDialog):
    """Top-level settings dialog.

    Sectioned layout: TMDB / OMDB integration, Library roots,
    Apply behaviour. Library roots are inline with Browse buttons
    (replacing the prior nested LibraryRootsDialog hop).
    """

    settings_saved = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setAccessibleName("settings-dialog")
        self.setMinimumWidth(560)
        self._settings = settings

        # --- TMDB / OMDB integration ---
        self._tmdb_key = QLineEdit(settings.tmdb_api_key or "")
        self._tmdb_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._tmdb_key.setPlaceholderText("paste your TMDB v3 API key here")
        self._tmdb_key.setAccessibleName("TMDB API key")

        self._tmdb_help = QLabel(
            'Get one for free at <a href="https://www.themoviedb.org/settings/api">'
            "themoviedb.org → Profile → Settings → API</a>. "
            "Use the v3 key, not the v4 read-access token."
        )
        self._tmdb_help.setOpenExternalLinks(True)
        self._tmdb_help.setWordWrap(True)
        self._tmdb_help.setStyleSheet("color: palette(mid); font-size: 11px;")

        self._omdb_key = QLineEdit(settings.omdb_api_key or "")
        self._omdb_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._omdb_key.setPlaceholderText("optional, used for IMDb fallbacks")
        self._omdb_key.setAccessibleName("OMDB API key")

        # --- Library roots ---
        self._movies_root = QLineEdit(settings.movies_root or "")
        self._movies_root.setPlaceholderText("e.g. /Volumes/Plex/Movies")
        self._movies_root.setAccessibleName("Movies library root path")
        movies_browse = QPushButton("Browse…")
        movies_browse.clicked.connect(self._browse_movies)

        self._tv_root = QLineEdit(settings.tv_root or "")
        self._tv_root.setPlaceholderText("e.g. /Volumes/Plex/TV")
        self._tv_root.setAccessibleName("TV library root path")
        tv_browse = QPushButton("Browse…")
        tv_browse.clicked.connect(self._browse_tv)

        # --- Apply behaviour ---
        self._cleanup_warning = QLabel(
            "⚠ Cleanup deletes source files after every successful copy. "
            "A per-batch confirmation dialog still appears before any deletion runs."
        )
        self._cleanup_warning.setWordWrap(True)
        self._cleanup_warning.setStyleSheet(
            "background: rgba(241, 196, 15, 0.15); "
            "border: 1px solid rgba(241, 196, 15, 0.5); "
            "border-radius: 4px; padding: 8px; color: #8a6d3b;"
        )

        self._cleanup_toggle = QCheckBox(
            "Delete source files after successful copy (gated by a per-batch confirmation)"
        )
        self._cleanup_toggle.setChecked(settings.cleanup_enabled)
        self._cleanup_toggle.setAccessibleName("Enable source cleanup after copy")

        self._auto_accept_toggle = QCheckBox(
            "Auto-accept high-confidence TMDB matches without prompting"
        )
        self._auto_accept_toggle.setChecked(settings.auto_accept_top_hit)
        self._auto_accept_toggle.setAccessibleName("Auto-accept high-confidence TMDB matches")

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(_section_header("TMDB / OMDB integration"))
        tmdb_form = QGridLayout()
        tmdb_form.addWidget(QLabel("TMDB API key:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        tmdb_form.addWidget(self._tmdb_key, 0, 1, 1, 2)
        tmdb_form.addWidget(self._tmdb_help, 1, 1, 1, 2)
        tmdb_form.addWidget(QLabel("OMDB API key:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        tmdb_form.addWidget(self._omdb_key, 2, 1, 1, 2)
        layout.addLayout(tmdb_form)

        layout.addWidget(_hr())
        layout.addWidget(_section_header("Library roots"))
        roots_form = QGridLayout()
        roots_form.addWidget(QLabel("Movies:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        roots_form.addWidget(self._movies_root, 0, 1)
        roots_form.addWidget(movies_browse, 0, 2)
        roots_form.addWidget(QLabel("TV:"), 1, 0, Qt.AlignmentFlag.AlignRight)
        roots_form.addWidget(self._tv_root, 1, 1)
        roots_form.addWidget(tv_browse, 1, 2)
        layout.addLayout(roots_form)

        layout.addWidget(_hr())
        layout.addWidget(_section_header("Apply behaviour"))
        layout.addWidget(self._cleanup_warning)
        layout.addWidget(self._cleanup_toggle)
        layout.addWidget(self._auto_accept_toggle)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def _browse_movies(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Pick the Movies root", self._movies_root.text() or ""
        )
        if chosen:
            self._movies_root.setText(chosen)

    def _browse_tv(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Pick the TV root", self._tv_root.text() or ""
        )
        if chosen:
            self._tv_root.setText(chosen)

    def _save(self) -> None:
        # Use setters so the file is persisted on each mutation; we then
        # toggle the in-memory flags and call ``save`` for the rest.
        self._settings.set_tmdb_api_key(self._tmdb_key.text().strip() or None)
        self._settings.set_omdb_api_key(self._omdb_key.text().strip() or None)
        self._settings.movies_root = self._movies_root.text().strip() or None
        self._settings.tv_root = self._tv_root.text().strip() or None
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
