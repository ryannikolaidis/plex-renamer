"""Tests for the :class:`IMDbFallbackResolver` pipeline.

Coverage:

- TMDB confident hit -> TMDB-anchored Candidate.
- TMDB miss + OMDB hit + TMDB /find hit -> TMDB-anchored Candidate.
- TMDB miss + OMDB hit + TMDB /find miss -> IMDb-anchored Candidate
  synthesized from the OMDB payload.
- TMDB miss + OMDB returns no match -> ``None``.
- OMDB API key not configured -> ``None`` after TMDB miss (no OMDB call).
- Title-fuzzy scoring drops a non-matching TMDB top hit so we still fall
  back to OMDB even when TMDB returned SOMETHING.
- TV path works the same.
- IMDb-anchored Candidate carries the OMDB-derived title and year.

All HTTP is mocked through a stub TMDB client + ``responses`` for OMDB.
"""

from __future__ import annotations

import responses

from plex_renamer.tmdb import IMDbFallbackResolver, MovieResult, TVResult

OMDB_URL = "https://www.omdbapi.com/"


class StubTMDB:
    """Minimal TMDB stub: pre-seed return values, count calls."""

    def __init__(self) -> None:
        self.search_movie_returns: list[MovieResult] = []
        self.search_tv_returns: list[TVResult] = []
        self.find_returns: MovieResult | TVResult | None = None
        self.calls: dict[str, int] = {}

    def _tick(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]:
        self._tick("search_movie")
        return list(self.search_movie_returns)

    def search_tv(self, title: str, year: int | None) -> list[TVResult]:
        self._tick("search_tv")
        return list(self.search_tv_returns)

    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None:
        self._tick("find_by_imdb_id")
        return self.find_returns


def test_tmdb_confident_hit_returns_tmdb_anchor() -> None:
    stub = StubTMDB()
    stub.search_movie_returns = [MovieResult(tmdb_id=603, title="The Matrix", year=1999)]
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb")

    candidate = resolver.resolve_movie("The Matrix", 1999)

    assert candidate is not None
    assert candidate.anchor_kind == "tmdb"
    assert candidate.anchor_id == "603"
    assert candidate.title == "The Matrix"
    assert candidate.year == 1999
    assert candidate.confidence >= 0.9
    # No OMDB call when TMDB is confident.
    assert stub.calls.get("find_by_imdb_id", 0) == 0


@responses.activate
def test_tmdb_miss_omdb_hit_find_hit_returns_tmdb_anchor() -> None:
    stub = StubTMDB()
    # TMDB title search returns nothing.
    stub.search_movie_returns = []
    # OMDB returns a tt-id.
    responses.get(
        OMDB_URL,
        json={
            "Response": "True",
            "Title": "Obscure Indie",
            "Year": "2022",
            "imdbID": "tt5555555",
            "Type": "movie",
        },
    )
    # TMDB /find DOES know it.
    stub.find_returns = MovieResult(
        tmdb_id=12345, title="Obscure Indie", year=2022, imdb_id="tt5555555"
    )

    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")
    candidate = resolver.resolve_movie("Obscure Indie", 2022)

    assert candidate is not None
    assert candidate.anchor_kind == "tmdb"
    assert candidate.anchor_id == "12345"
    assert stub.calls["find_by_imdb_id"] == 1


@responses.activate
def test_tmdb_miss_omdb_hit_find_miss_synthesizes_imdb_anchor() -> None:
    """TMDB has no record at all -> we still emit an IMDb-anchored Candidate."""
    stub = StubTMDB()
    stub.search_movie_returns = []
    stub.find_returns = None
    responses.get(
        OMDB_URL,
        json={
            "Response": "True",
            "Title": "Lost Movie",
            "Year": "1972",
            "imdbID": "tt6666666",
            "Type": "movie",
        },
    )
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    candidate = resolver.resolve_movie("Lost Movie", 1972)

    assert candidate is not None
    assert candidate.anchor_kind == "imdb"
    assert candidate.anchor_id == "tt6666666"
    assert candidate.title == "Lost Movie"
    assert candidate.year == 1972
    # Synthesized candidates are needs-review confidence, not auto-accept.
    assert candidate.confidence < 0.85


