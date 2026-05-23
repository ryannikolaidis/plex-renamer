"""TV flat-with-season shape — Doctor Who Classic / Video/ corpus.

Shape: episode title at the START, noise tokens (``ANIMATED FULL EPISODES``)
in the middle, season buried as a literal ``Season N`` token, show name at
the END. No episode number; the user surfaces these for manual review.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser import parse_file


def _parse(rel: str, *, root: Path) -> object:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.touch()
    return parse_file(full, input_root=root)


def test_doctor_who_classic_flat_shape(tmp_path: Path) -> None:
    name = "The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.mp4"
    result = _parse(name, root=tmp_path)
    assert result.kind == "tv"
    # Season buried in title is extracted.
    assert result.season == 5
    # Episode number is absent — the parser doesn't invent one.
    assert result.episode is None
    # The title residue contains both episode title and show name; downstream
    # code will fuzzy-match. The parser preserves the whole residue, with the
    # double-spaces collapsed.
    assert result.title_candidate is not None
    assert "The Tomb of the Cybermen" in result.title_candidate
    assert "Doctor Who Classic" in result.title_candidate


def test_flat_shape_no_quality_tokens_leak_into_title(tmp_path: Path) -> None:
    name = "Pilot  ANIMATED FULL EPISODES  Season 1  Some Show.mp4"
    result = _parse(name, root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    # "ANIMATED" and "FULL EPISODES" are not in the quality token list, so
    # they stay in the title residue. Downstream cleanup happens in the
    # planner once the show is anchored.
    assert result.title_candidate is not None
    assert "Pilot" in result.title_candidate
    assert "Some Show" in result.title_candidate
