"""Cleanup-guard tests.

The cleanup pass must NEVER delete:

* The user's ``input_root`` itself.
* A path with fewer than 3 components below the FS root.
* A path matching the always-disallowed list.
* A path that is not a strict descendant of ``input_root``.

ANY guard fire aborts the WHOLE cleanup pass; we never delete a subset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plex_renamer.executor.cleanup import CleanupRefused, cleanup_sources


def test_cleanup_refuses_when_root_too_shallow(tmp_path: Path) -> None:
    """An input_root with fewer than 3 components below / refuses outright."""
    # /Users/ryan has 2 components. The cleanup helper inspects the
    # input_root depth as a defence-in-depth guard.
    with pytest.raises(CleanupRefused):
        cleanup_sources([Path("/Users/ryan/something")], input_root=Path("/Users"))


def test_cleanup_refuses_for_non_descendant(tmp_path: Path) -> None:
    """A source outside input_root refuses."""
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True)
    outside = tmp_path / "outside" / "file.mkv"
    outside.parent.mkdir(parents=True)
    outside.touch()
    with pytest.raises(CleanupRefused):
        cleanup_sources([outside], input_root=input_root)


def test_cleanup_refuses_always_disallowed(tmp_path: Path) -> None:
    """An always-disallowed path (e.g. /Users/<any>) refuses."""
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True)
    # Synthesize a path that's a descendant but happens to be guarded.
    # /Users/ryan looks like input_root /Users/ryan; we just check the
    # function directly.
    with pytest.raises(CleanupRefused):
        cleanup_sources([Path("/Users/ryan")], input_root=Path("/Users/ryan/scratch"))


def test_cleanup_deletes_sources_in_tree(tmp_path: Path) -> None:
    """Happy path: sources land in tmp_path and get deleted."""
    input_root = tmp_path / "in"
    sub = input_root / "Movies" / "X"
    sub.mkdir(parents=True)
    src1 = sub / "a.mkv"
    src2 = sub / "b.mkv"
    src1.touch()
    src2.touch()
    cleanup_sources([src1, src2], input_root=input_root)
    assert not src1.exists()
    assert not src2.exists()


def test_cleanup_prunes_empty_descendants(tmp_path: Path) -> None:
    """Empty dirs under input_root get removed up to but not including input_root."""
    input_root = tmp_path / "in"
    deep = input_root / "Movies" / "X" / "deep"
    deep.mkdir(parents=True)
    src = deep / "file.mkv"
    src.touch()
    cleanup_sources([src], input_root=input_root)
    # The leaf and its empty parents up to input_root are gone.
    assert not deep.exists()
    assert not (input_root / "Movies" / "X").exists()
    # input_root itself remains.
    assert input_root.exists()


def test_cleanup_keeps_non_empty_parents(tmp_path: Path) -> None:
    """A parent with other contents stays."""
    input_root = tmp_path / "in"
    parent = input_root / "Movies"
    parent.mkdir(parents=True)
    src = parent / "x.mkv"
    sibling = parent / "y.nfo"
    src.touch()
    sibling.touch()
    cleanup_sources([src], input_root=input_root)
    assert not src.exists()
    # Sibling intact; parent intact.
    assert sibling.exists()
    assert parent.exists()


def test_cleanup_refuses_zero_sources(tmp_path: Path) -> None:
    """Empty list returns False without raising."""
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True)
    result = cleanup_sources([], input_root=input_root)
    assert result is False


def test_cleanup_validates_all_before_deleting(tmp_path: Path) -> None:
    """One bad source aborts the whole batch; no partial deletion."""
    input_root = tmp_path / "in"
    input_root.mkdir(parents=True)
    good = input_root / "good.mkv"
    good.touch()
    bad = tmp_path / "elsewhere" / "bad.mkv"
    bad.parent.mkdir(parents=True)
    bad.touch()
    with pytest.raises(CleanupRefused):
        cleanup_sources([good, bad], input_root=input_root)
    # The good one survived because the whole batch aborted.
    assert good.exists()
