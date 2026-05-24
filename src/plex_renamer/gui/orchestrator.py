"""GUI orchestrator: binds the engine surfaces to the review window.

The :class:`MainWindow` is a thin Qt assembly layer. All the real engine
work — running the TMDB+IMDb resolver against parsed rows, opening the
show-anchor picker when a TV group needs anchoring, building a
:class:`~plex_renamer.planner.RenamePlan`, applying it via
:func:`~plex_renamer.executor.apply_plan`, and undoing through
:func:`~plex_renamer.executor.undo_batch` — runs here.

The orchestrator owns NO Qt widgets directly; it manipulates the
window's models and pops sub-dialogs through method calls. The window
re-emits its inner widgets' signals (``tmdb_search_requested``,
``imdb_resolve_requested``, ``group_clicked``, ``reanchor_requested``,
``undone``) so :func:`Orchestrator.connect` wires the orchestrator to
one object and one object only.

The orchestrator's TMDB collaborator is typed as ``_TMDBLike`` so a
:class:`TMDBClient`, a :class:`TMDBCache`, or a test fake all work.
That keeps the orchestrator testable without a real network: the fake
just implements the same five method names.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch
from plex_renamer.gui.models import ItemModel, ItemRow, RunReport
from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker
from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult


class _TMDBLike(Protocol):
    """Subset of TMDBClient / TMDBCache the orchestrator needs.

    The resolver consumes the same protocol for the search/find calls;
    the orchestrator additionally needs ``get_season`` so it can populate
    episode lists after a show is picked.
    """

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]: ...
    def search_tv(self, title: str, year: int | None) -> list[TVResult]: ...
    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None: ...
    def get_season(self, tmdb_id: int, season: int) -> list[Episode]: ...


# A factory that returns the ShowAnchorPicker for a given group key. The
# default builds the real dialog; tests inject a fake so they can drive
# the orchestrator without an event loop.
ShowAnchorPickerFactory = Callable[[str], ShowAnchorPicker]


def _default_picker_factory(group_key: str) -> ShowAnchorPicker:
    return ShowAnchorPicker(group_key=group_key)


@dataclass
class OrchestratorDeps:
    """Bundle of engine collaborators the orchestrator needs.

    Keeping this as a dataclass lets tests construct one with explicit
    fakes; production wires the real :class:`TMDBCache` +
    :class:`IMDbFallbackResolver`.
    """

    tmdb: _TMDBLike
    resolver: IMDbFallbackResolver
    movies_root: Path
    tv_root: Path
    journal_dir: Path
    cleanup_enabled: bool = False
    picker_factory: ShowAnchorPickerFactory = _default_picker_factory


class Orchestrator:
    """Engine binder for :class:`MainWindow`.

    Construct with the GUI :class:`ItemModel`, the dependency bundle,
    and an optional ``main_window`` (only needed for the show-anchor
    picker's parent — tests skip it). Then call :meth:`connect` with
    the MainWindow to wire every signal in one place, OR call the
    handler methods directly (tests do this to avoid event-loop spin).
    """

    def __init__(
        self,
        item_model: ItemModel,
        deps: OrchestratorDeps,
        *,
        main_window: object | None = None,
    ) -> None:
        self._model = item_model
        self._deps = deps
        self._main_window = main_window
        # The currently-open picker, if any. We hold a reference so the
        # picker isn't garbage-collected while the user interacts.
        self._open_picker: ShowAnchorPicker | None = None

    # ----- Wiring ---------------------------------------------------------

    def connect(self, main_window: object) -> None:
        """Hook every MainWindow signal up to the orchestrator handlers."""
        self._main_window = main_window
        main_window.tmdb_search_requested.connect(self.on_tmdb_search)  # type: ignore[attr-defined]
        main_window.imdb_resolve_requested.connect(self.on_imdb_resolve)  # type: ignore[attr-defined]
        main_window.group_clicked.connect(self.on_group_clicked)  # type: ignore[attr-defined]
        main_window.reanchor_requested.connect(self.on_reanchor_requested)  # type: ignore[attr-defined]
        main_window.undone.connect(self.on_undo_requested)  # type: ignore[attr-defined]

    # ----- Parse + resolve ------------------------------------------------

    def parse(self, input_root: Path) -> list[ParseResult]:
        """Parse the tree at ``input_root`` and return the ParseResults.

        This is the function the GUI hands to ``MainWindow(parse_fn=)``.
        Resolution is intentionally deferred to :meth:`resolve_rows` so
        the GUI can render the source panel BEFORE the network calls
        finish — the user sees rows immediately, with candidates filling
        in as resolution completes.

        We don't run resolution inline here because callers may want to
        update the model first; the wiring layer that drives MainWindow
        is responsible for calling :meth:`resolve_rows` after.
        """
        return list(parse_tree(input_root))

    def resolve_rows(self, rows: list[ItemRow]) -> None:
        """Resolve every row's candidate via the IMDb-fallback resolver.

        For TV rows, after a candidate lands we additionally fetch the
        season's episode list so the planner can match episodes by title
        downstream. Failures don't abort the loop; an unresolved row
        stays without a candidate and lands as "unresolved" in the UI.
        """
        for row in rows:
            parsed = row.parsed
            try:
                if parsed.kind == "movie":
                    candidate = self._deps.resolver.resolve_movie(
                        parsed.title_candidate or "", parsed.year
                    )
                elif parsed.kind == "tv":
                    candidate = self._deps.resolver.resolve_tv(
                        parsed.title_candidate or "", parsed.year
                    )
                else:
                    continue
            except Exception:
                continue
            if candidate is None:
                continue
            # For TV, hydrate the season's episode list when possible so
            # the planner can title-match episodes downstream.
            if parsed.kind == "tv" and candidate.anchor_kind == "tmdb":
                candidate = self._hydrate_tv_season(candidate, parsed.season)
            self._model.set_candidate(row.source_path, candidate)

    def parse_and_resolve(self, input_root: Path) -> list[ItemRow]:
        """Convenience: parse + filter + resolve in one call.

        Returns the list of :class:`ItemRow` (movie/tv only, unknowns and
        skips dropped). The orchestrator pushes them into the model and
        runs resolution. The MainWindow can subscribe to ``rows_reset``
        if it wants to react to the population.
        """
        parsed_list = self.parse(input_root)
        rows = [
            ItemRow(parsed=p) for p in parsed_list if p.kind != "unknown" and p.skip_reason is None
        ]
        self._model.set_rows(rows)
        self.resolve_rows(rows)
        return rows

    def _hydrate_tv_season(self, candidate: Candidate, season_hint: int | None) -> Candidate:
        """Fetch season episodes for a TMDB-anchored TV candidate.

        Returns a NEW Candidate with the populated ``episode_list``. Any
        TMDB error short-circuits to returning the original candidate
        unchanged — the planner falls back to filename hints downstream.
        """
        season = season_hint if season_hint is not None else 1
        try:
            tmdb_id = int(candidate.anchor_id)
        except (TypeError, ValueError):
            return candidate
        try:
            episodes = self._deps.tmdb.get_season(tmdb_id, season)
        except Exception:
            return candidate
        if not episodes:
            return candidate
        return Candidate(
            anchor_kind=candidate.anchor_kind,
            anchor_id=candidate.anchor_id,
            kind=candidate.kind,
            title=candidate.title,
            year=candidate.year,
            confidence=candidate.confidence,
            episode_list=tuple(episodes),
        )

    # ----- Signal handlers ------------------------------------------------

    def on_tmdb_search(self, source_path: Path, query: str) -> None:
        """Run a TMDB search for the row at ``source_path`` and post results.

        Combines movie + TV results into one candidate list (the user
        picks any of them) — the row is allowed to flip kinds on a
        manual TMDB pick.
        """
        row = self._model.row_for(source_path)
        if row is None:
            return
        candidates: list[Candidate] = []
        try:
            movies = self._deps.tmdb.search_movie(query, row.parsed.year)
        except Exception:
            movies = []
        for m in movies:
            candidates.append(
                Candidate(
                    anchor_kind="tmdb",
                    anchor_id=str(m.tmdb_id),
                    kind="movie",
                    title=m.title,
                    year=m.year,
                    confidence=0.7,
                )
            )
        try:
            shows = self._deps.tmdb.search_tv(query, row.parsed.year)
        except Exception:
            shows = []
        for s in shows:
            candidates.append(
                Candidate(
                    anchor_kind="tmdb",
                    anchor_id=str(s.tmdb_id),
                    kind="tv",
                    title=s.title,
                    year=s.year,
                    confidence=0.7,
                )
            )
        if self._main_window is not None:
            edit_pane = self._main_window.edit_pane()  # type: ignore[attr-defined]
            edit_pane.set_tmdb_results(source_path, candidates)

    def on_imdb_resolve(self, source_path: Path, imdb_id: str) -> None:
        """Resolve an IMDb tt-id to a Candidate and store it on the row."""
        row = self._model.row_for(source_path)
        if row is None:
            return
        try:
            hit = self._deps.tmdb.find_by_imdb_id(imdb_id)
        except Exception:
            hit = None
        if hit is None:
            # Synthesize an IMDb-anchored candidate so the user can still
            # proceed with an IMDb folder name; confidence is moderate.
            candidate = Candidate(
                anchor_kind="imdb",
                anchor_id=imdb_id,
                kind=row.parsed.kind if row.parsed.kind != "unknown" else "movie",
                title=row.parsed.title_candidate or "",
                year=row.parsed.year,
                confidence=0.55,
            )
        elif isinstance(hit, MovieResult):
            candidate = Candidate(
                anchor_kind="tmdb",
                anchor_id=str(hit.tmdb_id),
                kind="movie",
                title=hit.title,
                year=hit.year,
                confidence=0.8,
            )
        else:
            # TVResult.
            candidate = Candidate(
                anchor_kind="tmdb",
                anchor_id=str(hit.tmdb_id),
                kind="tv",
                title=hit.title,
                year=hit.year,
                confidence=0.8,
            )
            candidate = self._hydrate_tv_season(candidate, row.parsed.season)
        self._model.set_candidate(source_path, candidate)

    def on_group_clicked(self, group_key: str) -> None:
        """Open the show-anchor picker for the group, populate TMDB results.

        The picker is created via the dep-injected factory so tests can
        substitute a fake. We pre-seed search results based on the
        group's representative title (the first row's title_candidate).
        """
        rows = self._rows_in_group(group_key)
        if not rows:
            return
        title_hint = rows[0].parsed.title_candidate or ""
        year_hint = rows[0].parsed.year
        try:
            shows = self._deps.tmdb.search_tv(title_hint, year_hint)
        except Exception:
            shows = []
        candidates = [
            Candidate(
                anchor_kind="tmdb",
                anchor_id=str(s.tmdb_id),
                kind="tv",
                title=s.title,
                year=s.year,
                confidence=0.7,
            )
            for s in shows
        ]
        picker = self._deps.picker_factory(group_key)
        picker.set_results(candidates)
        picker.show_chosen.connect(self.on_show_chosen)
        self._open_picker = picker
        picker.exec()

    def on_show_chosen(self, group_key: str, candidate: Candidate) -> None:
        """Apply the picked show to every row in the group.

        After the user picks a show, fetch the relevant season once and
        push the hydrated candidate onto every row in the group so the
        planner has the episode list it needs to match by title.
        """
        rows = self._rows_in_group(group_key)
        if not rows:
            return
        season_hint = rows[0].parsed.season
        hydrated = self._hydrate_tv_season(candidate, season_hint)
        for row in rows:
            self._model.set_candidate(row.source_path, hydrated)
        self._open_picker = None

    def on_reanchor_requested(self, target: Path) -> None:
        """Open the edit pane on the first source colliding with ``target``.

        The collision-review widget tells us which TARGET collided; we
        look up the first source for that target and route the edit pane
        there so the user can change the anchor.
        """
        if self._main_window is None:
            return
        # Find the first source whose proposed target matches.
        for row in self._model.rows():
            if row.proposed_op is not None and row.proposed_op.target == target:
                self._main_window.edit_pane().load_row(row.source_path)  # type: ignore[attr-defined]
                return

    def on_undo_requested(self, journal_path: Path) -> None:
        """Run the engine's undo against the journal at ``journal_path``."""
        try:
            journal = Journal.load(journal_path)
        except (OSError, ValueError):
            return
        try:
            undo_batch(journal)
        except Exception:
            # Undo failures don't propagate to the GUI thread; the user
            # can re-trigger if the on-disk state has issues.
            return

    # ----- Apply ----------------------------------------------------------

    def apply(self, item_model: ItemModel, input_root: Path) -> RunReport:
        """Build a plan from the model and call ``apply_plan``.

        This is the function the GUI hands to ``MainWindow(apply_fn=)``.
        Rows without a candidate are dropped to the skipped list; rows
        flagged ``skip`` are dropped silently.
        """
        pairs: list[tuple[ParseResult, Candidate | None]] = []
        skipped: list[tuple[Path, str]] = []
        for row in item_model.rows():
            if row.skip:
                skipped.append((row.source_path, "user_skip"))
                continue
            pairs.append((row.parsed, row.candidate))

        plan = build_plan_from_pairs(
            pairs,
            movies_root=self._deps.movies_root,
            tv_root=self._deps.tv_root,
            input_root=input_root,
            fetch_season=self._deps.tmdb.get_season,
            skipped=skipped,
        )
        # Record proposed ops on the model so the target panel + the
        # reanchor lookup find the corresponding row.
        for op in plan.ops:
            item_model.set_proposed_op(op.source, op)

        result = apply_plan(
            plan,
            journal_dir=self._deps.journal_dir,
            cleanup=self._deps.cleanup_enabled,
            verify_hash=False,
        )

        # Build the GUI-facing report from the ApplyResult.
        error_messages: list[str] = []
        try:
            journal = Journal.load(result.journal_path)
        except (OSError, ValueError):
            journal = None
        if journal is not None:
            for entry in journal.entries:
                if entry.status == "failed" and entry.error:
                    error_messages.append(entry.error)

        return RunReport(
            succeeded=result.succeeded,
            skipped=len(plan.skipped),
            errored=result.failed,
            journal_path=result.journal_path,
            error_messages=tuple(error_messages),
        )

    # ----- Helpers --------------------------------------------------------

    def _rows_in_group(self, group_key: str) -> list[ItemRow]:
        groups = self._model.groups()
        return list(groups.get(group_key, []))


__all__ = [
    "Orchestrator",
    "OrchestratorDeps",
    "ShowAnchorPickerFactory",
]
