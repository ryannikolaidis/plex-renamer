"""Qt-aware models for the review UI.

These wrap engine outputs (``ParseResult``, ``Candidate``, ``RenameOp``,
``Collision``) into UI rows the panels render. They emit Qt signals on
mutation so the source panel, target panel, and edit pane stay in sync.

Design choices
--------------

* **No engine logic lives here.** The model holds candidate state and
  proposed targets, but recomputing a target after the user edits a row
  is delegated to a caller-provided callback (typically the engine's
  ``movie_target_path`` / ``tv_target_path`` builders). The model is a
  view-state cache, not a planner clone.
* **Rows are addressed by ``source_path``**, which is unique across the
  parse tree.
* **Confidence band** is derived in one place
  (:func:`confidence_band_for`) so panels and badges agree.

Confidence bands match the slice 3 thresholds documented in
``IMDbFallbackResolver``:

- ``>= 0.85`` → ``auto`` (green): auto-accept.
- ``>= 0.60`` → ``review`` (yellow): needs human review.
- ``<  0.60`` → ``unresolved`` (red): no usable candidate.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, Signal

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.models import Collision, RenameOp
from plex_renamer.tmdb.models import Candidate

ConfidenceBand = Literal["auto", "review", "unresolved"]

# Confidence thresholds. These match the fallback resolver's documented
# bands; the GUI MUST NOT diverge from the engine, since the
# "auto-accept top hit" toggle relies on the same numbers downstream.
AUTO_ACCEPT_THRESHOLD = 0.85
NEEDS_REVIEW_THRESHOLD = 0.60


def confidence_band_for(candidate: Candidate | None) -> ConfidenceBand:
    """Map a Candidate's confidence (or absence) to a UI band."""
    if candidate is None:
        return "unresolved"
    score = candidate.confidence
    if score >= AUTO_ACCEPT_THRESHOLD:
        return "auto"
    if score >= NEEDS_REVIEW_THRESHOLD:
        return "review"
    return "unresolved"


def color_for_band(band: ConfidenceBand) -> str:
    """CSS hex color for a confidence band. Kept here so the badge widget
    and any other consumer agree.
    """
    return {
        "auto": "#2ecc71",  # green
        "review": "#f1c40f",  # yellow
        "unresolved": "#e74c3c",  # red
    }[band]


@dataclass
class ItemRow:
    """One source file's full UI state.

    The row holds the parser output, the current candidate (which the user
    can replace via the edit pane), the proposed RenameOp the engine
    would emit, and per-row toggles (``skip``, manual overrides).
    """

    parsed: ParseResult
    candidate: Candidate | None = None
    proposed_op: RenameOp | None = None
    skip: bool = False
    manual_title: str | None = None
    manual_year: int | None = None
    manual_season: int | None = None
    manual_episode: int | None = None
    manual_edition: str | None = None
    # When the user pastes an IMDb ID and picks the IMDb anchor, store
    # the raw id here; the resolver re-runs against this id.
    imdb_id_override: str | None = None
    anchor_kind_override: Literal["tmdb", "imdb"] | None = None
    # The SHOW name derived from the path tree (TV rows only). For
    # filenames like ``[S01.E01] Goodbye Cruel World.mp4`` the parser
    # correctly puts the episode title in ``episode_title`` and leaves
    # ``title_candidate`` empty; the show name lives on a parent
    # directory or on the user's drop root. The orchestrator's
    # ``derive_show_name`` populates this at parse time so the source
    # panel's group label, the resolver's TMDB query, and the
    # show-anchor picker all agree on the show name without each having
    # to walk the path tree independently.
    show_name_hint: str | None = None

    @property
    def source_path(self) -> Path:
        return self.parsed.source_path

    @property
    def confidence_band(self) -> ConfidenceBand:
        if self.skip:
            return "unresolved"
        return confidence_band_for(self.candidate)

    @property
    def group_key(self) -> str:
        """Group rows by detected show or movie.

        For TV, the SHOW name anchors the group so the UI can present
        "Show X — 12 episodes" with one anchor picker. The preferred
        source is ``show_name_hint`` (derived from the path tree at
        parse time); when that's absent (older test paths that
        instantiate ``ItemRow`` without going through the orchestrator)
        we fall back to ``title_candidate`` and finally the closest
        parent directory name. The fallback chain matters for back-
        compat with tests that pre-date the hint.

        For movies, each movie is its own group keyed by the source
        path so per-row review is the default.
        """
        if self.parsed.kind == "tv":
            hint = self.show_name_hint
            if hint:
                return f"tv::{hint}"
            base = self.parsed.title_candidate or ""
            parent = (
                self.parsed.parent_dirs[-1] if self.parsed.parent_dirs else self.parsed.raw_filename
            )
            return f"tv::{base or parent}"
        if self.parsed.kind == "movie":
            return f"movie::{self.parsed.source_path}"
        return f"unknown::{self.parsed.source_path}"


