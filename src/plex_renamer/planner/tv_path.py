"""Build Plex-canonical TV target paths.

Path shape (from INVARIANTS.md)::

    <tv_root>/<Show> (<Year>) {<anchor>}/Season <NN>/<Show> (<Year>) - S<NN>E<NN> - <Episode Title>.<ext>

Specials route to ``Season 00/``. Multi-episode files render as
``S01E01-E02``. The show folder always carries the year; the episode
files do not repeat the anchor.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.planner.movie_path import render_anchor
from plex_renamer.planner.path_safety import sanitize_component
from plex_renamer.tmdb.models import Candidate, Episode


def tv_show_folder_name(show: Candidate) -> str:
    """Build ``<Show> (<Year>) {<anchor>}``."""
    parts = [show.title]
    if show.year is not None:
        parts.append(f"({show.year})")
    parts.append("{" + render_anchor(show) + "}")
    return sanitize_component(" ".join(parts))


def tv_season_folder_name(season: int) -> str:
    """Two-digit zero-padded; ``Season 00`` for specials."""
    return f"Season {season:02d}"


def tv_episode_basename(
    show: Candidate,
    season: int,
    episode: int,
    episode_title: str,
    episode_end: int | None = None,
) -> str:
    """Build ``<Show> (<Year>) - S01E02 - <Episode Title>``.

    ``episode_end`` produces the ``S01E01-E02`` multi-episode form when
    provided. The episode title is taken from the TMDB episode list (the
    canonical source) — fall back to the parser's episode_title when
    TMDB has no record.
    """
    show_part = show.title
    if show.year is not None:
        show_part = f"{show_part} ({show.year})"
    se_marker = f"S{season:02d}E{episode:02d}"
    if episode_end is not None and episode_end > episode:
        se_marker = f"{se_marker}-E{episode_end:02d}"
    stem = f"{show_part} - {se_marker}"
    if episode_title:
        stem = f"{stem} - {episode_title}"
    return sanitize_component(stem)


def tv_target_path(
    show: Candidate,
    tv_root: Path,
    season: int,
    episode: int,
    episode_title: str,
    ext: str,
    episode_end: int | None = None,
) -> Path:
    """Build the full TV episode target path."""
    folder = tv_show_folder_name(show)
    season_folder = tv_season_folder_name(season)
    stem = tv_episode_basename(show, season, episode, episode_title, episode_end)
    safe_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    filename = sanitize_component(f"{stem}{safe_ext}")
    return tv_root / folder / season_folder / filename


def tv_sidecar_target(
    show: Candidate,
    tv_root: Path,
    season: int,
    episode: int,
    episode_title: str,
    sidecar_suffix: str,
    episode_end: int | None = None,
) -> Path:
    folder = tv_show_folder_name(show)
    season_folder = tv_season_folder_name(season)
    stem = tv_episode_basename(show, season, episode, episode_title, episode_end)
    filename = sanitize_component(f"{stem}{sidecar_suffix}")
    return tv_root / folder / season_folder / filename


def episode_from_list(
    episodes: tuple[Episode, ...] | None, season: int, episode: int
) -> Episode | None:
    """Look up an Episode by (season, episode). ``None`` when missing."""
    if not episodes:
        return None
    for ep in episodes:
        if ep.season == season and ep.episode == episode:
            return ep
    return None


__all__ = [
    "episode_from_list",
    "tv_episode_basename",
    "tv_season_folder_name",
    "tv_show_folder_name",
    "tv_sidecar_target",
    "tv_target_path",
]
