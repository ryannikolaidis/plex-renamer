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


__all__ = ["CleanupRefused", "cleanup_sources"]
