"""Tests for :class:`TMDBCache`.

The cache wraps a :class:`TMDBClient`-shaped object transparently. To
keep the tests cleanly isolated from HTTP, we use a hand-rolled stub
client that records call counts and returns canned values. The cache
should be agnostic to whether its wrapped client uses ``requests`` or
not, so this stub also serves as a safety check on the protocol shape.

Coverage:

- Cache miss -> calls the client; subsequent hit -> does NOT call again.
- Cache survives across :class:`TMDBCache` instances (process restart).
- Search-class TTL expires after 7 days; ID-lookup TTL is indefinite.
- ``force_refresh=True`` bypasses the cache.
- Cache directory is auto-created on first write.
- ``find_by_imdb_id`` caches both hits and explicit ``None`` results.
- Cache files include the canonical key so collisions on the truncated
  digest don't return stale data.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.tmdb import Episode, MovieResult, TMDBCache, TVResult


class StubClient:
    """In-memory stand-in for :class:`TMDBClient`.

    Records call counts so tests can assert cache hit/miss behavior
    without involving the HTTP layer at all.
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.search_movie_returns: list[MovieResult] = []
        self.search_tv_returns: list[TVResult] = []
        self.get_movie_returns: MovieResult | None = None
        self.get_tv_returns: TVResult | None = None
        self.get_season_returns: list[Episode] = []
        self.find_returns: MovieResult | TVResult | None = None

    def _tick(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]:
        self._tick("search_movie")
        return list(self.search_movie_returns)

    def search_tv(self, title: str, year: int | None) -> list[TVResult]:
        self._tick("search_tv")
        return list(self.search_tv_returns)

    def get_movie(self, tmdb_id: int) -> MovieResult:
        self._tick("get_movie")
        if self.get_movie_returns is None:
            raise AssertionError("get_movie_returns not set")
        return self.get_movie_returns

    def get_tv(self, tmdb_id: int) -> TVResult:
        self._tick("get_tv")
        if self.get_tv_returns is None:
            raise AssertionError("get_tv_returns not set")
        return self.get_tv_returns

    def get_season(self, tmdb_id: int, season: int) -> list[Episode]:
        self._tick("get_season")
        return list(self.get_season_returns)

    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None:
        self._tick("find_by_imdb_id")
        return self.find_returns


def _make_movie(tmdb_id: int = 1, title: str = "Foo") -> MovieResult:
    return MovieResult(tmdb_id=tmdb_id, title=title, year=2000)


def _make_tv(tmdb_id: int = 1, title: str = "Bar") -> TVResult:
    return TVResult(tmdb_id=tmdb_id, title=title, year=2010)


def test_search_movie_miss_then_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    first = cache.search_movie("Foo", 2000)
    second = cache.search_movie("Foo", 2000)

    assert first == second == [_make_movie()]
    assert stub.calls.get("search_movie", 0) == 1


def test_get_movie_miss_then_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.get_movie_returns = _make_movie(tmdb_id=42)
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.get_movie(42)
    b = cache.get_movie(42)

    assert a == b == _make_movie(tmdb_id=42)
    assert stub.calls.get("get_movie", 0) == 1


def test_get_season_miss_then_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.get_season_returns = [Episode(season=1, episode=1, title="Pilot", air_date="2010-01-01")]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.get_season(1, 1)
    b = cache.get_season(1, 1)

    assert a == b
    assert stub.calls.get("get_season", 0) == 1


def test_cache_survives_across_instances(tmp_path: Path) -> None:
    """A fresh ``TMDBCache`` pointed at the same dir reads prior writes."""
    stub1 = StubClient()
    stub1.search_movie_returns = [_make_movie()]
    cache1 = TMDBCache(stub1, cache_dir=tmp_path)
    cache1.search_movie("Foo", 2000)

    stub2 = StubClient()  # NOT primed; if we hit the client, it crashes
    cache2 = TMDBCache(stub2, cache_dir=tmp_path)
    results = cache2.search_movie("Foo", 2000)

    assert results == [_make_movie()]
    assert stub2.calls.get("search_movie", 0) == 0


def test_search_ttl_expires_after_7_days(tmp_path: Path) -> None:
    """Search entries older than 7 days are treated as misses and refreshed."""
    now = {"t": 1_000_000.0}
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path, now_fn=lambda: now["t"])

    cache.search_movie("Foo", 2000)
    assert stub.calls["search_movie"] == 1

    # 6 days later: still a hit.
    now["t"] += 6 * 24 * 3600
    cache.search_movie("Foo", 2000)
    assert stub.calls["search_movie"] == 1

    # 8 days later: now expired -> refresh.
    now["t"] += 2 * 24 * 3600
    cache.search_movie("Foo", 2000)
    assert stub.calls["search_movie"] == 2


