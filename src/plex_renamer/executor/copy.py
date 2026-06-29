"""Plan application: move/copy every op with verification + journal.

The flow per op is::

    1. journal.add_pending(op_index, source, target)
    2. mkdir parents
    3. _do_copy(source, target, allow_rename=cleanup)
       - When ``cleanup=True`` AND source/target share a filesystem, this
         is ``os.rename`` (atomic, metadata-only, no bytes copied).
       - Otherwise (or when ``cleanup=False``), this is ``shutil.copy2``.
    4. If copied: verify_size + optional verify_hash.
       If renamed: skip verification — the inode is identical and the
       source no longer exists.
    5. journal.mark_verified(op_index, bytes, sha)
       OR journal.mark_failed(op_index, error)
    6. for each sidecar pair, repeat 2-5

Sidecars are recorded as separate journal entries with
``parent_op_index`` set to the primary's op_index and their own
``op_index`` equal to the sidecar's position within the parent's
sidecar tuple. The (op_index, parent_op_index) tuple is the journal's
lookup key so a sidecar's local position cannot collide with a later
primary op's index.

Two public entry points:

* :func:`apply_plan` — synchronous; returns an :class:`ApplyResult`.
* :func:`apply_plan_iter` — generator; yields per-op progress events
  (``op_started`` / ``op_verified`` / ``op_failed``) interleaved with
  the actual copies, then a final ``done`` event carrying the same
  :class:`ApplyResult`. Use this when a caller needs to render live
  progress (the daemon's JSON-RPC streaming path, a future TQDM-backed
  CLI mode, etc.). :func:`apply_plan` is a thin wrapper that exhausts
  :func:`apply_plan_iter` and returns the result.
"""

from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def apply_plan_iter(
    plan: RenamePlan,
    *,
    journal: Journal | None = None,
    journal_dir: Path | None = None,
    cleanup: bool = False,
    verify_hash: bool = False,
    prune_empty_parents: bool = True,
) -> Iterator[dict[str, Any]]:
    """Generator variant of :func:`apply_plan` that yields progress.

    Yields dicts with an ``"event"`` key:

    * ``{"event": "op_started", "op_index": int, "total_ops": int,
        "source": str, "target": str, "total_bytes": int | None}`` — emitted
      immediately before the copy of op ``op_index`` begins.
    * ``{"event": "op_verified", "op_index": int, "total_ops": int,
        "source": str, "target": str, "bytes": int}`` — emitted after a
      successful primary + sidecar copy + verification.
    * ``{"event": "op_failed", "op_index": int, "total_ops": int,
        "source": str, "target": str, "error": str}`` — emitted after a
      primary OR sidecar failure.
    * ``{"event": "done", "result": ApplyResult}`` — the terminal event.

    The events are interleaved with the actual copy work — each
    ``op_started`` lands BEFORE the corresponding :func:`shutil.copy2`
    runs and the matching ``op_verified`` / ``op_failed`` lands AFTER.
    A streaming consumer (the daemon's apply_plan RPC, a CLI progress
    bar) sees per-op cadence even when individual copies take minutes.
    """
    if journal is None:
        journal = Journal.new(
            input_root=plan.input_root,
            library_root=plan.movies_root,
            journal_dir=journal_dir,
        )

    total_ops = len(plan.ops)
    succeeded = 0
    failed = 0
    for idx, op in enumerate(plan.ops):
        try:
            source_size = op.source.stat().st_size
        except OSError:
            source_size = None
        yield {
            "event": "op_started",
            "op_index": idx,
            "total_ops": total_ops,
            "source": str(op.source),
            "target": str(op.target),
            "total_bytes": source_size,
        }
        try:
            # ``allow_rename`` mirrors ``cleanup``: the rename fast-path
            # only runs when the caller has opted into the source-gets-
            # removed semantics. With ``cleanup=False`` we must preserve
            # the source so we always copy.
            _copy_one(op, idx, journal, verify_hash=verify_hash, allow_rename=cleanup)
            succeeded += 1
            try:
                bytes_copied = op.target.stat().st_size
            except OSError:
                bytes_copied = 0
            yield {
                "event": "op_verified",
                "op_index": idx,
                "total_ops": total_ops,
                "source": str(op.source),
                "target": str(op.target),
                "bytes": bytes_copied,
            }
        except _SidecarCopyError as sc_exc:
            journal.mark_failed(
                sc_exc.sidecar_op_index,
                error=str(sc_exc.__cause__ or sc_exc),
                parent_op_index=idx,
            )
            failed += 1
            yield {
                "event": "op_failed",
                "op_index": idx,
                "total_ops": total_ops,
                "source": str(op.source),
                "target": str(op.target),
                "error": str(sc_exc.__cause__ or sc_exc),
            }
        except Exception as exc:
            journal.mark_failed(idx, error=str(exc))
            failed += 1
            yield {
                "event": "op_failed",
                "op_index": idx,
                "total_ops": total_ops,
                "source": str(op.source),
                "target": str(op.target),
                "error": str(exc),
            }

    cleanup_did_run = False
    if cleanup and failed == 0 and journal.all_verified:
        sources = [Path(e.source) for e in journal.verified_entries]
        cleanup_did_run = cleanup_sources(
            sources,
            input_root=plan.input_root,
            prune_empty_parents=prune_empty_parents,
        )
        journal.mark_cleanup(cleanup_did_run)

    yield {
        "event": "done",
        "result": ApplyResult(
            succeeded=succeeded,
            failed=failed,
            cleanup_ran=cleanup_did_run,
            journal_path=journal.path,
        ),
    }


