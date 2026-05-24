"""``plex-renamer plan`` subcommand.

Walks the source tree, resolves each parsed item via the TMDB + IMDb
fallback pipeline, and writes a JSON plan to disk.

The CLI is a thin wrapper around :func:`plex_renamer.planner.build_plan`.
For the TMDB key the resolution order is:

1. ``--tmdb-key`` flag.
2. Persisted :class:`Settings` (from app-config).
3. ``TMDB_API_KEY`` env var (via :class:`Settings`'s .env hydration).

If no key is available we still emit a plan, but every parsed item will
land in ``skipped`` with reason ``unresolved`` (no resolver to call).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from plex_renamer.config.settings import Settings
from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.errors import TMDBAuthError
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode

ResolveFn = Callable[[ParseResult], Candidate | None]
FetchFn = Callable[[int, int], list[Episode]]


def run_plan(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    movies = Path(args.movies).resolve()
    tv = Path(args.tv).resolve()
    output = Path(args.output)

    # Resolve TMDB key.
    settings = Settings.load()
    tmdb_key = args.tmdb_key or settings.tmdb_api_key

    resolve_movie, resolve_tv, fetch_season = _build_resolvers(
        tmdb_key,
        omdb_key=settings.omdb_api_key,
    )

    plan = build_plan(
        input_root=source,
        movies_root=movies,
        tv_root=tv,
        resolve_movie=resolve_movie,
        resolve_tv=resolve_tv,
        fetch_season=fetch_season,
        apply_editions=bool(getattr(args, "apply_editions", False)),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fp:
        fp.write(plan.to_json())
    print(
        f"plex-renamer: wrote plan to {output} "
        f"({len(plan.ops)} ops, {len(plan.collisions)} collisions, "
        f"{len(plan.skipped)} skipped)"
    )
    return 0


def _build_resolvers(
    tmdb_key: str | None, omdb_key: str | None
) -> tuple[ResolveFn, ResolveFn, FetchFn | None]:
    """Build resolve_movie / resolve_tv / fetch_season callables.

    When no TMDB key is present every call returns ``None`` and the
    plan ends up with everything in skipped — still a useful run for
    diagnosing input.
    """
    if not tmdb_key:

        def _noop(p: ParseResult) -> Candidate | None:
            return None

        return (_noop, _noop, None)

    try:
        client = TMDBClient(api_key=tmdb_key)
    except TMDBAuthError:

        def _noop(p: ParseResult) -> Candidate | None:
            return None

        return (_noop, _noop, None)
    cache = TMDBCache(client)
    resolver = IMDbFallbackResolver(tmdb=cache, omdb_api_key=omdb_key)

    def _movie(p: ParseResult) -> Candidate | None:
        return resolver.resolve_movie(p.title_candidate or "", p.year)

    def _tv(p: ParseResult) -> Candidate | None:
        return resolver.resolve_tv(p.title_candidate or "", p.year)

    def _fetch_season(tmdb_id: int, season: int) -> list[Episode]:
        return cache.get_season(tmdb_id, season)

    return (_movie, _tv, _fetch_season)


__all__ = ["run_plan"]