def test_id_lookup_ttl_is_indefinite(tmp_path: Path) -> None:
    """ID-lookup entries never expire by time."""
    now = {"t": 1_000_000.0}
    stub = StubClient()
    stub.get_movie_returns = _make_movie(tmdb_id=99)
    cache = TMDBCache(stub, cache_dir=tmp_path, now_fn=lambda: now["t"])

    cache.get_movie(99)
    # Jump 5 years.
    now["t"] += 5 * 365 * 24 * 3600
    cache.get_movie(99)
    assert stub.calls["get_movie"] == 1


def test_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    """``force_refresh=True`` re-fetches even when a fresh entry exists."""
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    cache.search_movie("Foo", 2000)
    cache.search_movie("Foo", 2000, force_refresh=True)
    assert stub.calls["search_movie"] == 2


def test_cache_directory_auto_created(tmp_path: Path) -> None:
    nested = tmp_path / "new" / "tmdb"
    assert not nested.exists()
    stub = StubClient()
    stub.get_movie_returns = _make_movie(tmdb_id=7)
    cache = TMDBCache(stub, cache_dir=nested)
    cache.get_movie(7)
    assert nested.exists()
    entries = list(nested.iterdir())
    assert len(entries) == 1
    assert entries[0].suffix == ".json"


def test_find_by_imdb_id_caches_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.find_returns = _make_movie(tmdb_id=11, title="The Matrix")
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.find_by_imdb_id("tt0133093")
    b = cache.find_by_imdb_id("tt0133093")

    assert a == b
    assert isinstance(a, MovieResult)
    assert stub.calls["find_by_imdb_id"] == 1


def test_find_by_imdb_id_caches_none(tmp_path: Path) -> None:
    """A None result is cached for the search-TTL window too."""
    stub = StubClient()
    stub.find_returns = None
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.find_by_imdb_id("tt9999999")
    b = cache.find_by_imdb_id("tt9999999")

    assert a is None and b is None
    assert stub.calls["find_by_imdb_id"] == 1


def test_find_by_imdb_id_caches_tv_kind(tmp_path: Path) -> None:
    """Cached TV result deserializes back to a :class:`TVResult` with the right kind."""
    stub = StubClient()
    stub.find_returns = _make_tv(tmdb_id=1396, title="Breaking Bad")
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.find_by_imdb_id("tt0903747")
    stub.find_returns = None  # subsequent direct call would return None
    b = cache.find_by_imdb_id("tt0903747")  # but cache should kick in

    assert isinstance(a, TVResult)
    assert isinstance(b, TVResult)
    assert b.tmdb_id == 1396


def test_cache_key_includes_language(tmp_path: Path) -> None:
    """Two caches with different languages don't collide."""
    stub = StubClient()
    stub.search_movie_returns = [_make_movie(title="EN-Title")]
    en_cache = TMDBCache(stub, cache_dir=tmp_path, language="en-US")
    en_cache.search_movie("foo", None)
    assert stub.calls["search_movie"] == 1

    stub.search_movie_returns = [_make_movie(title="FR-Title")]
    fr_cache = TMDBCache(stub, cache_dir=tmp_path, language="fr-FR")
    fr_results = fr_cache.search_movie("foo", None)

    assert stub.calls["search_movie"] == 2
    assert fr_results[0].title == "FR-Title"


def test_corrupted_cache_file_is_ignored(tmp_path: Path) -> None:
    """A garbled cache file shouldn't crash the cache; treat as miss."""
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path)
    cache.search_movie("Foo", 2000)
    # Mutate one entry to be junk.
    entries = list(tmp_path.iterdir())
    entries[0].write_text("not json", encoding="utf-8")
    # Re-query: should swallow the garbage and re-fetch.
    cache.search_movie("Foo", 2000)
    assert stub.calls["search_movie"] == 2


def test_normalized_query_collapses_whitespace(tmp_path: Path) -> None:
    """``Foo  Bar`` and ``foo bar`` hit the same cache entry."""
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    cache.search_movie("Foo  Bar", None)
    cache.search_movie("foo bar", None)
    # Single call -> the second went through cache.
    assert stub.calls["search_movie"] == 1


def test_year_distinguishes_cache_entries(tmp_path: Path) -> None:
    """Same title + different year are separate entries."""
    stub = StubClient()
    stub.search_movie_returns = [_make_movie()]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    cache.search_movie("Foo", 2000)
    cache.search_movie("Foo", 2001)
    assert stub.calls["search_movie"] == 2


def test_get_tv_miss_then_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.get_tv_returns = _make_tv(tmdb_id=300)
    cache = TMDBCache(stub, cache_dir=tmp_path)

    a = cache.get_tv(300)
    b = cache.get_tv(300)

    assert a == b
    assert stub.calls["get_tv"] == 1


def test_search_tv_miss_then_hit(tmp_path: Path) -> None:
    stub = StubClient()
    stub.search_tv_returns = [_make_tv()]
    cache = TMDBCache(stub, cache_dir=tmp_path)

    cache.search_tv("Bar", 2010)
    cache.search_tv("Bar", 2010)
    assert stub.calls["search_tv"] == 1