class ItemModel(QObject):
    """Holds the list of :class:`ItemRow` and emits signals on change.

    The model is a flat list addressed by source path. Panels group by
    :attr:`ItemRow.group_key` on the fly; we don't precompute a tree
    because the group key can change when the user manually overrides
    the kind of a row (and the source panel needs to re-group cleanly).
    """

    # Emitted whenever any row's state changes (candidate, skip, manual
    # override). The receiver re-renders the relevant panel.
    row_changed = Signal(Path)
    rows_reset = Signal()

    # Emitted when the user double-clicks / clicks-to-edit a row. The
    # main window listens and pops the edit pane.
    row_activated = Signal(Path)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ItemRow] = []

    # ----- Population -----------------------------------------------------

    def set_rows(self, rows: Iterable[ItemRow]) -> None:
        self._rows = list(rows)
        self.rows_reset.emit()

    def add_row(self, row: ItemRow) -> None:
        self._rows.append(row)
        self.rows_reset.emit()

    def clear(self) -> None:
        self._rows = []
        self.rows_reset.emit()

    def notify_rows_reset(self) -> None:
        """Force a panel rebuild without mutating data.

        Used by callers that directly mutate ``ItemRow`` fields (e.g.
        the orchestrator backfilling ``show_name_hint`` on TV rows after
        ``set_rows`` already fired). Direct mutation bypasses the
        per-field setters that emit ``row_changed``, leaving the panels
        rendering stale labels. ``notify_rows_reset`` re-emits
        ``rows_reset`` so the source/target panels regenerate their
        tree from the current row state.
        """
        self.rows_reset.emit()

    # ----- Access ---------------------------------------------------------

    def rows(self) -> list[ItemRow]:
        return list(self._rows)

    def row_for(self, source_path: Path) -> ItemRow | None:
        for r in self._rows:
            if r.source_path == source_path:
                return r
        return None

    def group_for(self, source_path: Path) -> str | None:
        """Return the group key for ``source_path``, or None if not loaded."""
        row = self.row_for(source_path)
        return row.group_key if row is not None else None

    def groups(self) -> dict[str, list[ItemRow]]:
        """Return rows grouped by ``group_key``, preserving insertion order."""
        out: dict[str, list[ItemRow]] = {}
        for r in self._rows:
            out.setdefault(r.group_key, []).append(r)
        return out

    def __len__(self) -> int:
        return len(self._rows)

    # ----- Mutation -------------------------------------------------------

    def set_candidate(self, source_path: Path, candidate: Candidate | None) -> None:
        row = self.row_for(source_path)
        if row is None:
            return
        row.candidate = candidate
        self.row_changed.emit(source_path)

    def set_proposed_op(self, source_path: Path, op: RenameOp | None) -> None:
        row = self.row_for(source_path)
        if row is None:
            return
        row.proposed_op = op
        self.row_changed.emit(source_path)

    def set_skip(self, source_path: Path, skip: bool) -> None:
        row = self.row_for(source_path)
        if row is None:
            return
        row.skip = skip
        self.row_changed.emit(source_path)

    def set_manual_override(
        self,
        source_path: Path,
        *,
        title: str | None = None,
        year: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        edition: str | None = None,
    ) -> None:
        row = self.row_for(source_path)
        if row is None:
            return
        if title is not None:
            row.manual_title = title
        if year is not None:
            row.manual_year = year
        if season is not None:
            row.manual_season = season
        if episode is not None:
            row.manual_episode = episode
        if edition is not None:
            row.manual_edition = edition
        self.row_changed.emit(source_path)

    def set_anchor_override(
        self,
        source_path: Path,
        *,
        kind: Literal["tmdb", "imdb"] | None,
        imdb_id: str | None = None,
    ) -> None:
        row = self.row_for(source_path)
        if row is None:
            return
        row.anchor_kind_override = kind
        if imdb_id is not None:
            row.imdb_id_override = imdb_id
        self.row_changed.emit(source_path)

    def emit_activated(self, source_path: Path) -> None:
        self.row_activated.emit(source_path)


@dataclass
class CollisionItem:
    """One collision the review widget surfaces."""

    target: Path
    sources: tuple[Path, ...]
    reason: str
    # Per-collision user action. ``None`` means unresolved.
    action: Literal["keep_both", "keep_first", "reanchor"] | None = None


class CollisionModel(QObject):
    """List of unresolved collisions plus the user's chosen actions."""

    item_changed = Signal(Path)  # the collision's target
    reset = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[CollisionItem] = []

    def set_collisions(self, collisions: Iterable[Collision]) -> None:
        self._items = [
            CollisionItem(target=c.target, sources=c.sources, reason=c.reason) for c in collisions
        ]
        self.reset.emit()

    def items(self) -> list[CollisionItem]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def set_action(
        self,
        target: Path,
        action: Literal["keep_both", "keep_first", "reanchor"] | None,
    ) -> None:
        for it in self._items:
            if it.target == target:
                it.action = action
                self.item_changed.emit(target)
                return

    def all_resolved(self) -> bool:
        return all(it.action is not None for it in self._items)


@dataclass
class RunReport:
    """Post-run summary the report widget renders."""

    succeeded: int = 0
    skipped: int = 0
    errored: int = 0
    journal_path: Path | None = None
    error_messages: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "AUTO_ACCEPT_THRESHOLD",
    "CollisionItem",
    "CollisionModel",
    "ConfidenceBand",
    "ItemModel",
    "ItemRow",
    "NEEDS_REVIEW_THRESHOLD",
    "RunReport",
    "color_for_band",
    "confidence_band_for",
]
