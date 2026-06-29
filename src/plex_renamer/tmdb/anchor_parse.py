"""Parse TMDB / IMDb anchor strings from CLI / URL forms.

The user types anchors in two shapes:

1. **Canonical short form** (what the rest of the codebase uses for
   ``anchor_kind`` + ``anchor_id``):

   * ``tmdb-12345`` — kind inferred from context (movie or tv).
   * ``tmdb-movie-12345`` — explicit movie.
   * ``tmdb-tv-12345`` — explicit tv.
   * ``tmdb-tv-12345/season/2`` — tv with a season override.
   * ``imdb-tt0123456`` — IMDb anchor.

2. **TMDB URL** (what a user pastes from the browser):

   * ``https://www.themoviedb.org/movie/12345``
   * ``https://www.themoviedb.org/movie/12345-some-slug``
   * ``https://www.themoviedb.org/tv/678``
   * ``https://www.themoviedb.org/tv/678/season/3``
   * ``https://www.themoviedb.org/tv/678/season/3/episode/7``

This module is read-only and TMDB-free; it only normalizes the input.
The caller hands the resulting :class:`AnchorRef` to the resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

AnchorKind = Literal["tmdb", "imdb"]
ItemKind = Literal["movie", "tv"]


@dataclass(frozen=True)
class AnchorRef:
    """Parsed anchor identifier.

    ``kind`` is the kind of identifier (TMDB or IMDb).
    ``item_kind`` is what the identifier points at: ``"movie"`` or
    ``"tv"``. For canonical short forms that don't include the item
    kind (``tmdb-12345``), ``item_kind`` is None and the caller fills
    it from context (the parser's classification).

    ``id`` is the bare identifier:

    * TMDB: numeric string (``"12345"``).
    * IMDb: ``tt``-prefixed string (``"tt0123456"``).

    Optional ``season`` and ``episode`` are populated only when the
    input URL or short form includes them — used to scope a TV anchor
    to a specific season/episode for episode-level matching.
    """

    kind: AnchorKind
    item_kind: ItemKind | None
    id: str
    season: int | None = None
    episode: int | None = None


class AnchorParseError(ValueError):
    """Raised when a string can't be parsed as a TMDB/IMDb anchor."""


_TMDB_URL_RE = re.compile(
    r"""
    ^https?://
    (?:www\.)?themoviedb\.org/
    (?P<kind>movie|tv)/
    (?P<id>\d+)
    (?:-[^/]+)?           # optional slug after the id (movie/12345-some-slug)
    (?:/season/(?P<season>\d+))?
    (?:/episode/(?P<episode>\d+))?
    /?$
    """,
    re.VERBOSE | re.IGNORECASE,
)

_TMDB_SHORT_RE = re.compile(
    r"""
    ^tmdb
    (?:-(?P<kind>movie|tv))?
    -(?P<id>\d+)
    (?:/season/(?P<season>\d+))?
    (?:/episode/(?P<episode>\d+))?
    $
    """,
    re.VERBOSE | re.IGNORECASE,
)

_IMDB_RE = re.compile(r"^(?:imdb-)?(?P<id>tt\d{7,8})$", re.IGNORECASE)
_IMDB_URL_RE = re.compile(
    r"""
    ^https?://
    (?:www\.)?imdb\.com/
    title/
    (?P<id>tt\d{7,8})
    /?
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_anchor(raw: str) -> AnchorRef:
    """Parse a TMDB / IMDb anchor string. Raises on invalid input.

    Accepts canonical short forms and TMDB/IMDb URLs. The match is case-
    insensitive and tolerant of trailing whitespace, but the returned
    ``id`` is normalized (digits-only for TMDB, ``tt``-lowercase for
    IMDb).
    """
    s = raw.strip()
    if not s:
        raise AnchorParseError("empty anchor string")

    # TMDB URL
    if (m := _TMDB_URL_RE.match(s)) is not None:
        return AnchorRef(
            kind="tmdb",
            item_kind=m.group("kind").lower(),  # type: ignore[arg-type]
            id=m.group("id"),
            season=int(m.group("season")) if m.group("season") else None,
            episode=int(m.group("episode")) if m.group("episode") else None,
        )

    # TMDB canonical short form
    if (m := _TMDB_SHORT_RE.match(s)) is not None:
        kind_str = m.group("kind")
        return AnchorRef(
            kind="tmdb",
            item_kind=kind_str.lower() if kind_str else None,  # type: ignore[arg-type]
            id=m.group("id"),
            season=int(m.group("season")) if m.group("season") else None,
            episode=int(m.group("episode")) if m.group("episode") else None,
        )

    # IMDb URL
    if (m := _IMDB_URL_RE.match(s)) is not None:
        return AnchorRef(
            kind="imdb",
            item_kind=None,
            id=m.group("id").lower(),
        )

    # IMDb canonical short form (or bare tt-id)
    if (m := _IMDB_RE.match(s)) is not None:
        return AnchorRef(
            kind="imdb",
            item_kind=None,
            id=m.group("id").lower(),
        )

    raise AnchorParseError(
        f"could not parse {raw!r} as a TMDB or IMDb anchor. "
        "Expected one of: tmdb-12345, tmdb-movie-12345, tmdb-tv-12345, "
        "tmdb-tv-12345/season/3, imdb-tt0123456, "
        "https://www.themoviedb.org/movie/12345, "
        "https://www.themoviedb.org/tv/678/season/3"
    )


def render_anchor(ref: AnchorRef) -> str:
    """Render an :class:`AnchorRef` back into canonical short form."""
    if ref.kind == "imdb":
        return f"imdb-{ref.id}"
    out = f"tmdb-{ref.id}" if ref.item_kind is None else f"tmdb-{ref.item_kind}-{ref.id}"
    if ref.season is not None:
        out += f"/season/{ref.season}"
    if ref.episode is not None:
        out += f"/episode/{ref.episode}"
    return out


__all__ = ["AnchorParseError", "AnchorRef", "parse_anchor", "render_anchor"]
