"""Show-anchor picker for ambiguous TV groups.

When a TV group has multiple low-confidence rows (or none of its rows
produced a confident candidate), the source panel's group node becomes
clickable and surfaces this picker. The user picks the show once on
TMDB; the picker emits :attr:`show_chosen` and the orchestrator then
re-resolves every row in the group against the picked show's TMDB
episode list.

The picker hosts a search box at the top so the user can iterate when
the auto-seeded query returns nothing (the show name in the path
doesn't match a known title): typing a new query and clicking Search
emits :attr:`search_requested`; the orchestrator re-queries TMDB and
pushes new results back via :meth:`set_results`. The picker itself
does NOT call TMDB.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.tmdb.models import Candidate


class ShowAnchorPicker(QDialog):
    """Modal-ish dialog for picking a TV show on TMDB.

    Construction:
        ``picker = ShowAnchorPicker(group_key="tv::Foo", parent=window)``
        ``picker.set_results([...])``
        ``picker.show_chosen.connect(on_show)``
        ``picker.search_requested.connect(on_search)``
        ``picker.exec()``
    """

    # Emitted with the candidate the user picked.
    show_chosen = Signal(str, object)  # group_key, Candidate
    # Emitted when the picker wants a TMDB lookup for the group. The
    # orchestrator subscribes and re-queries TMDB; new results land back
    # on the picker via :meth:`set_results`.
    search_requested = Signal(str, str)  # group_key, query

    def __init__(self, group_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick a show on TMDB")
        self.setAccessibleName("show-anchor-picker")
        self._group_key = group_key
        self._candidates: list[Candidate] = []

        # Search row at the top: text input + Search button. The user
        # types a different show name when the auto-seeded query
        # returns nothing.
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Type a show name and press Enter...")
        self._search_input.returnPressed.connect(self.trigger_search)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self.trigger_search)
        search_row = QHBoxLayout()
        search_row.addWidget(self._search_input)
        search_row.addWidget(self._search_btn)

        self._empty_label = QLabel("")
        self._empty_label.setVisible(False)
        self._empty_label.setAccessibleName("show-anchor-picker-empty")

        self._results = QListWidget()
        self._results.itemSelectionChanged.connect(self._on_selection_changed)
        self._results.itemDoubleClicked.connect(lambda _item: self._emit_chosen())

        self._use_btn = QPushButton("Pick this show")
        self._use_btn.clicked.connect(self._emit_chosen)
        self._use_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._results)
        layout.addWidget(self._use_btn)

    # ----- Public API -----------------------------------------------------

    def group_key(self) -> str:
        return self._group_key

    def set_results(self, candidates: list[Candidate]) -> None:
        """Replace the result list with ``candidates``.

        Resets the selection (the Pick button disables until the user
        selects a row). When the list is empty AND the search box has a
        query, surfaces a hint label so the user sees the dialog isn't
        broken -- they need to try a different query.
        """
        self._candidates = list(candidates)
        self._results.clear()
        for c in self._candidates:
            label = f"{c.title} ({c.year or '----'}) — {c.anchor_kind}:{c.anchor_id}"
            QListWidgetItem(label, self._results)
        self._use_btn.setEnabled(False)
        if not self._candidates:
            query = self._search_input.text().strip()
            if query:
                self._empty_label.setText(f"No matches for {query!r}. Try a different name.")
            else:
                self._empty_label.setText("No matches. Type a show name above and press Search.")
            self._empty_label.setVisible(True)
        else:
            self._empty_label.setVisible(False)

    def selected_candidate(self) -> Candidate | None:
        row = self._results.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row]

    def search_text(self) -> str:
        return self._search_input.text()

    def set_search_text(self, text: str) -> None:
        """Pre-populate the search input (typically with the show name hint)."""
        self._search_input.setText(text)

    def trigger_search(self) -> None:
        """Emit :attr:`search_requested` with the current search box text.

        Public so tests can drive the search without firing a synthetic
        Qt button click. A no-op when the input is empty -- searching
        for an empty string is never the user's intent.
        """
        query = self._search_input.text().strip()
        if not query:
            return
        self.search_requested.emit(self._group_key, query)

    # ----- Signal handlers ------------------------------------------------

    def _emit_chosen(self) -> None:
        c = self.selected_candidate()
        if c is not None:
            self.show_chosen.emit(self._group_key, c)
            self.accept()

    def _on_selection_changed(self) -> None:
        self._use_btn.setEnabled(self._results.currentRow() >= 0)


__all__ = ["ShowAnchorPicker"]
