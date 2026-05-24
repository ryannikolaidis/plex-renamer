"""Specials routing tests.

Specials (season 0 or under a ``Specials/`` parent) route to
``Season 00/``.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.specials import is_special
from plex_renamer.tmdb.models import Candidate, Episode


def test_is_special_season_zero() -> None:
    parsed = ParseResult(source_path=Path("/x"), kind="tv", season=0, episode=1)
    assert is_special(parsed)


def test_is_special_parent_dir() -> None:
    parsed = ParseResult(
        source_path=Path("/x"),
        kind="tv",
        season=None,
        episode=1,
        parent_dirs=["Show", "Specials"],
    )
    assert is_special(parsed)


def test_is_special_case_insensitive() -> None:
    parsed = ParseResult(
        source_path=Path("/x"),
        kind="tv",
        season=None,
        episode=2,
        parent_dirs=["Show", "specials"],
    )
    assert is_special(parsed)


def test_is_special_negative() -> None:
    parsed = ParseResult(source_path=Path("/x"), kind="tv", season=1, episode=1)
    assert not is_special(parsed)


def test_specials_route_to_season_00(tmp_path: Path) -> None:
    source = tmp_path / "input" / "Show.S00E01.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="tv",
        title_candidate="Show",
        season=0,
        episode=1,
        episode_title="Christmas Special",
        raw_filename=source.name,
    )
    show = Candidate(
        anchor_kind="tmdb",
        anchor_id="9999",
        kind="tv",
        title="Show",
        year=2020,
        confidence=0.9,
        episode_list=(Episode(season=0, episode=1, title="Christmas Special"),),
    )
    plan = build_plan_from_pairs(
        [(parsed, show)],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert len(plan.ops) == 1
    target_str = str(plan.ops[0].target)
    assert "Season 00" in target_str
    assert "S00E01" in target_str


def test_specials_under_specials_dir(tmp_path: Path) -> None:
    source = tmp_path / "input" / "Show" / "Specials" / "X.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="tv",
        title_candidate="Show",
        season=None,
        episode=3,
        episode_title="Holiday",
        raw_filename=source.name,
        parent_dirs=["Show", "Specials"],
    )
    show = Candidate(
        anchor_kind="tmdb",
        anchor_id="1",
        kind="tv",
        title="Show",
        year=2020,
        confidence=0.9,
        episode_list=(),
    )
    plan = build_plan_from_pairs(
        [(parsed, show)],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert "Season 00" in str(plan.ops[0].target)
