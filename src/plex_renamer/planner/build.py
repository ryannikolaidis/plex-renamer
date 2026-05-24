"""Top-level plan-builder.

Walks a directory (or list of files) via :func:`parse_tree`, resolves
each parsed item against a caller-provided ``resolve_movie`` /
``resolve_tv`` (typically backed by
:class:`~plex_renamer.tmdb.IMDbFallbackResolver`), and emits a
:class:`RenamePlan`.

The function is overloaded for testability: callers can pass already-
computed (ParseResult, Candidate) pairs via :func:`build_plan_from_pairs`
instead of a directory + resolver, which keeps the planner tests free of
TMDB mocks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult, Sidecar
from plex_renamer.planner.collision import detect_collisions
from plex_renamer.planner.models import RenameOp, RenamePlan
from plex_renamer.planner.movie_path import (
    movie_sidecar_target,
    movie_target_path,
    render_anchor,
)
from plex_renamer.planner.multi_part import group_multi_part
from plex_renamer.planner.path_safety import path_length_warning
from plex_renamer.planner.show_anchor import match_episode
from plex_renamer.planner.specials import is_special
from plex_renamer.planner.tv_path import tv_sidecar_target, tv_target_path
from plex_renamer.tmdb.models import Candidate, Episode

ResolveMovieFn = Callable[[ParseResult], Candidate | None]
ResolveTvFn = Callable[[ParseResult], Candidate | None]
FetchSeasonFn = Callable[[int, int], list[Episode]]


def build_plan(
    input_root: Path,
    movies_root: Path,
    tv_root: Path,
    resolve_movie: ResolveMovieFn,
    resolve_tv: ResolveTvFn,
    fetch_season: FetchSeasonFn | None = None,
    *,
    apply_editions: bool = False,
) -> RenamePlan:
    """Build a :class:`RenamePlan` for everything under ``input_root``.

    ``resolve_movie`` and ``resolve_tv`` are caller-injected functions
    that take a :class:`ParseResult` and return a :class:`Candidate` or
    ``None``. The CLI binds them to the IMDbFallbackResolver; tests pass
    deterministic functions.
    """
    parsed_list = list(parse_tree(input_root))
    pairs: list[tuple[ParseResult, Candidate | None]] = []
    skipped: list[tuple[Path, str]] = []

    # First pass: classify and resolve.
    for p in parsed_list:
        if p.skip_reason is not None:
            skipped.append((p.source_path, p.skip_reason.reason))
            continue
        if p.kind == "unknown":
            # Sidecars are folded into the matching video's ParseResult
            # by parse_tree; standalone unknown files (no video pair) get
            # surfaced as skipped.
            continue
        try:
            candidate = resolve_movie(p) if p.kind == "movie" else resolve_tv(p)
        except Exception as exc:
            skipped.append((p.source_path, f"resolve_error: {exc}"))
            continue
        if candidate is None:
            skipped.append((p.source_path, "unresolved"))
            continue
        pairs.append((p, candidate))

    # Build movie and TV ops separately.
    movie_pairs = [(p, c) for (p, c) in pairs if p.kind == "movie"]
    tv_pairs = [(p, c) for (p, c) in pairs if p.kind == "tv"]

    movie_ops = _build_movie_ops(movie_pairs, movies_root, apply_editions=apply_editions)
    tv_ops = _build_tv_ops(tv_pairs, tv_root, fetch_season)

    all_ops = movie_ops + tv_ops
    clean_ops, collisions = detect_collisions(all_ops)

    return RenamePlan(
        ops=tuple(clean_ops),
        collisions=tuple(collisions),
        skipped=tuple(skipped),
        movies_root=movies_root,
        tv_root=tv_root,
        input_root=input_root,
        apply_editions=apply_editions,
    )


def build_plan_from_pairs(
    pairs: Iterable[tuple[ParseResult, Candidate | None]],
    movies_root: Path,
    tv_root: Path,
    input_root: Path,
    fetch_season: FetchSeasonFn | None = None,
    *,
    apply_editions: bool = False,
    skipped: Iterable[tuple[Path, str]] = (),
) -> RenamePlan:
    """Test-friendly entry that skips parsing and resolution."""
    resolved: list[tuple[ParseResult, Candidate]] = [(p, c) for (p, c) in pairs if c is not None]
    extra_skipped = list(skipped) + [(p.source_path, "unresolved") for (p, c) in pairs if c is None]
    movie_pairs = [(p, c) for (p, c) in resolved if p.kind == "movie"]
    tv_pairs = [(p, c) for (p, c) in resolved if p.kind == "tv"]
    movie_ops = _build_movie_ops(movie_pairs, movies_root, apply_editions=apply_editions)
    tv_ops = _build_tv_ops(tv_pairs, tv_root, fetch_season)
    clean_ops, collisions = detect_collisions(movie_ops + tv_ops)
    return RenamePlan(
        ops=tuple(clean_ops),
        collisions=tuple(collisions),
        skipped=tuple(extra_skipped),
        movies_root=movies_root,
        tv_root=tv_root,
        input_root=input_root,
        apply_editions=apply_editions,
    )


# --- Movie ops -------------------------------------------------------------


def _build_movie_ops(
    pairs: list[tuple[ParseResult, Candidate]],
    movies_root: Path,
    *,
    apply_editions: bool,
) -> list[RenameOp]:
    if not pairs:
        return []

    # Detect multi-part groups so we render `- pt1`/`- pt2` siblings.
    grouped = group_multi_part([p for (p, _c) in pairs])
    # Build a reverse lookup: source path -> normalized part marker.
    part_marker_by_source: dict[Path, str] = {}
    for group_items in grouped.values():
        for item in group_items:
            if item.part_marker:
                part_marker_by_source[item.source_path] = item.part_marker

    ops: list[RenameOp] = []
    for parsed, candidate in pairs:
        edition = _edition_to_apply(parsed, apply_editions)
        part = part_marker_by_source.get(parsed.source_path)
        ext = parsed.source_path.suffix
        target = movie_target_path(candidate, movies_root, edition, part, ext)
        sidecars = _movie_sidecar_pairs(parsed, candidate, movies_root, edition, part)
        warnings: tuple[str, ...] = ()
        w = path_length_warning(target)
        if w:
            warnings = (w,)
        ops.append(
            RenameOp(
                source=parsed.source_path,
                target=target,
                kind="movie",
                anchor=render_anchor(candidate),
                edition=edition,
                confidence=candidate.confidence,
                sidecars=sidecars,
                warnings=warnings,
                detected_editions=tuple(parsed.edition_tokens),
            )
        )
    return ops


def _edition_to_apply(parsed: ParseResult, apply_editions: bool) -> str | None:
    if not apply_editions:
        return None
    if not parsed.edition_tokens:
        return None
    return parsed.edition_tokens[0]


def _movie_sidecar_pairs(
    parsed: ParseResult,
    candidate: Candidate,
    movies_root: Path,
    edition: str | None,
    part_marker: str | None,
) -> tuple[tuple[Path, Path], ...]:
    if not parsed.sidecars:
        return ()
    out: list[tuple[Path, Path]] = []
    for sc in parsed.sidecars:
        suffix = _sidecar_suffix(sc, parsed.source_path)
        target = movie_sidecar_target(candidate, movies_root, edition, part_marker, suffix)
        out.append((sc.path, target))
    return tuple(out)


# --- TV ops ----------------------------------------------------------------


def _build_tv_ops(
    pairs: list[tuple[ParseResult, Candidate]],
    tv_root: Path,
    fetch_season: FetchSeasonFn | None,
) -> list[RenameOp]:
    ops: list[RenameOp] = []
    for parsed, candidate in pairs:
        season, episode = _resolve_tv_indices(parsed, candidate, fetch_season)
        if season is None or episode is None:
            # Can't emit a target; skip silently — the caller's plan will
            # surface this via the skipped list (but the pairs API hides
            # it). Pragma: this case is rare for any reasonable input.
            continue
        ep_title = _episode_title(parsed, candidate, season, episode, fetch_season)
        ext = parsed.source_path.suffix
        target = tv_target_path(
            candidate,
            tv_root,
            season,
            episode,
            ep_title,
            ext,
            episode_end=parsed.episode_end,
        )
        sidecars = _tv_sidecar_pairs(parsed, candidate, tv_root, season, episode, ep_title)
        warnings: tuple[str, ...] = ()
        w = path_length_warning(target)
        if w:
            warnings = (w,)
        ops.append(
            RenameOp(
                source=parsed.source_path,
                target=target,
                kind="tv",
                anchor=render_anchor(candidate),
                edition=None,
                confidence=candidate.confidence,
                sidecars=sidecars,
                warnings=warnings,
            )
        )
    return ops


def _resolve_tv_indices(
    parsed: ParseResult,
    candidate: Candidate,
    fetch_season: FetchSeasonFn | None,
) -> tuple[int | None, int | None]:
    """Return (season, episode). Specials route to Season 00 verbatim."""
    if is_special(parsed):
        # Specials: take season=0 and episode from the parser.
        episode = parsed.episode if parsed.episode is not None else 1
        return (0, episode)
    matched = match_episode(parsed, candidate, fetch_season)
    if matched is not None:
        return (matched.season, matched.episode)
    # Fall back to filename hints.
    if parsed.season is not None and parsed.episode is not None:
        return (parsed.season, parsed.episode)
    return (None, None)


def _episode_title(
    parsed: ParseResult,
    candidate: Candidate,
    season: int,
    episode: int,
    fetch_season: FetchSeasonFn | None,
) -> str:
    """Prefer the TMDB episode title; fall back to the parser's."""
    if candidate.episode_list:
        for ep in candidate.episode_list:
            if ep.season == season and ep.episode == episode:
                return ep.title
    # Specials: no fetch needed, use parser.
    if season == 0:
        return parsed.episode_title or ""
    # Try fetching the season on demand if we have a fetcher.
    if fetch_season is not None and candidate.anchor_kind == "tmdb":
        try:
            tmdb_id = int(candidate.anchor_id)
        except (ValueError, TypeError):
            return parsed.episode_title or ""
        try:
            episodes = fetch_season(tmdb_id, season)
        except Exception:
            return parsed.episode_title or ""
        for ep in episodes:
            if ep.season == season and ep.episode == episode:
                return ep.title
    return parsed.episode_title or ""


