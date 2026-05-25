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

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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


def _candidate_url(c: Candidate) -> str:
    """Public URL for the candidate's anchor record.

    TMDB anchors get a themoviedb.org page; IMDb anchors get an imdb.com
    page. Used both for the picker's "View on TMDB" button and for the
    selection-preview label so the user can verify each suggestion
    before committing.
    """
    if c.anchor_kind == "tmdb":
        path = "tv" if c.kind == "tv" else "movie"
        return f"https://www.themoviedb.org/{path}/{c.anchor_id}"
    return f"https://www.imdb.com/title/{c.anchor_id}/"


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

        # Notice label for the fuzzy fallback case. When the auto-seeded
        # query produced zero results and the orchestrator retried with
        # a cleaned variant, this label tells the user what was actually
        # searched so they don't wonder why the result list disagrees
        # with the search box's initial content.
        self._fallback_notice = QLabel("")
        self._fallback_notice.setVisible(False)
        self._fallback_notice.setAccessibleName("show-anchor-picker-fallback-notice")
        self._fallback_notice.setStyleSheet("color: #8a6d3b; font-style: italic;")
        self._fallback_notice.setWordWrap(True)

        self._results = QListWidget()
        self._results.itemSelectionChanged.connect(self._on_selection_changed)
        self._results.itemDoubleClicked.connect(lambda _item: self._emit_chosen())

        # Selection-preview row: renders the highlighted candidate's
        # canonical URL as an HTML hyperlink so the user can click
        # straight through to TMDB / IMDb to validate the suggestion
        # before committing. Display only — the orchestrator doesn't
        # see this label.
        self._url_label = QLabel("")
        self._url_label.setOpenExternalLinks(True)
        self._url_label.setVisible(False)
        self._url_label.setAccessibleName("show-anchor-picker-url")
        self._url_label.setTextFormat(Qt.TextFormat.RichText)

        self._view_btn = QPushButton("View on TMDB")
        self._view_btn.clicked.connect(self._open_selected_url)
        self._view_btn.setEnabled(False)

        self._use_btn = QPushButton("Pick this show")
        self._use_btn.clicked.connect(self._emit_chosen)
        self._use_btn.setEnabled(False)

        action_row = QHBoxLayout()
        action_row.addWidget(self._view_btn)
        action_row.addWidget(self._use_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self._fallback_notice)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._results)
        layout.addWidget(self._url_label)
        layout.addLayout(action_row)

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

    def set_fallback_notice(self, original: str, used: str) -> None:
        """Show or hide the fuzzy-fallback notice label.

        Called by the orchestrator when the auto-seeded query returned
        zero results AND a cleaned variant did return results. Passing
        empty strings hides the label (the normal case).
        """
        if original and used:
            self._fallback_notice.setText(
                f"No matches for {original!r} — showing results for {used!r}"
            )
            self._fallback_notice.setVisible(True)
        else:
            self._fallback_notice.setText("")
            self._fallback_notice.setVisible(False)

    def fallback_notice_text(self) -> str:
        """Return the current text of the fallback notice (empty if hidden)."""
        return self._fallback_notice.text()

    def empty_hint_text(self) -> str:
        """Return the empty-results hint text (empty when results exist)."""
        return self._empty_label.text()

    def has_results(self) -> bool:
        """Return True if at least one candidate is loaded into the list."""
        return bool(self._candidates)

    def candidates(self) -> list[Candidate]:
        """Return the current ordered candidate list."""
        return list(self._candidates)

    def select_result(self, index: int) -> None:
        """Programmatically select a row by index. No-op on out-of-range."""
        if 0 <= index < len(self._candidates):
            self._results.setCurrentRow(index)

    def trigger_pick(self) -> None:
        """Programmatically click "Pick this show".

        Tests use this to drive the choose flow without a synthetic
        click. A no-op when nothing is selected.
        """
        self._emit_chosen()

    # ----- Signal handlers ------------------------------------------------

    def _emit_chosen(self) -> None:
        c = self.selected_candidate()
        if c is not None:
            self.show_chosen.emit(self._group_key, c)
            self.accept()

    def _on_selection_changed(self) -> None:
        has_selection = self._results.currentRow() >= 0
        self._use_btn.setEnabled(has_selection)
        self._view_btn.setEnabled(has_selection)
        c = self.selected_candidate()
        if c is None:
            self._url_label.setVisible(False)
            self._url_label.setText("")
            return
        url = _candidate_url(c)
        # Adapt the "View on" button text to the anchor system.
        self._view_btn.setText("View on TMDB" if c.anchor_kind == "tmdb" else "View on IMDb")
        self._url_label.setText(f'<a href="{url}">{url}</a>')
        self._url_label.setVisible(True)

    def _open_selected_url(self) -> None:
        c = self.selected_candidate()
        if c is None:
            return
        QDesktopServices.openUrl(QUrl(_candidate_url(c)))


__all__ = ["ShowAnchorPicker"]
