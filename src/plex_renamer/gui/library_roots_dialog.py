"""Library roots picker dialog.

The user picks the ``Movies/`` and ``TV Shows/`` library roots; the
dialog persists them via :class:`Settings`. The widget is a simple
form with two file-picker buttons and a Save / Cancel pair.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.config.settings import Settings


class LibraryRootsDialog(QDialog):
    """Pick the Movies and TV Shows roots."""

    roots_saved = Signal(Path, Path)  # movies_root, tv_root

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Library roots")
        self.setAccessibleName("library-roots-dialog")
        self._settings = settings

        self._movies = QLineEdit(settings.movies_root or "")
        movies_btn = QPushButton("...")
        movies_btn.clicked.connect(self._pick_movies)
        movies_row = QHBoxLayout()
        movies_row.addWidget(self._movies)
        movies_row.addWidget(movies_btn)

        self._tv = QLineEdit(settings.tv_root or "")
        tv_btn = QPushButton("...")
        tv_btn.clicked.connect(self._pick_tv)
        tv_row = QHBoxLayout()
        tv_row.addWidget(self._tv)
        tv_row.addWidget(tv_btn)

        form = QFormLayout()
        form.addRow("Movies root:", movies_row)
        form.addRow("TV Shows root:", tv_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _pick_movies(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Pick the Movies root")
        if chosen:
            self._movies.setText(chosen)

    def _pick_tv(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Pick the TV Shows root")
        if chosen:
            self._tv.setText(chosen)

    def _save(self) -> None:
        movies_text = self._movies.text().strip()
        tv_text = self._tv.text().strip()
        self._settings.movies_root = movies_text or None
        self._settings.tv_root = tv_text or None
        self._settings.save()
        if movies_text and tv_text:
            self.roots_saved.emit(Path(movies_text), Path(tv_text))
        self.accept()

    # ----- Test helpers ---------------------------------------------------

    def set_paths(self, movies: str, tv: str) -> None:
        self._movies.setText(movies)
        self._tv.setText(tv)


__all__ = ["LibraryRootsDialog"]
