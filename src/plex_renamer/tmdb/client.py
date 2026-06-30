"""TMDB HTTP v3 client.

Authentication
--------------

We use the ``?api_key=<key>`` query-param flavor of TMDB v3 auth (not the
v4 bearer-token flow). v3 keys are the only ones the user has in the
account they're using, and the query-param shape works for every endpoint
we touch. The choice is documented here so reviewers don't have to guess.

Endpoints
---------

- ``GET /search/movie?query=...&year=...`` — title search for movies
- ``GET /search/tv?query=...&first_air_date_year=...`` — title search for TV
- ``GET /movie/{id}`` — movie detail (populates ``imdb_id``)
- ``GET /tv/{id}`` — TV detail
- ``GET /tv/{id}/season/{season}`` — season detail with episode list
- ``GET /find/{external_id}?external_source=imdb_id`` — IMDb-to-TMDB lookup

No retries
----------

Network or HTTP errors propagate. 401 -> :class:`TMDBAuthError`,
404 -> :class:`TMDBNotFound`, 429 -> :class:`TMDBRateLimitError`, anything
else -> :class:`TMDBError` with the status code. The caller (planner) is
the right layer to decide whether to back off, fall back, or surface.
"""

from __future__ import annotations

from typing import Any

import requests

from plex_renamer.tmdb.errors import (
    TMDBAuthError,
    TMDBError,
    TMDBNotFound,
    TMDBRateLimitError,
)
from plex_renamer.tmdb.models import Episode, MovieResult, TVResult

DEFAULT_BASE_URL = "https://api.themoviedb.org/3"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_TIMEOUT_SECONDS = 10.0


class TMDBClient:
    """Thin wrapper around the TMDB v3 HTTP API.

    Methods return parsed model objects (or empty lists for searches that
    came back empty). They never return ``None`` from a successful call —
    that pattern reads ambiguously in callers. Missing IDs raise
    :class:`TMDBNotFound` and missing keys raise :class:`TMDBAuthError`.

    :param api_key: the TMDB v3 API key. Required and non-empty.
    :param base_url: override for tests; defaults to ``api.themoviedb.org``.
    :param language: BCP-47 language passed to TMDB. Defaults to ``en-US``.
    :param session: optional preconfigured :class:`requests.Session`.
    :param timeout: per-request timeout in seconds; raised on hang.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        language: str = DEFAULT_LANGUAGE,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise TMDBAuthError("TMDB API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    # ----- Public API ------------------------------------------------------

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]:
        """Search TMDB movies by title and optional year."""
        params: dict[str, Any] = {"query": title}
        if year is not None:
            params["year"] = year
        payload = self._get("/search/movie", params=params)
        return [_movie_from_payload(item) for item in payload.get("results", [])]

    def search_tv(self, title: str, year: int | None) -> list[TVResult]:
        """Search TMDB TV shows by title and optional first-air-date year."""
        params: dict[str, Any] = {"query": title}
        if year is not None:
            params["first_air_date_year"] = year
        payload = self._get("/search/tv", params=params)
        return [_tv_from_payload(item) for item in payload.get("results", [])]

    def get_movie(self, tmdb_id: int) -> MovieResult:
        """Fetch movie detail by TMDB id. Raises :class:`TMDBNotFound` on 404."""
        payload = self._get(f"/movie/{tmdb_id}")
        return _movie_from_payload(payload)

    def get_tv(self, tmdb_id: int) -> TVResult:
        """Fetch TV detail by TMDB id. Raises :class:`TMDBNotFound` on 404."""
        payload = self._get(f"/tv/{tmdb_id}")
        return _tv_from_payload(payload)

    def get_season(self, tmdb_id: int, season: int) -> list[Episode]:
        """Fetch one season's episode list.

        Returns the season's episodes ordered by ``episode_number``. The
        planner uses this for show-anchor matching: fuzzy-match the parsed
        episode title against each ``Episode.title``.
        """
        payload = self._get(f"/tv/{tmdb_id}/season/{season}")
        episodes = payload.get("episodes", [])
        return [_episode_from_payload(season, ep) for ep in episodes]

    def get_tv_external_ids(self, tmdb_id: int) -> dict[str, Any]:
        """Fetch a TV show's external-id map (``tvdb_id``, ``imdb_id``, etc.).

        Used to bridge a TMDB-anchored show to its TVDB id so we can ask
        TVDB for alternate episode orderings. Returns the raw payload —
        callers read whichever field they need. Raises
        :class:`TMDBNotFound` on 404.
        """
        return self._get(f"/tv/{tmdb_id}/external_ids")

    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None:
        """Look up a TMDB record by IMDb ``tt``-id via ``/find``.

        Returns the first movie hit if any, else the first TV hit, else
        ``None``. We do NOT raise :class:`TMDBNotFound` here — ``/find``
        returns 200 with empty result arrays when there is no record,
        which is the explicit "no match" case the fallback resolver
        depends on.
        """
        payload = self._get(
            f"/find/{imdb_id}",
            params={"external_source": "imdb_id"},
        )
        movie_results = payload.get("movie_results", [])
        if movie_results:
            return _movie_from_payload(movie_results[0])
        tv_results = payload.get("tv_results", [])
        if tv_results:
            return _tv_from_payload(tv_results[0])
        return None

    # ----- Internals --------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        merged: dict[str, Any] = {
            "api_key": self._api_key,
            "language": self._language,
        }
        if params:
            merged.update(params)
        try:
            response = self._session.get(url, params=merged, timeout=self._timeout)
        except requests.RequestException as exc:
            raise TMDBError(f"TMDB request failed: {exc}") from exc
        status = response.status_code
        if status == 401:
            raise TMDBAuthError("TMDB rejected the API key (HTTP 401)")
        if status == 404:
            raise TMDBNotFound(f"TMDB returned 404 for {path}")
        if status == 429:
            raise TMDBRateLimitError("TMDB rate limit exceeded (HTTP 429)")
        if status >= 400:
            raise TMDBError(f"TMDB returned HTTP {status} for {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise TMDBError(f"TMDB returned non-JSON body for {path}") from exc


# --- Payload helpers --------------------------------------------------------


def _year_from_date(date_str: str | None) -> int | None:
    """Extract a 4-digit year from a ``YYYY-MM-DD`` or empty string."""
    if not date_str:
        return None
    head = date_str.split("-", 1)[0]
    if len(head) == 4 and head.isdigit():
        return int(head)
    return None


def _movie_from_payload(data: dict[str, Any]) -> MovieResult:
    return MovieResult(
        tmdb_id=int(data["id"]),
        title=data.get("title") or data.get("original_title") or "",
        year=_year_from_date(data.get("release_date")),
        original_title=data.get("original_title"),
        overview=data.get("overview"),
        imdb_id=data.get("imdb_id"),
    )


def _tv_from_payload(data: dict[str, Any]) -> TVResult:
    return TVResult(
        tmdb_id=int(data["id"]),
        title=data.get("name") or data.get("original_name") or "",
        year=_year_from_date(data.get("first_air_date")),
        original_title=data.get("original_name"),
        overview=data.get("overview"),
        # /tv/{id} does NOT include imdb_id at the top level; callers can
        # fetch external IDs separately. We surface whatever TMDB sent.
        imdb_id=data.get("imdb_id"),
    )


def _episode_from_payload(season: int, data: dict[str, Any]) -> Episode:
    return Episode(
        season=int(data.get("season_number", season)),
        episode=int(data["episode_number"]),
        title=data.get("name") or "",
        air_date=data.get("air_date") or None,
    )
