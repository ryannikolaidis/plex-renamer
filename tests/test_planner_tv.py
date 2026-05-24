"""TV planner output-path tests.

Shape::

    <tv_root>/<Show> (<Year>) {<anchor>}/Season <NN>/<Show> (<Year>) - S<NN>E<NN> - <Episode Title>.<ext>
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.tv_path import (
    tv_episode_basename,
    tv_season_folder_name,
    tv_show_folder_name,
    tv_target_path,
)
from plex_renamer.tmdb.models import Candidate, Episode


def _show(title: str = "Breaking Bad", year: int | None = 2008) -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id="1396",
        kind="tv",
        title=title,
        year=year,
        confidence=0.95,
        episode_list=(
            Episode(season=1, episode=1, title="Pilot"),
            Episode(season=1, episode=2, title="Cat's in the Bag..."),
            Episode(season=2, episode=1, title="Seven Thirty-Seven"),
        ),
    )


def test_tv_show_folder_name() -> None:
    show = _show()
    assert tv_show_folder_name(show) == "Breaking Bad (2008) {tmdb-1396}"


def test_tv_season_folder_zero_padded() -> None:
    assert tv_season_folder_name(1) == "Season 01"
    assert tv_season_folder_name(0) == "Season 00"
    assert tv_season_folder_name(15) == "Season 15"


def test_tv_episode_basename_zero_padded() -> None:
    show = _show()
    # Note: trailing dots get stripped by sanitize_component to keep Windows
    # filenames legal. The TMDB title ``Cat's in the Bag...`` lands as
    # ``Cat's in the Bag``.
    base = tv_episode_basename(show, 1, 2, "Cat's in the Bag")
    assert base == "Breaking Bad (2008) - S01E02 - Cat's in the Bag"


def test_tv_episode_basename_multi_episode() -> None:
    show = _show()
    base = tv_episode_basename(show, 1, 1, "Two-Parter", episode_end=2)
    assert "S01E01-E02" in base


def test_tv_target_path_full(tmp_path: Path) -> None:
    show = _show()
    target = tv_target_path(show, tmp_path / "TV", 1, 2, "Cat's in the Bag", ".mkv")
    expected = (
        tmp_path
        / "TV"
        / "Breaking Bad (2008) {tmdb-1396}"
        / "Season 01"
        / "Breaking Bad (2008) - S01E02 - Cat's in the Bag.mkv"
    )
    assert target == expected


def test_build_plan_tv_episode(tmp_path: Path) -> None:
    source = tmp_path / "input" / "Breaking.Bad.S01E02.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="tv",
        title_candidate="Breaking Bad",
        year=2008,
        season=1,
        episode=2,
        episode_title="Cat's in the Bag",
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _show())],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert op.kind == "tv"
    assert op.anchor == "tmdb-1396"
    # The TMDB episode title wins over the parser's; sanitize strips
    # trailing dots so the canonical form has no ``...`` suffix.
    assert "Cat's in the Bag" in str(op.target)
    assert "Season 01" in str(op.target)