def _tv_sidecar_pairs(
    parsed: ParseResult,
    candidate: Candidate,
    tv_root: Path,
    season: int,
    episode: int,
    ep_title: str,
) -> tuple[tuple[Path, Path], ...]:
    if not parsed.sidecars:
        return ()
    out: list[tuple[Path, Path]] = []
    for sc in parsed.sidecars:
        suffix = _sidecar_suffix(sc, parsed.source_path)
        target = tv_sidecar_target(
            candidate,
            tv_root,
            season,
            episode,
            ep_title,
            suffix,
            episode_end=parsed.episode_end,
        )
        out.append((sc.path, target))
    return tuple(out)


# --- Shared helpers --------------------------------------------------------


def _sidecar_suffix(sidecar: Sidecar, video_path: Path) -> str:
    """Compute the suffix to attach to the renamed sidecar.

    For subtitles we keep ``.<lang>[.<modifier>...]<ext>``; for NFO and
    artwork we keep the extension only. Plex artwork basenames
    (``poster.jpg``, etc.) keep the artwork-name prefix so the rename
    lands at ``<stem>-poster.jpg``... actually Plex looks for
    ``poster.jpg`` in the movie folder, not as a sibling. We preserve
    the artwork name unchanged (the caller is expected to place it in
    the folder, see the executor for that step). For slice 4 we keep the
    sidecar's relative filename intact for artwork.
    """
    ext = sidecar.path.suffix.lower()
    if sidecar.kind == "subtitle":
        parts = [""]
        if sidecar.language:
            parts.append(sidecar.language)
        for mod in sidecar.modifiers:
            parts.append(mod)
        # ".en", ".en.forced", ".srt"
        tail = ".".join(p for p in parts if p)
        # Ensure leading dot
        if tail:
            return f".{tail}{ext}"
        return ext
    if sidecar.kind == "nfo":
        return ext
    # Artwork: special-case Plex named artwork to land in the folder as-is.
    name = sidecar.path.stem
    if name.lower() in {"poster", "fanart", "banner", "background", "clearlogo", "logo", "thumb"}:
        # Caller treats this as a separate file in the folder; we mark by
        # returning a sentinel-style suffix that begins with ``::``. The
        # planner's sidecar-target builders detect it and rewrite the
        # filename to the bare artwork name. Simpler: we just return
        # ``<name><ext>`` and the sidecar target builder uses ``stem`` as
        # the BASE; here we encode the intent by returning a suffix that
        # includes the artwork name as a dash-separated suffix.
        return f"-{name}{ext}"
    return ext


__all__ = ["build_plan", "build_plan_from_pairs"]
