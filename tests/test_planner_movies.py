"""Movie planner output-path tests.

Verifies the Plex-canonical movie path shape:

    <movies_root>/<Title> (<Year>) {<anchor>}/<Title> (<Year>) {<anchor>}.<ext>

Anchor renders as ``tmdb-<id>`` or ``imdb-tt<id>``. Tests inject a
deterministic Candidate so we never touch TMDB.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.movie_path import movie_target_path
from plex_renamer.tmdb.models import Candidate


def _movie_candidate(title: str = "The Matrix", year: int | None = 1999) -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id="603",
        kind="movie",
        title=title,
        year=year,
        confidence=0.95,
    )


def test_movie_target_path_basic(tmp_path: Path) -> None:
    movies = tmp_path / "Movies"
    candidate = _movie_candidate()
    target = movie_target_path(
        candidate, movies_root=movies, edition=None, part_marker=None, ext=".mkv"
    )
    assert target == movies / "The Matrix (1999) {tmdb-603}" / "The Matrix (1999) {tmdb-603}.mkv"


def test_movie_target_path_imdb_anchor(tmp_path: Path) -> None:
    movies = tmp_path / "Movies"
    candidate = Candidate(
        anchor_kind="imdb",
        anchor_id="tt0133093",
        kind="movie",
        title="The Matrix",
        year=1999,
        confidence=0.6,
    )
    target = movie_target_path(candidate, movies, None, None, ".mp4")
    assert "{imdb-tt0133093}" in str(target)


def test_movie_target_path_no_year(tmp_path: Path) -> None:
    """When year is None, the path should not contain ``()``."""
    movies = tmp_path / "Movies"
    candidate = _movie_candidate(year=None)
    target = movie_target_path(candidate, movies, None, None, ".mp4")
    assert "()" not in str(target)
    assert "{tmdb-603}" in str(target)


def test_build_plan_from_pairs_movie(tmp_path: Path) -> None:
    source = tmp_path / "input" / "The Matrix 1999 1080p.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="The Matrix",
        year=1999,
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _movie_candidate())],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert op.kind == "movie"
    assert op.anchor == "tmdb-603"
    assert op.target.name == "The Matrix (1999) {tmdb-603}.mkv"


def test_plan_skipped_when_unresolved(tmp_path: Path) -> None:
    """When the resolver returns None, the parse result lands in skipped."""
    source = tmp_path / "input" / "Mystery 2010.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="Mystery",
        year=2010,
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, None)],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert len(plan.ops) == 0
    assert plan.skipped == ((source, "unresolved"),)


def test_movie_edition_not_auto_applied(tmp_path: Path) -> None:
    """Edition tokens never land in the path unless apply_editions=True."""
    source = tmp_path / "input" / "X.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="X",
        year=2020,
        edition_tokens=["Director's Cut"],
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _movie_candidate("X", 2020))],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert "{edition" not in str(plan.ops[0].target)


def test_movie_edition_applied_on_opt_in(tmp_path: Path) -> None:
    source = tmp_path / "input" / "X.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="X",
        year=2020,
        edition_tokens=["Director's Cut"],
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _movie_candidate("X", 2020))],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
        apply_editions=True,
    )
    assert "{edition-Director's Cut}" in str(plan.ops[0].target)
    assert plan.ops[0].edition == "Director's Cut"


def test_plan_json_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "input" / "X.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="X",
        year=2020,
        raw_filename=source.name,
    )
    original = build_plan_from_pairs(
        [(parsed, _movie_candidate("X", 2020))],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    json_text = original.to_json()
    rebuilt = type(original).from_json(json_text)
    assert rebuilt.ops[0].source == original.ops[0].source
    assert rebuilt.ops[0].target == original.ops[0].target
    assert rebuilt.ops[0].anchor == original.ops[0].anchor
    assert rebuilt.movies_root == original.movies_root
