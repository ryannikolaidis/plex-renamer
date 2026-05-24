"""Orchestrator wiring: engine surfaces driven through the GUI seams.

The orchestrator binds parsing, TMDB resolution, plan-building, apply,
and undo to the :class:`MainWindow`'s models and signals. These tests
verify the wiring against fake collaborators so no real TMDB or
filesystem traffic happens.

Coverage:

* resolve pass populates :class:`ItemModel` rows with :class:`Candidate`.
* season hydration for TV runs after a movie/TV resolve.
* ``on_tmdb_search`` queries the client and posts results back to the
  edit pane.
* ``on_imdb_resolve`` calls ``find_by_imdb_id`` and stores the result.
* ``on_show_chosen`` propagates the picked Candidate to every group row
  and fetches the season episode list.
* ``on_undo_requested`` loads the journal and runs the engine's undo.
* ``apply`` builds a plan from the model and dispatches ``apply_plan``
  with the expected args, returning a :class:`RunReport`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")


# --- Fake collaborators ----------------------------------------------------


class FakeTMDB:
    """In-memory TMDB stub matching the client/cache method shape."""

    def __init__(self) -> None:
        from plex_renamer.tmdb.models import Episode, MovieResult, TVResult

        self.search_movie_returns: list[MovieResult] = []
        self.search_tv_returns: list[TVResult] = []
        self.find_returns: MovieResult | TVResult | None = None
        self.get_season_returns: list[Episode] = []
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def search_movie(self, title: str, year: int | None):
        self._record("search_movie", title, year)
        return list(self.search_movie_returns)

    def search_tv(self, title: str, year: int | None):
        self._record("search_tv", title, year)
        return list(self.search_tv_returns)

    def find_by_imdb_id(self, imdb_id: str):
        self._record("find_by_imdb_id", imdb_id)
        return self.find_returns

    def get_season(self, tmdb_id: int, season: int):
        self._record("get_season", tmdb_id, season)
        return list(self.get_season_returns)


class FakePicker:
    """ShowAnchorPicker stand-in. Records calls; never opens a dialog."""

    def __init__(self, group_key: str) -> None:
        self.group_key = group_key
        self.results: list = []
        self.execed = False
        self._chosen_handlers: list = []

    def set_results(self, candidates: list) -> None:
        self.results = list(candidates)

    @property
    def show_chosen(self):
        # Minimal Signal-like shim — orchestrator calls ``connect``.
        outer = self

        class _Sig:
            def connect(self, fn):
                outer._chosen_handlers.append(fn)

            def emit(self, *args, **kwargs):
                for h in outer._chosen_handlers:
                    h(*args, **kwargs)

        return _Sig()

    def exec(self) -> int:
        self.execed = True
        return 0


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def deps(tmp_path):
    """Build an OrchestratorDeps with a FakeTMDB + real resolver."""
    from plex_renamer.gui.orchestrator import OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    tmdb = FakeTMDB()
    resolver = IMDbFallbackResolver(tmdb, omdb_api_key=None)
    return OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=tmp_path / "movies",
        tv_root=tmp_path / "tv",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
        picker_factory=FakePicker,
    )


# --- Tests -----------------------------------------------------------------


def test_resolve_populates_movie_candidate(deps) -> None:
    """Resolving a movie row stores the resulting Candidate on the model."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import MovieResult

    deps.tmdb.search_movie_returns = [MovieResult(tmdb_id=603, title="The Matrix", year=1999)]
    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/Matrix.mkv"),
            kind="movie",
            title_candidate="The Matrix",
            year=1999,
            raw_filename="Matrix.mkv",
        )
    )
    model.set_rows([row])

    orch = Orchestrator(model, deps)
    orch.resolve_rows([row])

    updated = model.row_for(row.source_path)
    assert updated.candidate is not None
    assert updated.candidate.anchor_id == "603"
    assert updated.candidate.kind == "movie"


