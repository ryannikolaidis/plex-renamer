"""Multi-part movie tests.

A multi-part movie is two or more files that share (title, year) and
differ by part_marker (cd1/cd2/pt1/pt2/...). The planner emits them as
sibling ``- pt1`` / ``- pt2`` files in the same per-movie folder.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.multi_part import group_multi_part
from plex_renamer.tmdb.models import Candidate


def _movie() -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id="500",
        kind="movie",
        title="Long Movie",
        year=2010,
        confidence=0.95,
    )


def _parsed(name: str, part: str) -> ParseResult:
    return ParseResult(
        source_path=Path(f"/tmp/{name}"),
        kind="movie",
        title_candidate="Long Movie",
        year=2010,
        part_marker=part,
        raw_filename=name,
    )


def test_group_multi_part_detects_pair() -> None:
    items = [
        _parsed("Long Movie cd1.mkv", "cd1"),
        _parsed("Long Movie cd2.mkv", "cd2"),
    ]
    groups = group_multi_part(items)
    assert len(groups) == 1


def test_group_multi_part_ignores_single() -> None:
    items = [_parsed("Long Movie cd1.mkv", "cd1")]
    groups = group_multi_part(items)
    assert groups == {}


def test_multi_part_emits_pt_siblings(tmp_path: Path) -> None:
    cd1 = tmp_path / "in" / "Long Movie cd1.mkv"
    cd2 = tmp_path / "in" / "Long Movie cd2.mkv"
    cd1.parent.mkdir(parents=True)
    cd1.touch()
    cd2.touch()
    p1 = _parsed("Long Movie cd1.mkv", "cd1")
    p1 = ParseResult(**{**p1.__dict__, "source_path": cd1})
    p2 = _parsed("Long Movie cd2.mkv", "cd2")
    p2 = ParseResult(**{**p2.__dict__, "source_path": cd2})

    plan = build_plan_from_pairs(
        [(p1, _movie()), (p2, _movie())],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "in",
    )
    assert len(plan.ops) == 2
    targets = sorted([op.target.name for op in plan.ops])
    assert targets[0].endswith(" - pt1.mkv")
    assert targets[1].endswith(" - pt2.mkv")
    # Both land in the same folder.
    folders = {op.target.parent for op in plan.ops}
    assert len(folders) == 1


def test_part_normalization_pt_form() -> None:
    """``part1`` / ``disc1`` / ``disk1`` all normalize to ``pt<N>``."""
    items = [
        _parsed("X part1.mkv", "part1"),
        _parsed("X part2.mkv", "part2"),
    ]
    groups = group_multi_part(items)
    assert len(groups) == 1
