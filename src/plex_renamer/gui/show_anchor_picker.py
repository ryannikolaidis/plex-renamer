"""Show-anchor picker for ambiguous TV groups.

When a TV group has multiple low-confidence rows (or none of its rows
produced a confident candidate), the source panel's group node becomes
clickable and surfaces this picker. The user picks the show once on
TMDB; the picker emits :attr:`show_chosen` and the orchestrator then
re-resolves every row in the group against the picked show's TMDB
episode list.

The picker is a thin variant of :class:`TMDBSearchPanel` scoped to TV
search results. It does NOT itself call TMDB; the orchestrator injects
results.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
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
        ``picker.exec()``
    """

    # Emitted with the candidate the user picked.
    show_chosen = Signal(str, object)  # group_key, Candidate
    # Emitted when the picker wants a TMDB lookup for the group.
    search_requested = Signal(str, str)  # group_key, query

    def __init__(self, group_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick a show on TMDB")
        self.setAccessibleName("show-anchor-picker")
        self._group_key = group_key
        self._candidates: list[Candidate] = []

        self._results = QListWidget()
        self._results.itemSelectionChanged.connect(self._on_selection_changed)

        self._use_btn = QPushButton("Use this show")
        self._use_btn.clicked.connect(self._emit_chosen)
        self._use_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._results)
        layout.addWidget(self._use_btn)

    # ----- Public API -----------------------------------------------------

    def group_key(self) -> str:
        return self._group_key

    def set_results(self, candidates: list[Candidate]) -> None:
        self._candidates = list(candidates)
        self._results.clear()
        for c in self._candidates:
            label = f"{c.title} ({c.year or '----'}) — {c.anchor_kind}:{c.anchor_id}"
            QListWidgetItem(label, self._results)
        self._use_btn.setEnabled(False)

    def selected_candidate(self) -> Candidate | None:
        row = self._results.currentRow()
        if row < 0 or row >= len(self._candidates):
            return None
        return self._candidates[row]

    # ----- Signal handlers ------------------------------------------------

    def _emit_chosen(self) -> None:
        c = self.selected_candidate()
        if c is not None:
            self.show_chosen.emit(self._group_key, c)
            self.accept()

    def _on_selection_changed(self) -> None:
        self._use_btn.setEnabled(self._results.currentRow() >= 0)


__all__ = ["ShowAnchorPicker"]