def test_resolve_populates_tv_candidate_with_season(deps) -> None:
    """Resolving a TV row hydrates the episode_list from get_season."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import Episode, TVResult

    deps.tmdb.search_tv_returns = [TVResult(tmdb_id=1234, title="Foo", year=2020)]
    deps.tmdb.get_season_returns = [
        Episode(season=1, episode=1, title="Pilot"),
        Episode(season=1, episode=2, title="Second"),
    ]
    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/Foo.S01E01.mkv"),
            kind="tv",
            title_candidate="Foo",
            year=2020,
            season=1,
            episode=1,
            raw_filename="Foo.S01E01.mkv",
        )
    )
    model.set_rows([row])

    orch = Orchestrator(model, deps)
    orch.resolve_rows([row])

    updated = model.row_for(row.source_path)
    assert updated.candidate is not None
    assert updated.candidate.anchor_id == "1234"
    assert updated.candidate.episode_list is not None
    assert len(updated.candidate.episode_list) == 2
    # get_season was called with the season hint from the parsed row.
    assert ("get_season", (1234, 1), {}) in deps.tmdb.calls


def test_parse_and_resolve_filters_unknown_and_skipped(deps, tmp_path) -> None:
    """parse_and_resolve skips kind=unknown and skip-reason rows."""
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult, SkipReason

    parsed = [
        ParseResult(
            source_path=tmp_path / "ok.mkv",
            kind="movie",
            title_candidate="OK",
            year=2020,
            raw_filename="ok.mkv",
        ),
        ParseResult(
            source_path=tmp_path / "unknown.txt",
            kind="unknown",
            raw_filename="unknown.txt",
        ),
        ParseResult(
            source_path=tmp_path / "junk.tmp",
            kind="movie",
            title_candidate="Junk",
            raw_filename="junk.tmp",
            skip_reason=SkipReason(reason="non_media_extension", detail=".tmp"),
        ),
    ]

    model = ItemModel()
    orch = Orchestrator(model, deps)
    # Bypass parse_tree by monkey-patching the helper.
    orch.parse = lambda root: parsed
    rows = orch.parse_and_resolve(tmp_path)

    assert len(rows) == 1
    assert rows[0].parsed.title_candidate == "OK"


def test_on_tmdb_search_posts_results_to_edit_pane(qtbot, deps) -> None:
    """on_tmdb_search calls TMDB and pushes the candidates back to the edit pane."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import MovieResult, TVResult

    deps.tmdb.search_movie_returns = [MovieResult(tmdb_id=1, title="Foo", year=2020)]
    deps.tmdb.search_tv_returns = [TVResult(tmdb_id=2, title="Foo (TV)", year=2020)]

    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/Foo.mkv"),
            kind="movie",
            title_candidate="Foo",
            year=2020,
            raw_filename="Foo.mkv",
        )
    )
    model.set_rows([row])

    # We need a real edit pane to verify the set_tmdb_results call.
    from plex_renamer.gui.edit_pane import EditPane

    edit_pane = EditPane(model)
    qtbot.addWidget(edit_pane)
    edit_pane.load_row(row.source_path)

    class _Window:
        def edit_pane(self):
            return edit_pane

    orch = Orchestrator(model, deps, main_window=_Window())
    orch.on_tmdb_search(row.source_path, "Foo")

    # Both search_movie and search_tv were consulted.
    assert any(c[0] == "search_movie" for c in deps.tmdb.calls)
    assert any(c[0] == "search_tv" for c in deps.tmdb.calls)
    # The combined candidate list landed on the edit pane.
    assert len(edit_pane._tmdb_panel._candidates) == 2


