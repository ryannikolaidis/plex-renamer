"""Collision-detection tests."""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.collision import detect_collisions
from plex_renamer.planner.models import RenameOp
from plex_renamer.tmdb.models import Candidate


def test_detect_collisions_partitions_ops() -> None:
    target = Path("/lib/Movies/X (2010) {tmdb-1}/X (2010) {tmdb-1}.mkv")
    op_a = RenameOp(
        source=Path("/in/a.mkv"),
        target=target,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
    )
    op_b = RenameOp(
        source=Path("/in/b.mkv"),
        target=target,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
    )
    op_c = RenameOp(
        source=Path("/in/c.mkv"),
        target=Path("/lib/Movies/Other.mkv"),
        kind="movie",
        anchor="tmdb-2",
        edition=None,
        confidence=0.9,
    )
    clean, collisions = detect_collisions([op_a, op_b, op_c])
    assert len(clean) == 1
    assert clean[0] is op_c
    assert len(collisions) == 1
    assert collisions[0].target == target
    assert set(collisions[0].sources) == {op_a.source, op_b.source}
    assert collisions[0].reason == "same_anchor_different_source"


def test_detect_collisions_distinguishes_reason() -> None:
    """Different anchors landing at the same target -> duplicate_input."""
    target = Path("/lib/X.mkv")
    op_a = RenameOp(
        source=Path("/in/a.mkv"),
        target=target,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
    )
    op_b = RenameOp(
        source=Path("/in/b.mkv"),
        target=target,
        kind="movie",
        anchor="tmdb-2",
        edition=None,
        confidence=0.9,
    )
    _, collisions = detect_collisions([op_a, op_b])
    assert collisions[0].reason == "duplicate_input"


def test_collision_in_full_plan(tmp_path: Path) -> None:
    """Two duplicate inputs (e.g. _1 suffix) collide at the same target."""
    src1 = tmp_path / "in" / "Matrix.mkv"
    src2 = tmp_path / "in" / "Matrix_1.mkv"
    src1.parent.mkdir(parents=True)
    src1.touch()
    src2.touch()
    cand = Candidate(
        anchor_kind="tmdb",
        anchor_id="603",
        kind="movie",
        title="The Matrix",
        year=1999,
        confidence=0.9,
    )
    p1 = ParseResult(
        source_path=src1,
        kind="movie",
        title_candidate="The Matrix",
        year=1999,
        raw_filename=src1.name,
    )
    p2 = ParseResult(
        source_path=src2,
        kind="movie",
        title_candidate="The Matrix",
        year=1999,
        raw_filename=src2.name,
    )
    plan = build_plan_from_pairs(
        [(p1, cand), (p2, cand)],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "in",
    )
    assert len(plan.ops) == 0
    assert len(plan.collisions) == 1
    assert set(plan.collisions[0].sources) == {src1, src2}
