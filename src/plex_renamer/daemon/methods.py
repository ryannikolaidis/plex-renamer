"""JSON-RPC method handlers.

Each public function here implements one of the daemon's RPC methods.
The :mod:`plex_renamer.daemon.server` loop dispatches by method name;
this module owns the engine wiring (TMDB cache, IMDb fallback resolver,
settings, journal directory) so the server stays a thin I/O loop.

Settings handling
-----------------

Every method that needs settings either:

1. Takes a ``settings`` dict (the shell passes the current settings on
   each call), OR
2. Reads from :class:`plex_renamer.config.settings.Settings` via the
   ``get_settings`` / ``save_settings`` round-trip.

The daemon does NOT cache a long-lived Settings instance. Each call
constructs one from disk via ``Settings.load`` (no config_path override)
so the daemon picks up edits the shell made through its own settings
dialog without an explicit reload.

TMDB wiring
-----------

The daemon constructs a fresh :class:`TMDBClient` per call, wrapped by
the same :class:`TMDBCache` the GUI uses. The cache directory is the
package default so multiple processes can share the cache file
contents (the cache uses atomic writes). This keeps each call
self-contained even though it costs a few microseconds of HTTP-session
construction.

Tests inject their own ``tmdb`` and ``resolver`` collaborators via the
module-level ``_TMDB_FACTORY`` / ``_RESOLVER_FACTORY`` hooks so the
end-to-end subprocess test doesn't need a live TMDB key.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from plex_renamer.config.paths import app_config_dir
from plex_renamer.config.settings import Settings
from plex_renamer.daemon import orchestrator as orch
from plex_renamer.daemon import schemas
from plex_renamer.daemon.orchestrator import Row, TMDBLike
from plex_renamer.executor.copy import apply_plan as engine_apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch as engine_undo_batch
from plex_renamer.parser.extract import parse_tree
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.ranking import cleaned_query_variants

# ---------------------------------------------------------------------------
# Collaborator wiring (test-overridable).
#
# Tests substitute these factories with fakes that return a stub TMDB
# implementing the ``TMDBLike`` protocol. The shell never sees these.
# ---------------------------------------------------------------------------

TMDBFactory = Callable[[Settings], TMDBLike]
ResolverFactory = Callable[[TMDBLike, Settings], IMDbFallbackResolver]


def _default_tmdb_factory(settings: Settings) -> TMDBLike:
    """Construct the TMDB client + cache from the persisted settings."""
    api_key = settings.tmdb_api_key or os.environ.get("TMDB_API_KEY", "")
    if not api_key:
        # The daemon defers the auth failure until the first actual
        # network call so methods like ``get_settings`` and ``save_settings``
        # still work without a key. We construct with a placeholder so
        # the cache wrapper has something to call through to; TMDB calls
        # will raise on first attempt.
        api_key = "missing-tmdb-key"
    client = TMDBClient(api_key=api_key)
    cache = TMDBCache(client=client)
    return cache


def _default_resolver_factory(tmdb: TMDBLike, settings: Settings) -> IMDbFallbackResolver:
    return IMDbFallbackResolver(tmdb=tmdb, omdb_api_key=settings.omdb_api_key)


_TMDB_FACTORY: TMDBFactory = _default_tmdb_factory
_RESOLVER_FACTORY: ResolverFactory = _default_resolver_factory


def set_collaborators(
    *,
    tmdb_factory: TMDBFactory | None = None,
    resolver_factory: ResolverFactory | None = None,
) -> None:
    """Override the TMDB / resolver factories. Used by tests."""
    global _TMDB_FACTORY, _RESOLVER_FACTORY
    if tmdb_factory is not None:
        _TMDB_FACTORY = tmdb_factory
    if resolver_factory is not None:
        _RESOLVER_FACTORY = resolver_factory


# ---------------------------------------------------------------------------
# Settings load / save.
#
# These resolve the config path lazily: tests can override
# ``PLEX_RENAMER_CONFIG_DIR`` in the env so the daemon writes to a tmp
# directory instead of the user's real app-config dir. Without that
# override the default app-config dir is used.
# ---------------------------------------------------------------------------


def _config_path_override() -> Path | None:
    val = os.environ.get("PLEX_RENAMER_CONFIG_DIR")
    if not val:
        return None
    return Path(val) / "config.json"


def _journal_dir_override() -> Path | None:
    val = os.environ.get("PLEX_RENAMER_CONFIG_DIR")
    if not val:
        return None
    return Path(val) / "journals"


def _load_settings_from_params(params: dict[str, Any] | None) -> Settings:
    """Apply ``params['settings']`` over the persisted settings.

    The shell holds the canonical settings in its own state, so every
    operation may pass a settings dict to override the disk-resident
    values for that one call. When ``params`` lacks ``settings`` we
    load from disk.
    """
    config_path = _config_path_override()
    settings = Settings.load(config_path=config_path) if config_path else Settings.load()
    if params is None:
        return settings
    overrides = params.get("settings") if isinstance(params, dict) else None
    if not overrides:
        return settings
    for key, value in overrides.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    return settings


def _settings_to_dict(s: Settings) -> dict[str, Any]:
    return {
        "tmdb_api_key": s.tmdb_api_key,
        "omdb_api_key": s.omdb_api_key,
        "movies_root": s.movies_root,
        "tv_root": s.tv_root,
        "cleanup_enabled": s.cleanup_enabled,
        "auto_accept_top_hit": s.auto_accept_top_hit,
    }


# ---------------------------------------------------------------------------
# Method handlers. Every function takes ``params: dict | None`` and
# returns the JSON-serializable result dict (or yields progress for
# streaming methods).
# ---------------------------------------------------------------------------


def get_settings(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the persisted settings."""
    config_path = _config_path_override()
    settings = Settings.load(config_path=config_path) if config_path else Settings.load()
    return _settings_to_dict(settings)


