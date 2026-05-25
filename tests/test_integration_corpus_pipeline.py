"""End-to-end integration test: corpus generator -> orchestrator -> plan.

This is the load-bearing test for the parse -> resolve -> plan pipeline.
It drives the slice-2 corpus generator's output (every observed input
pattern + plausible permutations) through the full pipeline with a
HERMETIC mock TMDB (no real network). It catches whole classes of bug
that per-layer unit tests miss:

- Show name derivation from the path tree when the filename leaves
  ``title_candidate`` empty (bracketed ``[S01.E01]`` shape).
- Group label correctness in the source panel (show name, not episode
  filename, not season folder).
- Show-anchor picker query content (show name, not episode title).
- End-to-end Plex path correctness against the canonical shape.

Per-layer parser / planner / GUI tests verify individual components;
this test verifies they COMPOSE correctly. Both layers are mandatory
per ``INVARIANTS.md`` "Testing discipline".

Runs under ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")


# --- Hermetic mock TMDB ---------------------------------------------------


class FakeTMDB:
    """In-memory TMDB stub returning deterministic results for known shows.

    Implements the orchestrator's ``_TMDBLike`` protocol: ``search_movie``,
    ``search_tv``, ``find_by_imdb_id``, ``get_season``. Returns realistic
    candidates for a small set of shows and movies the corpus generator
    emits; everything else returns an empty list so the pipeline's
    "needs review" path is exercised.
    """

    # Known TV shows: keyed by what derive_show_name returns from the
    # corpus tree (i.e. the directory name as authored in patterns.py,
    # case-preserved).
    _TV_SHOWS: dict[str, tuple[int, str, int | None]] = {
        "Game Of Thrones": (1399, "Game of Thrones", 2011),
        "Mad Men": (1104, "Mad Men", 2007),
        "Warehouse 13": (18347, "Warehouse 13", 2009),
        "House Of The Dragon": (94997, "House of the Dragon", 2022),
        # Doctor Who appears in the corpus as a Specials-folder show.
        "Doctor Who": (57243, "Doctor Who", 2005),
    }

    # Known movies: keyed by what the parser puts in title_candidate
    # for the corpus filenames.
    _MOVIES: dict[str, tuple[int, str, int | None]] = {
        "A Field In England": (174349, "A Field in England", 2013),
        "Spaceballs": (11968, "Spaceballs", 1987),
        "The Matrix": (603, "The Matrix", 1999),
        "Inception": (27205, "Inception", 2010),
        "The Godfather": (238, "The Godfather", 1972),
    }

    # Realistic 10-episode list for GoT season 1.
    _GOT_SEASON_1: tuple[tuple[int, int, str], ...] = (
        (1, 1, "Winter Is Coming"),
        (1, 2, "The Kingsroad"),
        (1, 3, "Lord Snow"),
        (1, 4, "Cripples, Bastards, and Broken Things"),
        (1, 5, "The Wolf and the Lion"),
        (1, 6, "A Golden Crown"),
        (1, 7, "You Win or You Die"),
        (1, 8, "The Pointy End"),
        (1, 9, "Baelor"),
        (1, 10, "Fire and Blood"),
    )

    # Realistic 10-episode list for GoT season 2; lets the multi-season
    # hydration test verify the merged Candidate's episode_list spans
    # both seasons.
    _GOT_SEASON_2: tuple[tuple[int, int, str], ...] = (
        (2, 1, "The North Remembers"),
        (2, 2, "The Night Lands"),
        (2, 3, "What Is Dead May Never Die"),
        (2, 4, "Garden of Bones"),
        (2, 5, "The Ghost of Harrenhal"),
        (2, 6, "The Old Gods and the New"),
        (2, 7, "A Man Without Honor"),
        (2, 8, "The Prince of Winterfell"),
        (2, 9, "Blackwater"),
        (2, 10, "Valar Morghulis"),
    )

    # Doctor Who specials (S00) — minimal list so the specials-routing
    # test can fuzzy-match "Time Crash" against an episode_list.
    _DOCTOR_WHO_SPECIALS: tuple[tuple[int, int, str], ...] = (
        (0, 1, "Time Crash"),
        (0, 2, "Music of the Spheres"),
    )

    def __init__(self) -> None:
        # Record every call so tests can introspect what the orchestrator
        # asked for; lets us verify the show-anchor picker sent the
        # SHOW name to TMDB rather than the episode title.
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def search_movie(self, title: str, year: int | None):
        from plex_renamer.tmdb.models import MovieResult

        self.calls.append(("search_movie", (title, year)))
        hit = self._MOVIES.get(title)
        if hit is None:
            return []
        tmdb_id, canon_title, canon_year = hit
        return [MovieResult(tmdb_id=tmdb_id, title=canon_title, year=canon_year)]

    def search_tv(self, title: str, year: int | None):
        from plex_renamer.tmdb.models import TVResult

        self.calls.append(("search_tv", (title, year)))
        hit = self._TV_SHOWS.get(title)
        if hit is None:
            return []
        tmdb_id, canon_title, canon_year = hit
        return [TVResult(tmdb_id=tmdb_id, title=canon_title, year=canon_year)]

    def find_by_imdb_id(self, imdb_id: str):
        self.calls.append(("find_by_imdb_id", (imdb_id,)))
        return None

    def get_season(self, tmdb_id: int, season: int):
        from plex_renamer.tmdb.models import Episode

        self.calls.append(("get_season", (tmdb_id, season)))
        if tmdb_id == 1399 and season == 1:
            return [Episode(season=s, episode=e, title=t) for (s, e, t) in self._GOT_SEASON_1]
        if tmdb_id == 1399 and season == 2:
            return [Episode(season=s, episode=e, title=t) for (s, e, t) in self._GOT_SEASON_2]
        if tmdb_id == 57243 and season == 0:
            return [
                Episode(season=s, episode=e, title=t) for (s, e, t) in self._DOCTOR_WHO_SPECIALS
            ]
        # Realistic-shape minimal episode list for any other known show:
        # one episode per season. This is enough for the planner to
        # synthesize a valid path; the matcher accepts synthetic
        # episodes when the show list is sparse.
        return [Episode(season=season, episode=1, title=f"Episode {season:02d}x01")]


# --- Fixtures -------------------------------------------------------------


@pytest.fixture
def mock_tmdb() -> FakeTMDB:
    return FakeTMDB()


def make_test_settings(tmp_path: Path):
    """Construct an isolated :class:`Settings` whose config file lands in
    ``tmp_path``. The .env path points at a guaranteed-nonexistent file
    so first-run hydration finds nothing.
    """
    from plex_renamer.config.settings import Settings

    cfg = tmp_path / "config.json"
    fake_env = tmp_path / "nonexistent.env"
    return Settings.load(config_path=cfg, dotenv_path=fake_env)


def make_orchestrator(settings, *, tmdb: FakeTMDB, tmp_path: Path):
    """Construct an :class:`Orchestrator` + :class:`ItemModel` with the
    mock TMDB injected via :class:`OrchestratorDeps`. The resolver wraps
    the mock; no real network calls happen.
    """
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    resolver = IMDbFallbackResolver(tmdb, omdb_api_key=None)
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
    )
    model = ItemModel()
    return Orchestrator(model, deps), model


def make_window_with_orchestrator(settings, *, tmdb: FakeTMDB, tmp_path: Path):
    """Construct the full production wiring: MainWindow + Orchestrator +
    parse/apply/preview wrappers, just like ``plex_renamer.gui.app.build_window``.
    Tests that need to inspect the source panel's group label or drive
    a real group click through the UI use this.
    """
    from plex_renamer.gui.app import build_window
    from plex_renamer.gui.orchestrator import OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    resolver = IMDbFallbackResolver(tmdb, omdb_api_key=None)
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
    )
    window = build_window(settings, deps)
    return window


# --- Tests ----------------------------------------------------------------


def test_lazarus_pattern_resolves_to_show_name(tmp_path, qapp, mock_tmdb) -> None:
    """The exact bug the user hit: episode files under Show/s1/ resolve correctly.

    Filenames shaped like ``[S01.E01] Episode Title.mp4`` leave the
    parser's ``title_candidate`` empty (episode title belongs in
    ``episode_title``). Before the fix, the orchestrator searched TMDB
    with the EPISODE title (or with the season folder name "s1"); both
    produce empty or wrong hits. After the fix, the show name is
    derived from the path tree at parse time and used as the TMDB
    query.
    """
    # Use "Game Of Thrones" (a corpus-generator show) since FakeTMDB
    # knows it; build a Lazarus-shape subtree under MAX/.
    root = tmp_path / "MAX"
    root.mkdir()
    show_dir = root / "Game Of Thrones" / "s1"
    show_dir.mkdir(parents=True)
    titles = [
        "Winter Is Coming",
        "The Kingsroad",
        "Lord Snow",
        "Cripples, Bastards, and Broken Things",
    ]
    for i, title in enumerate(titles, start=1):
        (show_dir / f"[S01.E{i:02d}] {title}.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)

    assert len(rows) == 4

    # Every row carries a Candidate (not None) AND it's the TMDB show.
    for r in rows:
        assert r.candidate is not None, f"Row {r.parsed.raw_filename} stayed unresolved"
        assert r.candidate.anchor_kind == "tmdb"
        assert r.candidate.title == "Game of Thrones"

    # Every row's show_name_hint is the show name from the path tree.
    for r in rows:
        assert r.show_name_hint == "Game Of Thrones"

    # All 4 rows share the same group_key (anchored on the SHOW name).
    keys = {r.group_key for r in rows}
    assert len(keys) == 1
    assert keys.pop() == "tv::Game Of Thrones"


def test_lazarus_pattern_when_drop_is_show_root(tmp_path, qapp, mock_tmdb) -> None:
    """When the user drops the SHOW directory directly (no parent), the
    show name comes from input_root.name. derive_show_name's fallback
    handles the case where every parent_dirs entry is season-like.
    """
    root = tmp_path / "Lazarus-Like"
    show_dir = root / "s1"
    show_dir.mkdir(parents=True)
    (show_dir / "[S01.E01] Winter Is Coming.mp4").touch()
    (show_dir / "[S01.E02] The Kingsroad.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)

    assert len(rows) == 2
    # show_name_hint falls back to input_root.name when parent_dirs is
    # ``["s1"]`` (every entry matches the season-folder regex).
    for r in rows:
        assert r.show_name_hint == "Lazarus-Like"


def test_group_label_uses_show_name(tmp_path, qapp, mock_tmdb) -> None:
    """The source-panel group label is the show name, not the first
    episode's filename or the season folder name.
    """
    root = tmp_path / "MAX"
    show_dir = root / "Game Of Thrones" / "s1"
    show_dir.mkdir(parents=True)
    (show_dir / "[S01.E01] Winter Is Coming.mp4").touch()
    (show_dir / "[S01.E02] The Kingsroad.mp4").touch()

    settings = make_test_settings(tmp_path)
    window = make_window_with_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)

    # Drive the drop through the same path the production drop zone uses.
    window._on_paths_dropped([root])

    panel = window.source_panel()
    panel.refresh()  # idempotent; ensures the tree reflects the model
    assert panel._tree.topLevelItemCount() == 1
    group_item = panel._tree.topLevelItem(0)
    label = group_item.text(0)

    # The label includes the show name (canonical or path-derived). It
    # MUST NOT contain the episode title or the .mp4 extension.
    assert "Game of Thrones" in label or "Game Of Thrones" in label, label
    assert "Winter Is Coming" not in label, label
    assert ".mp4" not in label, label
    assert "[S01" not in label, label


def test_movie_in_flat_dir_resolves(tmp_path, qapp, mock_tmdb) -> None:
    """Tubitv-shape flat movie files resolve cleanly through the pipeline."""
    root = tmp_path / "Tubitv"
    root.mkdir()
    (root / "A Field In England.mp4").touch()
    (root / "Spaceballs.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)

    titles = {r.candidate.title for r in rows if r.candidate is not None}
    assert "A Field in England" in titles, titles
    assert "Spaceballs" in titles, titles


def test_full_corpus_pipeline_no_tv_left_unresolved_when_show_in_tmdb(
    tmp_path, qapp, mock_tmdb
) -> None:
    """Run the slice-2 corpus generator and walk every TV item through the pipeline.

    For every show the FakeTMDB knows about, every episode of that
    show in the corpus must resolve to a Candidate with
    ``anchor_kind="tmdb"``. Shows the FakeTMDB doesn't know about may
    stay unresolved — those are the "needs review" path.
    """
    from plex_renamer.test_corpus import build_corpus

    corpus_root = tmp_path / "corpus"
    build_corpus(corpus_root)

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(corpus_root)

    # FakeTMDB knows these shows; every episode under one of them must
    # carry a Candidate.
    known_shows = {
        "Game Of Thrones",
        "Mad Men",
        "Warehouse 13",
        "House Of The Dragon",
        "Doctor Who",
    }
    relevant = [r for r in rows if r.parsed.kind == "tv" and r.show_name_hint in known_shows]
    assert len(relevant) > 0, "Corpus should produce TV rows for the known shows"
    for r in relevant:
        assert r.candidate is not None, (
            f"Row for known show {r.show_name_hint!r} stayed unresolved: {r.parsed.raw_filename}"
        )
        assert r.candidate.anchor_kind == "tmdb"


def test_proposed_plex_paths_match_canonical_shape(tmp_path, qapp, mock_tmdb) -> None:
    """End-to-end: parse + resolve + plan yields canonical Plex paths."""
    root = tmp_path / "MAX"
    show_dir = root / "Game Of Thrones" / "s1"
    show_dir.mkdir(parents=True)
    (show_dir / "[S01.E01] Winter Is Coming.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    orchestrator.parse_and_resolve(root)

    plan = orchestrator.preview(model, input_root=root)
    assert len(plan.ops) == 1, [op.target for op in plan.ops]
    op = plan.ops[0]
    expected = (
        tmp_path
        / "TV"
        / "Game of Thrones (2011) {tmdb-1399}"
        / "Season 01"
        / "Game of Thrones (2011) - S01E01 - Winter Is Coming.mp4"
    )
    assert op.target == expected, f"Got: {op.target}"


def test_show_anchor_picker_uses_orchestrator_factory(tmp_path, qapp, mock_tmdb) -> None:
    """The orchestrator's picker_factory receives the group key on click,
    and the candidates seeded into it come from a SHOW-name TMDB search.

    This pins the integration between ``on_group_clicked`` and the
    picker so future refactors can't silently route the picker through
    a different code path.
    """
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    root = tmp_path / "MAX"
    show_dir = root / "Game Of Thrones" / "s1"
    show_dir.mkdir(parents=True)
    (show_dir / "[S01.E01] Winter Is Coming.mp4").touch()

    # Build a stub picker we control directly.
    captured: dict = {"group_key": None, "candidates": None, "execed": False}

    class _StubPicker:
        def __init__(self, group_key):
            captured["group_key"] = group_key

        @property
        def show_chosen(self):
            class _Sig:
                def connect(self, _fn):
                    pass

            return _Sig()

        def set_results(self, c):
            captured["candidates"] = list(c)

        def exec(self):
            captured["execed"] = True
            return 0

    resolver = IMDbFallbackResolver(mock_tmdb, omdb_api_key=None)
    deps = OrchestratorDeps(
        tmdb=mock_tmdb,
        resolver=resolver,
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
        picker_factory=_StubPicker,
    )
    model = ItemModel()
    orch = Orchestrator(model, deps)

    # Drive the parse + resolve manually so we control the rows.
    rows = orch.parse_and_resolve(root)
    assert rows, "expected parsed rows"

    # Reset call recording so we observe ONLY the on_group_clicked
    # search.
    pre_count = len(mock_tmdb.calls)
    orch.on_group_clicked("tv::Game Of Thrones")

    assert captured["group_key"] == "tv::Game Of Thrones"
    assert captured["execed"] is True
    # The picker received TMDB candidates (FakeTMDB has Game of Thrones).
    assert captured["candidates"] is not None
    assert len(captured["candidates"]) >= 1

    # The TMDB search fired with the SHOW name as the query.
    new_search_calls = [c for c in mock_tmdb.calls[pre_count:] if c[0] == "search_tv"]
    assert new_search_calls, "on_group_clicked should have issued a search_tv"
    queried_title = new_search_calls[0][1][0]
    assert queried_title == "Game Of Thrones"

    # And it MUST NOT have queried with the episode title.
    queried_titles = [c[1][0] for c in mock_tmdb.calls[pre_count:] if c[0] == "search_tv"]
    assert "Winter Is Coming" not in queried_titles
    assert "" not in queried_titles


def test_full_corpus_resolves_under_a_drop(tmp_path, qapp, mock_tmdb) -> None:
    """Sanity: the full corpus tree parses without exceptions and
    produces ItemRows for every non-skip, non-unknown entry. Catches
    regressions where the orchestrator chokes on a real-shaped tree.
    """
    from plex_renamer.test_corpus import build_corpus

    corpus_root = tmp_path / "corpus"
    build_corpus(corpus_root)

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(corpus_root)

    # At least one row of each kind survives the filter.
    kinds = {r.parsed.kind for r in rows}
    assert "tv" in kinds, "Corpus should produce TV rows"
    assert "movie" in kinds, "Corpus should produce movie rows"

    # No row was emitted with kind=unknown or skip_reason set.
    for r in rows:
        assert r.parsed.kind != "unknown"
        assert r.parsed.skip_reason is None


# --- Gap-fill: high-priority corpus patterns the smoke test only walked ----


def test_doctor_who_classic_flat_with_season_no_silent_unresolved(
    tmp_path, qapp, mock_tmdb
) -> None:
    """Doctor Who Classic flat files with the season buried mid-title.

    The corpus generator emits filenames shaped like

        'The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.mp4'

    where the show name lives at the END of the filename, not on a
    parent directory. The parser classifies these as ``tv`` (bare
    season-only signal) but ``derive_show_name`` falls back to
    ``input_root.name`` because there's no non-season parent folder.

    The bug being pinned is "silent unresolved": before the round-2
    fix, a TMDB miss for the wrong query (``input_root.name`` rather
    than "Doctor Who") left the row without a candidate and without
    any error surfaced. After the fix, either the resolver matches
    Doctor Who OR the failure shows up in
    :meth:`Orchestrator.last_resolve_errors`. This test pins the
    invariant: no row may silently stay unresolved.
    """
    root = tmp_path / "Video"
    root.mkdir()
    file = (
        root / "The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.mp4"
    )
    file.touch()

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)

    # The parser may classify this filename differently depending on
    # how it tokenizes the trailing show name; what matters is that no
    # row silently stays without either a candidate or a surfaced
    # error. We assert the no-silent-unresolved invariant against
    # every TV row produced.
    tv_rows = [r for r in rows if r.parsed.kind == "tv"]
    assert tv_rows, "Doctor Who Classic flat file should produce at least one TV row"
    error_paths = {p for p, _ in orchestrator.last_resolve_errors()}
    for r in tv_rows:
        if r.candidate is None:
            assert r.source_path in error_paths, (
                f"TV row {r.source_path} stayed unresolved without surfacing an error"
            )


def test_sidecar_subtitles_retarget_with_video(tmp_path, qapp, mock_tmdb) -> None:
    """``.en.srt`` / ``.en.forced.srt`` / ``.en.sdh.srt`` sidecars travel with the video.

    A video with three subtitle sidecars sharing its basename stem must
    yield a single RenameOp whose ``sidecars`` list contains target
    paths that share the canonical Plex stem the video lands on. Pins
    that the parser pairs the sidecars by stem and the planner emits
    one target per sidecar with the show/season/episode-derived stem.
    """
    root = tmp_path / "MAX"
    root.mkdir()
    show = root / "Game Of Thrones" / "s1"
    show.mkdir(parents=True)
    # NOTE: the parser pairs sidecars by basename stem; bracketed
    # filenames like ``[S01.E01] ...`` have internal periods that
    # confuse the language/modifier tokenizer. Use a period-free shape
    # so the pairing logic exercised here is the planner's, not the
    # parser's stem heuristic.
    video = show / "Game of Thrones S01E01 Winter Is Coming.mp4"
    video.touch()
    (show / "Game of Thrones S01E01 Winter Is Coming.en.srt").touch()
    (show / "Game of Thrones S01E01 Winter Is Coming.en.forced.srt").touch()
    (show / "Game of Thrones S01E01 Winter Is Coming.en.sdh.srt").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    orchestrator.parse_and_resolve(root)
    plan = orchestrator.preview(model, input_root=root)

    assert len(plan.ops) == 1, [op.target for op in plan.ops]
    op = plan.ops[0]
    sidecar_targets = [str(target) for _, target in op.sidecars]
    assert any(t.endswith(".en.srt") for t in sidecar_targets), sidecar_targets
    assert any(t.endswith(".en.forced.srt") for t in sidecar_targets), sidecar_targets
    assert any(t.endswith(".en.sdh.srt") for t in sidecar_targets), sidecar_targets
    # Every sidecar shares the canonical Plex episode stem.
    canonical_stem = "Game of Thrones (2011) - S01E01 - Winter Is Coming"
    for t in sidecar_targets:
        assert canonical_stem in t, t


def test_specials_route_to_season_00(tmp_path, qapp, mock_tmdb) -> None:
    """Files under ``Specials/`` (or with an S00 marker) land in ``Season 00/``.

    The corpus generator emits Doctor Who specials under
    ``Doctor Who/Specials/``; the parser's parent-hint logic sets
    ``season=0`` from the folder name and the planner must route the
    file under ``Season 00/`` (not ``Season 0`` or ``Specials`` —
    Plex's canonical folder name is ``Season 00``).
    """
    root = tmp_path / "MAX"
    root.mkdir()
    show = root / "Doctor Who" / "Specials"
    show.mkdir(parents=True)
    (show / "S00E01 - Time Crash.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    orchestrator.parse_and_resolve(root)
    plan = orchestrator.preview(model, input_root=root)

    assert len(plan.ops) == 1, [op.target for op in plan.ops]
    op = plan.ops[0]
    assert "Season 00" in str(op.target), f"Special did not route to Season 00: {op.target}"


def test_multi_season_drop_hydrates_each_season(tmp_path, qapp, mock_tmdb) -> None:
    """When a group spans multiple seasons, every season's episode_list is merged.

    Pins the round-2 fix in :meth:`Orchestrator._hydrate_seasons`:
    before the fix, only the first row's season was hydrated, so
    rows in higher seasons carried a Candidate whose ``episode_list``
    didn't include their season's episodes. After the fix, the merged
    Candidate's ``episode_list`` spans every unique season present in
    the group.
    """
    root = tmp_path / "MAX"
    root.mkdir()
    show_dir = root / "Game Of Thrones"
    show_dir.mkdir()
    (show_dir / "s1").mkdir()
    (show_dir / "s2").mkdir()
    (show_dir / "s1" / "[S01.E01] Winter Is Coming.mp4").touch()
    (show_dir / "s2" / "[S02.E01] The North Remembers.mp4").touch()

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)

    assert len(rows) == 2
    for r in rows:
        assert r.candidate is not None, f"{r.parsed.raw_filename} stayed unresolved"
        eps = r.candidate.episode_list or ()
        assert any(e.season == 1 for e in eps), (
            f"s1 episodes missing from hydrated list for {r.parsed.raw_filename}"
        )
        assert any(e.season == 2 for e in eps), (
            f"s2 episodes missing from hydrated list for {r.parsed.raw_filename}"
        )


def test_resolve_errors_cleared_on_success(tmp_path, qapp, mock_tmdb) -> None:
    """A successful re-resolve clears stale per-path errors.

    Pins the dict-keyed error state in
    :attr:`Orchestrator._resolve_errors_by_path`: when a prior call
    surfaced an error for a path, a subsequent successful resolve for
    that path drops the entry instead of leaving it stale.
    """
    root = tmp_path / "MAX"
    show_dir = root / "Game Of Thrones" / "s1"
    show_dir.mkdir(parents=True)
    video = show_dir / "[S01.E01] Winter Is Coming.mp4"
    video.touch()

    settings = make_test_settings(tmp_path)
    orchestrator, _model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)
    rows = orchestrator.parse_and_resolve(root)
    assert len(rows) == 1
    # Seed a stale error against the row's path and verify the next
    # resolve pass clears it (the successful path drops the entry).
    orchestrator._resolve_errors_by_path[rows[0].source_path] = "stale error from earlier pass"
    assert orchestrator.last_resolve_errors(), "seeded error should be visible"

    orchestrator.resolve_rows(rows)
    remaining = {p for p, _ in orchestrator.last_resolve_errors()}
    assert rows[0].source_path not in remaining, (
        "Successful resolve_rows should clear stale errors for the affected paths"
    )


def test_unknown_show_recovers_via_picker_search(tmp_path, qapp, mock_tmdb) -> None:
    """End-to-end recovery flow when the dropped folder names an unknown show.

    User drops ``MAX/Lazarus_2/s1/...`` -- the FakeTMDB knows
    "Game Of Thrones" but not "Lazarus_2". The auto-resolve pass leaves
    every row unresolved with a surfaced error. The user opens the
    picker on the group, sees the empty list, types a corrected name
    (here we simulate with "Game Of Thrones" since FakeTMDB knows it),
    and the picker re-queries TMDB through the orchestrator. The
    re-query produces candidates; picking the first one propagates the
    candidate to every row in the group.

    This is the dead-end the v0.1.1 user hit: empty picker, no
    recourse. The fix is the search box on the picker plus the
    ``on_picker_search`` orchestrator handler.
    """
    # Use the fixture-from-tree helper to mirror a real-shaped subtree.
    import sys

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from fixture_from_tree import mirror_tree  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    src = tmp_path / "src"
    show_dir = src / "Lazarus_2" / "s1"
    show_dir.mkdir(parents=True)
    for i in range(1, 4):
        (show_dir / f"[S01.E{i:02d}] Episode {i}.mp4").touch()

    root = tmp_path / "MAX"
    mirror_tree(src, root, include_hidden=False, allowed_exts=None)
    drop_root = root / "Lazarus_2"

    settings = make_test_settings(tmp_path)
    orchestrator, model = make_orchestrator(settings, tmdb=mock_tmdb, tmp_path=tmp_path)

    # 1. Initial parse + resolve produces unresolved rows with errors.
    rows = orchestrator.parse_and_resolve(drop_root)
    assert len(rows) == 3
    for r in rows:
        assert r.candidate is None, f"{r.parsed.raw_filename} should not auto-resolve"
    err_paths = {p for p, _ in orchestrator.last_resolve_errors()}
    for r in rows:
        assert r.source_path in err_paths, (
            f"Row {r.source_path} stayed unresolved without surfacing an error"
        )

    # 2. The user opens the picker. We simulate by driving on_group_clicked
    #    with a stub picker so the test stays headless. The orchestrator
    #    seeds an empty result list (Lazarus_2 isn't in FakeTMDB).
    captured: dict = {}

    class _StubPicker:
        def __init__(self, group_key):
            self._group_key = group_key
            self._results: list = []

            class _Sig:
                def __init__(self):
                    self._slots: list = []

                def connect(self, fn):
                    self._slots.append(fn)

                def emit(self, *args):
                    for s in self._slots:
                        s(*args)

            self.show_chosen = _Sig()
            self.search_requested = _Sig()

        def group_key(self):
            return self._group_key

        def set_search_text(self, text):
            captured["seeded_text"] = text

        def set_results(self, c):
            self._results = list(c)
            captured["last_results"] = list(c)

        def exec(self):
            captured["execed"] = True
            return 0

    # Reinstall the orchestrator with a picker_factory pointing at our stub.
    from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    stub_picker_holder: dict = {}

    def _factory(group_key):
        p = _StubPicker(group_key)
        stub_picker_holder["picker"] = p
        return p

    resolver = IMDbFallbackResolver(mock_tmdb, omdb_api_key=None)
    deps = OrchestratorDeps(
        tmdb=mock_tmdb,
        resolver=resolver,
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
        picker_factory=_factory,
    )
    orchestrator2 = Orchestrator(model, deps)
    orchestrator2.on_group_clicked("tv::Lazarus_2")

    # The picker was seeded with the show name hint and an empty list
    # (FakeTMDB doesn't know "Lazarus_2").
    assert captured["seeded_text"] == "Lazarus_2"
    assert captured["execed"] is True
    assert captured["last_results"] == []

    # 3. User types "Game Of Thrones" in the search box and triggers
    #    search. The picker emits search_requested; the orchestrator
    #    re-queries TMDB and pushes new results back to the picker.
    picker = stub_picker_holder["picker"]
    picker.search_requested.emit("tv::Lazarus_2", "Game Of Thrones")
    assert captured["last_results"], "Picker should have received new candidates"
    new_cands = captured["last_results"]
    assert new_cands[0].title == "Game of Thrones"

    # 4. User picks the first candidate. on_show_chosen propagates the
    #    candidate to every row in the group.
    picker.show_chosen.emit("tv::Lazarus_2", new_cands[0])

    for r in model.rows():
        assert r.candidate is not None, f"{r.parsed.raw_filename} should now have a candidate"
        assert r.candidate.title == "Game of Thrones"

    # Errors cleared for the affected rows.
    err_paths_after = {p for p, _ in orchestrator2.last_resolve_errors()}
    for r in model.rows():
        assert r.source_path not in err_paths_after, (
            f"Row {r.source_path} should not have a stale error after picker pick"
        )
