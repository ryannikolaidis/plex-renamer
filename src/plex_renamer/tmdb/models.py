"""Data shapes for TMDB-backed identification.

The :class:`Candidate` is the resolver pipeline's public output and is what
the planner (slice 4) consumes alongside :class:`~plex_renamer.parser.ParseResult`.

These dataclasses are frozen so they can be cached, hashed, and passed
across slice boundaries without mutation surprises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AnchorKind = Literal["tmdb", "imdb", "tvdb"]
"""Which identifier system anchors the output folder."""

ItemKind = Literal["movie", "tv"]
"""Top-level classification of a resolver candidate. Note: this excludes
``unknown`` because a resolver only returns a Candidate when it has a usable
hit. Parser-level ``unknown`` items never reach the resolver."""


@dataclass(frozen=True)
class Episode:
    """A single TV episode as TMDB returns it.

    Used both for show-anchor matching (the planner fuzzy-matches parsed
    episode titles against the episode list) and for the final emitted
    filename (``- S01E02 - <title>.<ext>``).
    """

    season: int
    episode: int
    title: str
    air_date: str | None = None
    """ISO ``YYYY-MM-DD`` if TMDB knows it; ``None`` otherwise."""


@dataclass(frozen=True)
class MovieResult:
    """A TMDB movie record."""

    tmdb_id: int
    title: str
    year: int | None
    """Release year extracted from ``release_date`` when present."""

    original_title: str | None = None
    overview: str | None = None
    imdb_id: str | None = None
    """``tt`` IMDb id when TMDB has it (mostly populated by detail endpoints,
    not search)."""


@dataclass(frozen=True)
class TVResult:
    """A TMDB TV show record.

    ``year`` is the first-air-date year. ``episode_list`` is populated only
    when the caller has fetched seasons explicitly; the bare ``search_tv``
    and ``get_tv`` calls leave it as an empty tuple.
    """

    tmdb_id: int
    title: str
    year: int | None
    original_title: str | None = None
    overview: str | None = None
    imdb_id: str | None = None
    episode_list: tuple[Episode, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Candidate:
    """The resolver pipeline's public output.

    The planner consumes this. ``anchor_kind`` and ``anchor_id`` together
    determine the output folder anchor (``{tmdb-<id>}`` or ``{imdb-tt<id>}``).
    ``confidence`` is a 0.0-1.0 score blending normalized-title similarity
    with year-match status. ``episode_list`` is populated for TV when the
    resolver fetched seasons; ``None`` otherwise.
    """

    anchor_kind: AnchorKind
    anchor_id: str
    """For TMDB anchors this is the numeric id rendered as str (e.g.
    ``"12345"``); for IMDb anchors this is the ``tt``-prefixed id (e.g.
    ``"tt6293822"``)."""

    kind: ItemKind
    title: str
    year: int | None
    confidence: float
    episode_list: tuple[Episode, ...] | None = None
