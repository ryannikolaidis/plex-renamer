"""Public data shapes for the planner.

These dataclasses are the contract between the planner stage and the
executor + CLI. They are frozen so they round-trip cleanly through JSON
and so callers can't accidentally mutate them mid-pipeline.

The JSON serialization format is the same shape the CLI exposes between
``plan`` and ``apply``: it MUST be stable across versions, so we
serialize concrete fields with explicit keys rather than relying on
``dataclasses.asdict``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

PLAN_VERSION = 1
"""Bump when the JSON shape changes incompatibly."""

CollisionReason = Literal[
    "same_anchor_different_source",
    "duplicate_input",
    "title_collision",
]


@dataclass(frozen=True)
class RenameOp:
    """A single copy the executor will perform.

    ``sidecars`` carry their own (source, target) pairs because they're
    renamed alongside the primary video — the executor walks them too.
    """

    source: Path
    target: Path
    kind: Literal["movie", "tv"]
    anchor: str
    """Rendered anchor token: ``tmdb-12345`` or ``imdb-tt6293822``."""

    edition: str | None
    confidence: float
    sidecars: tuple[tuple[Path, Path], ...] = ()
    warnings: tuple[str, ...] = ()
    detected_editions: tuple[str, ...] = ()
    """Parser-surfaced edition tokens, regardless of ``apply_editions``.

    Always populated from ``ParseResult.edition_tokens`` even when the
    planner is invoked with ``apply_editions=False`` (the default). The
    field is the GUI's read-only view of what the parser found so the UI
    can render "we detected Director's Cut, accept?" without re-parsing
    the source. The ``edition`` field above gates the actual path stamp.
    """


@dataclass(frozen=True)
class Collision:
    """Two or more sources targeting the same path.

    The planner emits a Collision instead of the conflicting ops; the GUI
    review queue surfaces this for per-row resolution. Slice 4 does not
    auto-resolve.
    """

    target: Path
    sources: tuple[Path, ...]
    reason: CollisionReason


@dataclass(frozen=True)
class RenamePlan:
    """The full output of :func:`plex_renamer.planner.build_plan`.

    Skipped entries carry a free-form reason string for the run report.
    """

    ops: tuple[RenameOp, ...]
    collisions: tuple[Collision, ...]
    skipped: tuple[tuple[Path, str], ...]
    movies_root: Path
    tv_root: Path
    input_root: Path
    apply_editions: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ----- Serialization ---------------------------------------------------

    def to_json(self) -> str:
        """Serialize to a stable JSON string."""
        return json.dumps(self._to_dict(), indent=2, sort_keys=False)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "version": PLAN_VERSION,
            "movies_root": _path_str(self.movies_root),
            "tv_root": _path_str(self.tv_root),
            "input_root": _path_str(self.input_root),
            "apply_editions": self.apply_editions,
            "warnings": list(self.warnings),
            "ops": [_op_to_dict(op) for op in self.ops],
            "collisions": [_collision_to_dict(c) for c in self.collisions],
            "skipped": [{"path": _path_str(p), "reason": r} for (p, r) in self.skipped],
        }

    @classmethod
    def from_json(cls, text: str) -> RenamePlan:
        """Deserialize a JSON string produced by :meth:`to_json`."""
        data = json.loads(text)
        version = data.get("version", 1)
        if version != PLAN_VERSION:
            raise ValueError(
                f"plan version mismatch: file is v{version}, current code is v{PLAN_VERSION}"
            )
        ops = tuple(_op_from_dict(d) for d in data.get("ops", []))
        collisions = tuple(_collision_from_dict(d) for d in data.get("collisions", []))
        skipped = tuple((Path(item["path"]), item["reason"]) for item in data.get("skipped", []))
        return cls(
            ops=ops,
            collisions=collisions,
            skipped=skipped,
            movies_root=Path(data["movies_root"]),
            tv_root=Path(data["tv_root"]),
            input_root=Path(data["input_root"]),
            apply_editions=bool(data.get("apply_editions", False)),
            warnings=tuple(data.get("warnings", [])),
        )


def _path_str(p: Path) -> str:
    """Stringify a Path for JSON. Stored as POSIX style; the OS reads it back fine."""
    return str(p)


def _op_to_dict(op: RenameOp) -> dict[str, Any]:
    return {
        "source": _path_str(op.source),
        "target": _path_str(op.target),
        "kind": op.kind,
        "anchor": op.anchor,
        "edition": op.edition,
        "confidence": op.confidence,
        "sidecars": [[_path_str(s), _path_str(t)] for (s, t) in op.sidecars],
        "warnings": list(op.warnings),
        "detected_editions": list(op.detected_editions),
    }


def _op_from_dict(d: dict[str, Any]) -> RenameOp:
    sidecars_raw = d.get("sidecars") or []
    sidecars = tuple((Path(s), Path(t)) for (s, t) in sidecars_raw)
    return RenameOp(
        source=Path(d["source"]),
        target=Path(d["target"]),
        kind=d["kind"],
        anchor=d["anchor"],
        edition=d.get("edition"),
        confidence=float(d.get("confidence", 0.0)),
        sidecars=sidecars,
        warnings=tuple(d.get("warnings", [])),
        detected_editions=tuple(d.get("detected_editions", [])),
    )


def _collision_to_dict(c: Collision) -> dict[str, Any]:
    return {
        "target": _path_str(c.target),
        "sources": [_path_str(s) for s in c.sources],
        "reason": c.reason,
    }


def _collision_from_dict(d: dict[str, Any]) -> Collision:
    return Collision(
        target=Path(d["target"]),
        sources=tuple(Path(s) for s in d.get("sources", [])),
        reason=d.get("reason", "duplicate_input"),
    )


# Re-export PurePosixPath for callers that want to compare logical paths
# across platforms without resolving.
__all__ = [
    "Collision",
    "CollisionReason",
    "PLAN_VERSION",
    "PurePosixPath",
    "RenameOp",
    "RenamePlan",
]
