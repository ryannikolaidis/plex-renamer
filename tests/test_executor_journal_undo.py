"""Journal + undo tests."""

from __future__ import annotations

import json
from pathlib import Path

from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch
from plex_renamer.planner.models import RenameOp, RenamePlan


def _basic_plan(tmp_path: Path) -> RenamePlan:
    in_root = tmp_path / "in"
    in_root.mkdir(parents=True)
    src = in_root / "x.mkv"
    src.write_bytes(b"data")
    target = tmp_path / "lib" / "Movies" / "X" / "X.mkv"
    op = RenameOp(
        source=src,
        target=target,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
    )
    return RenamePlan(
        ops=(op,),
        collisions=(),
        skipped=(),
        movies_root=tmp_path / "lib" / "Movies",
        tv_root=tmp_path / "lib" / "TV",
        input_root=in_root,
    )


def test_journal_write_ahead_then_verify(tmp_path: Path) -> None:
    plan = _basic_plan(tmp_path)
    journal_dir = tmp_path / "journals"
    result = apply_plan(plan, journal_dir=journal_dir, cleanup=False)
    data = json.loads(result.journal_path.read_text())
    assert data["entries"][0]["status"] == "verified"
    # Bytes recorded.
    assert data["entries"][0]["bytes"] == 4


def test_journal_load_round_trip(tmp_path: Path) -> None:
    plan = _basic_plan(tmp_path)
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    reloaded = Journal.load(result.journal_path)
    assert reloaded.batch_id
    assert reloaded.entries[0].status == "verified"
    assert reloaded.all_verified


def test_undo_without_cleanup_restores(tmp_path: Path) -> None:
    """When cleanup did NOT run, undo deletes the targets and sources remain."""
    plan = _basic_plan(tmp_path)
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    journal = Journal.load(result.journal_path)
    undo_result = undo_batch(journal)
    assert undo_result.sources_recoverable is True
    assert undo_result.reverted == 1
    # Target gone.
    assert not plan.ops[0].target.exists()
    # Source still there.
    assert plan.ops[0].source.exists()


def test_undo_with_cleanup_moves_to_review(safe_tmp_path: Path) -> None:
    """When cleanup ran, undo moves targets to a review folder.

    Uses ``safe_tmp_path`` instead of ``tmp_path`` because cleanup refuses
    paths under ``/private/var/folders/...`` (the macOS realpath form of
    pytest's tmp dirs).
    """
    plan = _basic_plan(safe_tmp_path)
    result = apply_plan(plan, journal_dir=safe_tmp_path / "journals", cleanup=True)
    assert result.cleanup_ran is True
    # Source is gone after cleanup.
    assert not plan.ops[0].source.exists()
    journal = Journal.load(result.journal_path)
    undo_result = undo_batch(journal)
    assert undo_result.sources_recoverable is False
    assert undo_result.review_dir is not None
    assert undo_result.review_dir.exists()
    # The target moved into the review folder.
    moved = list(undo_result.review_dir.iterdir())
    assert any("X.mkv" in str(p) for p in moved)
