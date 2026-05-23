"""Tests for the :class:`TMDBClient`.

All HTTP is mocked via the ``responses`` library. No test in this file
hits a live network or reads ``.env``.

Coverage targets:

- Each public method's happy path (search, detail, find).
- URL shape, query params, and ``api_key`` parameter inclusion.
- 401 -> :class:`TMDBAuthError`, 404 -> :class:`TMDBNotFound`,
  429 -> :class:`TMDBRateLimitError`, generic 5xx -> :class:`TMDBError`.
- ``search_*`` empty-results paths return ``[]`` (not None).
- ``find_by_imdb_id`` empty payload returns ``None``.
- API key required at construction time.
"""

from __future__ import annotations

import pytest
import responses

from plex_renamer.tmdb import (
    Episode,
    MovieResult,
    TMDBAuthError,
    TMDBClient,
    TMDBError,
    TMDBNotFound,
    TMDBRateLimitError,
    TVResult,
)

BASE = "https://api.themoviedb.org/3"


def make_client(**kwargs: object) -> TMDBClient:
    return TMDBClient(api_key="testkey", **kwargs)  # type: ignore[arg-type]


def test_client_requires_api_key() -> None:
    with pytest.raises(TMDBAuthError):
        TMDBClient(api_key="")


@responses.activate
def test_search_movie_happy_path() -> None:
    responses.get(
        f"{BASE}/search/movie",
        json={
            "results": [
                {
                    "id": 11,
                    "title": "The Matrix",
                    "original_title": "The Matrix",
                    "release_date": "1999-03-31",
                    "overview": "A hacker.",
                }
            ]
        },
    )
    client = make_client()
    results = client.search_movie("The Matrix", 1999)
    assert len(results) == 1
    assert results[0] == MovieResult(
        tmdb_id=11,
        title="The Matrix",
        year=1999,
        original_title="The Matrix",
        overview="A hacker.",
        imdb_id=None,
    )
    # Verify URL + query params on the recorded call.
    call = responses.calls[0]
    assert "api_key=testkey" in call.request.url
    assert "query=The+Matrix" in call.request.url
    assert "year=1999" in call.request.url
    assert "language=en-US" in call.request.url


@responses.activate
def test_search_movie_empty_results() -> None:
    responses.get(f"{BASE}/search/movie", json={"results": []})
    client = make_client()
    assert client.search_movie("Nonexistent", None) == []


@responses.activate
def test_search_tv_uses_first_air_date_year() -> None:
    responses.get(
        f"{BASE}/search/tv",
        json={
            "results": [
                {
                    "id": 42,
                    "name": "Twin Peaks",
                    "first_air_date": "1990-04-08",
                }
            ]
        },
    )
    client = make_client()
    results = client.search_tv("Twin Peaks", 1990)
    assert results == [
        TVResult(tmdb_id=42, title="Twin Peaks", year=1990, original_title=None, overview=None)
    ]
    call = responses.calls[0]
    assert "first_air_date_year=1990" in call.request.url


@responses.activate
def test_search_tv_without_year() -> None:
    responses.get(f"{BASE}/search/tv", json={"results": []})
    client = make_client()
    client.search_tv("Some Show", None)
    call = responses.calls[0]
    assert "first_air_date_year" not in call.request.url


@responses.activate
def test_get_movie_happy_path() -> None:
    responses.get(
        f"{BASE}/movie/603",
        json={
            "id": 603,
            "title": "The Matrix",
            "release_date": "1999-03-31",
            "imdb_id": "tt0133093",
        },
    )
    client = make_client()
    movie = client.get_movie(603)
    assert movie.tmdb_id == 603
    assert movie.imdb_id == "tt0133093"
    assert movie.year == 1999


@responses.activate
def test_get_movie_404_raises_not_found() -> None:
    responses.get(f"{BASE}/movie/999999", json={"status_code": 34}, status=404)
    client = make_client()
    with pytest.raises(TMDBNotFound):
        client.get_movie(999999)


