"""Source-cleanup pass with hard safety guards.

This module's job: given a list of verified source files plus the
user's ``input_root``, delete only the sources (and their now-empty
descendants up to but not including the input_root) IF AND ONLY IF
every guard passes.

Guards (every guard must pass for any deletion to proceed):

1. The path is a strict descendant of ``input_root`` (never the input
   root itself).
2. The path has at least 3 components below the FS root.
3. The path is not in the always-disallowed prefix list
   (``/``, ``/Users``, ``/Users/<any>``, etc. — see ``path_safety``).
4. The path resolves cleanly (no symlink games into a guarded area).

If ANY single source fails a guard, the WHOLE cleanup pass aborts. We
don't delete a subset and silently skip the violators. The error
propagates to the run report.

Cleanup of empty parents walks up the chain stopping when:

* The next parent is the input_root itself, OR
* The next parent has remaining contents, OR
* The next parent fails a guard.

This module also exposes :func:`deletion_preview`: a pure-inspection
function that returns the deduplicated list of paths the cleanup pass
WOULD remove (the source files plus the parent chain that would be
pruned). The GUI's cleanup-confirmation modal renders this preview so
the user sees every directory that will disappear, not just the source
files themselves.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.planner.path_safety import (
    has_at_least_three_components,
    is_always_disallowed,
    is_strict_descendant,
)


def cleanup_sources(sources: list[Path], input_root: Path) -> bool:
    """Delete every source file plus now-empty descendants.

    Returns True if cleanup succeeded; False or raises on guard failure.
    Raises :class:`CleanupRefused` when ANY guard fires; no deletion
    occurs in that case.
    """
    if not sources:
        return False

    input_root_abs = input_root.resolve()
    if is_always_disallowed(input_root_abs):
        raise CleanupRefused(f"input_root itself is always-disallowed: {input_root_abs}")
    if not has_at_least_three_components(input_root_abs):
        raise CleanupRefused(
            f"input_root has fewer than 3 components below FS root: {input_root_abs}"
        )

    # Validate every source up front; nothing deletes on guard failure.
    resolved_sources: list[Path] = []
    for src in sources:
        try:
            r = src.resolve()
        except OSError as exc:
            raise CleanupRefused(f"cannot resolve source {src}: {exc}") from exc
        _check_guards(r, input_root_abs)
        resolved_sources.append(r)

    # First pass: delete the source files.
    for path in resolved_sources:
        if path.exists():
            path.unlink()

    # Second pass: walk up parents, deleting empties; never cross
    # ``input_root_abs`` or violate guards.
    parents_to_check = {p.parent for p in resolved_sources}
    for parent in sorted(parents_to_check, key=lambda p: -len(p.parts)):
        _prune_empty_chain(parent, input_root_abs)
    return True


def _check_guards(path: Path, input_root: Path) -> None:
    """Raise :class:`CleanupRefused` if any guard fires for ``path``."""
    if not is_strict_descendant(path, input_root):
        raise CleanupRefused(
            f"cleanup target {path} is not a strict descendant of input_root {input_root}"
        )
    if not has_at_least_three_components(path):
        raise CleanupRefused(f"cleanup target has fewer than 3 components below FS root: {path}")
    if is_always_disallowed(path):
        raise CleanupRefused(f"cleanup target matches always-disallowed list: {path}")


def _prune_empty_chain(start: Path, input_root: Path) -> None:
    """Walk up from ``start`` deleting empty directories.

    Stops at ``input_root`` (never deletes it), at the first non-empty
    parent, or at the first parent that fails a guard.
    """
    current = start
    while True:
        if current == input_root:
            return
        if not _is_strict_descendant_or_equal(current, input_root):
            return
        if current == input_root:
            return
        try:
            _check_guards(current, input_root)
        except CleanupRefused:
            return
        if not current.is_dir():
            return
        # is_dir() may follow symlinks; we want a structural empty check.
        try:
            next(iter(current.iterdir()))
            return  # not empty
        except StopIteration:
            pass
        current.rmdir()
        next_parent = current.parent
        if next_parent == current:
            return
        current = next_parent


def _is_strict_descendant_or_equal(path: Path, root: Path) -> bool:
    if path == root:
        return True
    return is_strict_descendant(path, root)


class CleanupRefused(RuntimeError):
    """Raised when any cleanup-guard fires. No deletion happens."""


def deletion_preview(sources: list[Path], input_root: Path) -> list[Path]:
    """Return every path the cleanup pass would delete, sources + pruned parents.

    The preview is the UI-facing complement to :func:`cleanup_sources`: it
    answers the question "if I clicked Apply right now with cleanup
    enabled, which paths would actually disappear?" without performing
    any filesystem mutation.

    Rules:

    * Sources that fail the same guards :func:`cleanup_sources` enforces
      are SILENTLY OMITTED from the preview. The preview is informational;
      we don't raise here. If the executor later refuses, the modal's
      preview was just optimistic.
    * Parents are included when, after subtracting the planned sources
      (and the parents we've already decided to prune) from the parent's
      contents, the parent has nothing left. The check uses the real
      filesystem snapshot at preview time.
    * Parent inclusion stops at the input_root itself (we never include
      ``input_root``), at the first parent that fails a guard, and at
      the first parent that still has contents after subtraction.
    * The returned list is deduplicated and ordered: sources first (in
      input order), then parents from deepest to shallowest. The order
      matches what cleanup_sources would actually remove.

    The function tolerates sources whose path doesn't exist on disk
    (e.g. the GUI is previewing before the copy stage). Missing parents
    are simply skipped.
    """
    if not sources:
        return []

    try:
        input_root_abs = input_root.resolve()
    except OSError:
        return []

    # If the input_root itself can't survive the guards, no preview.
    if is_always_disallowed(input_root_abs):
        return []
    if not has_at_least_three_components(input_root_abs):
        return []

    # Filter to sources that pass guards. Missing-from-disk paths still
    # appear in the preview as long as their structural guards pass —
    # the preview is about what WOULD be deleted, not what currently
    # exists.
    eligible_sources: list[Path] = []
    seen_sources: set[Path] = set()
    for src in sources:
        try:
            r = src.resolve() if src.exists() else _abspath_no_resolve(src)
        except OSError:
            continue
        if not _passes_guards(r, input_root_abs):
            continue
        if r in seen_sources:
            continue
        seen_sources.add(r)
        eligible_sources.append(r)

    if not eligible_sources:
        return []

    planned: set[Path] = set(eligible_sources)
    parents: list[Path] = []
    parents_seen: set[Path] = set()

    # Walk every unique source-parent and ascend the chain. We process
    # deepest first so a deeper parent's emptiness gets recorded before
    # its grandparent inspects it.
    starting_parents = sorted({p.parent for p in eligible_sources}, key=lambda p: -len(p.parts))
    for start in starting_parents:
        current = start
        while True:
            if current == input_root_abs:
                break
            if not _is_strict_descendant_or_equal(current, input_root_abs):
                break
            if not _passes_guards(current, input_root_abs):
                break
            if not current.exists():
                # If the parent doesn't exist on disk we can't preview
                # its contents; the executor wouldn't delete a missing
                # directory anyway.
                break
            if not current.is_dir():
                break
            if not _would_be_empty_after_pruning(current, planned):
                break
            if current not in parents_seen:
                parents_seen.add(current)
                parents.append(current)
                planned.add(current)
            next_parent = current.parent
            if next_parent == current:
                break
            current = next_parent

    # Sources first (input order), then parents deepest-first.
    parents.sort(key=lambda p: -len(p.parts))
    return list(eligible_sources) + parents


def _passes_guards(path: Path, input_root: Path) -> bool:
    """Return True iff ``path`` would pass every cleanup guard."""
    try:
        _check_guards(path, input_root)
    except CleanupRefused:
        return False
    return True


def _abspath_no_resolve(path: Path) -> Path:
    """Return an absolute Path without touching the filesystem.

    ``Path.resolve()`` errors if a parent doesn't exist on some platforms
    and walks symlinks; for the preview we want a structural absolute
    form so the guards inspect the literal path the executor would see.
    """
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _would_be_empty_after_pruning(directory: Path, planned: set[Path]) -> bool:
    """Return True iff ``directory`` has nothing left after subtracting ``planned``.

    Pure read-only inspection: we list the directory and check that every
    entry is in the planned-removal set.
    """
    try:
        entries = list(directory.iterdir())
    except OSError:
        return False
    return all(entry in planned for entry in entries)


__all__ = ["CleanupRefused", "cleanup_sources", "deletion_preview"]
