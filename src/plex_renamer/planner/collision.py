"""Target-path collision detection.

When two source files would land at the same target path, the planner
removes BOTH from the op list and emits a :class:`Collision` instead.
The GUI's review queue surfaces these for per-row resolution (slice 5).
Slice 4 does not auto-resolve.

Why both are removed: keeping one and dropping the other silently picks
a winner. That's the wrong behavior — the user needs to see which two
sources collided and decide which to keep, rename, or skip.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from plex_renamer.planner.models import Collision, CollisionReason, RenameOp


def detect_collisions(
    ops: list[RenameOp],
) -> tuple[list[RenameOp], list[Collision]]:
    """Partition ``ops`` into (clean_ops, collisions).

    Two ops with the same target are a collision. The classification
    heuristic for ``reason``:

    * If both share an anchor: ``same_anchor_different_source``.
    * Else: ``duplicate_input`` (we don't try to distinguish further).
    """
    by_target: dict[Path, list[RenameOp]] = defaultdict(list)
    for op in ops:
        by_target[op.target].append(op)

    clean: list[RenameOp] = []
    collisions: list[Collision] = []
    for target, group in by_target.items():
        if len(group) == 1:
            clean.append(group[0])
            continue
        reason = _reason_for(group)
        collisions.append(
            Collision(
                target=target,
                sources=tuple(op.source for op in group),
                reason=reason,
            )
        )
    return clean, collisions


def _reason_for(group: list[RenameOp]) -> CollisionReason:
    anchors = {op.anchor for op in group}
    if len(anchors) == 1:
        return "same_anchor_different_source"
    return "duplicate_input"


__all__ = ["detect_collisions"]
