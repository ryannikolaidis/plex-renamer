"""Qt-free port of :mod:`plex_renamer.gui.orchestrator`.

The Qt orchestrator binds engine modules to a PySide6 ``QObject`` and
emits signals on state change. The native shell does not run Qt; it
holds the row-state in its own UI layer and asks the daemon for engine
work via JSON-RPC. The functions in this module are pure-Python over
plain ``Row`` dataclasses; they have no signals, no QObject base, and
no Qt imports.

Each function maps 1:1 to a flow in the Qt orchestrator. We deliberately
keep the function shapes very close to the originals so the two paths
can be cross-checked side-by-side during the win-native rollout:

* :func:`derive_show_name` — pulled in from the Qt module verbatim.
* :func:`resolve_rows` — per-row movie resolve + per-group TV resolve
  with multi-season episode-list hydration.
* :func:`run_picker_search` — TMDB search with cleaned-variant retries
  for zero-result fallback.
* :func:`hydrate_group_with_anchor` — apply a chosen TMDB candidate to every row in
  a group and merge episode lists across present seasons.
* :func:`build_candidate_for_search` — combined movie + TV results for
  a single-row TMDB search.
* :func:`resolve_imdb` — IMDb-paste resolver that mirrors
  ``Orchestrator.on_imdb_resolve`` semantics.

The native shell holds onto :class:`Row` dataclasses; the daemon's
``methods`` layer translates to/from JSON dicts at the wire boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult
from plex_renamer.tmdb.ranking import cleaned_query_variants, rank_candidates

# Matches a directory name that looks like a season folder, not a show
# name. Same shape as the Qt orchestrator's regex; ports verbatim.
_SEASON_FOLDER_RE = re.compile(
    r"^(s|season\s*|series\s*)\d{1,2}$|^specials$",
    re.IGNORECASE,
)


def derive_show_name(input_root: Path, parent_dirs: list[str]) -> str:
    """Find the most likely TV show name from the path tree.

    Walks ``parent_dirs`` left-to-right (closest to ``input_root`` first),
    returning the first entry that does NOT look like a season folder.
    Falls back to ``input_root.name`` when every ``parent_dirs`` entry is
    season-like.
    """
    for d in parent_dirs:
        if not _SEASON_FOLDER_RE.match(d.strip()):
            return d
    return input_root.name


class TMDBLike(Protocol):
    """Subset of TMDBClient / TMDBCache the daemon orchestrator needs."""

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]: ...
    def search_tv(self, title: str, year: int | None) -> list[TVResult]: ...
    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None: ...
    def get_season(self, tmdb_id: int, season: int) -> list[Episode]: ...


@dataclass
class Row:
    """One source-row in the daemon orchestrator's working set.

    Mirrors :class:`plex_renamer.gui.models.ItemRow` field-for-field so
    porting code remains 1:1, but holds no Qt references. The shell
    layer round-trips this as the ``RowDict`` shape in
    :mod:`plex_renamer.daemon.schemas`.
    """

    parsed: ParseResult
    candidate: Candidate | None = None
    show_name_hint: str | None = None
    skip: bool = False
    manual_title: str | None = None
    manual_year: int | None = None
    manual_season: int | None = None
    manual_episode: int | None = None
    manual_edition: str | None = None
    imdb_id_override: str | None = None
    anchor_kind_override: str | None = None

    @property
    def source_path(self) -> Path:
        return self.parsed.source_path

    @property
    def group_key(self) -> str:
        """Group rows by detected show or movie.

        Mirrors :attr:`plex_renamer.gui.models.ItemRow.group_key` exactly so
        the daemon's group keys round-trip with the Qt path identically.
        """
        if self.parsed.kind == "tv":
            hint = self.show_name_hint
            if hint:
                return f"tv::{hint}"
            base = self.parsed.title_candidate or ""
            parent = (
                self.parsed.parent_dirs[-1] if self.parsed.parent_dirs else self.parsed.raw_filename
            )
            return f"tv::{base or parent}"
        if self.parsed.kind == "movie":
            return f"movie::{self.parsed.source_path}"
        return f"unknown::{self.parsed.source_path}"


# ---------------------------------------------------------------------------
# Group helpers.
# ---------------------------------------------------------------------------


@dataclass
class Group:
    """A presentation group as the source panel renders it."""

    group_key: str
    kind: str  # "movie" | "tv"
    label: str
    row_ids: list[str] = field(default_factory=list)


def group_rows(rows: list[Row]) -> list[Group]:
    """Compute presentation groups in insertion order.

    A movie group has one row; a TV group has every row sharing the
    same ``show_name_hint`` (or fallback). The label is the show name
    for TV, the parser title (or filename) for movies.
    """
    groups: dict[str, Group] = {}
    for row in rows:
        key = row.group_key
        if key not in groups:
            if row.parsed.kind == "tv":
                label = row.show_name_hint or row.parsed.title_candidate or row.parsed.raw_filename
                groups[key] = Group(group_key=key, kind="tv", label=label)
            elif row.parsed.kind == "movie":
                label = row.parsed.title_candidate or row.parsed.raw_filename
                groups[key] = Group(group_key=key, kind="movie", label=label)
            else:
                groups[key] = Group(group_key=key, kind="unknown", label=row.parsed.raw_filename)
        groups[key].row_ids.append(str(row.source_path))
    return list(groups.values())


# ---------------------------------------------------------------------------
# Parse: walk the source tree and seat rows.
# ---------------------------------------------------------------------------


def parse_input_paths(paths: list[Path]) -> list[Row]:
    """Parse every input path into a list of rows.

    ``paths`` may be files or directories. For directories we use
    :func:`parse_tree`; for files we call :func:`parse_file` indirectly
    via ``parse_tree`` on the file path (which short-circuits to a
    single yield). Skipped / unknown results are dropped — the daemon's
    consumer only renders ``movie`` and ``tv`` rows. TV rows get a
    ``show_name_hint`` derived from the path tree via
    :func:`derive_show_name`.
    """
    out: list[Row] = []
    for path in paths:
        parsed_list = list(parse_tree(path))
        # For derive_show_name we need an input_root. For a directory
        # drop, that's the directory itself; for a single-file drop,
        # that's the file's parent.
        input_root = path if path.is_dir() else path.parent
        for p in parsed_list:
            if p.kind == "unknown" or p.skip_reason is not None:
                continue
            show_hint = derive_show_name(input_root, p.parent_dirs) if p.kind == "tv" else None
            out.append(Row(parsed=p, show_name_hint=show_hint))
    return out


# ---------------------------------------------------------------------------
# Resolve pass: TMDB + IMDb fallback for every parsed row.
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    """Outcome of :func:`resolve_rows`.

    ``rows`` is the updated row list (in the SAME order as the input).
    ``errors`` is a list of ``(source_path_str, message)`` recorded
    against rows that failed to resolve. The shell renders these in its
    Errors pane the same way the Qt orchestrator's ``run_report`` does.
    """

    rows: list[Row]
    errors: list[tuple[str, str]] = field(default_factory=list)


def resolve_rows(
    rows: list[Row],
    *,
    resolver: IMDbFallbackResolver,
    tmdb: TMDBLike,
) -> ResolveResult:
    """Resolve every row's candidate. Ports ``Orchestrator.resolve_rows``.

    Movie rows resolve per-row. TV rows resolve per ``show_name_hint``
    group — one TMDB query per show, one ``get_season`` per unique season
    present in the group. The merged episode list is attached to every
    row in the group so the planner has the full episode list for
    multi-season drops.
    """
    errors: list[tuple[str, str]] = []
    by_path: dict[Path, Row] = {r.source_path: r for r in rows}

    movie_rows = [r for r in rows if r.parsed.kind == "movie"]
    tv_rows = [r for r in rows if r.parsed.kind == "tv"]

    # ----- Movies: per-row ---------------------------------------------
    for row in movie_rows:
        query = row.parsed.title_candidate or ""
        try:
            candidate = resolver.resolve_movie(query, row.parsed.year)
        except Exception as exc:
            errors.append((str(row.source_path), f"resolve_movie failed: {exc}"))
            continue
        if candidate is None:
            errors.append(
                (
                    str(row.source_path),
                    f"no candidate matched movie query {query!r}",
                )
            )
            continue
        by_path[row.source_path] = replace(row, candidate=candidate)

    # ----- TV: grouped by show name hint -------------------------------
    tv_groups: dict[str, list[Row]] = {}
    for row in tv_rows:
        key = row.show_name_hint or row.parsed.title_candidate or row.parsed.raw_filename
        tv_groups.setdefault(key, []).append(row)

    for show_name, group_rows_list in tv_groups.items():
        first = group_rows_list[0]
        try:
            candidate = resolver.resolve_tv(show_name, first.parsed.year)
        except Exception as exc:
            for r in group_rows_list:
                errors.append((str(r.source_path), f"resolve_tv failed: {exc}"))
            continue
        if candidate is None:
            for r in group_rows_list:
                errors.append(
                    (
                        str(r.source_path),
                        f"no candidate matched TV query {show_name!r}",
                    )
                )
            continue
        # Hydrate every season present in the group so multi-season
        # drops share one merged candidate.
        if candidate.anchor_kind == "tmdb":
            seasons = {r.parsed.season for r in group_rows_list if r.parsed.season is not None}
            merged, hydrate_errors = hydrate_seasons(candidate, seasons, group_rows_list, tmdb=tmdb)
            errors.extend(hydrate_errors)
        else:
            merged = candidate
        for r in group_rows_list:
            by_path[r.source_path] = replace(r, candidate=merged)

    # Rebuild the row list in the original order.
    updated = [by_path[r.source_path] for r in rows]
    return ResolveResult(rows=updated, errors=errors)


def hydrate_tv_season(
    candidate: Candidate,
    season_hint: int | None,
    affected_rows: list[Row] | None,
    *,
    tmdb: TMDBLike,
) -> tuple[Candidate, list[tuple[str, str]]]:
    """Fetch one season's episodes and attach them to ``candidate``.

    Returns ``(candidate, errors)``. If the TMDB call fails, returns the
    original candidate untouched and a single error entry per affected
    row. The errors list is empty on success.
    """
    errors: list[tuple[str, str]] = []
    season = season_hint if season_hint is not None else 1
    try:
        tmdb_id = int(candidate.anchor_id)
    except (TypeError, ValueError):
        return candidate, errors
    try:
        episodes = tmdb.get_season(tmdb_id, season)
    except Exception as exc:
        if affected_rows:
            for r in affected_rows:
                errors.append((str(r.source_path), f"get_season(season={season}) failed: {exc}"))
        return candidate, errors
    if not episodes:
        return candidate, errors
    new_candidate = Candidate(
        anchor_kind=candidate.anchor_kind,
        anchor_id=candidate.anchor_id,
        kind=candidate.kind,
        title=candidate.title,
        year=candidate.year,
        confidence=candidate.confidence,
        episode_list=tuple(episodes),
    )
    return new_candidate, errors


def hydrate_seasons(
    candidate: Candidate,
    seasons: set[int],
    affected_rows: list[Row],
    *,
    tmdb: TMDBLike,
) -> tuple[Candidate, list[tuple[str, str]]]:
    """Merge episode lists for every season in ``seasons`` into one Candidate.

    Returns ``(candidate, errors)``. Falls back to single-season
    hydration (season 1) when ``seasons`` is empty.
    """
    errors: list[tuple[str, str]] = []
    if not seasons:
        return hydrate_tv_season(candidate, None, affected_rows, tmdb=tmdb)

    merged_eps: list[Episode] = []
    for season in sorted(seasons):
        hydrated, season_errors = hydrate_tv_season(candidate, season, affected_rows, tmdb=tmdb)
        errors.extend(season_errors)
        if hydrated.episode_list:
            merged_eps.extend(hydrated.episode_list)

    if not merged_eps:
        return candidate, errors

    merged_eps.sort(key=lambda e: (e.season, e.episode))
    new_candidate = Candidate(
        anchor_kind=candidate.anchor_kind,
        anchor_id=candidate.anchor_id,
        kind=candidate.kind,
        title=candidate.title,
        year=candidate.year,
        confidence=candidate.confidence,
        episode_list=tuple(merged_eps),
    )
    return new_candidate, errors


# ---------------------------------------------------------------------------
# Picker / anchor search.
# ---------------------------------------------------------------------------


@dataclass
class PickerSearchResult:
    """Outcome of :func:`run_picker_search`.

    ``candidates`` is the ranked list of TMDB hits. ``variant_used`` is
    set only when the original query produced zero results AND a cleaned
    variant did — its value is the variant string the shell should show
    in the search box. ``variant_original`` is the original query the
    user (or auto-seed) provided; the shell renders the fallback notice
    as "showed results for ``variant_used`` instead of
    ``variant_original``".
    """

    candidates: list[Candidate]
    variant_used: str | None = None
    variant_original: str | None = None


def run_picker_search(
    query: str,
    year: int | None,
    *,
    tmdb: TMDBLike,
) -> PickerSearchResult:
    """Run TMDB search with the cleaned-variant retry chain.

    Mirrors the picker logic in ``Orchestrator.on_group_clicked``: try
    the original query, locally re-rank, and on zero results walk
    :func:`cleaned_query_variants` until one returns hits.
    """
    try:
        shows = tmdb.search_tv(query, year)
    except Exception:
        shows = []
    candidates: list[Candidate] = [
        Candidate(
            anchor_kind="tmdb",
            anchor_id=str(s.tmdb_id),
            kind="tv",
            title=s.title,
            year=s.year,
            confidence=0.7,
        )
        for s in shows
    ]
    candidates = rank_candidates(query, candidates)

    if candidates:
        return PickerSearchResult(candidates=candidates)

    # Zero-result fallback chain.
    for variant in cleaned_query_variants(query)[1:]:
        try:
            retry_shows = tmdb.search_tv(variant, None)
        except Exception:
            retry_shows = []
        if retry_shows:
            retry_candidates = [
                Candidate(
                    anchor_kind="tmdb",
                    anchor_id=str(s.tmdb_id),
                    kind="tv",
                    title=s.title,
                    year=s.year,
                    confidence=0.7,
                )
                for s in retry_shows
            ]
            ranked = rank_candidates(variant, retry_candidates)
            return PickerSearchResult(
                candidates=ranked,
                variant_used=variant,
                variant_original=query,
            )
    return PickerSearchResult(candidates=[])


def hydrate_group_with_anchor(
    rows: list[Row],
    group_key: str,
    chosen: Candidate,
    *,
    tmdb: TMDBLike,
) -> tuple[list[Row], list[tuple[str, str]]]:
    """Apply ``chosen`` to every row in ``group_key`` and merge seasons.

    Mirrors ``Orchestrator.on_show_chosen``. Returns ``(updated_rows,
    errors)``. The rows OUTSIDE the group are returned unchanged at
    their original positions.
    """
    group_rows_list = [r for r in rows if r.group_key == group_key]
    if not group_rows_list:
        return rows, []
    seasons = {r.parsed.season for r in group_rows_list if r.parsed.season is not None}
    if chosen.anchor_kind == "tmdb":
        merged, errors = hydrate_seasons(chosen, seasons, group_rows_list, tmdb=tmdb)
    else:
        merged = chosen
        errors = []
    by_path: dict[Path, Row] = {r.source_path: r for r in rows}
    for r in group_rows_list:
        by_path[r.source_path] = replace(r, candidate=merged)
    return [by_path[r.source_path] for r in rows], errors


# ---------------------------------------------------------------------------
# Single-row IMDb workflow. (Free-text TMDB search is handled directly by
# ``methods.search_tmdb_free`` since it has no per-row state to thread.)
# ---------------------------------------------------------------------------


def resolve_imdb_for_row(
    row: Row,
    imdb_id: str,
    *,
    tmdb: TMDBLike,
) -> tuple[Candidate, list[tuple[str, str]]]:
    """Resolve an IMDb tt-id to a Candidate for one row.

    Mirrors ``Orchestrator.on_imdb_resolve``. When TMDB has no /find
    hit, synthesize an IMDb-anchored candidate so the user can still
    proceed with an IMDb folder name. When the /find hit is a TV show,
    hydrate the row's hinted season so the planner can title-match.
    """
    errors: list[tuple[str, str]] = []
    try:
        hit = tmdb.find_by_imdb_id(imdb_id)
    except Exception:
        hit = None
    if hit is None:
        candidate = Candidate(
            anchor_kind="imdb",
            anchor_id=imdb_id,
            kind=row.parsed.kind if row.parsed.kind != "unknown" else "movie",
            title=row.parsed.title_candidate or "",
            year=row.parsed.year,
            confidence=0.55,
        )
        return candidate, errors
    if isinstance(hit, MovieResult):
        candidate = Candidate(
            anchor_kind="tmdb",
            anchor_id=str(hit.tmdb_id),
            kind="movie",
            title=hit.title,
            year=hit.year,
            confidence=0.8,
        )
        return candidate, errors
    # TVResult.
    candidate = Candidate(
        anchor_kind="tmdb",
        anchor_id=str(hit.tmdb_id),
        kind="tv",
        title=hit.title,
        year=hit.year,
        confidence=0.8,
    )
    candidate, season_errors = hydrate_tv_season(candidate, row.parsed.season, [row], tmdb=tmdb)
    errors.extend(season_errors)
    return candidate, errors


__all__ = [
    "Group",
    "PickerSearchResult",
    "ResolveResult",
    "Row",
    "TMDBLike",
    "derive_show_name",
    "group_rows",
    "hydrate_group_with_anchor",
    "hydrate_seasons",
    "hydrate_tv_season",
    "parse_input_paths",
    "resolve_imdb_for_row",
    "resolve_rows",
    "run_picker_search",
]
