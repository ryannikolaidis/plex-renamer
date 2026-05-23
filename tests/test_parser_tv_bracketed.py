"""TV bracketed S/E shape: ``[S03.E12] Title.mp4`` (Amazon / MAX).

This shape buries season and episode inside a leading bracket group with a
period separator between them. The trailing residue is the episode title.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser import parse_file


def _parse(rel: str, *, root: Path) -> object:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.touch()
    return parse_file(full, input_root=root)


def test_got_s01e01(tmp_path: Path) -> None:
    result = _parse("Game Of Thrones/s1/[S01.E01] Winter Is Coming.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 1
    assert result.episode_end is None
    assert result.episode_title == "Winter Is Coming"
    # The filename itself has no show title — the residue after the bracket
    # is purely the episode title.
    assert result.title_candidate is None or result.title_candidate == "Winter Is Coming"
    # Parent dirs preserve the show folder hint.
    assert "Game Of Thrones" in result.parent_dirs


def test_mad_men_s07e14(tmp_path: Path) -> None:
    result = _parse("Mad Men/s7/[S07.E14] Person to Person.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 7
    assert result.episode == 14
    assert result.episode_title == "Person to Person"
    assert "Mad Men" in result.parent_dirs


def test_bracket_shape_records_se_as_hint(tmp_path: Path) -> None:
    """The brief locks the show-anchor invariant: filename S/E is a HINT only."""
    result = _parse("Mad Men/s7/[S07.E14] Person to Person.mp4", root=tmp_path)
    # Documented via the ParseResult: filename S/E are non-authoritative.
    # The hint is recorded but downstream code must treat episode_title as
    # the canonical lookup key against TMDB.
    assert result.episode_title == "Person to Person"
    assert result.episode == 14  # hint stored


def test_bracket_shape_with_multi_episode(tmp_path: Path) -> None:
    result = _parse("Show/s1/[S01.E01-E02] Pilot Part One.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 1
    assert result.episode_end == 2
