"""Deletion preview: surface the parent chain the executor will prune.

The cleanup confirmation modal must list every path that will disappear
when ``cleanup_sources`` runs, not just the source files. The engine's
:func:`cleanup_sources` already prunes empty parent directories up to
(but not including) the ``input_root``; the modal needs the matching
inspection-only function so the user's consent matches the actual
deletion set.

These tests cover the contract:

* Sources with shared empty parent -> parent included in the preview.
* Sources with a parent containing unrelated content -> parent excluded.
* Guard-violating sources are silently omitted (preview is informational,
  not enforcement).
* Preview is empty when the input_root itself would be refused.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.executor.cleanup import deletion_preview


def test_preview_includes_shared_empty_parent(safe_tmp_path: Path) -> None:
    """Two sources sharing a parent with no other contents -> parent in preview."""
    input_root = safe_tmp_path / "in"
    sub = input_root / "Movies" / "X"
    sub.mkdir(parents=True)
    src1 = sub / "a.mkv"
    src2 = sub / "b.mkv"
    src1.touch()
    src2.touch()

    preview = deletion_preview([src1, src2], input_root)

    assert src1.resolve() in preview
    assert src2.resolve() in preview
    # Both parents up to (but not including) input_root are empty after
    # we remove the two sources -> both included.
    assert sub.resolve() in preview
    assert (input_root / "Movies").resolve() in preview
    # input_root itself is never included.
    assert input_root.resolve() not in preview


def test_preview_excludes_parent_with_unrelated_contents(safe_tmp_path: Path) -> None:
    """A parent that has a non-source sibling is NOT pruned."""
    input_root = safe_tmp_path / "in"
    parent = input_root / "Movies"
    parent.mkdir(parents=True)
    src = parent / "x.mkv"
    sibling = parent / "y.nfo"
    src.touch()
    sibling.touch()

    preview = deletion_preview([src], input_root)

    assert src.resolve() in preview
    assert parent.resolve() not in preview


def test_preview_skips_guard_violating_sources(safe_tmp_path: Path) -> None:
    """A source outside input_root is silently dropped from the preview."""
    input_root = safe_tmp_path / "in"
    input_root.mkdir(parents=True)
    inside = input_root / "ok.mkv"
    inside.touch()
    outside = safe_tmp_path / "elsewhere" / "bad.mkv"
    outside.parent.mkdir(parents=True)
    outside.touch()

    preview = deletion_preview([inside, outside], input_root)

    # The outside path is not included; the inside one is.
    assert inside.resolve() in preview
    assert outside.resolve() not in preview


def test_preview_empty_when_input_root_refused() -> None:
    """An input_root that itself fails guards -> empty preview."""
    # /Users is in the always-disallowed list; the preview refuses to
    # produce anything.
    preview = deletion_preview([Path("/Users/ryan/something.mkv")], Path("/Users"))
    assert preview == []


def test_preview_no_sources_returns_empty(safe_tmp_path: Path) -> None:
    """An empty source list returns an empty preview without raising."""
    input_root = safe_tmp_path / "in"
    input_root.mkdir(parents=True)
    assert deletion_preview([], input_root) == []


def test_preview_orders_parents_deepest_first(safe_tmp_path: Path) -> None:
    """Parents are returned deepest-first so the UI lists them in walk order."""
    input_root = safe_tmp_path / "in"
    deep = input_root / "Movies" / "X" / "deep"
    deep.mkdir(parents=True)
    src = deep / "file.mkv"
    src.touch()

    preview = deletion_preview([src], input_root)

    # Source first, then parents from deep to shallow.
    parents_only = [p for p in preview if p.is_dir()]
    # Compare by depth (longer parts == deeper).
    depths = [len(p.parts) for p in parents_only]
    assert depths == sorted(depths, reverse=True)
    assert deep.resolve() in parents_only
    assert (input_root / "Movies" / "X").resolve() in parents_only
    assert (input_root / "Movies").resolve() in parents_only


def test_preview_deduplicates_repeated_sources(safe_tmp_path: Path) -> None:
    """Passing the same source twice yields one entry, not two."""
    input_root = safe_tmp_path / "in"
    sub = input_root / "Movies"
    sub.mkdir(parents=True)
    src = sub / "x.mkv"
    src.touch()

    preview = deletion_preview([src, src], input_root)
    assert preview.count(src.resolve()) == 1