def apply_plan(
    plan: RenamePlan,
    *,
    journal: Journal | None = None,
    journal_dir: Path | None = None,
    cleanup: bool = False,
    verify_hash: bool = False,
    prune_empty_parents: bool = True,
) -> ApplyResult:
    """Apply ``plan``'s ops. Writes a journal, verifies, optionally cleans up.

    Returns an :class:`ApplyResult` summarizing the batch. The journal
    object is persisted under ``journal_dir`` (or the app-data default)
    and the path is in the returned result.

    Failures don't abort the whole batch; each op fails independently so
    a partial success still produces a usable journal for the rest.
    Cleanup is gated on every op verifying.

    This wraps :func:`apply_plan_iter` so callers that want progress
    events can switch to the iterator variant without an API change
    here.
    """
    for event in apply_plan_iter(
        plan,
        journal=journal,
        journal_dir=journal_dir,
        cleanup=cleanup,
        verify_hash=verify_hash,
        prune_empty_parents=prune_empty_parents,
    ):
        if event.get("event") == "done":
            return event["result"]
    raise RuntimeError("apply_plan_iter exhausted without a 'done' event")


class _SidecarCopyError(Exception):
    """Raised when a sidecar copy fails after the primary already verified.

    Carries the sidecar's local ``op_index`` so the caller can mark the
    correct journal entry (composite key ``(sidecar_op_index, parent_op_index)``)
    rather than corrupting the primary's verified status.
    """

    def __init__(self, sidecar_op_index: int, message: str) -> None:
        super().__init__(message)
        self.sidecar_op_index = sidecar_op_index


def _copy_one(
    op: RenameOp,
    op_index: int,
    journal: Journal,
    *,
    verify_hash: bool,
    allow_rename: bool,
) -> None:
    # Primary file. Exceptions here propagate to the outer ``except`` in
    # ``apply_plan``, which marks the primary entry (op_index, None) failed.
    journal.add_pending(op_index, op.source, op.target)
    renamed = _do_copy(op.source, op.target, allow_rename=allow_rename)
    sha: str | None = None
    if not renamed:
        if not verify_size(op.source, op.target):
            raise RuntimeError(f"size mismatch on {op.target}")
        if verify_hash:
            matched, sha = verify_hash_with_digest(op.source, op.target)
            if not matched:
                raise RuntimeError(f"sha256 mismatch on {op.target}")
    journal.mark_verified(op_index, bytes_copied=op.target.stat().st_size, sha256=sha)

    # Sidecars: separate journal entries keyed by (sidecar_pos, parent_op_index).
    # Each sidecar copy is scoped so a failure surfaces as a
    # ``_SidecarCopyError`` carrying the sidecar's local op_index. The
    # primary remains verified — only the failing sidecar's entry flips
    # to "failed".
    for i, (src, dst) in enumerate(op.sidecars):
        try:
            journal.add_pending(i, src, dst, parent_op_index=op_index)
            sidecar_renamed = _do_copy(src, dst, allow_rename=allow_rename)
            sub_sha: str | None = None
            if not sidecar_renamed:
                if not verify_size(src, dst):
                    raise RuntimeError(f"sidecar size mismatch on {dst}")
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
        except Exception as exc:
            raise _SidecarCopyError(i, str(exc)) from exc


def _do_copy(source: Path, target: Path, *, allow_rename: bool) -> bool:
    """Move/copy ``source`` -> ``target``. Returns True on rename, False on copy.

    When ``allow_rename`` is set, tries ``os.rename`` first — metadata-
    only, instant, no bytes moved. Same-filesystem moves take this path.
    Cross-filesystem renames raise ``OSError(EXDEV)`` and we fall back
    to ``shutil.copy2``. The caller (``_copy_one``) skips size/hash
    verification when this returns True since the inode is identical.

    When ``allow_rename`` is False, always copies. This preserves the
    contract for callers that want the source file to survive (the CLI
    ``plan`` + ``apply --no-cleanup`` pair).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if allow_rename:
        try:
            os.rename(source, target)
            return True
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
    shutil.copy2(source, target)
    return False


__all__ = ["ApplyResult", "apply_plan"]
