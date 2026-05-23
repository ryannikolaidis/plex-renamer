"""On-disk JSON cache wrapping a :class:`TMDBClient`.

Why on disk
-----------

Search queries are repeatable across runs (the user re-opens the GUI to
the same source tree); ID lookups never change once TMDB has the record.
Caching to disk lets the user iterate on the review queue without
re-burning the TMDB free-tier rate-limit on every paint cycle.

Schema
------

Each entry is a single JSON file under ``app_cache_dir() / "tmdb"`` named
after a 16-hex-char SHA-256 prefix of the canonical key. The body is a
small envelope::

    {
      "method": "search_movie",
      "key": "{normalized canonical key}",
      "stored_at": <unix epoch>,
      "ttl_class": "search" | "id_lookup",
      "payload": <serialized model(s)>
    }

We store the serialized models, not the raw HTTP response. That keeps the
on-disk format stable across TMDB API changes; if TMDB renames a field we
care about, we adjust the deserialization in the client and run the
cache through a one-time invalidation.

TTL classes
-----------

- ``search`` (7 days): ``search_movie``, ``search_tv``, ``find_by_imdb_id``.
- ``id_lookup`` (indefinite): ``get_movie``, ``get_tv``, ``get_season``.

``force_refresh=True`` on any cache method skips the read and forces a
fresh HTTP call. Useful for the planner's "re-resolve" action in slice 5.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from plex_renamer.config.paths import app_cache_dir
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.models import Episode, MovieResult, TVResult

CACHE_SUBDIR = "tmdb"
SEARCH_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

TTLClass = Literal["search", "id_lookup"]


class TMDBCache:
    """Transparent cache around a :class:`TMDBClient`.

    Exposes the same method names as the underlying client. Consults disk
    first; on miss, calls through and persists the result. ``force_refresh``
    bypasses the read but still writes the fresh response.

    The cache directory is created lazily on first write.
    """

    def __init__(
        self,
        client: TMDBClient,
        cache_dir: Path | None = None,
        language: str = "en-US",
        now_fn: Any = None,
    ) -> None:
        self._client = client
        self._language = language
        self._cache_dir = cache_dir if cache_dir is not None else app_cache_dir() / CACHE_SUBDIR
        # Allow tests to inject a deterministic clock without monkeypatching
        # the module-level ``time`` import. Default to ``time.time``.
        self._now_fn = now_fn if now_fn is not None else time.time

    # ----- Public API ------------------------------------------------------

    def search_movie(
        self, title: str, year: int | None, *, force_refresh: bool = False
    ) -> list[MovieResult]:
        key = self._key("search_movie", f"{_norm(title)}|{year if year is not None else ''}")
        cached = None if force_refresh else self._read(key, ttl_class="search")
        if cached is not None:
            return [_movie_from_dict(item) for item in cached]
        fresh = self._client.search_movie(title, year)
        self._write(key, "search_movie", "search", [asdict(r) for r in fresh])
        return fresh

    def search_tv(
        self, title: str, year: int | None, *, force_refresh: bool = False
    ) -> list[TVResult]:
        key = self._key("search_tv", f"{_norm(title)}|{year if year is not None else ''}")
        cached = None if force_refresh else self._read(key, ttl_class="search")
        if cached is not None:
            return [_tv_from_dict(item) for item in cached]
        fresh = self._client.search_tv(title, year)
        self._write(key, "search_tv", "search", [asdict(r) for r in fresh])
        return fresh

    def get_movie(self, tmdb_id: int, *, force_refresh: bool = False) -> MovieResult:
        key = self._key("get_movie", str(tmdb_id))
        cached = None if force_refresh else self._read(key, ttl_class="id_lookup")
        if cached is not None:
            return _movie_from_dict(cached)
        fresh = self._client.get_movie(tmdb_id)
        self._write(key, "get_movie", "id_lookup", asdict(fresh))
        return fresh

    def get_tv(self, tmdb_id: int, *, force_refresh: bool = False) -> TVResult:
        key = self._key("get_tv", str(tmdb_id))
        cached = None if force_refresh else self._read(key, ttl_class="id_lookup")
        if cached is not None:
            return _tv_from_dict(cached)
        fresh = self._client.get_tv(tmdb_id)
        self._write(key, "get_tv", "id_lookup", asdict(fresh))
        return fresh

    def get_season(
        self, tmdb_id: int, season: int, *, force_refresh: bool = False
    ) -> list[Episode]:
        key = self._key("get_season", f"{tmdb_id}|{season}")
        cached = None if force_refresh else self._read(key, ttl_class="id_lookup")
        if cached is not None:
            return [_episode_from_dict(item) for item in cached]
        fresh = self._client.get_season(tmdb_id, season)
        self._write(key, "get_season", "id_lookup", [asdict(e) for e in fresh])
        return fresh

    def find_by_imdb_id(
        self, imdb_id: str, *, force_refresh: bool = False
    ) -> MovieResult | TVResult | None:
        key = self._key("find_by_imdb_id", _norm(imdb_id))
        cached = None if force_refresh else self._read(key, ttl_class="search")
        if cached is not None:
            # The serialized payload carries a marker for kind. ``None``
            # is encoded as the sentinel object {"_none": true}.
            if cached.get("_none"):
                return None
            if cached["_kind"] == "movie":
                return _movie_from_dict(cached["data"])
            return _tv_from_dict(cached["data"])
        fresh = self._client.find_by_imdb_id(imdb_id)
        envelope: dict[str, Any]
        if fresh is None:
            envelope = {"_none": True}
        elif isinstance(fresh, MovieResult):
            envelope = {"_kind": "movie", "data": asdict(fresh)}
        else:
            envelope = {"_kind": "tv", "data": asdict(fresh)}
        self._write(key, "find_by_imdb_id", "search", envelope)
        return fresh

    # ----- Internals --------------------------------------------------------

    def _key(self, method: str, query: str) -> str:
        """Canonical cache key. Includes API version + language for forward-safety."""
        return f"v3|{self._language}|{method}|{query}"

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._cache_dir / f"{digest}.json"

    def _read(self, key: str, ttl_class: TTLClass) -> Any:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fp:
                envelope = json.load(fp)
        except (OSError, ValueError):
            return None
        # Guard against key collisions on the truncated digest: confirm the
        # full canonical key matches.
        if envelope.get("key") != key:
            return None
        if ttl_class == "search":
            stored_at = float(envelope.get("stored_at", 0))
            if self._now_fn() - stored_at > SEARCH_TTL_SECONDS:
                return None
        # id_lookup entries never expire by time.
        return envelope.get("payload")

    def _write(self, key: str, method: str, ttl_class: TTLClass, payload: Any) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        envelope = {
            "method": method,
            "key": key,
            "stored_at": float(self._now_fn()),
            "ttl_class": ttl_class,
            "payload": payload,
        }
        path = self._path_for(key)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(envelope, fp, sort_keys=True)


def _norm(s: str) -> str:
    """Normalize a query for cache-key stability.

    Lowercase, strip leading/trailing whitespace, collapse runs of internal
    whitespace. We deliberately keep punctuation: ``Foo & Bar`` and
    ``Foo and Bar`` are different searches for TMDB.
    """
    return " ".join(s.lower().split())


# --- Dataclass deserialization helpers --------------------------------------


def _movie_from_dict(d: dict[str, Any]) -> MovieResult:
    return MovieResult(
        tmdb_id=int(d["tmdb_id"]),
        title=d["title"],
        year=d.get("year"),
        original_title=d.get("original_title"),
        overview=d.get("overview"),
        imdb_id=d.get("imdb_id"),
    )


def _tv_from_dict(d: dict[str, Any]) -> TVResult:
    episode_list = tuple(_episode_from_dict(e) for e in d.get("episode_list") or ())
    return TVResult(
        tmdb_id=int(d["tmdb_id"]),
        title=d["title"],
        year=d.get("year"),
        original_title=d.get("original_title"),
        overview=d.get("overview"),
        imdb_id=d.get("imdb_id"),
        episode_list=episode_list,
    )


def _episode_from_dict(d: dict[str, Any]) -> Episode:
    return Episode(
        season=int(d["season"]),
        episode=int(d["episode"]),
        title=d["title"],
        air_date=d.get("air_date"),
    )
