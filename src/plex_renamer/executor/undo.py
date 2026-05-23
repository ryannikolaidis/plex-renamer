"""Undo a previously-applied batch.

Reads a journal and inverts every verified op. Two cases:

* **Cleanup did NOT run**: undo deletes the verified targets (sources
  still exist). Idempotent on missing targets.
* **Cleanup ran**: undo cannot restore the deleted sources. Instead it
  moves the verified targets to a ``_undo_<batch_id>/`` review folder
  under the library root and reports that sources are unrecoverable.

Either way we mark every entry as ``reverted`` in the journal.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from plex_renamer.executor.journal import Journal


@dataclass
class UndoResult:
    reverted: int
    moved_to_review: int
    review_dir: Path | None
    sources_recoverable: bool


def undo_batch(journal: Journal) -> UndoResult:
    """Invert the operations recorded in ``journal``.

    Returns an :class:`UndoResult` summarizing what happened.
    """
    review_dir: Path | None = None
    if journal.cleanup_ran:
        review_dir = Path(journal.library_root) / f"_undo_{journal.batch_id}"
        review_dir.mkdir(parents=True, exist_ok=True)

    reverted = 0
    moved = 0
    # Reverse order: nested files first, then parents.
    for entry in sorted(journal.verified_entries, key=lambda e: -len(Path(e.target).parts)):
        target = Path(entry.target)
        if not target.exists():
            journal.mark_reverted(entry.op_index)
            reverted += 1
            continue
        if journal.cleanup_ran and review_dir is not None:
            dest = review_dir / target.name
            # If a name collision occurs (rare), suffix with op_index.
            if dest.exists():
                dest = review_dir / f"{target.stem}__op{entry.op_index}{target.suffix}"
            shutil.move(str(target), str(dest))
            moved += 1
        else:
            target.unlink()
        journal.mark_reverted(entry.op_index)
        reverted += 1
        _prune_empty_dirs(target.parent, stop_at=Path(journal.library_root))

    return UndoResult(
        reverted=reverted,
        moved_to_review=moved,
        review_dir=review_dir,
        sources_recoverable=not journal.cleanup_ran,
    )


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    """Walk up from ``start``, removing empty dirs until ``stop_at``.

    Soft-fails on any error (the journal is the source of truth, not
    the directory state).
    """
    current = start
    try:
        stop_resolved = stop_at.resolve()
    except OSError:
        return
    while True:
        try:
            if current.resolve() == stop_resolved:
                return
        except OSError:
            return
        if not current.is_dir():
            return
        try:
            next(iter(current.iterdir()))
            return  # not empty
        except StopIteration:
            pass
        try:
            current.rmdir()
        except OSError:
            return
        parent = current.parent
        if parent == current:
            return
        current = parent


__all__ = ["UndoResult", "undo_batch"]
