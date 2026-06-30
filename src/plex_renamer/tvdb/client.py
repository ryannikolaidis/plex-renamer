"""TheTVDB v4 HTTP client.

Authentication
--------------

TVDB v4 requires a login → JWT exchange before any read endpoint
will respond. ``POST /v4/login`` accepts an API key (and an optional
subscriber PIN for personal-tier accounts) and returns a token good
for ~30 days. Every subsequent request carries
``Authorization: Bearer <token>``.

The client logs in lazily on the first call that needs the token and
re-authenticates on a 401 (covers token expiry / revocation). Tokens
live in memory only — the caller is responsible for caching response
*payloads* via the companion :class:`TVDBCache`.

Endpoints we use
----------------

- ``POST /v4/login`` — token issuance
- ``GET /v4/series/{id}`` — series detail (light)
- ``GET /v4/series/{id}/episodes/{season-type}`` — paginated episodes
  for one of the season orderings TVDB tracks. ``season-type`` is one
  of ``default``, ``official``, ``dvd``, ``absolute``, ``alternate``,
  ``regional``.

Errors
------

- 401 → :class:`TVDBAuthError` (after one re-auth attempt)
- 404 → :class:`TVDBNotFound`
- 429 → :class:`TVDBRateLimitError`
- anything else → :class:`TVDBError` with the status code in the message
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import requests

from plex_renamer.tmdb.models import Episode
from plex_renamer.tvdb.errors import (
    TVDBAuthError,
    TVDBError,
    TVDBNotFound,
    TVDBRateLimitError,
)

DEFAULT_BASE_URL = "https://api4.thetvdb.com/v4"
DEFAULT_TIMEOUT_SECONDS = 15.0

TVDBSeasonType = Literal["default", "official", "dvd", "absolute", "alternate", "regional"]


@dataclass(frozen=True)
class TVDBSeriesResult:
    """One series-search hit from TVDB.

    ``tvdb_id`` is the canonical series identifier; the picker uses
    ``title`` + ``year`` for display.
    """

    tvdb_id: int
    title: str
    year: int | None
    overview: str | None = None


@dataclass(frozen=True)
class TVDBSeriesEpisodes:
    """Container for the episodes of one series under one ordering.

    ``season_type`` is the TVDB ordering name; ``episodes`` is the
    flat sorted list. The caller filters by season when needed.
    """

    series_id: int
    season_type: TVDBSeasonType
    episodes: tuple[Episode, ...]


class TVDBClient:
    """Thin wrapper around the TVDB v4 HTTP API.

    Construct with an API key (required). The optional ``pin`` is
    only needed for personal-tier (subscriber) accounts; developer
    keys work without it.
    """

    def __init__(
        self,
        api_key: str,
        pin: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise TVDBAuthError("TVDB API key is required")
        self._api_key = api_key
        self._pin = pin
        self._base_url = base_url.rstrip("/")
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._token: str | None = None

    # ----- Public API ------------------------------------------------------

    def search_series(self, query: str, limit: int = 20) -> list[TVDBSeriesResult]:
        """Search TVDB for series whose name contains ``query``.

        Returns up to ``limit`` hits in TVDB's relevance order. Each
        hit carries the TVDB id and the basic display fields the picker
        needs (title, year). Slugs and overviews are dropped — they're
        cosmetic and the picker stays compact.
        """
        payload = self._get(
            "/search",
            params={"query": query, "type": "series", "limit": limit},
        )
        out: list[TVDBSeriesResult] = []
        for item in payload.get("data") or []:
            try:
                tvdb_id = int(item.get("tvdb_id") or item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if not tvdb_id:
                continue
            year_value = item.get("year")
            year: int | None
            try:
                year = int(year_value) if year_value else None
            except (TypeError, ValueError):
                year = None
            out.append(
                TVDBSeriesResult(
                    tvdb_id=tvdb_id,
                    title=str(item.get("name") or item.get("translations", {}).get("eng") or ""),
                    year=year,
                    overview=str(item.get("overview") or "") or None,
                )
            )
        return out

    def get_series_episodes(
        self,
        series_id: int,
        season_type: TVDBSeasonType = "default",
    ) -> TVDBSeriesEpisodes:
        """Return every episode for ``series_id`` under the given ordering.

        TVDB paginates this endpoint; we walk every page and concatenate.
        Episodes are returned in TVDB's stable order (i.e., the
        season/episode numbers reflect the chosen ``season_type``).
        """
        episodes: list[Episode] = []
        page = 0
        while True:
            payload = self._get(
                f"/series/{series_id}/episodes/{season_type}",
                params={"page": page},
            )
            data = payload.get("data") or {}
            for item in data.get("episodes") or []:
                ep = _episode_from_payload(item)
                if ep is not None:
                    episodes.append(ep)
            links = payload.get("links") or {}
            next_page = links.get("next")
            if next_page is None:
                break
            page += 1
        return TVDBSeriesEpisodes(
            series_id=series_id,
            season_type=season_type,
            episodes=tuple(episodes),
        )

    # ----- Internals -------------------------------------------------------

    def _login(self) -> None:
        """Exchange API key (+ optional PIN) for a JWT token."""
        body: dict[str, Any] = {"apikey": self._api_key}
        if self._pin:
            body["pin"] = self._pin
        try:
            response = self._session.post(
                f"{self._base_url}/login",
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise TVDBError(f"TVDB login request failed: {exc}") from exc
        if response.status_code == 401:
            raise TVDBAuthError("TVDB rejected the API key")
        if response.status_code >= 400:
            raise TVDBError(
                f"TVDB login returned HTTP {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        token = (payload.get("data") or {}).get("token")
        if not token:
            raise TVDBAuthError("TVDB login response did not include a token")
        self._token = token

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` with the current bearer token; re-auth once on 401."""
        if self._token is None:
            self._login()
        for attempt in range(2):
            try:
                response = self._session.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise TVDBError(f"TVDB request failed: {exc}") from exc
            if response.status_code == 401 and attempt == 0:
                # Token expired or revoked — log in fresh and retry once.
                self._token = None
                self._login()
                continue
            if response.status_code == 404:
                raise TVDBNotFound(f"TVDB returned 404 for {path}")
            if response.status_code == 429:
                raise TVDBRateLimitError("TVDB rate-limited the request")
            if response.status_code >= 400:
                raise TVDBError(
                    f"TVDB returned HTTP {response.status_code} for {path}: {response.text[:200]}"
                )
            return response.json()
        # Unreachable: the loop returns or raises.
        raise TVDBError("TVDB request loop exhausted without returning")


def _episode_from_payload(item: dict[str, Any]) -> Episode | None:
    """Parse one TVDB episode payload into our internal :class:`Episode`.

    TVDB fields used:

    - ``seasonNumber`` — season index in the chosen ordering
    - ``number`` — episode index within that season in the chosen ordering
    - ``name`` — episode title
    - ``aired`` — air date (YYYY-MM-DD)

    Items missing season or episode are filtered out (TVDB occasionally
    returns specials with null numbers under some orderings).
    """
    season = item.get("seasonNumber")
    episode = item.get("number")
    if season is None or episode is None:
        return None
    return Episode(
        season=int(season),
        episode=int(episode),
        title=str(item.get("name") or ""),
        air_date=item.get("aired") or None,
    )


__all__ = [
    "TVDBClient",
    "TVDBSeasonType",
    "TVDBSeriesEpisodes",
    "TVDBSeriesResult",
]
