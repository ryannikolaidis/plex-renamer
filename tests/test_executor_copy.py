"""Executor copy + verify tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_apply_plan_renames_when_cleanup_and_same_filesystem(safe_tmp_path: Path) -> None:
    """With ``cleanup=True`` and source/target on one FS, the op_rename
    fast-path runs: source is gone (no longer at the original path),
    target exists, and zero bytes were copied (the inode moved).
    """
    plan = _plan_with_op(safe_tmp_path, payload=b"rename-fast-path")
    src = plan.ops[0].source
    target = plan.ops[0].target
    src_inode = src.stat().st_ino

    result = apply_plan(plan, journal_dir=safe_tmp_path / "journals", cleanup=True)
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.cleanup_ran is True
    # Source path is now empty; target carries the same inode.
    assert not src.exists()
    assert target.exists()
    assert target.stat().st_ino == src_inode
    assert target.read_bytes() == b"rename-fast-path"


def test_apply_plan_falls_back_to_copy_on_exdev(
    safe_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``os.rename`` raises EXDEV (cross-filesystem), the executor
    falls back to ``shutil.copy2`` + verify + (eventual) cleanup-delete.
    Simulated by monkeypatching ``os.rename`` to raise EXDEV.
    """
    import errno
    import os as os_mod

    from plex_renamer.executor import copy as copy_module

    plan = _plan_with_op(safe_tmp_path, payload=b"cross-fs-fallback")

    real_rename = os_mod.rename

    def fake_rename(_src: str, _dst: str) -> None:
        raise OSError(errno.EXDEV, "simulated cross-device")

    monkeypatch.setattr(copy_module.os, "rename", fake_rename)

    result = apply_plan(plan, journal_dir=safe_tmp_path / "journals", cleanup=True)
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.cleanup_ran is True
    # Source removed by cleanup pass, target carries the payload.
    assert not plan.ops[0].source.exists()
    assert plan.ops[0].target.read_bytes() == b"cross-fs-fallback"

    # Sanity: with the patch off, real rename works again.
    _ = real_rename


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
