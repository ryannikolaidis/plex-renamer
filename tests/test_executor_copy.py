"""Executor copy + verify tests."""

from __future__ import annotations

from pathlib import Path

from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.verify import sha256_of, verify_hash, verify_size
from plex_renamer.planner.models import RenameOp, RenamePlan


def _write_bytes(p: Path, payload: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _plan_with_op(tmp_path: Path, payload: bytes = b"abc") -> RenamePlan:
    src = _write_bytes(tmp_path / "in" / "x.mkv", payload)
    target = tmp_path / "out" / "Movies" / "X" / "X.mkv"
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
        movies_root=tmp_path / "out" / "Movies",
        tv_root=tmp_path / "out" / "TV",
        input_root=tmp_path / "in",
    )


def test_verify_size(tmp_path: Path) -> None:
    a = _write_bytes(tmp_path / "a", b"hello")
    b = _write_bytes(tmp_path / "b", b"hello")
    assert verify_size(a, b)
    c = _write_bytes(tmp_path / "c", b"hellox")
    assert not verify_size(a, c)


def test_verify_hash(tmp_path: Path) -> None:
    a = _write_bytes(tmp_path / "a", b"payload")
    b = _write_bytes(tmp_path / "b", b"payload")
    assert verify_hash(a, b)
    c = _write_bytes(tmp_path / "c", b"payloaX")
    assert not verify_hash(a, c)


def test_sha256_of(tmp_path: Path) -> None:
    a = _write_bytes(tmp_path / "a", b"hello")
    digest = sha256_of(a)
    # sha256("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_apply_plan_copies_and_verifies(tmp_path: Path) -> None:
    plan = _plan_with_op(tmp_path, payload=b"matrix-bytes")
    journal_dir = tmp_path / "journals"
    result = apply_plan(plan, journal_dir=journal_dir, cleanup=False)
    assert result.succeeded == 1
    assert result.failed == 0
    assert plan.ops[0].target.exists()
    assert plan.ops[0].target.read_bytes() == b"matrix-bytes"
    # Source remains.
    assert plan.ops[0].source.exists()


def test_apply_plan_with_hash_verification(tmp_path: Path) -> None:
    plan = _plan_with_op(tmp_path, payload=b"abc" * 1024)
    journal_dir = tmp_path / "journals"
    result = apply_plan(plan, journal_dir=journal_dir, cleanup=False, verify_hash=True)
    assert result.succeeded == 1
    assert result.failed == 0


def test_apply_plan_writes_journal(tmp_path: Path) -> None:
    plan = _plan_with_op(tmp_path)
    journal_dir = tmp_path / "journals"
    result = apply_plan(plan, journal_dir=journal_dir, cleanup=False)
    assert result.journal_path.exists()
    import json

    data = json.loads(result.journal_path.read_text())
    assert data["version"] == 1
    assert len(data["entries"]) == 1
    assert data["entries"][0]["status"] == "verified"


def test_apply_plan_handles_sidecars(tmp_path: Path) -> None:
    src = _write_bytes(tmp_path / "in" / "x.mkv", b"video")
    sc_src = _write_bytes(tmp_path / "in" / "x.en.srt", b"subtitle")
    target = tmp_path / "out" / "X" / "X.mkv"
    sc_target = tmp_path / "out" / "X" / "X.en.srt"
    op = RenameOp(
        source=src,
        target=target,
        kind="movie",
        anchor="tmdb-1",
        edition=None,
        confidence=0.9,
        sidecars=((sc_src, sc_target),),
    )
    plan = RenamePlan(
        ops=(op,),
        collisions=(),
        skipped=(),
        movies_root=tmp_path / "out",
        tv_root=tmp_path / "tv",
        input_root=tmp_path / "in",
    )
    result = apply_plan(plan, journal_dir=tmp_path / "journals", cleanup=False)
    assert result.succeeded == 1
    assert sc_target.exists()
    assert sc_target.read_bytes() == b"subtitle"
