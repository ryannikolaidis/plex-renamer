"""Show-anchor flow: match each episode file to a TMDB episode.

This is the load-bearing engine invariant of the project. Given a TV
:class:`~plex_renamer.tmdb.Candidate` and a list of
:class:`~plex_renamer.parser.ParseResult` from sibling episode files,
we attach each file to a specific :class:`~plex_renamer.tmdb.Episode`
using **fuzzy title match FIRST** (rapidfuzz token_set_ratio against the
episode's TMDB title), with filename ``(season, episode)`` HINTS as the
tiebreaker only when the fuzzy match is ambiguous.

Why title-first: filename S/E numbering disagrees with TMDB's canonical
numbering in many cases (regional episode splits, special-episode
interleaving, animation vs original air orderings). Trusting the
filename's S/E numbers as the primary key produces wrong output the
moment the user has any anime, classic Doctor Who, or regionally-split
release. Fuzzy title matching is wrong less often.

Tiebreaker rule: if two or more episodes score within N=5 points of the
top match, fall through to filename S/E as the disambiguator.

Fallback: when the parser produced no episode_title, the file's S/E is
the only signal we have. We use it directly.

Season fetching: the Candidate's ``episode_list`` may be ``None`` (TMDB
hit but season not yet fetched). The matcher accepts a ``_TMDBLike``
protocol or a callable ``fetch_season(tmdb_id, season)`` so it can
populate on demand without import-coupling to the cache.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from rapidfuzz import fuzz

from plex_renamer.parser.models import ParseResult
from plex_renamer.tmdb.models import Candidate, Episode

TITLE_TIEBREAK_THRESHOLD = 5

SE_TITLE_CONSISTENCY_THRESHOLD = 60
"""partial_ratio cutoff for "the canonical title at the filename's S/E
slot is consistent with the parsed title". Above this we trust the S/E
direct lookup; below, we fall through to fuzzy. 60 separates substring
matches (XLV in 'XLV: The Scotsman Saves Jack (1)' = 100) from genuinely
unrelated pairs ("Cat's in the Bag" vs 'Seven Thirty-Seven' = 42)."""
"""Episodes within this many points of the top match are 'ambiguous'."""

MIN_TITLE_SCORE_FOR_MATCH = 50
"""Below this score, we don't trust the title match. Fall back to S/E."""


class _SeasonFetcher(Protocol):
    """Subset of ``TMDBCache`` that the matcher calls back into."""

    def get_season(self, tmdb_id: int, season: int) -> list[Episode]: ...


