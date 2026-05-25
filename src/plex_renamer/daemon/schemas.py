"""Wire-shape definitions for the JSON-RPC daemon.

These are the JSON-serializable shapes the daemon emits on every method
result. They are NOT engine dataclasses (those are Python-only); they
are plain ``dict``s with documented keys so the C# shell (and any future
native shell) can mirror them as POCO records without importing Python.

Every helper here turns an engine object into one of these dict shapes
or parses one back. Keep these in lockstep with ``docs/win-native-bridge.md``
— that file is the source of truth for the protocol contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from plex_renamer.parser.models import ParseResult, SkipReason
from plex_renamer.planner.models import Collision, RenameOp, RenamePlan
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult

# ---------------------------------------------------------------------------
# TypedDicts: documentation-only at runtime, but useful for static checking
# and grepping. None of these are validated at runtime — JSON-RPC takes plain
# dicts and the daemon trusts the shell.
# ---------------------------------------------------------------------------


class CandidateDict(TypedDict, total=False):
    """JSON shape of :class:`plex_renamer.tmdb.models.Candidate`."""

    anchor_kind: str  # "tmdb" | "imdb"
    anchor_id: str
    kind: str  # "movie" | "tv"
    title: str
    year: int | None
    confidence: float
    episode_list: list[EpisodeDict]


class EpisodeDict(TypedDict):
    season: int
    episode: int
    title: str
    air_date: str | None


class ParseResultDict(TypedDict, total=False):
    """JSON shape of :class:`plex_renamer.parser.models.ParseResult`."""

    source_path: str
    kind: str  # "movie" | "tv" | "unknown"
    title_candidate: str | None
    year: int | None
    season: int | None
    episode: int | None
    episode_end: int | None
    episode_title: str | None
    edition_tokens: list[str]
    quality_tokens: list[str]
    group_tag: str | None
    part_marker: str | None
    raw_filename: str
    parent_dirs: list[str]
    skip_reason: dict[str, str] | None


class RowDict(TypedDict, total=False):
    """A source-row carried across the wire.

    Mirrors :class:`plex_renamer.gui.models.ItemRow` but is Qt-free. The
    shell holds these in its own state and passes them back to the daemon
    on every method call that needs row context (``build_plan``,
    ``edit_row``, ``select_anchor``).
    """

    row_id: str  # stable id; usually str(source_path)
    parsed: ParseResultDict
    candidate: CandidateDict | None
    show_name_hint: str | None
    group_key: str
    skip: bool
    manual_title: str | None
    manual_year: int | None
    manual_season: int | None
    manual_episode: int | None
    manual_edition: str | None
    imdb_id_override: str | None
    anchor_kind_override: str | None  # "tmdb" | "imdb" | None


class GroupDict(TypedDict):
    """One group from the source panel.

    A movie's group has exactly one row; a TV group has 1..N rows that
    share a show.
    """

    group_key: str
    kind: str  # "movie" | "tv"
    label: str
    row_ids: list[str]


class RenameOpDict(TypedDict, total=False):
    source: str
    target: str
    kind: str
    anchor: str
    edition: str | None
    confidence: float
    sidecars: list[list[str]]
    warnings: list[str]
    detected_editions: list[str]


class CollisionDict(TypedDict):
    target: str
    sources: list[str]
    reason: str


class RenamePlanDict(TypedDict, total=False):
    ops: list[RenameOpDict]
    collisions: list[CollisionDict]
    skipped: list[dict[str, str]]
    movies_root: str
    tv_root: str
    input_root: str
    apply_editions: bool
    warnings: list[str]


class RunReportDict(TypedDict, total=False):
    succeeded: int
    failed: int
    skipped: int
    cleanup_ran: bool
    journal_path: str | None
    error_messages: list[str]


class UndoReportDict(TypedDict, total=False):
    reverted: int
    moved_to_review: int
    review_dir: str | None
    sources_recoverable: bool


# ---------------------------------------------------------------------------
# Encoders: engine object -> JSON-friendly dict.
# ---------------------------------------------------------------------------


def episode_to_dict(ep: Episode) -> EpisodeDict:
    return {
        "season": ep.season,
        "episode": ep.episode,
        "title": ep.title,
        "air_date": ep.air_date,
    }


def candidate_to_dict(c: Candidate | None) -> CandidateDict | None:
    if c is None:
        return None
    out: CandidateDict = {
        "anchor_kind": c.anchor_kind,
        "anchor_id": c.anchor_id,
        "kind": c.kind,
        "title": c.title,
        "year": c.year,
        "confidence": c.confidence,
    }
    if c.episode_list:
        out["episode_list"] = [episode_to_dict(e) for e in c.episode_list]
    else:
        out["episode_list"] = []
    return out


def movie_result_to_candidate_dict(m: MovieResult, *, confidence: float = 0.7) -> CandidateDict:
    """Encode a TMDB MovieResult as a Candidate dict for picker results."""
    return {
        "anchor_kind": "tmdb",
        "anchor_id": str(m.tmdb_id),
        "kind": "movie",
        "title": m.title,
        "year": m.year,
        "confidence": confidence,
        "episode_list": [],
    }


def tv_result_to_candidate_dict(t: TVResult, *, confidence: float = 0.7) -> CandidateDict:
    return {
        "anchor_kind": "tmdb",
        "anchor_id": str(t.tmdb_id),
        "kind": "tv",
        "title": t.title,
        "year": t.year,
        "confidence": confidence,
        "episode_list": [episode_to_dict(e) for e in t.episode_list] if t.episode_list else [],
    }


def skip_reason_to_dict(s: SkipReason | None) -> dict[str, str] | None:
    if s is None:
        return None
    return {"reason": s.reason, "detail": s.detail}


def parse_result_to_dict(p: ParseResult) -> ParseResultDict:
    return {
        "source_path": str(p.source_path),
        "kind": p.kind,
        "title_candidate": p.title_candidate,
        "year": p.year,
        "season": p.season,
        "episode": p.episode,
        "episode_end": p.episode_end,
        "episode_title": p.episode_title,
        "edition_tokens": list(p.edition_tokens),
        "quality_tokens": list(p.quality_tokens),
        "group_tag": p.group_tag,
        "part_marker": p.part_marker,
        "raw_filename": p.raw_filename,
        "parent_dirs": list(p.parent_dirs),
        "skip_reason": skip_reason_to_dict(p.skip_reason),
    }


def rename_op_to_dict(op: RenameOp) -> RenameOpDict:
    return {
        "source": str(op.source),
        "target": str(op.target),
        "kind": op.kind,
        "anchor": op.anchor,
        "edition": op.edition,
        "confidence": op.confidence,
        "sidecars": [[str(s), str(t)] for (s, t) in op.sidecars],
        "warnings": list(op.warnings),
        "detected_editions": list(op.detected_editions),
    }


def collision_to_dict(c: Collision) -> CollisionDict:
    return {
        "target": str(c.target),
        "sources": [str(s) for s in c.sources],
        "reason": c.reason,
    }


def rename_plan_to_dict(plan: RenamePlan) -> RenamePlanDict:
    return {
        "ops": [rename_op_to_dict(o) for o in plan.ops],
        "collisions": [collision_to_dict(c) for c in plan.collisions],
        "skipped": [{"path": str(p), "reason": r} for (p, r) in plan.skipped],
        "movies_root": str(plan.movies_root),
        "tv_root": str(plan.tv_root),
        "input_root": str(plan.input_root),
        "apply_editions": plan.apply_editions,
        "warnings": list(plan.warnings),
    }


# ---------------------------------------------------------------------------
# Decoders: JSON-friendly dict -> engine object.
# ---------------------------------------------------------------------------


def episode_from_dict(d: dict[str, Any]) -> Episode:
    return Episode(
        season=int(d["season"]),
        episode=int(d["episode"]),
        title=d.get("title", ""),
        air_date=d.get("air_date"),
    )


def candidate_from_dict(d: dict[str, Any] | None) -> Candidate | None:
    if not d:
        return None
    ep_list = d.get("episode_list") or []
    episodes = tuple(episode_from_dict(e) for e in ep_list)
    return Candidate(
        anchor_kind=d["anchor_kind"],
        anchor_id=str(d["anchor_id"]),
        kind=d["kind"],
        title=d.get("title", ""),
        year=d.get("year"),
        confidence=float(d.get("confidence", 0.0)),
        episode_list=episodes if episodes else None,
    )


def skip_reason_from_dict(d: dict[str, Any] | None) -> SkipReason | None:
    if not d:
        return None
    return SkipReason(reason=d["reason"], detail=d.get("detail", ""))


def parse_result_from_dict(d: dict[str, Any]) -> ParseResult:
    return ParseResult(
        source_path=Path(d["source_path"]),
        kind=d.get("kind", "unknown"),
        title_candidate=d.get("title_candidate"),
        year=d.get("year"),
        season=d.get("season"),
        episode=d.get("episode"),
        episode_end=d.get("episode_end"),
        episode_title=d.get("episode_title"),
        edition_tokens=list(d.get("edition_tokens", [])),
        quality_tokens=list(d.get("quality_tokens", [])),
        group_tag=d.get("group_tag"),
        part_marker=d.get("part_marker"),
        raw_filename=d.get("raw_filename", ""),
        parent_dirs=list(d.get("parent_dirs", [])),
        skip_reason=skip_reason_from_dict(d.get("skip_reason")),
    )


def rename_op_from_dict(d: dict[str, Any]) -> RenameOp:
    sidecars_raw = d.get("sidecars") or []
    sidecars = tuple((Path(s), Path(t)) for (s, t) in sidecars_raw)
    return RenameOp(
        source=Path(d["source"]),
        target=Path(d["target"]),
        kind=d["kind"],
        anchor=d.get("anchor", ""),
        edition=d.get("edition"),
        confidence=float(d.get("confidence", 0.0)),
        sidecars=sidecars,
        warnings=tuple(d.get("warnings", [])),
        detected_editions=tuple(d.get("detected_editions", [])),
    )


def collision_from_dict(d: dict[str, Any]) -> Collision:
    return Collision(
        target=Path(d["target"]),
        sources=tuple(Path(s) for s in d.get("sources", [])),
        reason=d.get("reason", "duplicate_input"),
    )


def rename_plan_from_dict(d: dict[str, Any]) -> RenamePlan:
    return RenamePlan(
        ops=tuple(rename_op_from_dict(o) for o in d.get("ops", [])),
        collisions=tuple(collision_from_dict(c) for c in d.get("collisions", [])),
        skipped=tuple((Path(s["path"]), s["reason"]) for s in d.get("skipped", [])),
        movies_root=Path(d["movies_root"]),
        tv_root=Path(d["tv_root"]),
        input_root=Path(d["input_root"]),
        apply_editions=bool(d.get("apply_editions", False)),
        warnings=tuple(d.get("warnings", [])),
    )


# ---------------------------------------------------------------------------
# JSON-RPC envelope helpers.
# ---------------------------------------------------------------------------


def make_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def make_notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-RPC notification (no ``id``, no response expected)."""
    return {"jsonrpc": "2.0", "method": method, "params": params}


# Standard JSON-RPC 2.0 error codes plus our local ones.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_APP = -32000  # generic application-level error


__all__ = [
    "CandidateDict",
    "CollisionDict",
    "EpisodeDict",
    "ERR_APP",
    "ERR_INTERNAL",
    "ERR_INVALID_PARAMS",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_PARSE",
    "GroupDict",
    "ParseResultDict",
    "RenameOpDict",
    "RenamePlanDict",
    "RowDict",
    "RunReportDict",
    "UndoReportDict",
    "candidate_from_dict",
    "candidate_to_dict",
    "collision_from_dict",
    "collision_to_dict",
    "episode_from_dict",
    "episode_to_dict",
    "make_error",
    "make_notification",
    "make_response",
    "movie_result_to_candidate_dict",
    "parse_result_from_dict",
    "parse_result_to_dict",
    "rename_op_from_dict",
    "rename_op_to_dict",
    "rename_plan_from_dict",
    "rename_plan_to_dict",
    "skip_reason_from_dict",
    "skip_reason_to_dict",
    "tv_result_to_candidate_dict",
]
