"""Show-anchor flow tests.

The load-bearing engine invariant: each episode file matches against
the show's TMDB episode list by RapidFuzz title score FIRST, with
filename S/E as a tiebreaker only when fuzzy match is ambiguous.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.show_anchor import match_episode
from plex_renamer.tmdb.models import Candidate, Episode


def _show_with_episodes(episodes: list[Episode]) -> Candidate:
    return Candidate(
        anchor_kind="tmdb",
        anchor_id="1396",
        kind="tv",
        title="Test Show",
        year=2020,
        confidence=0.95,
        episode_list=tuple(episodes),
    )


def _parsed_tv(
    title: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> ParseResult:
    return ParseResult(
        source_path=Path("/tmp/x.mkv"),
        kind="tv",
        title_candidate="Test Show",
        episode_title=title,
        season=season,
        episode=episode,
    )


def test_match_by_title_first() -> None:
    """Title-fuzzy match wins even when filename S/E disagrees."""
    show = _show_with_episodes(
        [
            Episode(season=1, episode=1, title="The Pilot"),
            Episode(season=1, episode=2, title="Cat's in the Bag"),
            Episode(season=2, episode=1, title="Seven Thirty-Seven"),
        ]
    )
    # Filename says S02E01, but the title clearly matches S01E02.
    parsed = _parsed_tv(title="Cat's in the Bag", season=2, episode=1)
    match = match_episode(parsed, show)
    assert match is not None
    assert match.season == 1
    assert match.episode == 2


def test_match_se_fallback_when_no_title() -> None:
    """Without an episode title we fall back to filename S/E."""
    show = _show_with_episodes(
        [
            Episode(season=1, episode=1, title="The Pilot"),
            Episode(season=1, episode=2, title="Cat's in the Bag"),
        ]
    )
    parsed = _parsed_tv(title=None, season=1, episode=2)
    match = match_episode(parsed, show)
    assert match is not None
    assert match.episode == 2


def test_match_se_tiebreaker_when_titles_ambiguous() -> None:
    """When two episodes score within N=5 of the top, filename S/E breaks the tie."""
    show = _show_with_episodes(
        [
            Episode(season=1, episode=1, title="The Adventure"),
            Episode(season=2, episode=1, title="The Adventure"),  # same title!
        ]
    )
    parsed = _parsed_tv(title="The Adventure", season=2, episode=1)
    match = match_episode(parsed, show)
    assert match is not None
    assert match.season == 2


def test_fetch_season_called_when_episode_list_empty() -> None:
    """When the candidate has no episode_list, the matcher calls fetch_season."""
    show = Candidate(
        anchor_kind="tmdb",
        anchor_id="1396",
        kind="tv",
        title="Test Show",
        year=2020,
        confidence=0.95,
        episode_list=None,
    )
    called = {"args": None}

    def _fetch(tmdb_id: int, season: int) -> list[Episode]:
        called["args"] = (tmdb_id, season)
        return [Episode(season=1, episode=1, title="Pilot")]

    parsed = _parsed_tv(title="Pilot", season=1, episode=1)
    match = match_episode(parsed, show, fetch_season=_fetch)
    assert match is not None
    assert called["args"] == (1396, 1)


def test_returns_none_when_no_signal() -> None:
    """No title, no S/E, no episode list -> None."""
    show = _show_with_episodes([])
    parsed = _parsed_tv(title=None, season=None, episode=None)
    assert match_episode(parsed, show) is None


def test_synthetic_episode_when_se_not_in_list() -> None:
    """When S/E points somewhere TMDB doesn't know, we still emit a match."""
    show = _show_with_episodes([Episode(season=1, episode=1, title="Pilot")])
    # Filename S99E99 not in the list. We still get an Episode back so the
    # planner can emit a path.
    parsed = _parsed_tv(title=None, season=99, episode=99)
    match = match_episode(parsed, show)
    assert match is not None
    assert match.season == 99
    assert match.episode == 99