def save_settings(params: dict[str, Any]) -> dict[str, Any]:
    """Persist the supplied settings dict to ``config.json``."""
    if not isinstance(params, dict):
        raise ValueError("save_settings requires a params dict")
    new_settings = params.get("settings", params)
    config_path = _config_path_override()
    settings = Settings.load(config_path=config_path) if config_path else Settings.load()
    for key, value in new_settings.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    settings.save()
    return _settings_to_dict(settings)


def parse_inputs(params: dict[str, Any]) -> dict[str, Any]:
    """Parse every input path; return parsed rows + computed groups.

    No TMDB calls. The shell gets the parser output verbatim plus the
    ``show_name_hint`` (TV only) so it can pre-render the source panel
    before the resolve pass completes.
    """
    raw_paths: list[str] = list(params.get("paths", []))
    paths = [Path(p) for p in raw_paths]
    rows = orch.parse_input_paths(paths)
    groups = orch.group_rows(rows)
    return {
        "rows": [_row_to_dict(r) for r in rows],
        "groups": [_group_to_dict(g) for g in groups],
    }


def parse_and_resolve(params: dict[str, Any]) -> dict[str, Any]:
    """High-level parse + TMDB resolve flow.

    Ports ``Orchestrator.parse_and_resolve``. Returns rows hydrated with
    their resolver candidate (TV rows carry the merged multi-season
    episode list), the presentation groups, and any per-row resolver
    errors. The shell renders errors verbatim in its Errors pane.
    """
    raw_paths: list[str] = list(params.get("paths", []))
    paths = [Path(p) for p in raw_paths]
    settings = _load_settings_from_params(params)
    tmdb = _TMDB_FACTORY(settings)
    resolver = _RESOLVER_FACTORY(tmdb, settings)

    rows = orch.parse_input_paths(paths)
    result = orch.resolve_rows(rows, resolver=resolver, tmdb=tmdb)
    groups = orch.group_rows(result.rows)
    return {
        "rows": [_row_to_dict(r) for r in result.rows],
        "groups": [_group_to_dict(g) for g in groups],
        "errors": [{"source_path": p, "message": m} for p, m in result.errors],
    }


def search_tmdb_free(params: dict[str, Any]) -> dict[str, Any]:
    """Free-text TMDB search across movies + TV.

    ``kind`` selects which endpoint(s) to hit: ``movie``, ``tv``, or
    ``any`` (default) for both.
    """
    query = str(params.get("query", ""))
    kind = str(params.get("kind", "any"))
    settings = _load_settings_from_params(params)
    tmdb = _TMDB_FACTORY(settings)

    candidates: list[dict[str, Any]] = []
    if kind in ("movie", "any"):
        try:
            for m in tmdb.search_movie(query, None):
                candidates.append(schemas.movie_result_to_candidate_dict(m))
        except Exception as exc:
            return {"candidates": candidates, "error": f"search_movie failed: {exc}"}
    if kind in ("tv", "any"):
        try:
            for t in tmdb.search_tv(query, None):
                candidates.append(schemas.tv_result_to_candidate_dict(t))
        except Exception as exc:
            return {"candidates": candidates, "error": f"search_tv failed: {exc}"}
    return {"candidates": candidates}


