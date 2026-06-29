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

from PySide6.QtCore import QObject, Signal

from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch
from plex_renamer.gui.models import ItemModel, ItemRow, RunReport
from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker
from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult

# Re-exported for back-compat with callers that import
# :func:`derive_show_name` from this module. The implementation lives
# in :mod:`plex_renamer.parser.show_name` so the diagnostics CLI can
# use the same rule without depending on the Qt GUI.
from plex_renamer.parser.show_name import derive_show_name as derive_show_name  # noqa: F401
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.models import RenamePlan
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult
from plex_renamer.tmdb.ranking import cleaned_query_variants, rank_candidates


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


class Orchestrator(QObject):
    """Engine binder for :class:`MainWindow`.

    Construct with the GUI :class:`ItemModel`, the dependency bundle,
    and an optional ``main_window`` (only needed for the show-anchor
    picker's parent — tests skip it). Then call :meth:`connect` with
    the MainWindow to wire every signal in one place, OR call the
    handler methods directly (tests do this to avoid event-loop spin).
    """

    # Emitted whenever the resolve pass produces per-row failures. The
    # main window subscribes and forwards to the run-report widget so
    # silent resolver exceptions become visible to the user instead of
    # being swallowed.
    resolve_errors_changed = Signal(list)  # list[tuple[Path, str]]

    def __init__(
        self,
        item_model: ItemModel,
        deps: OrchestratorDeps,
        *,
        main_window: object | None = None,
    ) -> None:
        super().__init__()
        self._model = item_model
        self._deps = deps
        self._main_window = main_window
        # The currently-open picker, if any. We hold a reference so the
        # picker isn't garbage-collected while the user interacts.
        self._open_picker: ShowAnchorPicker | None = None
        # Per-source-path resolver errors, keyed so each public resolver
        # entry point can clear only the paths it touches before
        # attempting the work. The dict is rendered to a list and
        # emitted on ``resolve_errors_changed`` so the run-report
        # surfaces failures; absent that subscription, tests read via
        # :meth:`last_resolve_errors`.
        self._resolve_errors_by_path: dict[Path, str] = {}

    # ----- Wiring ---------------------------------------------------------

    def connect(self, main_window: object) -> None:
        """Hook every MainWindow signal up to the orchestrator handlers.

        Also subscribes to ``MainWindow.parsed_inputs`` so the resolve
        pass fires AFTER the drop handler has populated the model. This
        is what keeps production's ``_parse_fn`` shape aligned with the
        tests: ``_parse_fn`` returns a ``ParseResult`` list and the
        orchestrator runs resolution on the seated rows out-of-band.

        The orchestrator's own ``resolve_errors_changed`` signal is
        wired into the main window's run-report widget so per-row
        resolver failures (TMDB exceptions, missing API keys mid-batch)
        surface in the Errors pane instead of disappearing into a
        swallowed except clause.
        """
        self._main_window = main_window
        main_window.tmdb_search_requested.connect(self.on_tmdb_search)  # type: ignore[attr-defined]
        main_window.imdb_resolve_requested.connect(self.on_imdb_resolve)  # type: ignore[attr-defined]
        main_window.group_clicked.connect(self.on_group_clicked)  # type: ignore[attr-defined]
        main_window.reanchor_requested.connect(self.on_reanchor_requested)  # type: ignore[attr-defined]
        main_window.undone.connect(self.on_undo_requested)  # type: ignore[attr-defined]
        main_window.parsed_inputs.connect(self._on_parsed_inputs)  # type: ignore[attr-defined]
        main_window.library_roots_changed.connect(self.update_library_roots)  # type: ignore[attr-defined]
        run_report = main_window.run_report_widget()  # type: ignore[attr-defined]
        self.resolve_errors_changed.connect(run_report.set_resolve_errors)
        # Plumb the streaming-apply path: the MainWindow routes Apply
        # clicks through this orchestrator's prepare_apply +
        # build_run_report, with apply_plan_iter running on a worker
        # thread so the GUI stays responsive during file copies.
        if hasattr(main_window, "set_apply_adapter"):
            main_window.set_apply_adapter(self)  # type: ignore[attr-defined]

    def update_library_roots(self, movies_root: str, tv_root: str) -> None:
        """Refresh the orchestrator's library roots after a user change.

        ``OrchestratorDeps.movies_root`` / ``tv_root`` are snapshotted at
        construction time; without this hook the planner keeps using the
        old paths even after the user picked a new destination via the
        bottom-bar Change... buttons. The signal carries the raw settings
        strings (since Settings stores them as ``str | None``); we coerce
        to ``Path`` here and leave empty strings alone (Settings.save
        with ``movies_root=""`` is treated the same as ``None``).
        """
        if movies_root:
            self._deps.movies_root = Path(movies_root)
        if tv_root:
            self._deps.tv_root = Path(tv_root)

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

        TV rows resolve PER GROUP: a 13-episode show is one TMDB search
        and one season hydration per unique season, not 13 of each. The
        group key is each row's :attr:`ItemRow.show_name_hint` —
        episodes shaped like ``[S01.E01] Title.mp4`` leave the show name
        on a parent directory, not in ``title_candidate``, and the hint
        is what derive_show_name extracted at parse time.

        Movie rows resolve per-row as before.

        Failures don't abort the loop; an unresolved row stays without
        a candidate and lands as "unresolved" in the UI. Each failure
        is recorded against the affected source path in
        ``_resolve_errors_by_path`` and the full dict is emitted via
        ``resolve_errors_changed`` so the run-report surfaces them
        instead of letting them disappear into a swallowed except clause.
        Any prior error attached to one of THESE rows is cleared at the
        start so a successful re-resolve doesn't leave stale messages.
        """
        # Clear stale errors for every path this pass touches so a
        # success leaves no residue from a prior failed resolve.
        for r in rows:
            self._resolve_errors_by_path.pop(r.source_path, None)

        movie_rows = [r for r in rows if r.parsed.kind == "movie"]
        tv_rows = [r for r in rows if r.parsed.kind == "tv"]

        # ----- Movies: per-row -------------------------------------------
        for row in movie_rows:
            parsed = row.parsed
            query = parsed.title_candidate or ""
            try:
                candidate = self._deps.resolver.resolve_movie(query, parsed.year)
            except Exception as exc:
                self._resolve_errors_by_path[row.source_path] = f"resolve_movie failed: {exc}"
                continue
            if candidate is None:
                # No candidate found is itself a surfaced failure: a
                # silent "no match" gets the user no signal at all,
                # which is the bug the round-2 review caught. Record
                # the miss against the row so the Errors pane shows it.
                self._resolve_errors_by_path[row.source_path] = (
                    f"no candidate matched movie query {query!r}"
                )
                continue
            self._model.set_candidate(row.source_path, candidate)

        # ----- TV: grouped by show name hint -----------------------------
        tv_groups: dict[str, list[ItemRow]] = {}
        for row in tv_rows:
            key = row.show_name_hint or row.parsed.title_candidate or row.parsed.raw_filename
            tv_groups.setdefault(key, []).append(row)

        for show_name, group_rows in tv_groups.items():
            first = group_rows[0]
            try:
                candidate = self._deps.resolver.resolve_tv(show_name, first.parsed.year)
            except Exception as exc:
                for r in group_rows:
                    self._resolve_errors_by_path[r.source_path] = f"resolve_tv failed: {exc}"
                continue
            if candidate is None:
                # Surface the miss against every row in the group so
                # the user sees that the show search produced nothing
                # — silent unresolved was the round-2 bug.
                for r in group_rows:
                    self._resolve_errors_by_path[r.source_path] = (
                        f"no candidate matched TV query {show_name!r}"
                    )
                continue
            # Hydrate the union of unique seasons in this group so a
            # multi-season drop (Show/s1/ + Show/s2/) merges both
            # season's episode_list into a single Candidate. Every row
            # in the group then carries the same merged Candidate; the
            # downstream planner disambiguates by (season, episode) on
            # each Episode entry.
            if candidate.anchor_kind == "tmdb":
                seasons = {r.parsed.season for r in group_rows if r.parsed.season is not None}
                merged = self._hydrate_seasons(candidate, seasons, group_rows)
            else:
                merged = candidate
            for r in group_rows:
                self._model.set_candidate(r.source_path, merged)

        # Always emit so a successful resolve pass clears any prior
        # error state on the UI even when there are no current errors.
        self.resolve_errors_changed.emit(self._errors_snapshot())

    def parse_and_resolve(self, input_root: Path) -> list[ItemRow]:
        """Convenience: parse + filter + resolve in one call.

        Returns the list of :class:`ItemRow` (movie/tv only, unknowns and
        skips dropped). The orchestrator pushes them into the model and
        runs resolution. The MainWindow can subscribe to ``rows_reset``
        if it wants to react to the population.

        Each TV row gets a ``show_name_hint`` derived from the path tree
        via :func:`derive_show_name`. Episode files shaped like
        ``[S01.E01] Title.mp4`` leave ``title_candidate`` empty (the
        episode title belongs in ``episode_title``); the hint is the
        show name pulled from the closest non-season-folder ancestor or
        from ``input_root`` itself.
        """
        parsed_list = self.parse(input_root)
        rows: list[ItemRow] = []
        for p in parsed_list:
            if p.kind == "unknown" or p.skip_reason is not None:
                continue
            show_hint = derive_show_name(input_root, p.parent_dirs) if p.kind == "tv" else None
            rows.append(ItemRow(parsed=p, show_name_hint=show_hint))
        self._model.set_rows(rows)
        self.resolve_rows(rows)
        return rows

    def _hydrate_tv_season(
        self,
        candidate: Candidate,
        season_hint: int | None,
        affected_rows: list[ItemRow] | None = None,
    ) -> Candidate:
        """Fetch season episodes for a TMDB-anchored TV candidate.

        Returns a NEW Candidate with the populated ``episode_list``. Any
        TMDB error is recorded against every row in ``affected_rows`` (so
        the Errors pane surfaces the failure) and the original candidate
        is returned unchanged — the planner falls back to filename hints
        downstream. The caller is expected to emit
        ``resolve_errors_changed`` after the hydrate pass so the recorded
        failures reach the UI.
        """
        season = season_hint if season_hint is not None else 1
        try:
            tmdb_id = int(candidate.anchor_id)
        except (TypeError, ValueError):
            return candidate
        try:
            episodes = self._deps.tmdb.get_season(tmdb_id, season)
        except Exception as exc:
            if affected_rows:
                for r in affected_rows:
                    self._resolve_errors_by_path[r.source_path] = (
                        f"get_season(season={season}) failed: {exc}"
                    )
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

    def _hydrate_seasons(
        self,
        candidate: Candidate,
        seasons: set[int],
        affected_rows: list[ItemRow],
    ) -> Candidate:
        """Merge episode lists for every season in ``seasons`` into one Candidate.

        Returns a NEW Candidate whose ``episode_list`` is the union of
        each season's episodes, sorted by ``(season, episode)`` so
        downstream title-fuzzy matching has the full multi-season list.
        Falls back to single-season hydration (season 1) when
        ``seasons`` is empty. Per-season failures are recorded against
        ``affected_rows`` via :meth:`_hydrate_tv_season`; the merge
        proceeds with whatever episodes did come back.
        """
        if not seasons:
            return self._hydrate_tv_season(candidate, None, affected_rows)

        merged: list[Episode] = []
        for season in sorted(seasons):
            hydrated = self._hydrate_tv_season(candidate, season, affected_rows)
            if hydrated.episode_list:
                merged.extend(hydrated.episode_list)

        if not merged:
            return candidate

        merged.sort(key=lambda e: (e.season, e.episode))
        return Candidate(
            anchor_kind=candidate.anchor_kind,
            anchor_id=candidate.anchor_id,
            kind=candidate.kind,
            title=candidate.title,
            year=candidate.year,
            confidence=candidate.confidence,
            episode_list=tuple(merged),
        )

    def last_resolve_errors(self) -> list[tuple[Path, str]]:
        """Return the current per-path resolver errors as a list.

        Snapshot of ``_resolve_errors_by_path``: ``[(path, message),
        ...]`` in insertion order. Tests inspect this without needing to
        subscribe to ``resolve_errors_changed``.
        """
        return self._errors_snapshot()

    def _errors_snapshot(self) -> list[tuple[Path, str]]:
        """Internal helper: convert the dict to a list for emission/inspection."""
        return [(p, msg) for p, msg in self._resolve_errors_by_path.items()]

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
        """Resolve an IMDb tt-id to a Candidate and store it on the row.

        Clears any prior error attached to ``source_path`` at the start
        so a successful re-resolve doesn't leave stale messages showing.
        """
        row = self._model.row_for(source_path)
        if row is None:
            return
        self._resolve_errors_by_path.pop(source_path, None)
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
            candidate = self._hydrate_tv_season(candidate, row.parsed.season, [row])
        self._model.set_candidate(source_path, candidate)
        self.resolve_errors_changed.emit(self._errors_snapshot())

    def on_group_clicked(self, group_key: str) -> None:
        """Open the show-anchor picker for the group, populate TMDB results.

        The picker is created via the dep-injected factory so tests can
        substitute a fake. We pre-seed search results using the group's
        SHOW name (from ``ItemRow.show_name_hint``) — NOT the first
        row's ``title_candidate`` which for episode-shaped filenames
        like ``[S01.E01] Goodbye Cruel World.mp4`` is empty (the
        episode title sits in ``episode_title``). Searching TMDB with
        the episode title produces zero or wrong hits; searching with
        the show name is what the user expects.

        Any prior error attached to one of the affected rows is cleared
        at the start so a successful re-search doesn't leave stale
        messages in the Errors pane.
        """
        rows = self._rows_in_group(group_key)
        if not rows:
            return
        # Clear stale errors for every path this call touches before
        # attempting the search; re-emit the dict whether the search
        # succeeds or fails.
        for r in rows:
            self._resolve_errors_by_path.pop(r.source_path, None)

        first = rows[0]
        title_hint = first.show_name_hint or first.parsed.title_candidate or ""
        year_hint = first.parsed.year
        try:
            shows = self._deps.tmdb.search_tv(title_hint, year_hint)
        except Exception as exc:
            shows = []
            for r in rows:
                self._resolve_errors_by_path[r.source_path] = f"search_tv failed: {exc}"
        self.resolve_errors_changed.emit(self._errors_snapshot())
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
        # Local relevance re-rank so exact / prefix matches outrank
        # TMDB's default popularity order. The user typing "Lazarus"
        # expects the exact title first, not "The Lazarus Project".
        candidates = rank_candidates(title_hint, candidates)

        # Fuzzy fallback: when the auto-seeded query produced nothing,
        # walk the cleaned variants (strip trailing _N, parenthesized
        # suffixes, leading "The ") until one returns results. The
        # picker surfaces a notice naming the variant that succeeded so
        # the user understands why the search box shows a different
        # query than the folder name.
        fallback_original: str | None = None
        fallback_used: str | None = None
        search_box_query = title_hint
        if not candidates:
            for variant in cleaned_query_variants(title_hint)[1:]:
                try:
                    retry_shows = self._deps.tmdb.search_tv(variant, None)
                except Exception:
                    retry_shows = []
                if retry_shows:
                    retry_candidates = [
                        Candidate(
                            anchor_kind="tmdb",
                            anchor_id=str(s.tmdb_id),
                            kind="tv",
                            title=s.title,
                            year=s.year,
                            confidence=0.7,
                        )
                        for s in retry_shows
                    ]
                    candidates = rank_candidates(variant, retry_candidates)
                    fallback_original = title_hint
                    fallback_used = variant
                    search_box_query = variant
                    break

        picker = self._deps.picker_factory(group_key)
        # Pre-populate the search box with the show name hint so the user
        # sees what the auto-seed query was, and can edit it directly
        # when the seeded results don't include the right show. When a
        # fallback fired, show the cleaned variant in the box instead so
        # it matches what actually produced the results.
        if hasattr(picker, "set_search_text"):
            picker.set_search_text(search_box_query)
        picker.set_results(candidates)
        if fallback_used is not None and hasattr(picker, "set_fallback_notice"):
            picker.set_fallback_notice(fallback_original or "", fallback_used)
        elif hasattr(picker, "set_fallback_notice"):
            picker.set_fallback_notice("", "")
        picker.show_chosen.connect(self.on_show_chosen)
        # Hook up the picker's interactive search box. When the user
        # types a different query, we re-fire TMDB and push results
        # back into the picker in place.
        if hasattr(picker, "search_requested"):
            picker.search_requested.connect(self.on_picker_search)
        self._open_picker = picker
        picker.exec()

    def on_picker_search(self, group_key: str, query: str) -> None:
        """Re-query TMDB on behalf of an open picker and push results back.

        Triggered by :attr:`ShowAnchorPicker.search_requested` when the
        user types a different show name and clicks Search. The query
        runs against the same ``search_tv`` collaborator as the
        auto-seed; results land back on the picker via
        :meth:`set_results`. Search failures are surfaced as resolver
        errors against every row in the group AND the picker is left
        with an empty list (the empty-label hint informs the user).
        """
        rows = self._rows_in_group(group_key)
        for r in rows:
            self._resolve_errors_by_path.pop(r.source_path, None)
        try:
            shows = self._deps.tmdb.search_tv(query, None)
        except Exception as exc:
            shows = []
            for r in rows:
                self._resolve_errors_by_path[r.source_path] = f"search_tv failed: {exc}"
        self.resolve_errors_changed.emit(self._errors_snapshot())
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
        # Same relevance re-rank as the auto-seed path so the user's
        # typed query is ranked by query-relevance, not TMDB popularity.
        candidates = rank_candidates(query, candidates)
        if self._open_picker is not None and self._open_picker.group_key() == group_key:
            # User-driven search: their query is authoritative; clear
            # any auto-seed fallback notice from the prior render.
            if hasattr(self._open_picker, "set_fallback_notice"):
                self._open_picker.set_fallback_notice("", "")
            self._open_picker.set_results(candidates)

    def on_show_chosen(self, group_key: str, candidate: Candidate) -> None:
        """Apply the picked show to every row in the group.

        After the user picks a show, fetch the relevant seasons (every
        unique season present in the group) and push the merged
        candidate onto every row in the group so the planner has the
        full episode list it needs to match by title — multi-season
        drops are common (``Show/s1/`` + ``Show/s2/``) and a single-
        season hydration would leave higher-season rows without their
        episode_list.

        Any prior error attached to a row in this group is cleared
        before the hydration pass so a successful re-pick doesn't leave
        stale messages showing.
        """
        rows = self._rows_in_group(group_key)
        if not rows:
            return
        for r in rows:
            self._resolve_errors_by_path.pop(r.source_path, None)

        seasons = {r.parsed.season for r in rows if r.parsed.season is not None}
        hydrated = self._hydrate_seasons(candidate, seasons, rows)
        for row in rows:
            self._model.set_candidate(row.source_path, hydrated)
        self.resolve_errors_changed.emit(self._errors_snapshot())
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

    def _on_parsed_inputs(self, _count: int) -> None:
        """Run resolution after the drop handler seats the rows.

        ``MainWindow._on_paths_dropped`` builds ``ItemRow`` instances
        from the ``ParseResult`` list returned by ``_parse_fn`` and
        emits ``parsed_inputs``. This slot back-fills each TV row's
        ``show_name_hint`` from the path tree (the drop handler doesn't
        know about derive_show_name) and then resolves every row's
        candidate via the IMDb-fallback resolver so the badges + target
        column populate without the drop handler having to know about
        the resolver at all.
        """
        rows = self._model.rows()
        input_root: Path | None = None
        if self._main_window is not None:
            input_root = self._main_window.input_root()  # type: ignore[attr-defined]
        for row in rows:
            if row.parsed.kind != "tv" or row.show_name_hint is not None:
                continue
            root = input_root if input_root is not None else row.parsed.source_path.parent
            row.show_name_hint = derive_show_name(root, row.parsed.parent_dirs)
        # The drop handler emitted ``rows_reset`` BEFORE this backfill,
        # so the source panel rendered group labels with the stale (None)
        # show_name_hint -- a TV row whose only show name lives on a
        # parent dir shows the episode title as the group label. Force a
        # rebuild now that the hints exist. If TMDB resolution succeeds
        # the subsequent ``set_candidate`` calls will trigger another
        # row-level refresh; if resolution fails, the rebuild here is the
        # only chance the panel has to display the correct group label.
        self._model.notify_rows_reset()
        self.resolve_rows(rows)

    def _build_plan_from_model(
        self, item_model: ItemModel, input_root: Path
    ) -> tuple[RenamePlan, list[tuple[Path, str]]]:
        """Build a RenamePlan from the model's current state.

        Returns the plan plus the skipped list (user-skipped rows). Rows
        without a candidate fall through to ``build_plan_from_pairs``,
        which classifies them as ``unresolved``.
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
        return plan, skipped

    def preview(self, item_model: ItemModel, input_root: Path) -> RenamePlan:
        """Build a plan without applying it.

        Populates the model's proposed ops (so the target panel renders
        the proposed paths) and the collision model (so the collision
        review panel surfaces conflicts). Returns the plan; callers can
        inspect it for tests.
        """
        plan, _ = self._build_plan_from_model(item_model, input_root)
        for op in plan.ops:
            item_model.set_proposed_op(op.source, op)
        if self._main_window is not None and hasattr(self._main_window, "collision_model"):
            collision_model = self._main_window.collision_model()  # type: ignore[attr-defined]
            collision_model.set_collisions(plan.collisions)
        return plan

    def prepare_apply(
        self, item_model: ItemModel, input_root: Path
    ) -> tuple[RenamePlan | None, RunReport | None]:
        """Build the plan and handle collisions; return either a ready Plan or an early-exit RunReport.

        This is the GUI-thread-safe part of an apply pass: it mutates
        ``item_model`` (proposed-op stamps, collision model) and either
        returns a resolved :class:`RenamePlan` ready for the executor,
        OR a :class:`RunReport` that the GUI should render directly
        (unresolved collisions blocking the apply).

        Exactly one of the two tuple slots is non-None.

        Splitting this out from :meth:`apply` lets the GUI build the
        plan on the main thread (model mutation must stay there) then
        hand the resolved plan to a worker thread that consumes the
        streaming :func:`apply_plan_iter` for live progress events.
        """
        plan, _ = self._build_plan_from_model(item_model, input_root)

        collision_model = None
        if self._main_window is not None and hasattr(self._main_window, "collision_model"):
            collision_model = self._main_window.collision_model()  # type: ignore[attr-defined]

        if plan.collisions:
            actions = self._collision_actions_for(plan.collisions, collision_model)
            unresolved = [c for c in plan.collisions if actions.get(c.target) is None]
            if unresolved:
                if collision_model is not None:
                    collision_model.set_collisions(plan.collisions)
                for op in plan.ops:
                    item_model.set_proposed_op(op.source, op)
                early = RunReport(
                    succeeded=0,
                    skipped=len(plan.skipped),
                    errored=0,
                    journal_path=None,
                    error_messages=(
                        "Unresolved collisions; resolve in the review panel before applying.",
                    ),
                )
                return None, early
            plan = self._apply_collision_actions(plan, actions)

        for op in plan.ops:
            item_model.set_proposed_op(op.source, op)
        if collision_model is not None:
            collision_model.set_collisions(())

        return plan, None

    def build_run_report(self, plan: RenamePlan, result: object) -> RunReport:
        """Translate an executor :class:`ApplyResult` into the GUI's :class:`RunReport`.

        Reads the journal to surface per-op error messages. Used by both
        :meth:`apply` (synchronous path) and the GUI's worker thread
        (after the streaming :func:`apply_plan_iter` yields its final
        ``done`` event).
        """
        error_messages: list[str] = []
        try:
            journal = Journal.load(result.journal_path)  # type: ignore[attr-defined]
        except (OSError, ValueError):
            journal = None
        if journal is not None:
            for entry in journal.entries:
                if entry.status == "failed" and entry.error:
                    error_messages.append(entry.error)
        return RunReport(
            succeeded=result.succeeded,  # type: ignore[attr-defined]
            skipped=len(plan.skipped),
            errored=result.failed,  # type: ignore[attr-defined]
            journal_path=result.journal_path,  # type: ignore[attr-defined]
            error_messages=tuple(error_messages),
        )

    def apply_journal_dir(self) -> Path:
        """Journal directory the executor writes into.

        Exposed for the GUI's worker thread, which calls
        :func:`apply_plan_iter` directly and needs the same path the
        synchronous :meth:`apply` would have used.
        """
        return self._deps.journal_dir

    def apply_cleanup_enabled(self) -> bool:
        """Whether the deps want cleanup to run after a successful apply."""
        return self._deps.cleanup_enabled

    def apply(self, item_model: ItemModel, input_root: Path) -> RunReport:
        """Synchronous apply that returns a :class:`RunReport`.

        Thin wrapper around :meth:`prepare_apply` + the executor's
        synchronous :func:`apply_plan`. The Qt GUI now goes through
        :meth:`prepare_apply` + a worker thread iterating
        :func:`apply_plan_iter`; this method stays as the test surface
        and the headless entry point.
        """
        plan, early = self.prepare_apply(item_model, input_root)
        if early is not None:
            return early
        assert plan is not None  # prepare_apply contract

        result = apply_plan(
            plan,
            journal_dir=self._deps.journal_dir,
            cleanup=self._deps.cleanup_enabled,
            verify_hash=False,
        )
        return self.build_run_report(plan, result)

    @staticmethod
    def _collision_actions_for(collisions, collision_model) -> dict:
        """Return ``{target: action}`` for current collisions from the model.

        Targets not present in the collision model map to ``None``,
        which the apply path treats as "unresolved — bail out".
        """
        actions: dict = {c.target: None for c in collisions}
        if collision_model is None:
            return actions
        for item in collision_model.items():
            if item.target in actions:
                actions[item.target] = item.action
        return actions

    def _apply_collision_actions(self, plan, actions: dict):
        """Return a new plan with per-collision actions consumed.

        Action semantics (mirrors the brief):

        * ``keep_first`` — first source keeps the target; later sources
          drop out of the run.
        * ``keep_both`` — first source keeps the target; later sources
          get a ``_2``/``_3``/... stem suffix injected.
        * ``reanchor`` — the user is expected to have updated the
          source's anchor via the edit pane before the second Apply.
          Drop the colliding sources from this run; they re-surface on
          the next Preview if the user actually changed the anchor (in
          which case the rebuilt plan won't collide), or as
          ``unresolved`` if not.
        """
        # Pre-build a (source -> (parsed, candidate)) lookup so we can
        # rebuild ops for sources that were stripped by detect_collisions.
        from plex_renamer.planner.collision import detect_collisions

        # Rebuild the "raw" op list (including the colliding ops) by
        # calling the lower-level builders with the same pairs the plan
        # was built from. We don't have direct access to the pre-strip
        # ops, so we look up each colliding source's row in the model
        # and synthesize the same op shape via build_plan_from_pairs on
        # a per-collision basis. Simpler: just keep the collisions
        # surfaced as-is and post-process targets here.

        new_ops = list(plan.ops)
        consumed_collisions: list = []

        # We need the original ops for each collision source so we can
        # rewrite their targets per action. Rebuild them via a minimal
        # synthesis: each collision tells us the target + sources; we
        # adopt the SHARED target as the canonical, and synthesize one
        # op per source with that target (or a suffix variant). Anchor
        # / kind / confidence aren't accessible from the Collision
        # alone, so we re-pull them from the model's rows.
        for col in plan.collisions:
            action = actions.get(col.target)
            if action is None:
                continue
            consumed_collisions.append(col)
            ops_for_col = self._rebuild_ops_for_collision(col, plan)
            if action == "keep_first" and ops_for_col:
                new_ops.append(ops_for_col[0])
            elif action == "keep_both":
                for i, op in enumerate(ops_for_col):
                    if i == 0:
                        new_ops.append(op)
                    else:
                        new_ops.append(self._suffix_op_target(op, i + 1))
            elif action == "reanchor":
                # Drop these sources from this run. The user is meant
                # to re-anchor and re-Preview.
                continue

        # detect_collisions a second time over new_ops in case the
        # suffix path introduced a new conflict (unlikely but safe).
        clean_ops, residual_collisions = detect_collisions(new_ops)
        remaining_collisions = tuple(
            c for c in plan.collisions if c not in consumed_collisions
        ) + tuple(residual_collisions)
        from plex_renamer.planner.models import RenamePlan

        return RenamePlan(
            ops=tuple(clean_ops),
            collisions=remaining_collisions,
            skipped=plan.skipped,
            movies_root=plan.movies_root,
            tv_root=plan.tv_root,
            input_root=plan.input_root,
            apply_editions=plan.apply_editions,
            warnings=plan.warnings,
        )

    def _rebuild_ops_for_collision(self, collision, plan) -> list:
        """Return a list of synthesized RenameOps, one per collision source.

        Each op uses ``collision.target`` as its target and pulls
        anchor/kind/confidence from the model's row for that source.
        Sources without a candidate are dropped (they were unresolved).
        """
        from plex_renamer.planner.models import RenameOp
        from plex_renamer.planner.movie_path import render_anchor

        ops: list[RenameOp] = []
        for source in collision.sources:
            row = self._model.row_for(source)
            if row is None or row.candidate is None:
                continue
            anchor = render_anchor(row.candidate)
            ops.append(
                RenameOp(
                    source=source,
                    target=collision.target,
                    kind=row.candidate.kind,  # type: ignore[arg-type]
                    anchor=anchor,
                    edition=None,
                    confidence=row.candidate.confidence,
                )
            )
        return ops

    @staticmethod
    def _suffix_op_target(op, idx: int):
        """Return a new op with ``_<idx>`` injected before the extension.

        ``idx`` is 1-based for the user's mental model — the first
        kept-both sibling is the canonical target (no suffix); ``idx=2``
        for the second, etc.
        """
        from dataclasses import replace

        target = op.target
        stem = target.stem
        suffix = target.suffix
        new_target = target.with_name(f"{stem}_{idx}{suffix}")
        return replace(op, target=new_target)

    # ----- Helpers --------------------------------------------------------

    def _rows_in_group(self, group_key: str) -> list[ItemRow]:
        groups = self._model.groups()
        return list(groups.get(group_key, []))


__all__ = [
    "Orchestrator",
    "OrchestratorDeps",
    "ShowAnchorPickerFactory",
]
