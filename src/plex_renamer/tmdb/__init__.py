"""TMDB HTTP client, on-disk cache, and IMDb fallback resolver.

This package is the identification layer of plex-renamer. It is pure-logic
plus HTTP: no filesystem mutations outside the user's cache directory, no
GUI imports.

Public surface
--------------

- :class:`TMDBClient` — thin HTTP wrapper over the TMDB v3 API.
- :class:`TMDBCache` — transparent on-disk cache that wraps a ``TMDBClient``
  with the same method names. Two TTL classes: 7 days for search responses,
  indefinite for ID lookups (immutable).
- :class:`IMDbFallbackResolver` — resolves a parsed title+year into a
  :class:`Candidate` by trying TMDB first, then optionally OMDB to extract
  an IMDb tt-id and re-resolve via TMDB's ``/find`` endpoint, and finally
  synthesizing an IMDb-anchored ``Candidate`` from the OMDB response when
  TMDB has no record at all.
- Data shapes: :class:`Candidate`, :class:`MovieResult`, :class:`TVResult`,
  :class:`Episode`.
- Errors: :class:`TMDBError`, :class:`TMDBAuthError`, :class:`TMDBNotFound`,
  :class:`TMDBRateLimitError`.

The cache lives at ``platformdirs.user_cache_dir("plex-renamer") / "tmdb"``.
"""

from __future__ import annotations

from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.errors import (
    TMDBAuthError,
    TMDBError,
    TMDBNotFound,
    TMDBRateLimitError,
)
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult

__all__ = [
    "Candidate",
    "Episode",
    "IMDbFallbackResolver",
    "MovieResult",
    "TMDBAuthError",
    "TMDBCache",
    "TMDBClient",
    "TMDBError",
    "TMDBNotFound",
    "TMDBRateLimitError",
    "TVResult",
]