def test_on_imdb_resolve_stores_candidate(deps) -> None:
    """on_imdb_resolve calls find_by_imdb_id and stores the Candidate."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import MovieResult

    deps.tmdb.find_returns = MovieResult(tmdb_id=42, title="X", year=2010)

    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/X.mkv"),
            kind="movie",
            title_candidate="X",
            year=2010,
            raw_filename="X.mkv",
        )
    )
    model.set_rows([row])

    orch = Orchestrator(model, deps)
    orch.on_imdb_resolve(row.source_path, "tt0000042")

    assert ("find_by_imdb_id", ("tt0000042",), {}) in deps.tmdb.calls
    updated = model.row_for(row.source_path)
    assert updated.candidate is not None
    assert updated.candidate.anchor_id == "42"


def test_on_imdb_resolve_synthesizes_when_tmdb_misses(deps) -> None:
    """When TMDB has no record, an IMDb-anchored Candidate is synthesized."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult

    deps.tmdb.find_returns = None

    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/Indie.mkv"),
            kind="movie",
            title_candidate="Indie",
            year=2010,
            raw_filename="Indie.mkv",
        )
    )
    model.set_rows([row])

    orch = Orchestrator(model, deps)
    orch.on_imdb_resolve(row.source_path, "tt9999999")

    updated = model.row_for(row.source_path)
    assert updated.candidate is not None
    assert updated.candidate.anchor_kind == "imdb"
    assert updated.candidate.anchor_id == "tt9999999"


def test_on_show_chosen_propagates_to_group(deps) -> None:
    """Picking a show pushes the hydrated Candidate onto every row in the group."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import Candidate, Episode

    deps.tmdb.get_season_returns = [
        Episode(season=1, episode=1, title="Pilot"),
        Episode(season=1, episode=2, title="Two"),
    ]

    model = ItemModel()
    rows = [
        ItemRow(
            parsed=ParseResult(
                source_path=Path(f"/in/Foo.S01E0{i}.mkv"),
                kind="tv",
                title_candidate="Foo",
                year=2020,
                season=1,
                episode=i,
                raw_filename=f"Foo.S01E0{i}.mkv",
            )
        )
        for i in (1, 2)
    ]
    model.set_rows(rows)

    chosen = Candidate(
        anchor_kind="tmdb",
        anchor_id="555",
        kind="tv",
        title="Foo",
        year=2020,
        confidence=0.9,
    )
    orch = Orchestrator(model, deps)
    group_key = rows[0].group_key
    orch.on_show_chosen(group_key, chosen)

    for r in model.rows():
        assert r.candidate is not None
        assert r.candidate.anchor_id == "555"
        assert r.candidate.episode_list is not None
        assert len(r.candidate.episode_list) == 2


def test_on_undo_requested_loads_journal_and_calls_undo(deps, tmp_path, monkeypatch) -> None:
    """on_undo_requested loads the journal at the given path and runs undo_batch."""
    from plex_renamer.executor.journal import Journal
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.orchestrator import Orchestrator

    journal = Journal.new(
        input_root=tmp_path / "in",
        library_root=tmp_path / "lib",
        journal_dir=tmp_path / "journals",
    )

    received = {"called": False, "journal_obj": None}

    def fake_undo(j):
        received["called"] = True
        received["journal_obj"] = j

        class _R:
            reverted = 0
            moved_to_review = 0
            review_dir = None
            sources_recoverable = True

        return _R()

    # Patch the orchestrator's undo_batch reference.
    monkeypatch.setattr("plex_renamer.gui.orchestrator.undo_batch", fake_undo)

    model = ItemModel()
    orch = Orchestrator(model, deps)
    orch.on_undo_requested(journal.path)

    assert received["called"] is True
    assert received["journal_obj"] is not None
    assert received["journal_obj"].batch_id == journal.batch_id


def test_apply_builds_plan_and_calls_apply_plan(deps, monkeypatch, tmp_path) -> None:
    """apply pulls (parsed, candidate) pairs from the model and calls apply_plan."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import Candidate

    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/X.mkv"),
            kind="movie",
            title_candidate="X",
            year=2010,
            raw_filename="X.mkv",
        ),
        candidate=Candidate(
            anchor_kind="tmdb",
            anchor_id="42",
            kind="movie",
            title="X",
            year=2010,
            confidence=0.9,
        ),
    )
    model.set_rows([row])

    captured: dict[str, Any] = {}

    def fake_apply_plan(plan, *, journal_dir=None, cleanup=False, verify_hash=False, **kwargs):
        captured["plan"] = plan
        captured["journal_dir"] = journal_dir
        captured["cleanup"] = cleanup
        captured["verify_hash"] = verify_hash

        # Mimic ApplyResult with a journal_path that loads cleanly.
        from plex_renamer.executor.journal import Journal

        journal = Journal.new(
            input_root=plan.input_root,
            library_root=plan.movies_root,
            journal_dir=tmp_path / "journals",
        )

        class _R:
            succeeded = 1
            failed = 0
            cleanup_ran = False
            journal_path = journal.path

        return _R()

    monkeypatch.setattr("plex_renamer.gui.orchestrator.apply_plan", fake_apply_plan)

    orch = Orchestrator(model, deps)
    report = orch.apply(model, input_root=Path("/in"))

    assert captured["plan"] is not None
    assert len(captured["plan"].ops) == 1
    assert captured["plan"].ops[0].source == Path("/in/X.mkv")
    assert captured["cleanup"] is False
    assert captured["verify_hash"] is False
    assert captured["journal_dir"] == deps.journal_dir

    assert report.succeeded == 1
    assert report.errored == 0
    assert report.journal_path is not None


