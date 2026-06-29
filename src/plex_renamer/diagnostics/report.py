"""Per-file matching report.

Walks a source tree, runs the parser + the TMDB resolver, and produces
a per-row record with the top candidate, top-N alternatives, and
diagnostic flags. Read-only by construction — never touches the
filesystem outside ``parse_tree``.

Diagnostic flags (lowercase strings on :attr:`RowReport.flags`):

* ``unknown``: parser couldn't classify the file. No resolver run.
* ``no-anchor``: parser classified the row but no resolver hit landed.
* ``low-confidence``: top candidate's confidence < 0.85.
* ``ambiguous``: top two ranked candidates are within 0.05 confidence.
* ``year-mismatch``: parser had a year but the top candidate's year
  differs by more than 1.
* ``empty-search``: resolver ran zero queries that returned any result
  (every variant TMDB tried came back empty).

The :func:`build_report` entry point is meant for both interactive
spot-check (the CLI command) and programmatic comparison (a future
"is this run as good as the last run" gate).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plex_renamer.diagnostics.overrides import OverrideSet, resolve_anchor_to_candidate
from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult
from plex_renamer.parser.show_name import derive_show_name
from plex_renamer.planner.show_anchor import match_episode
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult
from plex_renamer.tmdb.ranking import cleaned_query_variants


@dataclass(frozen=True)
class RowReport:
    source_path: Path
    raw_filename: str
    kind: str
    parsed_title: str | None
    parsed_year: int | None
    parsed_season: int | None
    parsed_episode: int | None
    parsed_episode_title: str | None
    group_key: str
    top_candidate: Candidate | None
    alternatives: list[Candidate]
    queries_tried: list[str]
    flags: list[str]
    # Episode-level mapping for TV rows. Populated when a TV show
    # anchor resolved AND TMDB's season list was reachable. None for
    # movies and for TV rows without a resolved show or no S/E info.
    matched_episode: Episode | None = None


@dataclass(frozen=True)
class GroupReport:
    group_key: str
    label: str
    kind: str
    row_count: int
    anchored: bool
    anchor_id: str | None
    anchor_kind: str | None
    rows: list[RowReport]


@dataclass(frozen=True)
class IgnoredPath:
    """A path the parser skipped (download shard, system file, non-media).

    Surfaced separately from the row list so the user can audit what
    was filtered out without those entries polluting the per-row report.
    """

    source_path: Path
    reason: str
    detail: str | None


@dataclass(frozen=True)
class ReportArtifact:
    source: Path
    total_rows: int
    anchored_rows: int
    high_confidence_rows: int  # confidence >= 0.85
    review_rows: int  # 0.60 <= confidence < 0.85
    low_confidence_rows: int  # confidence < 0.60
    unknown_rows: int  # parser produced kind=unknown (no skip_reason)
    groups: list[GroupReport]
    rows: list[RowReport]
    ignored: list[IgnoredPath]


# Resolver-pooled callables. Each returns the ranked candidate pool
# for a (title, year) search (the same shape
# :meth:`IMDbFallbackResolver.search_movie_pooled` /
# :meth:`search_tv_pooled` return). The CLI wires real TMDB-backed
# resolvers; tests inject fakes.
PooledSearchFn = Callable[[str, int | None], list[Candidate]]

# By-ID lookups used by the overrides flow to fetch the canonical
# TMDB record for an --anchor override (so the report can show the
# real title / year, not just the bare id).
GetMovieFn = Callable[[int], MovieResult]
GetTVFn = Callable[[int], TVResult]
GetSeasonFn = Callable[[int, int], list[Episode]]


def build_report(
    source: Path,
    *,
    search_movie: PooledSearchFn,
    search_tv: PooledSearchFn,
    top_n: int = 5,
    progress: Callable[[int, int, Path], None] | None = None,
    overrides: OverrideSet | None = None,
    get_movie: GetMovieFn | None = None,
    get_tv: GetTVFn | None = None,
    get_season: GetSeasonFn | None = None,
) -> ReportArtifact:
    """Walk ``source``, parse each item, run resolver, return a report.

    ``search_movie`` / ``search_tv`` are injected for testability. The
    real CLI wires :class:`TMDBCache`'s methods; a test fake can pass
    deterministic candidate lists.

    ``top_n`` is the alternative-list ceiling per row. The top
    candidate is recorded separately, so the alternative list is what
    the user would scroll to when overriding.

    ``progress`` (optional) fires per row with ``(index_0based, total,
    source_path)`` so a CLI can render a progress meter. Called BEFORE
    each row's resolver work so the per-row latency surfaces accurately.
    """
    parsed_rows = list(parse_tree(source))
    # Split parser output: rows the parser explicitly skipped land in
    # ``ignored`` so they don't drown out real media in the per-row
    # report. ``.download``, ``.tmp``, ``.part``, ``.crdownload``,
    # ``.DS_Store``, ``Thumbs.db``, files under ``temp_<n>_*`` dirs,
    # and anything with a non-media extension all funnel here. Real
    # media kept-but-unclassified (parser kind=unknown WITHOUT a
    # skip_reason) stays in ``rows`` so the user sees the gap.
    candidate_rows: list = []
    ignored: list[IgnoredPath] = []
    for p in parsed_rows:
        if p.skip_reason is not None:
            ignored.append(
                IgnoredPath(
                    source_path=p.source_path,
                    reason=p.skip_reason.reason,
                    detail=p.skip_reason.detail,
                )
            )
        else:
            candidate_rows.append(p)

    total = len(candidate_rows)
    rows: list[RowReport] = []
    for idx, parsed in enumerate(candidate_rows):
        if progress is not None:
            progress(idx, total, parsed.source_path)
        rows.append(_report_one_row(parsed, source, search_movie, search_tv, top_n))

    # Episode-level mapping for TV rows: for each row with a resolved
    # show anchor, look up the corresponding TMDB episode using the
    # same fuzzy-title-first / S+E-tiebreaker rule the planner uses at
    # apply time. This catches off-by-one numbering, regional episode
    # splits, and parser-title vs TMDB-title disagreements that the
    # show-anchor confidence alone doesn't surface. Skipped silently
    # when get_season is None (no TMDB-by-id lookups available).
    if get_season is not None:
        rows = _attach_episode_matches(
            rows,
            parsed_lookup={r.source_path: p for r, p in zip(rows, candidate_rows, strict=True)},
            get_season=get_season,
        )

    # Apply user-supplied overrides AFTER the initial resolver pass so
    # the report shows what changed. Row-level overrides win over
    # group-level overrides on the same row.
    if overrides is not None and not overrides.is_empty():
        rows = _apply_overrides(
            rows,
            overrides=overrides,
            parsed_kinds={r.source_path: r.kind for r in rows},
            get_movie=get_movie,
            get_tv=get_tv,
            get_season=get_season,
        )

    # Group rows by group_key (TV episodes from the same show stack;
    # movies are their own group). Group-level anchor is "any row in
    # the group has a top candidate" — for TV the anchor comes from
    # the first resolved row's candidate.
    groups_map: dict[str, list[RowReport]] = {}
    for row in rows:
        groups_map.setdefault(row.group_key, []).append(row)
    groups = [_group_report(key, group_rows) for key, group_rows in groups_map.items()]

    anchored = sum(1 for r in rows if r.top_candidate is not None)
    high = sum(
        1 for r in rows if r.top_candidate is not None and r.top_candidate.confidence >= 0.85
    )
    review = sum(
        1 for r in rows if r.top_candidate is not None and 0.60 <= r.top_candidate.confidence < 0.85
    )
    low = sum(1 for r in rows if r.top_candidate is not None and r.top_candidate.confidence < 0.60)
    unknown = sum(1 for r in rows if r.kind == "unknown")

    return ReportArtifact(
        source=source,
        total_rows=total,
        anchored_rows=anchored,
        high_confidence_rows=high,
        review_rows=review,
        low_confidence_rows=low,
        unknown_rows=unknown,
        groups=groups,
        rows=rows,
        ignored=ignored,
    )


def _report_one_row(
    parsed: ParseResult,
    input_root: Path,
    search_movie: PooledSearchFn,
    search_tv: PooledSearchFn,
    top_n: int,
) -> RowReport:
    flags: list[str] = []
    group_key = _group_key(parsed, input_root)
    if parsed.kind == "unknown" or parsed.skip_reason is not None:
        if parsed.kind == "unknown":
            flags.append("unknown")
        return RowReport(
            source_path=parsed.source_path,
            raw_filename=parsed.raw_filename,
            kind=parsed.kind,
            parsed_title=parsed.title_candidate,
            parsed_year=parsed.year,
            parsed_season=parsed.season,
            parsed_episode=parsed.episode,
            parsed_episode_title=parsed.episode_title,
            group_key=group_key,
            top_candidate=None,
            alternatives=[],
            queries_tried=[],
            flags=flags,
        )

    # For TV rows, the resolver must search by SHOW name, not by the
    # episode title that ``title_candidate`` carries. Derive the show
    # name from the path tree exactly the way the GUI orchestrator does
    # so the CLI report matches the GUI's resolution shape.
    if parsed.kind == "tv":
        show_name = derive_show_name(input_root, list(parsed.parent_dirs or []))
        query_title = show_name or parsed.title_candidate or ""
    else:
        query_title = parsed.title_candidate or ""

    # Pool every variant the ranker considers so an anchored "second
    # try" hit (e.g. ``Spaceballs`` after the parser produced
    # ``Spaceballs_1``) still appears in the ranked list. The resolver
    # does the same pooling now; we keep ``queries_tried`` populated
    # for human review.
    queries_run = cleaned_query_variants(query_title) if query_title else []
    if parsed.kind == "movie":
        ranked = search_movie(query_title, parsed.year) if query_title else []
    else:
        ranked = search_tv(query_title, parsed.year) if query_title else []

    top = ranked[0] if ranked else None
    alts = ranked[1 : top_n + 1]

    if top is None:
        flags.append("no-anchor")
        # Pooled search returned zero candidates across every variant.
        # Useful signal: parser likely produced a title that doesn't
        # match anything on TMDB.
        flags.append("empty-search")
    else:
        if top.confidence < 0.60:
            flags.append("low-confidence")
        elif top.confidence < 0.85:
            flags.append("low-confidence")  # also flag review band
        if parsed.year is not None and top.year is not None and abs(parsed.year - top.year) > 1:
            flags.append("year-mismatch")
        if (
            len(ranked) >= 2
            and ranked[1].confidence is not None
            and abs(ranked[0].confidence - ranked[1].confidence) < 0.05
        ):
            flags.append("ambiguous")

    return RowReport(
        source_path=parsed.source_path,
        raw_filename=parsed.raw_filename,
        kind=parsed.kind,
        parsed_title=parsed.title_candidate,
        parsed_year=parsed.year,
        parsed_season=parsed.season,
        parsed_episode=parsed.episode,
        parsed_episode_title=parsed.episode_title,
        group_key=group_key,
        top_candidate=top,
        alternatives=list(alts),
        queries_tried=queries_run,
        flags=flags,
    )


def _attach_episode_matches(
    rows: list[RowReport],
    *,
    parsed_lookup: dict[Path, ParseResult],
    get_season: GetSeasonFn,
) -> list[RowReport]:
    """Resolve every TV row's specific TMDB episode via the planner's matcher.

    Uses :func:`plex_renamer.planner.show_anchor.match_episode` so the
    report's episode mapping is identical to what the apply pass would
    produce. Flags added per row:

    * ``episode-renumbered``: TMDB's episode at the matched title is
      at a different (S, E) than the parser extracted from the
      filename. Usually means the literal S/E in the filename
      disagrees with TMDB's canonical numbering (regional splits,
      anime ordering, classic Doctor Who).
    * ``episode-title-mismatch``: parsed episode title and TMDB
      episode title don't loosely match. Often benign (parser may
      have truncated or the file is mis-named).
    * ``episode-unknown``: no episode could be matched at all.
    * ``episode-synthesized``: matched on S/E only; TMDB returned no
      episode at that position so the planner falls back to a
      synthetic Episode with the parsed title (the planner's
      last-ditch path).
    """
    new_rows: list[RowReport] = []
    for row in rows:
        if row.kind != "tv" or row.top_candidate is None or row.top_candidate.anchor_kind != "tmdb":
            new_rows.append(row)
            continue
        parsed = parsed_lookup.get(row.source_path)
        if parsed is None:
            new_rows.append(row)
            continue
        try:
            ep = match_episode(parsed, row.top_candidate, fetch_season=get_season)
        except Exception:
            ep = None
        new_flags = list(row.flags)
        if ep is None:
            new_flags.append("episode-unknown")
        else:
            # Synthesized when the matcher returned an Episode with the
            # parsed title (no real TMDB episode under that S/E).
            if (
                parsed.episode_title
                and ep.title == parsed.episode_title
                and parsed.season == ep.season
                and parsed.episode == ep.episode
            ):
                # Could be a real match OR a synthesized one. Detect
                # by checking whether the matched (S, E) exists in
                # the show's known episode list.
                known = row.top_candidate.episode_list or ()
                if known and not any(
                    e.season == ep.season and e.episode == ep.episode for e in known
                ):
                    new_flags.append("episode-synthesized")
            # Renumbered: TMDB places this episode at a different (S, E).
            if (
                parsed.season is not None
                and parsed.episode is not None
                and (parsed.season != ep.season or parsed.episode != ep.episode)
            ):
                new_flags.append("episode-renumbered")
            # Title mismatch: parsed and TMDB titles don't loosely
            # agree (rapidfuzz token_set_ratio < 60).
            if (
                parsed.episode_title
                and ep.title
                and _loose_title_score(parsed.episode_title, ep.title) < 60
            ):
                new_flags.append("episode-title-mismatch")
        new_rows.append(
            RowReport(
                source_path=row.source_path,
                raw_filename=row.raw_filename,
                kind=row.kind,
                parsed_title=row.parsed_title,
                parsed_year=row.parsed_year,
                parsed_season=row.parsed_season,
                parsed_episode=row.parsed_episode,
                parsed_episode_title=row.parsed_episode_title,
                group_key=row.group_key,
                top_candidate=row.top_candidate,
                alternatives=row.alternatives,
                queries_tried=row.queries_tried,
                flags=new_flags,
                matched_episode=ep,
            )
        )
    return new_rows


def _loose_title_score(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return float(fuzz.token_set_ratio(a.lower(), b.lower()))


def _apply_overrides(
    rows: list[RowReport],
    *,
    overrides: OverrideSet,
    parsed_kinds: dict[Path, str],
    get_movie: GetMovieFn | None,
    get_tv: GetTVFn | None,
    get_season: GetSeasonFn | None,
) -> list[RowReport]:
    """Replace ``top_candidate`` on every row matched by ``overrides``.

    Group overrides apply to every row in the matching group. Row
    overrides win over group overrides on the same row. Each
    overridden row gets the ``anchor-override`` flag and ``no-anchor``
    / ``low-confidence`` flags are cleared (the user vouched for this
    anchor).

    Overrides that reference rows / groups not present in the report
    are silently ignored — they may match a different source tree the
    user intends to reuse the same JSON file against.
    """
    # Cache resolved overrides by anchor key so we don't refetch when
    # multiple rows share the same group.
    resolved_cache: dict[tuple[str, str], Candidate] = {}

    def _resolve(ref, parsed_kind: str) -> Candidate | None:
        if get_movie is None or get_tv is None:
            return None
        cache_key = (ref.kind, ref.id)
        if cache_key in resolved_cache:
            return resolved_cache[cache_key]
        try:
            cand = resolve_anchor_to_candidate(
                ref,
                parsed_kind=parsed_kind,
                get_movie=get_movie,
                get_tv=get_tv,
                get_season=get_season,
            )
        except Exception:
            return None
        resolved_cache[cache_key] = cand
        return cand

    new_rows: list[RowReport] = []
    for row in rows:
        ref = overrides.rows.get(str(row.source_path))
        if ref is None:
            ref = overrides.groups.get(row.group_key)
        if ref is None:
            new_rows.append(row)
            continue
        cand = _resolve(ref, parsed_kinds.get(row.source_path, row.kind))
        if cand is None:
            new_rows.append(row)
            continue
        # Strip resolver-confidence flags; the user vouched for this
        # anchor so "low-confidence" / "no-anchor" no longer apply.
        new_flags = [
            f
            for f in row.flags
            if f
            not in {"no-anchor", "low-confidence", "ambiguous", "empty-search", "year-mismatch"}
        ]
        new_flags.append("anchor-override")
        new_rows.append(
            RowReport(
                source_path=row.source_path,
                raw_filename=row.raw_filename,
                kind=row.kind,
                parsed_title=row.parsed_title,
                parsed_year=row.parsed_year,
                parsed_season=row.parsed_season,
                parsed_episode=row.parsed_episode,
                parsed_episode_title=row.parsed_episode_title,
                group_key=row.group_key,
                top_candidate=cand,
                alternatives=row.alternatives,
                queries_tried=row.queries_tried,
                flags=new_flags,
            )
        )
    return new_rows


def _group_key(parsed: ParseResult, input_root: Path) -> str:
    """Mirror the planner's grouping rule for diagnostics.

    Movies group by ``"movie|<title>|<year>"``. TV groups by the
    derived show name (path tree → input_root.name fallback) so every
    episode of the same show lands in one group. Matches the GUI
    orchestrator's grouping rule via :func:`derive_show_name`.
    """
    if parsed.kind == "tv":
        show = derive_show_name(input_root, list(parsed.parent_dirs or []))
        return f"tv|{show}"
    return f"movie|{parsed.title_candidate or parsed.raw_filename}|{parsed.year or ''}"


def _group_report(group_key: str, rows: list[RowReport]) -> GroupReport:
    if not rows:
        return GroupReport(
            group_key=group_key,
            label=group_key,
            kind="unknown",
            row_count=0,
            anchored=False,
            anchor_id=None,
            anchor_kind=None,
            rows=[],
        )
    first = rows[0]
    label_parts = group_key.split("|")
    label = label_parts[1] if len(label_parts) >= 2 else group_key
    anchored_rows = [r for r in rows if r.top_candidate is not None]
    if anchored_rows:
        anchor = anchored_rows[0].top_candidate
        # ``anchor`` is non-None because the row's top_candidate is.
        assert anchor is not None
        return GroupReport(
            group_key=group_key,
            label=label,
            kind=first.kind,
            row_count=len(rows),
            anchored=True,
            anchor_id=anchor.anchor_id,
            anchor_kind=anchor.anchor_kind,
            rows=rows,
        )
    return GroupReport(
        group_key=group_key,
        label=label,
        kind=first.kind,
        row_count=len(rows),
        anchored=False,
        anchor_id=None,
        anchor_kind=None,
        rows=rows,
    )


def report_to_dict(artifact: ReportArtifact) -> dict[str, Any]:
    """Serialize a :class:`ReportArtifact` to a JSON-friendly dict.

    Output shape is stable enough to be diffed across runs — the user
    runs the report against the same tree before and after a parser /
    resolver change to verify accuracy didn't regress.
    """
    return {
        "source": str(artifact.source),
        "summary": {
            "total_rows": artifact.total_rows,
            "anchored_rows": artifact.anchored_rows,
            "high_confidence_rows": artifact.high_confidence_rows,
            "review_rows": artifact.review_rows,
            "low_confidence_rows": artifact.low_confidence_rows,
            "unknown_rows": artifact.unknown_rows,
            "ignored_count": len(artifact.ignored),
        },
        "ignored": [
            {
                "source_path": str(ig.source_path),
                "reason": ig.reason,
                "detail": ig.detail,
            }
            for ig in artifact.ignored
        ],
        "groups": [
            {
                "group_key": g.group_key,
                "label": g.label,
                "kind": g.kind,
                "row_count": g.row_count,
                "anchored": g.anchored,
                "anchor": (f"{g.anchor_kind}-{g.anchor_id}" if g.anchored else None),
            }
            for g in artifact.groups
        ],
        "rows": [_row_to_dict(r) for r in artifact.rows],
    }


def _row_to_dict(r: RowReport) -> dict[str, Any]:
    return {
        "source_path": str(r.source_path),
        "raw_filename": r.raw_filename,
        "kind": r.kind,
        "parsed": {
            "title": r.parsed_title,
            "year": r.parsed_year,
            "season": r.parsed_season,
            "episode": r.parsed_episode,
            "episode_title": r.parsed_episode_title,
        },
        "group_key": r.group_key,
        "top_candidate": _candidate_to_dict(r.top_candidate) if r.top_candidate else None,
        "alternatives": [_candidate_to_dict(c) for c in r.alternatives],
        "queries_tried": list(r.queries_tried),
        "flags": list(r.flags),
        "matched_episode": (
            {
                "season": r.matched_episode.season,
                "episode": r.matched_episode.episode,
                "title": r.matched_episode.title,
                "air_date": r.matched_episode.air_date,
            }
            if r.matched_episode is not None
            else None
        ),
    }


def _candidate_to_dict(c: Candidate) -> dict[str, Any]:
    return {
        "anchor_kind": c.anchor_kind,
        "anchor_id": c.anchor_id,
        "anchor": f"{c.anchor_kind}-{c.anchor_id}",
        "kind": c.kind,
        "title": c.title,
        "year": c.year,
        "confidence": round(c.confidence, 4),
    }


__all__ = [
    "GroupReport",
    "IgnoredPath",
    "ReportArtifact",
    "RowReport",
    "build_report",
    "report_to_dict",
]