def find_by_imdb(params: dict[str, Any]) -> dict[str, Any]:
    """IMDb-paste resolver. Returns a Candidate dict (or ``None``)."""
    imdb_id = str(params.get("imdb_id", ""))
    settings = _load_settings_from_params(params)
    tmdb = _TMDB_FACTORY(settings)
    try:
        hit = tmdb.find_by_imdb_id(imdb_id)
    except Exception as exc:
        return {"candidate": None, "error": str(exc)}
    if hit is None:
        return {"candidate": None}
    # Mirror the GUI's IMDb-resolve semantics: TMDB hits land as TMDB
    # anchors at 0.8 confidence; the daemon does NOT hydrate season here
    # because the caller may not have row context. ``select_anchor``
    # hydrates per row.
    if hasattr(hit, "tmdb_id"):
        # Could be MovieResult or TVResult.
        from plex_renamer.tmdb.models import MovieResult, TVResult

        if isinstance(hit, MovieResult):
            return {"candidate": schemas.movie_result_to_candidate_dict(hit, confidence=0.8)}
        if isinstance(hit, TVResult):
            return {"candidate": schemas.tv_result_to_candidate_dict(hit, confidence=0.8)}
    return {"candidate": None}


def iterate_anchor_search(params: dict[str, Any]) -> dict[str, Any]:
    """TMDB show search with cleaned-variant retry chain.

    Mirrors :func:`orch.run_picker_search`. Returns the ranked candidate
    list plus an optional ``variant_used`` and ``variant_original`` pair
    when a fallback variant produced results. The shell renders the
    fallback notice in the picker.
    """
    query = str(params.get("query", ""))
    year_raw = params.get("year")
    year = int(year_raw) if isinstance(year_raw, int) else None
    settings = _load_settings_from_params(params)
    tmdb = _TMDB_FACTORY(settings)

    picker_result = orch.run_picker_search(query, year, tmdb=tmdb)
    return {
        "candidates": [schemas.candidate_to_dict(c) for c in picker_result.candidates],
        "variant_used": picker_result.variant_used,
        "variant_original": picker_result.variant_original,
        "variants_tried": cleaned_query_variants(query),
    }


def select_anchor(params: dict[str, Any]) -> dict[str, Any]:
    """Apply a chosen anchor to every row in a TV group + hydrate seasons."""
    rows = _rows_from_params(params)
    group_key = str(params.get("group_key", ""))
    candidate_dict = params.get("candidate")
    if not candidate_dict:
        raise ValueError("select_anchor requires a 'candidate' params field")
    chosen = schemas.candidate_from_dict(candidate_dict)
    if chosen is None:
        raise ValueError("select_anchor: candidate could not be decoded")
    settings = _load_settings_from_params(params)
    tmdb = _TMDB_FACTORY(settings)
    updated, errors = orch.hydrate_group_with_anchor(rows, group_key, chosen, tmdb=tmdb)
    return {
        "rows": [_row_to_dict(r) for r in updated],
        "errors": [{"source_path": p, "message": m} for p, m in errors],
    }


def edit_row(params: dict[str, Any]) -> dict[str, Any]:
    """Apply per-row overrides (title/year/S/E/edition/skip/anchor) and recompute."""
    rows = _rows_from_params(params)
    row_id = str(params.get("row_id", ""))
    overrides = params.get("overrides", {}) or {}
    target = _find_row(rows, row_id)
    if target is None:
        raise ValueError(f"edit_row: no row with id {row_id!r}")

    # Apply each override; non-None values land on the row.
    updates: dict[str, Any] = {}
    for key in (
        "manual_title",
        "manual_year",
        "manual_season",
        "manual_episode",
        "manual_edition",
        "imdb_id_override",
        "anchor_kind_override",
        "show_name_hint",
        "skip",
    ):
        if key in overrides:
            updates[key] = overrides[key]

    # Allow the shell to attach a fully-formed candidate (e.g. after the
    # user picked from a TMDB search inside the edit pane).
    if "candidate" in overrides:
        updates["candidate"] = schemas.candidate_from_dict(overrides["candidate"])

    updated_row = replace(target, **updates)
    out_rows = [updated_row if r is target else r for r in rows]
    return {"rows": [_row_to_dict(r) for r in out_rows]}


