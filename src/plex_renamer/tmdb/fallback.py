"""IMDb-anchored fallback resolver.

The resolver pipeline:

1. **TMDB title+year search**. If a high-confidence match (normalized title
   match + year match) appears, return a TMDB-anchored ``Candidate``.
2. **OMDB title+year lookup** (only when ``OMDB_API_KEY`` is configured).
   OMDB returns an IMDb tt-id when it knows the title.
3. **TMDB /find/{imdb_id}**. If TMDB has the title under that IMDb id,
   return a TMDB-anchored ``Candidate`` (note: the IMDb id discovered the
   record, but TMDB is the canonical source once we have it).
4. **OMDB synthesis**. When TMDB has no record at all, build an
   IMDb-anchored ``Candidate`` directly from the OMDB payload so the
   planner can still emit ``{imdb-tt<id>}``.

Confidence scoring
------------------

Confidence is the harmonic-ish blend of normalized title similarity and
year match:

- normalized title equal: title score = 1.0
- normalized title is a strict substring of the candidate (or vice versa):
  title score = 0.8
- otherwise: title score = rapidfuzz token_set_ratio / 100.0
- year matches exactly: year score = 1.0
- year known on both sides but off by 1: year score = 0.5
- year unknown on either side: year score = 0.7 (neutral; can't penalize
  for missing data)
- year mismatch by more than 1: year score = 0.2

Final confidence = ``0.7 * title_score + 0.3 * year_score``, clamped to
[0.0, 1.0]. We pick the highest-scoring TMDB result; if its confidence is
below ``min_tmdb_confidence`` (default 0.6) we fall through to OMDB.

The numbers are tunable; the planner (slice 4) treats >=0.85 as
auto-accept (green), 0.6-0.85 as needs-review (yellow), and the
IMDb-synthesis path always emits as needs-review since OMDB matches alone
are weak.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests
from rapidfuzz import fuzz

from plex_renamer.tmdb.errors import TMDBError
from plex_renamer.tmdb.models import Candidate, MovieResult, TVResult
from plex_renamer.tmdb.ranking import (
    aggressive_query_variants,
    cleaned_query_variants,
    rank_candidates,
)

DEFAULT_OMDB_BASE_URL = "https://www.omdbapi.com/"
DEFAULT_MIN_TMDB_CONFIDENCE = 0.6
DEFAULT_TIMEOUT_SECONDS = 10.0


class _TMDBLike(Protocol):
    """Structural protocol covering ``TMDBClient`` AND ``TMDBCache``.

    Both expose the same method shape, and the resolver shouldn't care
    which one it got. This keeps the resolver decoupled from the cache
    decision (which is owned by the application wiring layer).
    """

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]: ...
    def search_tv(self, title: str, year: int | None) -> list[TVResult]: ...
    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None: ...


class IMDbFallbackResolver:
    """Resolve parsed (title, year, kind) into a :class:`Candidate`.

    :param tmdb: TMDB client (or cache-wrapped client).
    :param omdb_api_key: optional OMDB key for the secondary lookup. Without
        it, the resolver still runs but cannot synthesize an IMDb anchor.
    :param min_tmdb_confidence: TMDB confidence below this triggers the
        OMDB fallback path.
    :param omdb_base_url: override for tests.
    :param session: optional preconfigured :class:`requests.Session`.
    """

    def __init__(
        self,
        tmdb: _TMDBLike,
        omdb_api_key: str | None = None,
        min_tmdb_confidence: float = DEFAULT_MIN_TMDB_CONFIDENCE,
        omdb_base_url: str = DEFAULT_OMDB_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._tmdb = tmdb
        self._omdb_key = omdb_api_key
        self._min_conf = min_tmdb_confidence
        self._omdb_base_url = omdb_base_url
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    # ----- Public API ------------------------------------------------------

    def resolve_movie(self, title: str, year: int | None) -> Candidate | None:
        """Resolve a movie title+year. Returns ``None`` if no path produced a hit."""
        pooled = self.search_movie_pooled(title, year)
        if pooled:
            best = pooled[0]
            if best.confidence >= self._min_conf:
                return best
        # TMDB fallback path: ask OMDB for an IMDb id, then re-query TMDB.
        return self._imdb_fallback("movie", title, year)

    def resolve_tv(self, title: str, year: int | None) -> Candidate | None:
        """Resolve a TV title+year. Returns ``None`` if no path produced a hit."""
        pooled = self.search_tv_pooled(title, year)
        if pooled:
            best = pooled[0]
            if best.confidence >= self._min_conf:
                return best
        return self._imdb_fallback("tv", title, year)

    def search_movie_pooled(self, title: str, year: int | None) -> list[Candidate]:
        """Search every cleaned-query variant; return ranked, deduped candidates.

        Two-tier strategy:

        1. **Safe pool**: search across every form returned by
           :func:`cleaned_query_variants` (the original query plus
           trivial cleanings — trailing ``_N`` markers, parenthesized
           suffixes, leading ``The``). All results are pooled and
           re-ranked. This is what fixes the ``Spaceballs_1`` →
           ``Spaceballs`` class of bug while keeping
           ``El Día De La Bestia`` → ``The Day of the Beast`` intact.

        2. **Aggressive fallback**: if the safe pool came up empty,
           fall back to :func:`aggressive_query_variants` — progressive
           trailing-word strips like ``Detective Dee Demon Chonchon``
           → ``Detective Dee Demon`` → ``Detective Dee``. Only runs as
           a fallback because shortened queries can mask correct long
           matches (``El Día De La Bestia`` → ``El Día`` would crowd
           out the real Day of the Beast).

        Empty input returns an empty list.
        """
        if not title:
            return []
        candidates = self._pooled_movie(title, year, cleaned_query_variants(title))
        if not candidates:
            candidates = self._pooled_movie(title, year, aggressive_query_variants(title))
        return candidates

    def search_tv_pooled(self, title: str, year: int | None) -> list[Candidate]:
        """TV equivalent of :meth:`search_movie_pooled` (same two-tier strategy)."""
        if not title:
            return []
        candidates = self._pooled_tv(title, year, cleaned_query_variants(title))
        if not candidates:
            candidates = self._pooled_tv(title, year, aggressive_query_variants(title))
        return candidates

    def _pooled_movie(
        self, original_title: str, year: int | None, variants: list[str]
    ) -> list[Candidate]:
        pooled: list[MovieResult] = []
        seen: set[int] = set()
        for variant in variants:
            for r in self._tmdb.search_movie(variant, year):
                if r.tmdb_id in seen:
                    continue
                seen.add(r.tmdb_id)
                pooled.append(r)
        if not pooled:
            return []
        scored = [
            (r, _blend(_title_score(original_title, r.title), _year_score(year, r.year)))
            for r in pooled
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        candidates = [_movie_to_candidate(r, conf) for r, conf in scored]
        return rank_candidates(original_title, candidates)

    def _pooled_tv(
        self, original_title: str, year: int | None, variants: list[str]
    ) -> list[Candidate]:
        pooled: list[TVResult] = []
        seen: set[int] = set()
        for variant in variants:
            for r in self._tmdb.search_tv(variant, year):
                if r.tmdb_id in seen:
                    continue
                seen.add(r.tmdb_id)
                pooled.append(r)
        if not pooled:
            return []
        scored = [
            (r, _blend(_title_score(original_title, r.title), _year_score(year, r.year)))
            for r in pooled
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        candidates = [_tv_to_candidate(r, conf) for r, conf in scored]
        return rank_candidates(original_title, candidates)

    # ----- Internals --------------------------------------------------------

    def _imdb_fallback(self, kind: str, title: str, year: int | None) -> Candidate | None:
        """Try OMDB -> /find, then synthesize from OMDB if /find misses."""
        if self._omdb_key is None:
            return None
        omdb_payload = self._omdb_lookup(title, year)
        if omdb_payload is None:
            return None
        imdb_id = omdb_payload.get("imdbID")
        if not imdb_id:
            return None
        # Try TMDB /find first — TMDB is the canonical source when it has
        # the record. We then return a TMDB anchor.
        try:
            tmdb_hit = self._tmdb.find_by_imdb_id(imdb_id)
        except TMDBError:
            tmdb_hit = None
        if tmdb_hit is not None:
            if isinstance(tmdb_hit, MovieResult) and kind == "movie":
                # Confidence is moderate: OMDB matched but TMDB's search
                # didn't. The user reviews these.
                return _movie_to_candidate(tmdb_hit, confidence=0.7)
            if isinstance(tmdb_hit, TVResult) and kind == "tv":
                return _tv_to_candidate(tmdb_hit, confidence=0.7)
            # Kind mismatch (rare). Fall through to OMDB synthesis.
        # OMDB synthesis: build an IMDb-anchored Candidate directly.
        return _candidate_from_omdb(omdb_payload, kind, imdb_id)

    def _omdb_lookup(self, title: str, year: int | None) -> dict[str, Any] | None:
        """Query OMDB by title+year. Returns the payload on success or ``None``."""
        params: dict[str, Any] = {
            "apikey": self._omdb_key,
            "t": title,
        }
        if year is not None:
            params["y"] = year
        try:
            response = self._session.get(self._omdb_base_url, params=params, timeout=self._timeout)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        # OMDB returns ``{"Response": "False", "Error": "..."}`` on miss.
        if payload.get("Response") != "True":
            return None
        return payload


# --- Scoring + transformation -----------------------------------------------


def _normalize(s: str) -> str:
    """Aggressive normalization for comparison only (not for cache keys)."""
    return " ".join(s.lower().strip().split())


def _title_score(parsed: str, candidate: str) -> float:
    p = _normalize(parsed)
    c = _normalize(candidate)
    if not p or not c:
        return 0.0
    if p == c:
        return 1.0
    if p in c or c in p:
        return 0.8
    return fuzz.token_set_ratio(p, c) / 100.0


def _year_score(parsed: int | None, candidate: int | None) -> float:
    if parsed is None or candidate is None:
        return 0.7
    if parsed == candidate:
        return 1.0
    if abs(parsed - candidate) == 1:
        return 0.5
    return 0.2


def _blend(title_score: float, year_score: float) -> float:
    confidence = 0.7 * title_score + 0.3 * year_score
    return max(0.0, min(1.0, confidence))


def _best_movie_match(
    results: list[MovieResult], title: str, year: int | None
) -> tuple[MovieResult, float] | None:
    if not results:
        return None
    scored = [(r, _blend(_title_score(title, r.title), _year_score(year, r.year))) for r in results]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[0]


def _best_tv_match(
    results: list[TVResult], title: str, year: int | None
) -> tuple[TVResult, float] | None:
    if not results:
        return None
    scored = [(r, _blend(_title_score(title, r.title), _year_score(year, r.year))) for r in results]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[0]


def _movie_to_candidate(movie: MovieResult, confidence: float) -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id=str(movie.tmdb_id),
        kind="movie",
        title=movie.title,
        year=movie.year,
        confidence=confidence,
    )


def _tv_to_candidate(show: TVResult, confidence: float) -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id=str(show.tmdb_id),
        kind="tv",
        title=show.title,
        year=show.year,
        confidence=confidence,
        episode_list=show.episode_list if show.episode_list else None,
    )


def _candidate_from_omdb(payload: dict[str, Any], kind: str, imdb_id: str) -> Candidate:
    """Synthesize an IMDb-anchored Candidate when TMDB has no record at all.

    Confidence is intentionally moderate (0.55): OMDB matched our title+year
    but TMDB doesn't have the record, so the planner surfaces this as
    needs-review even though we have an anchor to emit.
    """
    title = payload.get("Title", "")
    year_str = payload.get("Year") or ""
    # OMDB sometimes returns "2014-" or "2014-2017" for TV; take the first 4.
    year_int: int | None = (
        int(year_str[:4]) if len(year_str) >= 4 and year_str[:4].isdigit() else None
    )
    return Candidate(
        anchor_kind="imdb",
        anchor_id=imdb_id,
        kind="movie" if kind == "movie" else "tv",
        title=title,
        year=year_int,
        confidence=0.55,
    )