def test_apply_skips_user_skipped_rows(deps, monkeypatch, tmp_path) -> None:
    """Rows flagged ``skip=True`` are dropped from the apply pass."""
    from plex_renamer.gui.models import ItemModel, ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator
    from plex_renamer.parser.models import ParseResult

    model = ItemModel()
    row = ItemRow(
        parsed=ParseResult(
            source_path=Path("/in/X.mkv"),
            kind="movie",
            title_candidate="X",
            year=2010,
            raw_filename="X.mkv",
        ),
    )
    row.skip = True
    model.set_rows([row])

    captured: dict[str, Any] = {}

    def fake_apply_plan(plan, **kwargs):
        captured["plan"] = plan
        from plex_renamer.executor.journal import Journal

        journal = Journal.new(
            input_root=plan.input_root,
            library_root=plan.movies_root,
            journal_dir=tmp_path / "journals",
        )

        class _R:
            succeeded = 0
            failed = 0
            cleanup_ran = False
            journal_path = journal.path

        return _R()

    monkeypatch.setattr("plex_renamer.gui.orchestrator.apply_plan", fake_apply_plan)

    orch = Orchestrator(model, deps)
    orch.apply(model, input_root=Path("/in"))

    # No ops because the only row was skipped.
    assert len(captured["plan"].ops) == 0
    # The skipped row appears in the plan's skipped list.
    assert len(captured["plan"].skipped) == 1


def test_connect_wires_main_window_signals(qtbot, deps, gui_settings) -> None:
    """Orchestrator.connect subscribes to every MainWindow signal."""
    from plex_renamer.gui.main_window import MainWindow
    from plex_renamer.gui.orchestrator import Orchestrator

    window = MainWindow(gui_settings)
    qtbot.addWidget(window)
    orch = Orchestrator(window.item_model(), deps, main_window=window)
    orch.connect(window)

    # Emit the MainWindow's re-exposed signals and verify the orchestrator
    # handlers fire. We use a sentinel via monkeypatching the handlers.
    received: dict[str, Any] = {}

    def _capture(name):
        def fn(*args, **kwargs):
            received[name] = (args, kwargs)

        return fn

    orch.on_tmdb_search = _capture("tmdb_search")  # type: ignore[method-assign]
    orch.on_imdb_resolve = _capture("imdb")  # type: ignore[method-assign]
    orch.on_group_clicked = _capture("group")  # type: ignore[method-assign]
    orch.on_reanchor_requested = _capture("reanchor")  # type: ignore[method-assign]
    orch.on_undo_requested = _capture("undo")  # type: ignore[method-assign]

    # Re-connect since we replaced the bound methods after the first connect.
    orch.connect(window)

    window.tmdb_search_requested.emit(Path("/in/x"), "q")
    window.imdb_resolve_requested.emit(Path("/in/x"), "tt1")
    window.group_clicked.emit("tv::Foo")
    window.reanchor_requested.emit(Path("/out/x"))
    window.undone.emit(Path("/journal"))

    assert "tmdb_search" in received
    assert "imdb" in received
    assert "group" in received
    assert "reanchor" in received
    assert "undo" in received
