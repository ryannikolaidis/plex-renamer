"""Plan application: copy every op with verification + journal.

The flow per op is::

    1. journal.add_pending(op_index, source, target)
    2. mkdir parents
    3. shutil.copy2(source, target)
    4. verify_size + optional verify_hash
    5. journal.mark_verified(op_index, bytes, sha)
       OR journal.mark_failed(op_index, error)
    6. for each sidecar pair, repeat 2-5

Sidecars are recorded as separate journal entries with
``parent_op_index`` set to the primary's op_index and their own
``op_index`` equal to the sidecar's position within the parent's
sidecar tuple. The (op_index, parent_op_index) tuple is the journal's
lookup key so a sidecar's local position cannot collide with a later
primary op's index.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from plex_renamer.executor.cleanup import cleanup_sources
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.verify import verify_hash_with_digest, verify_size
from plex_renamer.planner.models import RenameOp, RenamePlan


@dataclass
class ApplyResult:
    """Summary of an :func:`apply_plan` run.

    ``succeeded`` and ``failed`` are op counts. ``cleanup_ran`` reports
    whether the cleanup pass actually executed (gated on flag + all
    verified + no guard fires).
    """

    succeeded: int
    failed: int
    cleanup_ran: bool
    journal_path: Path


def apply_plan(
    plan: RenamePlan,
    *,
    journal: Journal | None = None,
    journal_dir: Path | None = None,
    cleanup: bool = False,
    verify_hash: bool = False,
) -> ApplyResult:
    """Apply ``plan``'s ops. Writes a journal, verifies, optionally cleans up.

    Returns an :class:`ApplyResult` summarizing the batch. The journal
    object is persisted under ``journal_dir`` (or the app-data default)
    and the path is in the returned result.

    Failures don't abort the whole batch; each op fails independently so
    a partial success still produces a usable journal for the rest.
    Cleanup is gated on every op verifying.
    """
    if journal is None:
        # library_root for the journal is just the closer of the two
        # roots; we record movies_root by convention.
        journal = Journal.new(
            input_root=plan.input_root,
            library_root=plan.movies_root,
            journal_dir=journal_dir,
        )

    succeeded = 0
    failed = 0
    for idx, op in enumerate(plan.ops):
        try:
            _copy_one(op, idx, journal, verify_hash=verify_hash)
            succeeded += 1
        except Exception as exc:
            journal.mark_failed(idx, error=str(exc))
            failed += 1

    cleanup_did_run = False
    if cleanup and failed == 0 and journal.all_verified:
        # The cleanup module enforces its own guards; we just call it.
        sources = [Path(e.source) for e in journal.verified_entries]
        cleanup_did_run = cleanup_sources(sources, input_root=plan.input_root)
        journal.mark_cleanup(cleanup_did_run)
    return ApplyResult(
        succeeded=succeeded,
        failed=failed,
        cleanup_ran=cleanup_did_run,
        journal_path=journal.path,
    )


def _copy_one(op: RenameOp, op_index: int, journal: Journal, *, verify_hash: bool) -> None:
    # Primary file.
    journal.add_pending(op_index, op.source, op.target)
    _do_copy(op.source, op.target)
    if not verify_size(op.source, op.target):
        raise RuntimeError(f"size mismatch on {op.target}")
    sha: str | None = None
    if verify_hash:
        matched, sha = verify_hash_with_digest(op.source, op.target)
        if not matched:
            raise RuntimeError(f"sha256 mismatch on {op.target}")
    journal.mark_verified(op_index, bytes_copied=op.target.stat().st_size, sha256=sha)

    # Sidecars: separate journal entries keyed by (sidecar_pos, parent_op_index).
    for i, (src, dst) in enumerate(op.sidecars):
        journal.add_pending(i, src, dst, parent_op_index=op_index)
        _do_copy(src, dst)
        if not verify_size(src, dst):
            raise RuntimeError(f"sidecar size mismatch on {dst}")
        sub_sha: str | None = None
        if verify_hash:
            matched, sub_sha = verify_hash_with_digest(src, dst)
            if not matched:
                raise RuntimeError(f"sidecar sha256 mismatch on {dst}")
        journal.mark_verified(
            i,
            bytes_copied=dst.stat().st_size,
            sha256=sub_sha,
            parent_op_index=op_index,
        )


def _do_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


__all__ = ["ApplyResult", "apply_plan"]
