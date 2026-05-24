"""Regression tests for the slice-4 bug fixes.

Three bugs covered:

1. Journal sidecar key collision — the prior integer-encoded
   ``op_index*1000 + sidecar_index + 1`` scheme made the sidecar of op 0
   share its lookup key with op 1 (1*1000+0+1 == 1, op 1's index == 1).
   ``all_verified`` then returned ``False``, cleanup silently skipped, and
   undo silently left op 1's target on disk. The journal now keys entries
   by the ``(op_index, parent_op_index)`` tuple — sidecars carry
   ``parent_op_index`` of the parent op, primaries carry ``None``.

2. ``is_always_disallowed`` used exact-string match against ``/var`` etc.
   On macOS, pytest's ``tmp_path`` resolves through ``/private/var/folders/...``
   so cleanup of a tmp-dir source slipped through. The check now uses
   ``PurePath.parts`` containment so the entire subtree of
   ``/var``, ``/private``, ``/System``, ``/Library``, ``/Applications``,
   and ``/tmp`` is refused regardless of depth.

3. Planner dropped ``parsed.edition_tokens`` whenever ``apply_editions``
   was off. The GUI in slice 5 needs to surface "we detected X" without
   re-parsing the source, so we now carry detected editions on the
   RenameOp via ``detected_editions``. The ``edition`` field continues
   to gate the actual path stamp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plex_renamer.executor.cleanup import CleanupRefused, cleanup_sources
from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch
from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.models import RenameOp, RenamePlan
from plex_renamer.planner.path_safety import is_always_disallowed
from plex_renamer.tmdb.models import Candidate

# --- Critical 1: journal sidecar key collision -----------------------------


def _two_op_plan_with_sidecar_on_first(tmp_path: Path) -> RenamePlan:
    """Build a 2-op plan where op 0 has exactly one sidecar.

    Under the old encoding the sidecar's derived id (0*1000+0+1 == 1)
    aliased onto op 1's index, so the journal's lookup-by-id returned the
    wrong entry when the executor marked op 1 verified.
    """
    src0 = tmp_path / "in" / "a.mkv"
    src0_sc = tmp_path / "in" / "a.en.srt"
    src1 = tmp_path / "in" / "b.mkv"
    for p in (src0, src0_sc, src1):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"data:" + p.name.encode())

    target0 = tmp_path / "lib" / "Movies" / "A" / "A.mkv"
    target0_sc = tmp_path / "lib" / "Movies" / "A" / "A.en.srt"
    target1 = tmp_path / "lib" / "Movies" / "B" / "B.mkv"

    op0 = RenameOp(
        source=src0,
        target=target0,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
        sidecars=((src0_sc, target0_sc),),
    )
    op1 = RenameOp(
        source=src1,
        target=target1,
        kind="movie",
        anchor="tmdb-2",
        edition=None,
        confidence=0.9,
    )
    return RenamePlan(
        ops=(op0, op1),
        collisions=(),
        skipped=(),
        movies_root=tmp_path / "lib" / "Movies",
        tv_root=tmp_path / "lib" / "TV",
        input_root=tmp_path / "in",
    )


def test_sidecar_does_not_collide_with_next_op_after_apply(tmp_path: Path) -> None:
    """Every entry verified; ``all_verified`` is True after a 2-op + sidecar apply."""
    plan = _two_op_plan_with_sidecar_on_first(tmp_path)
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    assert result.succeeded == 2
    assert result.failed == 0

    journal = Journal.load(result.journal_path)
    # Three entries: op0 primary, op0 sidecar, op1 primary.
    assert len(journal.entries) == 3
    statuses = [e.status for e in journal.entries]
    assert statuses == ["verified", "verified", "verified"]
    assert journal.all_verified is True

    # Verify the parent_op_index keying — sidecar is the only entry with
    # parent_op_index set; primaries carry None.
    primaries = [e for e in journal.entries if e.parent_op_index is None]
    sidecars = [e for e in journal.entries if e.parent_op_index is not None]
    assert len(primaries) == 2
    assert len(sidecars) == 1
    assert sidecars[0].parent_op_index == 0
    # Sidecar's local op_index is its position within the parent's tuple.
    assert sidecars[0].op_index == 0


def test_sidecar_collision_cleanup_runs_when_all_verified(safe_tmp_path: Path) -> None:
    """Cleanup must run when every primary + sidecar verifies."""
    plan = _two_op_plan_with_sidecar_on_first(safe_tmp_path)
    result = apply_plan(plan, journal_dir=safe_tmp_path / "journals", cleanup=True)
    assert result.cleanup_ran is True
    # Both primary sources and the sidecar source are gone.
    for op in plan.ops:
        assert not op.source.exists()
        for src, _dst in op.sidecars:
            assert not src.exists()


def test_sidecar_collision_undo_reverts_both_targets(tmp_path: Path) -> None:
    """Undo removes every target including the sidecar; pre-fix it missed op 1."""
    plan = _two_op_plan_with_sidecar_on_first(tmp_path)
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    journal = Journal.load(result.journal_path)

    undo_result = undo_batch(journal)
    # Three entries reverted: op0 primary + sidecar + op1 primary.
    assert undo_result.reverted == 3
    # Every target is gone.
    for op in plan.ops:
        assert not op.target.exists()
        for _src, dst in op.sidecars:
            assert not dst.exists()
    # Sources remained (cleanup didn't run).
    for op in plan.ops:
        assert op.source.exists()


def test_journal_persists_parent_op_index_round_trip(tmp_path: Path) -> None:
    """The ``parent_op_index`` field survives JSON round-trip via Journal.load."""
    plan = _two_op_plan_with_sidecar_on_first(tmp_path)
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    raw = json.loads(result.journal_path.read_text())
    # Three entries; exactly one carries a non-null parent_op_index.
    parents = [e.get("parent_op_index") for e in raw["entries"]]
    assert parents.count(None) == 2
    non_null = [p for p in parents if p is not None]
    assert non_null == [0]


# --- Critical 2: always-disallowed prefix on macOS -------------------------


@pytest.mark.parametrize(
    "guarded",
    [
        "/var/folders/abc/T/scratch.mp4",
        "/private/var/folders/abc/T/scratch.mp4",
        "/tmp/scratch.mp4",
        "/Applications/Movies/foo.mp4",
        "/System/Library/something/file.mp4",
        "/Library/Caches/file.mp4",
    ],
)
def test_is_always_disallowed_blocks_subtree_descendants(guarded: str) -> None:
    """The check must fire on descendants of ``/var``, ``/private``, ``/tmp``, etc."""
    assert is_always_disallowed(Path(guarded)) is True


@pytest.mark.parametrize(
    "allowed",
    [
        "/Users/ryan/media/scratch.mp4",
        "/Users/ryan/Documents/x.mp4",
        "/Volumes/Disk/Movies/x.mp4",
    ],
)
def test_is_always_disallowed_allows_descendants_under_users(allowed: str) -> None:
    """User-home descendants beyond ``/Users/<one>`` are allowed."""
    assert is_always_disallowed(Path(allowed)) is False


def test_cleanup_refuses_tmp_dir_source_even_inside_input_root() -> None:
    """``input_root=/var/folders/<...>`` + source inside it is refused.

    This is the scenario the brief calls out by name: a tmp-dir scratch
    directory is the user's input_root, the file is a strict descendant,
    yet cleanup must refuse because ``/var`` is subtree-disallowed.
    """
    input_root = Path("/var/folders/abc/T/scratch")
    src = input_root / "movie.mp4"
    with pytest.raises(CleanupRefused):
        cleanup_sources([src], input_root=input_root)


def test_cleanup_refuses_private_var_descendant() -> None:
    """The macOS realpath form ``/private/var/folders/...`` is also refused."""
    input_root = Path("/private/var/folders/abc/T/scratch")
    src = input_root / "movie.mp4"
    with pytest.raises(CleanupRefused):
        cleanup_sources([src], input_root=input_root)


def test_cleanup_refuses_tmp_descendant() -> None:
    """``/tmp/<anything>`` is refused regardless of depth."""
    input_root = Path("/tmp")
    src = input_root / "scratch" / "movie.mp4"
    with pytest.raises(CleanupRefused):
        cleanup_sources([src], input_root=input_root)


def test_cleanup_refuses_applications_descendant() -> None:
    """``/Applications/<anything>`` is refused regardless of depth."""
    input_root = Path("/Applications/Movies")
    src = input_root / "foo.mp4"
    with pytest.raises(CleanupRefused):
        cleanup_sources([src], input_root=input_root)


# --- Critical 3: detected_editions on RenameOp ----------------------------


def _movie_candidate() -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id="603",
        kind="movie",
        title="Some Movie",
        year=2010,
        confidence=0.95,
    )


def test_detected_editions_populated_even_without_apply_editions(tmp_path: Path) -> None:
    """``apply_editions=False`` keeps ``edition`` None but ``detected_editions`` is filled."""
    source = tmp_path / "input" / "Some Movie (2010) Director's Cut.mp4"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="Some Movie",
        year=2010,
        edition_tokens=["Director's Cut"],
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
    # edition is None — the path stamp did NOT happen.
    assert op.edition is None
    assert "{edition" not in str(op.target)
    # detected_editions captured what the parser saw.
    assert op.detected_editions == ("Director's Cut",)


def test_detected_editions_round_trips_through_json(tmp_path: Path) -> None:
    """The new field survives ``RenamePlan.to_json`` / ``from_json``."""
    source = tmp_path / "input" / "Some Movie (2010) Director's Cut.mp4"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="Some Movie",
        year=2010,
        edition_tokens=["Director's Cut", "Extended Edition"],
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _movie_candidate())],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    text = plan.to_json()
    reloaded = RenamePlan.from_json(text)
    assert reloaded.ops[0].detected_editions == ("Director's Cut", "Extended Edition")


def test_detected_editions_empty_when_parser_found_none(tmp_path: Path) -> None:
    """No edition_tokens -> empty detected_editions, not unset."""
    source = tmp_path / "input" / "Plain Movie (2010).mp4"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="Plain Movie",
        year=2010,
        edition_tokens=[],
        raw_filename=source.name,
    )
    plan = build_plan_from_pairs(
        [(parsed, _movie_candidate())],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "input",
    )
    assert plan.ops[0].detected_editions == ()


# --- Nice-to-fix: CLI --help noise ----------------------------------------


def test_cli_help_does_not_print_unknown_argument(capsys: pytest.CaptureFixture[str]) -> None:
    """``plex-renamer plan --help`` exits 0 and does not print an unknown-arg line."""
    from plex_renamer.cli.main import app

    code = app(["plan", "--help"])
    captured = capsys.readouterr()
    assert code == 0
    # Help text rendered on stdout.
    assert "--source" in captured.out
    # No spurious diagnostic on stderr.
    assert "unknown argument" not in captured.err


def test_cli_top_level_help_does_not_print_unknown_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``plex-renamer --help`` is clean too."""
    from plex_renamer.cli.main import app

    code = app(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "unknown argument" not in captured.err