def build_plan(params: dict[str, Any]) -> dict[str, Any]:
    """Build a RenamePlan from the current rows + settings."""
    rows = _rows_from_params(params)
    settings = _load_settings_from_params(params)
    movies_root = _path_or_default(settings.movies_root, Path.home() / "Movies")
    tv_root = _path_or_default(settings.tv_root, Path.home() / "TV Shows")
    input_root_str = params.get("input_root")
    input_root = (
        Path(input_root_str) if input_root_str else _common_parent([r.source_path for r in rows])
    )
    pairs: list[tuple[Any, Any]] = []
    skipped: list[tuple[Path, str]] = []
    for r in rows:
        if r.skip:
            skipped.append((r.source_path, "user_skip"))
            continue
        pairs.append((r.parsed, r.candidate))
    # fetch_season callback uses the current TMDB collaborator so
    # build_plan_from_pairs can fill in episode titles for shows whose
    # season wasn't pre-hydrated.
    tmdb = _TMDB_FACTORY(settings)
    plan = build_plan_from_pairs(
        pairs,
        movies_root=movies_root,
        tv_root=tv_root,
        input_root=input_root,
        fetch_season=tmdb.get_season,
        apply_editions=bool(params.get("apply_editions", False)),
        skipped=skipped,
    )
    return {"plan": schemas.rename_plan_to_dict(plan)}


