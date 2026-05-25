"""TMDB free-text search panel used inside the edit pane.

The widget exposes a search box, a result list, and a "Use this" button
that emits :attr:`candidate_chosen` with the user's selection. The
actual TMDB call is owned by the parent (the edit pane / main window)
so this widget stays decoupled from network code — it just renders a
list of candidates someone else fetched.

Poster thumbnails are optional; for now we render a placeholder pixmap
slot per result. Real artwork loading is a follow-up that doesn't change
the wiring.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.tmdb.models import Candidate


class TMDBSearchPanel(QWidget):
    """Free-text TMDB search panel.

    The parent injects search results via :meth:`set_results`; the widget
    emits :attr:`search_requested` when the user wants a new query and
    :attr:`candidate_chosen` when they pick a result.
    """

    search_requested = Signal(str)
    candidate_chosen = Signal(object)  # Candidate

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("tmdb-search-panel")

        self._query = QLineEdit()
        self._query.setPlaceholderText("Search TMDB...")
        # Pin a sensible Qt-default-ish text-field height; without this
        # the QFormLayout the pane is embedded in collapses the input
        # to a few pixels on certain platforms (the v0.1.1 user
        # reported the box was visually squished).
        self._query.setMinimumHeight(28)
        self._query.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._query.returnPressed.connect(self._emit_search)

        self._search_btn = QPushButton("Search")
        self._search_btn.setMinimumHeight(30)
        self._search_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._search_btn.clicked.connect(self._emit_search)

        self._results = QListWidget()
        # The candidate list is the visual centerpiece of the panel;
        # give it room to render multiple rows and let it grow when the
        # containing dialog resizes.
        self._results.setMinimumHeight(100)
        self._results.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )

        self._use_btn = QPushButton("Use this")
        self._use_btn.setMinimumHeight(30)
        self._use_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._use_btn.clicked.connect(self._emit_chosen)
        self._use_btn.setEnabled(False)
        self._results.itemSelectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self._query)
        row.addWidget(self._search_btn)
        layout.addLayout(row)
        layout.addWidget(self._results, stretch=1)
        layout.addWidget(self._use_btn)

        self._candidates: list[Candidate] = []

    # ----- Population -----------------------------------------------------

    def set_results(self, candidates: list[Candidate]) -> None:
        self._candidates = list(candidates)
        self._results.clear()
        for c in self._candidates:
            label = f"{c.title} ({c.year or '----'}) — {c.anchor_kind}:{c.anchor_id}"
            QListWidgetItem(label, self._results)
        self._use_btn.setEnabled(False)

    def query_text(self) -> str:
        return self._query.text()

    def set_query_text(self, text: str) -> None:
        self._query.setText(text)

    def selected_candidate(self) -> Candidate | None:
        row = self._results.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row]

    # ----- Signal emitters ------------------------------------------------

    def _emit_search(self) -> None:
        text = self._query.text().strip()
        if text:
            self.search_requested.emit(text)

    def _emit_chosen(self) -> None:
        c = self.selected_candidate()
        if c is not None:
            self.candidate_chosen.emit(c)

    def _on_selection_changed(self) -> None:
        self._use_btn.setEnabled(self._results.currentRow() >= 0)


__all__ = ["TMDBSearchPanel"]
