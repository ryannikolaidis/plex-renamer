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

import re
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
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult

# Matches a directory name that looks like a season folder, not a show
# name. ``s1``, ``S01``, ``Season 5``, ``Series 2``, ``Specials`` are all
# season folders — when we're walking a parent_dirs chain looking for
# the SHOW name, we skip these.
_SEASON_FOLDER_RE = re.compile(
    r"^(s|season\s*|series\s*)\d{1,2}$|^specials$",
    re.IGNORECASE,
)


def derive_show_name(input_root: Path, parent_dirs: list[str]) -> str:
    """Find the most likely TV show name from the path tree.

    Walks ``parent_dirs`` left-to-right (closest to ``input_root`` first),
    returning the first entry that does NOT look like a season folder
    (``s1``, ``S01``, ``Season 1``, ``Series 1``, ``Specials``). Falls
    back to ``input_root.name`` when every ``parent_dirs`` entry is
    season-like — that case covers the user dropping ``MAX/Lazarus/``
    directly, where ``parent_dirs`` is just ``["s1"]`` and the show name
    lives on the drop root itself.
    """
    for d in parent_dirs:
        if not _SEASON_FOLDER_RE.match(d.strip()):
            return d
    return input_root.name


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
        # Per-row resolver errors from the most recent resolve pass.
        # Emitted on ``resolve_errors_changed`` so the run-report
        # surfaces them; absent that subscription, tests can read this
        # directly.
        self._last_resolve_errors: list[tuple[Path, str]] = []

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
        run_report = main_window.run_report_widget()  # type: ignore[attr-defined]
        self.resolve_errors_changed.connect(run_report.set_resolve_errors)

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
        is captured in ``_last_resolve_errors`` and emitted via
        ``resolve_errors_changed`` so the run-report surfaces them
        instead of letting them disappear into a swallowed except clause.
        """
        errors: list[tuple[Path, str]] = []

        movie_rows = [r for r in rows if r.parsed.kind == "movie"]
        tv_rows = [r for r in rows if r.parsed.kind == "tv"]

        # ----- Movies: per-row -------------------------------------------
        for row in movie_rows:
            parsed = row.parsed
            try:
                candidate = self._deps.resolver.resolve_movie(
                    parsed.title_candidate or "", parsed.year
                )
            except Exception as exc:
                errors.append((row.source_path, f"resolve_movie failed: {exc}"))
                continue
            if candidate is None:
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
                    errors.append((r.source_path, f"resolve_tv failed: {exc}"))
                continue
            if candidate is None:
                continue
            # Hydrate ONCE per unique season in the group when the
            # candidate is TMDB-anchored. Each hydrated candidate is
            # cached by season so we don't refetch for repeat seasons.
            hydrated_by_season: dict[int | None, Candidate] = {}
            for r in group_rows:
                season_key = r.parsed.season
                if candidate.anchor_kind == "tmdb":
                    if season_key not in hydrated_by_season:
                        hydrated_by_season[season_key] = self._hydrate_tv_season(
                            candidate, season_key
                        )
                    row_candidate = hydrated_by_season[season_key]
                else:
                    row_candidate = candidate
                self._model.set_candidate(r.source_path, row_candidate)

        # Stash + surface errors. Always emit, even when empty, so a
        # successful resolve pass clears any prior error state on the
        # UI.
        self._last_resolve_errors = errors
        self.resolve_errors_changed.emit(list(errors))

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
        substitute a fake. We pre-seed search results using the group's
        SHOW name (from ``ItemRow.show_name_hint``) — NOT the first
        row's ``title_candidate`` which for episode-shaped filenames
        like ``[S01.E01] Goodbye Cruel World.mp4`` is empty (the
        episode title sits in ``episode_title``). Searching TMDB with
        the episode title produces zero or wrong hits; searching with
        the show name is what the user expects.
        """
        rows = self._rows_in_group(group_key)
        if not rows:
            return
        first = rows[0]
        title_hint = first.show_name_hint or first.parsed.title_candidate or ""
        year_hint = first.parsed.year
        errors: list[tuple[Path, str]] = []
        try:
            shows = self._deps.tmdb.search_tv(title_hint, year_hint)
        except Exception as exc:
            shows = []
            for r in rows:
                errors.append((r.source_path, f"search_tv failed: {exc}"))
            self._last_resolve_errors = errors
            self.resolve_errors_changed.emit(list(errors))
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
        self.resolve_rows(rows)

    def _build_plan_from_model(
        self, item_model: ItemModel, input_root: Path
    ) -> tuple[object, list[tuple[Path, str]]]:
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

    def preview(self, item_model: ItemModel, input_root: Path) -> object:
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

    def apply(self, item_model: ItemModel, input_root: Path) -> RunReport:
        """Build a plan from the model and call ``apply_plan``.

        This is the function the GUI hands to ``MainWindow(apply_fn=)``.
        Rows without a candidate are dropped to the skipped list; rows
        flagged ``skip`` are dropped silently.

        When the freshly-built plan has unresolved collisions, the
        method populates the collision model (so the review widget
        renders the conflicts) and returns a zero-count
        :class:`RunReport` WITHOUT calling :func:`apply_plan`. The
        MainWindow's pre-apply gate refuses the next Apply click until
        the user resolves each collision; the SECOND Apply rebuilds the
        plan applying the per-collision actions and proceeds.
        """
        plan, _ = self._build_plan_from_model(item_model, input_root)

        # Surface collisions on the model. If any are unresolved, bail
        # out without applying — the user resolves via the review panel
        # and clicks Apply again. We update the model so the review
        # widget repaints on the first Apply click rather than waiting
        # for an explicit Preview.
        collision_model = None
        if self._main_window is not None and hasattr(self._main_window, "collision_model"):
            collision_model = self._main_window.collision_model()  # type: ignore[attr-defined]

        if plan.collisions:
            actions = self._collision_actions_for(plan.collisions, collision_model)
            unresolved = [c for c in plan.collisions if actions.get(c.target) is None]
            if unresolved:
                if collision_model is not None:
                    collision_model.set_collisions(plan.collisions)
                # Record proposed ops for the clean (non-colliding) rows
                # so the target panel still renders them while the user
                # works through the conflicts.
                for op in plan.ops:
                    item_model.set_proposed_op(op.source, op)
                return RunReport(
                    succeeded=0,
                    skipped=len(plan.skipped),
                    errored=0,
                    journal_path=None,
                    error_messages=(
                        "Unresolved collisions; resolve in the review panel before applying.",
                    ),
                )
            # Every remaining collision has an action; rebuild the op
            # list applying the user's choices.
            plan = self._apply_collision_actions(plan, actions)

        # Record proposed ops on the model so the target panel + the
        # reanchor lookup find the corresponding row.
        for op in plan.ops:
            item_model.set_proposed_op(op.source, op)
        # Clear the collision model: every collision either resolved
        # (consumed above) or never existed.
        if collision_model is not None:
            collision_model.set_collisions(())

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