def apply_plan(params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Streaming method: apply a plan, yield progress, end with the result.

    Yields:

    1. Zero or more progress notifications of the form
       ``{"event": "op_started" | "op_verified" | "op_failed", ...}``.
    2. A final ``{"event": "done", "result": <RunReport>}`` which the
       server unwraps as the JSON-RPC ``result`` of the request.

    The server detects the ``"done"`` sentinel and emits it as the
    final response; everything else is emitted as a JSON-RPC
    ``progress`` notification (no ``id``).
    """
    plan_dict = params.get("plan")
    if not isinstance(plan_dict, dict):
        raise ValueError("apply_plan requires a 'plan' dict")
    plan = schemas.rename_plan_from_dict(plan_dict)
    cleanup = bool(params.get("cleanup", False))
    verify_hash = bool(params.get("verify_hash", False))
    journal_dir_override = _journal_dir_override()
    journal_dir = journal_dir_override or (app_config_dir() / "journals")

    # Stream a start event per op so the shell can render a progress bar.
    # We don't have op-level callbacks in the executor, so we emit
    # "op_started" before the apply call and rely on the journal as the
    # authoritative outcome ledger after. This is the same pattern the
    # GUI's run_report uses post-hoc.
    for idx, op in enumerate(plan.ops):
        yield {
            "event": "op_started",
            "op_index": idx,
            "source": str(op.source),
            "target": str(op.target),
        }

    result = engine_apply_plan(
        plan,
        journal_dir=journal_dir,
        cleanup=cleanup,
        verify_hash=verify_hash,
    )

    # Walk the journal to surface per-op outcomes.
    error_messages: list[str] = []
    try:
        journal = Journal.load(result.journal_path)
    except (OSError, ValueError):
        journal = None
    if journal is not None:
        for entry in journal.entries:
            if entry.status == "failed" and entry.error:
                error_messages.append(entry.error)
                yield {
                    "event": "op_failed",
                    "source": entry.source,
                    "target": entry.target,
                    "error": entry.error,
                }
            elif entry.status == "verified":
                yield {
                    "event": "op_verified",
                    "source": entry.source,
                    "target": entry.target,
                    "bytes": entry.bytes,
                }

    report: dict[str, Any] = {
        "succeeded": result.succeeded,
        "failed": result.failed,
        "skipped": len(plan.skipped),
        "cleanup_ran": result.cleanup_ran,
        "journal_path": str(result.journal_path),
        "error_messages": error_messages,
    }
    yield {"event": "done", "result": report}


def undo_batch(params: dict[str, Any]) -> dict[str, Any]:
    """Undo a previously-applied batch by journal path."""
    journal_path = Path(params.get("journal_path", ""))
    journal = Journal.load(journal_path)
    result = engine_undo_batch(journal)
    return {
        "reverted": result.reverted,
        "moved_to_review": result.moved_to_review,
        "review_dir": str(result.review_dir) if result.review_dir else None,
        "sources_recoverable": result.sources_recoverable,
    }


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _row_to_dict(r: Row) -> dict[str, Any]:
    return {
        "row_id": str(r.source_path),
        "parsed": schemas.parse_result_to_dict(r.parsed),
        "candidate": schemas.candidate_to_dict(r.candidate),
        "show_name_hint": r.show_name_hint,
        "group_key": r.group_key,
        "skip": r.skip,
        "manual_title": r.manual_title,
        "manual_year": r.manual_year,
        "manual_season": r.manual_season,
        "manual_episode": r.manual_episode,
        "manual_edition": r.manual_edition,
        "imdb_id_override": r.imdb_id_override,
        "anchor_kind_override": r.anchor_kind_override,
    }


def _group_to_dict(g: orch.Group) -> dict[str, Any]:
    return {
        "group_key": g.group_key,
        "kind": g.kind,
        "label": g.label,
        "row_ids": list(g.row_ids),
    }


def _row_from_dict(d: dict[str, Any]) -> Row:
    parsed_d = d.get("parsed")
    # Tolerate the shell sending only the row_id + minimal info; fall
    # back to re-parsing the source path. The shell SHOULD always send
    # the parsed dict though.
    if isinstance(parsed_d, dict):
        parsed = schemas.parse_result_from_dict(parsed_d)
    else:
        # Last resort: re-parse from row_id (assumed to be the source path).
        results = list(parse_tree(Path(d["row_id"])))
        if not results:
            raise ValueError(f"row could not be re-parsed: {d.get('row_id')!r}")
        parsed = results[0]
    candidate = schemas.candidate_from_dict(d.get("candidate"))
    return Row(
        parsed=parsed,
        candidate=candidate,
        show_name_hint=d.get("show_name_hint"),
        skip=bool(d.get("skip", False)),
        manual_title=d.get("manual_title"),
        manual_year=d.get("manual_year"),
        manual_season=d.get("manual_season"),
        manual_episode=d.get("manual_episode"),
        manual_edition=d.get("manual_edition"),
        imdb_id_override=d.get("imdb_id_override"),
        anchor_kind_override=d.get("anchor_kind_override"),
    )


def _rows_from_params(params: dict[str, Any]) -> list[Row]:
    raw = params.get("rows", [])
    if not isinstance(raw, list):
        raise ValueError("'rows' must be a list of row dicts")
    return [_row_from_dict(d) for d in raw]


def _find_row(rows: list[Row], row_id: str) -> Row | None:
    for r in rows:
        if str(r.source_path) == row_id:
            return r
    return None


def _path_or_default(value: str | None, default: Path) -> Path:
    if value:
        return Path(value)
    return default


def _common_parent(paths: list[Path]) -> Path:
    """Pick a sensible ``input_root`` when the shell didn't supply one."""
    if not paths:
        return Path.cwd()
    if len(paths) == 1:
        return paths[0].parent
    common = os.path.commonpath([str(p) for p in paths])
    return Path(common)


# ---------------------------------------------------------------------------
# Dispatch table.
# ---------------------------------------------------------------------------

# Methods that yield progress before a final ``done`` event. The server
# reads the iterator, emits each yielded dict as a JSON-RPC progress
# notification, and unwraps the trailing ``{"event": "done", "result":
# ...}`` envelope as the JSON-RPC ``result``.
STREAMING_METHODS: frozenset[str] = frozenset({"apply_plan"})

METHODS: dict[str, Callable[..., Any]] = {
    "get_settings": get_settings,
    "save_settings": save_settings,
    "parse_inputs": parse_inputs,
    "parse_and_resolve": parse_and_resolve,
    "search_tmdb_free": search_tmdb_free,
    "find_by_imdb": find_by_imdb,
    "iterate_anchor_search": iterate_anchor_search,
    "select_anchor": select_anchor,
    "edit_row": edit_row,
    "build_plan": build_plan,
    "apply_plan": apply_plan,
    "undo_batch": undo_batch,
}


__all__ = [
    "METHODS",
    "STREAMING_METHODS",
    "apply_plan",
    "build_plan",
    "edit_row",
    "find_by_imdb",
    "get_settings",
    "iterate_anchor_search",
    "parse_and_resolve",
    "parse_inputs",
    "save_settings",
    "search_tmdb_free",
    "select_anchor",
    "set_collaborators",
    "undo_batch",
]
