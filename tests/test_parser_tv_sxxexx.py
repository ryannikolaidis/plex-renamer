"""TV plain SxxExx shape: ``S01E26 - Title.mp4`` (Tubitv / Recipe For Crime)."""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser import parse_file


def _parse(rel: str, *, root: Path) -> object:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.touch()
    return parse_file(full, input_root=root)


def test_recipe_for_crime_s01e26(tmp_path: Path) -> None:
    result = _parse("Recipe For Crime/s1/S01E26 - Episode 26.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 26
    assert result.episode_end is None
    assert result.episode_title == "Episode 26"
    assert "Recipe For Crime" in result.parent_dirs


def test_multi_episode_range(tmp_path: Path) -> None:
    result = _parse("Sherlock - S01E01-E02 - A Study in Pink.mkv", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 1
    assert result.episode_end == 2
    assert result.episode_title == "A Study in Pink"
    assert result.title_candidate == "Sherlock"


def test_cross_format_1x12(tmp_path: Path) -> None:
    result = _parse("Show - 1x12 - Title.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 12
    assert result.title_candidate == "Show"
    assert result.episode_title == "Title"


def test_specials_s00e01(tmp_path: Path) -> None:
    result = _parse("Doctor Who/Specials/S00E01 - Time Crash.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 0
    assert result.episode == 1
    assert result.episode_title == "Time Crash"


def test_s00_folder_no_se_in_filename(tmp_path: Path) -> None:
    """``Doctor Who/S00/Christmas Special.mp4`` — season borrowed from parent."""
    result = _parse("Doctor Who/S00/Christmas Special.mp4", root=tmp_path)
    # Season 0 is borrowed from the S00 parent folder.
    assert result.season == 0
    assert result.kind == "tv"


def test_lowercase_se_marker(tmp_path: Path) -> None:
    result = _parse("show - s01e05 - title.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 5