def match_episode(
    parsed: ParseResult,
    show: Candidate,
    fetch_season: _SeasonFetcher | Callable[[int, int], list[Episode]] | None = None,
) -> Episode | None:
    """Resolve ``parsed`` to one Episode of ``show``.

    Returns ``None`` when no usable match exists (e.g. no episode title in
    the filename and no S/E in the filename either, or the show has no
    episode list and we can't fetch).

    Precedence:

    1. If the filename has both a season AND an episode AND the show's
       episode list has an entry at exactly that slot AND the filename's
       episode title (if any) is consistent with the canonical title at
       that slot, use it. "Consistent" = no title at all, or
       ``partial_ratio`` >= :data:`SE_TITLE_CONSISTENCY_THRESHOLD` (a
       substring-friendly score; a parsed ``"XLV"`` is consistent with
       ``"XLV: The Scotsman Saves Jack (1)"`` at 100).
    2. Else fuzzy-match the parsed episode title against the show's
       episodes; pick the top score above ``MIN_TITLE_SCORE_FOR_MATCH``,
       with the filename's S/E as a tiebreaker when multiple episodes
       share the top fuzzy band.
    3. Else (no S/E hit, no usable title) synthesize from the parsed
       S/E if available so the planner can still emit a path.

    Earlier the fuzzy match always ran first, which broke shows whose
    filenames carried only the bare episode marker (e.g. ``XLV.mp4``
    for Samurai Jack) — short tokens fuzz-match short unrelated tokens
    (``XCV``) in other seasons more strongly than the canonical entry's
    longer title under ``token_set_ratio``. The consistency check keeps
    the original auto-correct path open for the case where the filename
    has *correct* title but *wrong* S/E.
    """
    episodes = _ensure_episodes(parsed, show, fetch_season)
    if not episodes:
        # Last-ditch: if we have S/E hints, return a synthetic Episode so
        # the planner can still emit a path (with an empty title slot).
        if parsed.season is not None and parsed.episode is not None:
            return Episode(
                season=parsed.season,
                episode=parsed.episode,
                title=parsed.episode_title or "",
            )
        return None

    # 1. Filename S/E direct lookup — trust the user's numbering when
    # the canonical source has a slot for it AND the parsed title (if
    # given) doesn't strongly contradict the canonical title at that slot.
    if parsed.season is not None and parsed.episode is not None:
        direct = next(
            (
                e
                for e in episodes
                if e.season == parsed.season and e.episode == parsed.episode
            ),
            None,
        )
        if direct is not None:
            parsed_title = (parsed.episode_title or "").strip()
            if not parsed_title:
                return direct
            # ``partial_ratio`` rewards substring matches, which is what
            # we want: parsed ``"XLV"`` against the canonical
            # ``"XLV: The Scotsman Saves Jack (1)"`` scores 100. A
            # genuinely different title at the same slot scores low
            # (~40), letting fuzzy take over.
            if (
                fuzz.partial_ratio(parsed_title.lower(), direct.title.lower())
                >= SE_TITLE_CONSISTENCY_THRESHOLD
            ):
                return direct
        # Else: no canonical slot, or canonical title contradicts the
        # parsed one. Fall through to fuzzy.

    # 2. Fuzzy title match.
    title = (parsed.episode_title or "").strip()
    if title:
        scored = _score_episodes(title, episodes)
        if scored:
            top_score = scored[0][1]
            if top_score >= MIN_TITLE_SCORE_FOR_MATCH:
                near_top = [ep for ep, s in scored if (top_score - s) <= TITLE_TIEBREAK_THRESHOLD]
                if len(near_top) == 1:
                    return near_top[0]
                # Tiebreak using filename S/E hints when available.
                if parsed.season is not None and parsed.episode is not None:
                    for ep in near_top:
                        if ep.season == parsed.season and ep.episode == parsed.episode:
                            return ep
                # No S/E hint or no agreement: take the top by score.
                return near_top[0]

    # 3. Synthesize from S/E hints if available so the planner can still
    # emit a path. The episode title goes through as-is (may be empty).
    if parsed.season is not None and parsed.episode is not None:
        return Episode(
            season=parsed.season,
            episode=parsed.episode,
            title=parsed.episode_title or "",
        )

    return None


def _ensure_episodes(
    parsed: ParseResult,
    show: Candidate,
    fetch_season: _SeasonFetcher | Callable[[int, int], list[Episode]] | None,
) -> list[Episode]:
    """Return the episode list for the candidate, fetching one season on demand."""
    if show.episode_list:
        return list(show.episode_list)
    if fetch_season is None or show.anchor_kind != "tmdb":
        return []
    season_hint = parsed.season if parsed.season is not None else 1
    try:
        tmdb_id = int(show.anchor_id)
    except (ValueError, TypeError):
        return []
    try:
        if callable(fetch_season):
            episodes = fetch_season(tmdb_id, season_hint)
        else:
            episodes = fetch_season.get_season(tmdb_id, season_hint)
    except Exception:
        return []
    return list(episodes)


def _score_episodes(title: str, episodes: list[Episode]) -> list[tuple[Episode, float]]:
    """Score every episode by RapidFuzz token_set_ratio against ``title``.

    Returns the list sorted descending by score. Episodes whose title is
    empty score 0 (we can't compare to nothing).
    """
    out: list[tuple[Episode, float]] = []
    for ep in episodes:
        if not ep.title:
            out.append((ep, 0.0))
            continue
        score = fuzz.token_set_ratio(title.lower(), ep.title.lower())
        out.append((ep, float(score)))
    out.sort(key=lambda p: p[1], reverse=True)
    return out


__all__ = [
    "MIN_TITLE_SCORE_FOR_MATCH",
    "TITLE_TIEBREAK_THRESHOLD",
    "match_episode",
]