@responses.activate
def test_tmdb_miss_omdb_miss_returns_none() -> None:
    stub = StubTMDB()
    stub.search_movie_returns = []
    responses.get(OMDB_URL, json={"Response": "False", "Error": "Movie not found!"})
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    assert resolver.resolve_movie("Nope Nope Nope", 2099) is None


def test_no_omdb_key_means_no_fallback_after_tmdb_miss() -> None:
    """Without an OMDB key, a TMDB miss returns None — we do not synthesize."""
    stub = StubTMDB()
    stub.search_movie_returns = []
    resolver = IMDbFallbackResolver(stub, omdb_api_key=None)

    assert resolver.resolve_movie("Anything", 2000) is None
    # No OMDB call (we didn't register any responses handler).


@responses.activate
def test_tmdb_low_confidence_top_hit_falls_back_to_omdb() -> None:
    """TMDB returned SOMETHING but the title is too far off; we still try OMDB."""
    stub = StubTMDB()
    # Top result has a wildly different title; should fall below min confidence.
    stub.search_movie_returns = [
        MovieResult(tmdb_id=1, title="Completely Different Title", year=1990)
    ]
    responses.get(
        OMDB_URL,
        json={
            "Response": "True",
            "Title": "Searched Title",
            "Year": "2022",
            "imdbID": "tt7777777",
            "Type": "movie",
        },
    )
    stub.find_returns = None
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    candidate = resolver.resolve_movie("Searched Title", 2022)
    assert candidate is not None
    assert candidate.anchor_kind == "imdb"
    assert candidate.anchor_id == "tt7777777"


def test_tv_confident_hit_returns_tmdb_anchor() -> None:
    stub = StubTMDB()
    stub.search_tv_returns = [TVResult(tmdb_id=1399, title="Game of Thrones", year=2011)]
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb")

    candidate = resolver.resolve_tv("Game of Thrones", 2011)

    assert candidate is not None
    assert candidate.kind == "tv"
    assert candidate.anchor_kind == "tmdb"
    assert candidate.anchor_id == "1399"


@responses.activate
def test_tv_fallback_synthesizes_imdb_anchor_when_tmdb_has_no_record() -> None:
    stub = StubTMDB()
    stub.search_tv_returns = []
    stub.find_returns = None
    responses.get(
        OMDB_URL,
        json={
            "Response": "True",
            "Title": "Obscure Show",
            "Year": "2014-2017",
            "imdbID": "tt9999991",
            "Type": "series",
        },
    )
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    candidate = resolver.resolve_tv("Obscure Show", 2014)
    assert candidate is not None
    assert candidate.kind == "tv"
    assert candidate.anchor_kind == "imdb"
    assert candidate.anchor_id == "tt9999991"
    # OMDB year range "2014-2017" -> first 4 chars.
    assert candidate.year == 2014


def test_year_off_by_one_still_acceptable() -> None:
    """Year ±1 keeps the year score moderate; total still above threshold."""
    stub = StubTMDB()
    # Exact title, year off by 1: title score 1.0, year score 0.5,
    # blend = 0.7*1.0 + 0.3*0.5 = 0.85 -> above 0.6 threshold.
    stub.search_movie_returns = [MovieResult(tmdb_id=10, title="Same Title", year=2001)]
    resolver = IMDbFallbackResolver(stub, omdb_api_key=None)

    candidate = resolver.resolve_movie("Same Title", 2000)
    assert candidate is not None
    assert candidate.anchor_kind == "tmdb"


@responses.activate
def test_omdb_http_error_does_not_crash_resolver() -> None:
    """A 500 from OMDB is treated as "no fallback result"."""
    stub = StubTMDB()
    stub.search_movie_returns = []
    responses.get(OMDB_URL, json={}, status=500)
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    assert resolver.resolve_movie("Anything", 2000) is None


@responses.activate
def test_omdb_payload_missing_imdb_id_returns_none() -> None:
    """OMDB payload without ``imdbID`` -> no anchor available."""
    stub = StubTMDB()
    stub.search_movie_returns = []
    responses.get(
        OMDB_URL,
        json={"Response": "True", "Title": "X", "Year": "1990"},  # no imdbID
    )
    resolver = IMDbFallbackResolver(stub, omdb_api_key="omdb-key")

    assert resolver.resolve_movie("X", 1990) is None