@responses.activate
def test_get_tv_happy_path() -> None:
    responses.get(
        f"{BASE}/tv/1399",
        json={"id": 1399, "name": "Game of Thrones", "first_air_date": "2011-04-17"},
    )
    client = make_client()
    show = client.get_tv(1399)
    assert show.title == "Game of Thrones"
    assert show.year == 2011


@responses.activate
def test_get_season_returns_episode_list() -> None:
    responses.get(
        f"{BASE}/tv/1399/season/1",
        json={
            "episodes": [
                {
                    "season_number": 1,
                    "episode_number": 1,
                    "name": "Winter Is Coming",
                    "air_date": "2011-04-17",
                },
                {
                    "season_number": 1,
                    "episode_number": 2,
                    "name": "The Kingsroad",
                    "air_date": "2011-04-24",
                },
            ]
        },
    )
    client = make_client()
    episodes = client.get_season(1399, 1)
    assert episodes == [
        Episode(season=1, episode=1, title="Winter Is Coming", air_date="2011-04-17"),
        Episode(season=1, episode=2, title="The Kingsroad", air_date="2011-04-24"),
    ]


@responses.activate
def test_find_by_imdb_id_movie_hit() -> None:
    responses.get(
        f"{BASE}/find/tt0133093",
        json={
            "movie_results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-31"}],
            "tv_results": [],
        },
    )
    client = make_client()
    result = client.find_by_imdb_id("tt0133093")
    assert isinstance(result, MovieResult)
    assert result.tmdb_id == 603
    call = responses.calls[0]
    assert "external_source=imdb_id" in call.request.url


@responses.activate
def test_find_by_imdb_id_tv_hit_when_no_movie() -> None:
    responses.get(
        f"{BASE}/find/tt0903747",
        json={
            "movie_results": [],
            "tv_results": [{"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20"}],
        },
    )
    client = make_client()
    result = client.find_by_imdb_id("tt0903747")
    assert isinstance(result, TVResult)
    assert result.title == "Breaking Bad"


@responses.activate
def test_find_by_imdb_id_no_match_returns_none() -> None:
    responses.get(f"{BASE}/find/tt9999999", json={"movie_results": [], "tv_results": []})
    client = make_client()
    assert client.find_by_imdb_id("tt9999999") is None


@responses.activate
def test_401_raises_auth_error() -> None:
    responses.get(f"{BASE}/search/movie", json={"status_message": "bad"}, status=401)
    client = make_client()
    with pytest.raises(TMDBAuthError):
        client.search_movie("foo", None)


@responses.activate
def test_429_raises_rate_limit_error() -> None:
    responses.get(f"{BASE}/search/movie", json={}, status=429)
    client = make_client()
    with pytest.raises(TMDBRateLimitError):
        client.search_movie("foo", None)


@responses.activate
def test_500_raises_generic_tmdb_error() -> None:
    responses.get(f"{BASE}/search/movie", json={}, status=503)
    client = make_client()
    with pytest.raises(TMDBError):
        client.search_movie("foo", None)


@responses.activate
def test_non_json_response_raises_tmdb_error() -> None:
    responses.get(f"{BASE}/search/movie", body="not json", status=200)
    client = make_client()
    with pytest.raises(TMDBError):
        client.search_movie("foo", None)


def test_request_exception_raises_tmdb_error() -> None:
    """A connection refusal at the transport layer surfaces as TMDBError."""
    import requests

    class BoomSession:
        def get(self, *_args: object, **_kwargs: object) -> object:
            raise requests.ConnectionError("nope")

    client = TMDBClient(api_key="testkey", session=BoomSession())  # type: ignore[arg-type]
    with pytest.raises(TMDBError):
        client.search_movie("foo", None)


@responses.activate
def test_release_date_missing_returns_year_none() -> None:
    responses.get(f"{BASE}/search/movie", json={"results": [{"id": 1, "title": "Unknown"}]})
    client = make_client()
    [movie] = client.search_movie("Unknown", None)
    assert movie.year is None


@responses.activate
def test_custom_language_passed_through() -> None:
    responses.get(f"{BASE}/search/movie", json={"results": []})
    client = make_client(language="fr-FR")
    client.search_movie("foo", None)
    call = responses.calls[0]
    assert "language=fr-FR" in call.request.url
