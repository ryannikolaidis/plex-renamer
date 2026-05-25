"""Per-row edit pane.

Pops open when the user clicks a row in either panel. Surfaces:

* TMDB free-text search (delegated to :class:`TMDBSearchPanel`).
* IMDb ID paste with an anchor-type toggle (TMDB vs IMDb).
* Manual override fields (title, year, season, episode, edition).
* Skip toggle.

The pane is a thin widget over :class:`ItemModel`; every mutation calls
through the model so both panels and the badge update in lock-step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.models import ItemModel, ItemRow
from plex_renamer.gui.tmdb_search_panel import TMDBSearchPanel


class EditPane(QWidget):
    """The per-row edit pane.

    Construct with an :class:`ItemModel`; load a specific row via
    :meth:`load_row`. The pane is hidden until a row is loaded.
    """

    # Emitted when the user requests a TMDB search; the main window /
    # orchestrator fetches via the resolver and posts results back via
    # :meth:`set_tmdb_results`.
    tmdb_search_requested = Signal(Path, str)  # source_path, query

    # Emitted when the user pastes an IMDb id and asks to resolve it;
    # the main window calls the resolver and posts back via
    # :meth:`set_imdb_resolution`.
    imdb_resolve_requested = Signal(Path, str)  # source_path, imdb_id

    # Emitted when the user has finalized the edit (closes the pane).
    edit_committed = Signal(Path)

    def __init__(self, model: ItemModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._current_path: Path | None = None
        self.setAccessibleName("edit-pane")
        self._build_ui()

    # ----- Construction ---------------------------------------------------

    def _build_ui(self) -> None:
        self._title_label = QLabel("(no row selected)")

        # TMDB search.
        self._tmdb_panel = TMDBSearchPanel()
        self._tmdb_panel.search_requested.connect(self._on_tmdb_search)
        self._tmdb_panel.candidate_chosen.connect(self._on_candidate_chosen)

        # IMDb paste + anchor radios.
        self._imdb_input = QLineEdit()
        self._imdb_input.setPlaceholderText("tt1234567")
        self._imdb_resolve_btn = QPushButton("Resolve")
        self._imdb_resolve_btn.clicked.connect(self._on_imdb_resolve)
        self._anchor_tmdb = QRadioButton("Anchor: TMDB")
        self._anchor_imdb = QRadioButton("Anchor: IMDb")
        self._anchor_tmdb.setChecked(True)
        self._anchor_group = QButtonGroup(self)
        self._anchor_group.addButton(self._anchor_tmdb)
        self._anchor_group.addButton(self._anchor_imdb)
        self._anchor_tmdb.toggled.connect(self._on_anchor_toggled)

        imdb_box = QGroupBox("IMDb override")
        # The whole edit pane lives inside a QScrollArea (see the
        # bottom of this method), so the override box keeps its natural
        # Preferred/Preferred policy — Fixed previously caused the inner
        # QFormLayout rows to OVERLAP when Qt was forced to compress the
        # box below sizeHint. With a scroll wrapper there's always room
        # for sizeHint and the user scrolls when the right column is
        # short.
        imdb_layout = QFormLayout(imdb_box)
        imdb_row = QHBoxLayout()
        imdb_row.addWidget(self._imdb_input)
        imdb_row.addWidget(self._imdb_resolve_btn)
        imdb_layout.addRow("IMDb ID:", imdb_row)
        anchor_row = QHBoxLayout()
        anchor_row.addWidget(self._anchor_tmdb)
        anchor_row.addWidget(self._anchor_imdb)
        imdb_layout.addRow("Anchor kind:", anchor_row)

        # Manual override fields.
        self._manual_title = QLineEdit()
        self._manual_year = QSpinBox()
        self._manual_year.setRange(0, 9999)
        self._manual_year.setSpecialValueText(" ")  # blank when 0
        self._manual_season = QSpinBox()
        self._manual_season.setRange(0, 99)
        self._manual_season.setSpecialValueText(" ")
        self._manual_episode = QSpinBox()
        self._manual_episode.setRange(0, 999)
        self._manual_episode.setSpecialValueText(" ")
        self._manual_edition = QLineEdit()
        self._manual_edition.setPlaceholderText("Director's Cut, Extended, ...")
        self._apply_overrides_btn = QPushButton("Apply overrides")
        self._apply_overrides_btn.clicked.connect(self._on_apply_overrides)

        manual_box = QGroupBox("Manual override")
        # Default Preferred policy — see imdb_box note above. The
        # QScrollArea wrapper at the bottom prevents the squish-and-
        # overlap behavior that the previous Fixed policy was trying to
        # work around.
        manual_layout = QFormLayout(manual_box)
        manual_layout.addRow("Title:", self._manual_title)
        manual_layout.addRow("Year:", self._manual_year)
        manual_layout.addRow("Season:", self._manual_season)
        manual_layout.addRow("Episode:", self._manual_episode)
        manual_layout.addRow("Edition:", self._manual_edition)
        manual_layout.addRow(self._apply_overrides_btn)

        # Skip + commit.
        self._skip_checkbox = QCheckBox("Skip this item")
        self._skip_checkbox.toggled.connect(self._on_skip_toggled)

        self._commit_btn = QPushButton("Done")
        self._commit_btn.clicked.connect(self._on_commit)

        # Keep references on the instance so tests can read the actual
        # rendered heights and assert the layout is not crushed.
        self._imdb_box = imdb_box
        self._manual_box = manual_box

        # Build the inner content widget. The TMDB search panel still
        # gets a stretch factor so it claims any extra vertical room
        # inside the scroll viewport; the override boxes use their
        # natural sizeHint and the scroll area handles the case where
        # the right column is too short to fit everything.
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.addWidget(self._title_label)
        # The TMDB panel needs enough room for the query row + a few
        # result rows. 200px leaves space for the IMDb + Manual override
        # boxes to fit on a typical 850-1000px tall window without
        # forcing the user to scroll the edit pane viewport.
        self._tmdb_panel.setMinimumHeight(200)
        inner_layout.addWidget(self._tmdb_panel, stretch=1)
        inner_layout.addWidget(imdb_box)
        inner_layout.addWidget(manual_box)
        inner_layout.addWidget(self._skip_checkbox)
        inner_layout.addWidget(self._commit_btn)
        inner_layout.addStretch(0)

        # Wrap the inner content in a QScrollArea so the override boxes
        # can claim their natural sizeHint without competing with the
        # other widgets. When the right column is short, the user
        # scrolls vertically instead of seeing the inner widgets crushed
        # below their sizeHint (which causes the QFormLayout rows to
        # OVERLAP — the v0.1.3 user-reported squish).
        self._scroll = QScrollArea()
        self._scroll.setWidget(inner)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

    # ----- Loading / unloading -------------------------------------------

    def load_row(self, source_path: Path) -> None:
        row = self._model.row_for(source_path)
        if row is None:
            self._current_path = None
            self._title_label.setText("(no row selected)")
            return
        self._current_path = source_path
        self._title_label.setText(f"Editing: {row.parsed.raw_filename}")
        # Hydrate fields from the row's existing state.
        self._tmdb_panel.set_query_text(row.parsed.title_candidate or "")
        self._tmdb_panel.set_results([])
        self._imdb_input.setText(row.imdb_id_override or "")
        if row.anchor_kind_override == "imdb":
            self._anchor_imdb.setChecked(True)
        else:
            self._anchor_tmdb.setChecked(True)
        self._manual_title.setText(row.manual_title or "")
        self._manual_year.setValue(row.manual_year or 0)
        self._manual_season.setValue(row.manual_season or 0)
        self._manual_episode.setValue(row.manual_episode or 0)
        self._manual_edition.setText(row.manual_edition or "")
        self._skip_checkbox.setChecked(row.skip)

    def current_row(self) -> ItemRow | None:
        if self._current_path is None:
            return None
        return self._model.row_for(self._current_path)

    # ----- Callers post results back here --------------------------------

    def set_tmdb_results(self, source_path: Path, candidates: list) -> None:
        """Receive search results for the row currently being edited."""
        if self._current_path != source_path:
            return
        self._tmdb_panel.set_results(candidates)

    def set_imdb_resolution(self, source_path: Path, candidate) -> None:
        """Receive an IMDb ID resolution and store it on the model."""
        if self._current_path != source_path:
            return
        if candidate is not None:
            self._model.set_candidate(source_path, candidate)

    # ----- Signal handlers ------------------------------------------------

    def _on_tmdb_search(self, query: str) -> None:
        if self._current_path is None:
            return
        self.tmdb_search_requested.emit(self._current_path, query)

    def _on_candidate_chosen(self, candidate) -> None:
        if self._current_path is None:
            return
        self._model.set_candidate(self._current_path, candidate)

    def _on_imdb_resolve(self) -> None:
        if self._current_path is None:
            return
        text = self._imdb_input.text().strip()
        if text:
            self._model.set_anchor_override(self._current_path, kind="imdb", imdb_id=text)
            self.imdb_resolve_requested.emit(self._current_path, text)

    def _on_anchor_toggled(self, checked: bool) -> None:
        if self._current_path is None:
            return
        kind: Literal["tmdb", "imdb"] = "tmdb" if checked else "imdb"
        self._model.set_anchor_override(self._current_path, kind=kind)

    def _on_apply_overrides(self) -> None:
        if self._current_path is None:
            return
        title = self._manual_title.text().strip() or None
        year = self._manual_year.value() if self._manual_year.value() > 0 else None
        season = self._manual_season.value() if self._manual_season.value() > 0 else None
        episode = self._manual_episode.value() if self._manual_episode.value() > 0 else None
        edition = self._manual_edition.text().strip() or None
        self._model.set_manual_override(
            self._current_path,
            title=title,
            year=year,
            season=season,
            episode=episode,
            edition=edition,
        )

    def _on_skip_toggled(self, checked: bool) -> None:
        if self._current_path is None:
            return
        self._model.set_skip(self._current_path, checked)

    def _on_commit(self) -> None:
        if self._current_path is None:
            return
        self.edit_committed.emit(self._current_path)

    # ----- Test accessors ------------------------------------------------

    def tmdb_panel(self) -> TMDBSearchPanel:
        """Return the inner :class:`TMDBSearchPanel`.

        Used by integration tests that need to inspect the rendered
        height of the search panel after layout (Bug B regression gate).
        """
        return self._tmdb_panel

    def imdb_box(self) -> QGroupBox:
        """Return the IMDb override group box."""
        return self._imdb_box

    def manual_box(self) -> QGroupBox:
        """Return the manual override group box."""
        return self._manual_box


__all__ = ["EditPane"]
