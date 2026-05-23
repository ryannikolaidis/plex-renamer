"""Edge-case parser tests.

Covers: accented chars, HTML entities, Unicode NFD vs NFC, duplicates,
dot-separated tokens, bracketed years, quality tokens, group tags,
multi-episode, multi-part, specials, date-based episodes.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from plex_renamer.parser import parse_file


def _parse(name: str, *, root: Path) -> object:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return parse_file(path, input_root=root)


def test_accented_characters(tmp_path: Path) -> None:
    result = _parse("El Día De La Bestia.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "El Día De La Bestia"


def test_html_entities_decoded(tmp_path: Path) -> None:
    # &#8211; is an HTML numeric entity for an en-dash.
    result = _parse("Some Title &#8211; Subtitle_.mp4", root=tmp_path)
    assert result.title_candidate is not None
    # En-dash present after decoding.
    assert "–" in result.title_candidate
    # Underscore collapsed to space.
    assert "_" not in result.title_candidate


def test_unicode_nfd_normalized_to_nfc(tmp_path: Path) -> None:
    nfd_name = unicodedata.normalize("NFD", "Pokémon The Movie.mp4")
    assert nfd_name != "Pokémon The Movie.mp4"  # confirm it's actually NFD
    result = _parse(nfd_name, root=tmp_path)
    assert result.title_candidate is not None
    # The parser normalizes to NFC.
    nfc_title = unicodedata.normalize("NFC", result.title_candidate)
    assert result.title_candidate == nfc_title
    assert "Pokémon" in result.title_candidate


def test_dot_separated_tokens_collapse(tmp_path: Path) -> None:
    result = _parse("The.Matrix.1999.mp4", root=tmp_path)
    assert result.title_candidate == "The Matrix"
    assert result.year == 1999


def test_year_in_brackets_extracted(tmp_path: Path) -> None:
    result = _parse("Inception [2010].mp4", root=tmp_path)
    assert result.year == 2010
    assert result.title_candidate == "Inception"


def test_quality_tokens_stripped(tmp_path: Path) -> None:
    result = _parse("Film 2020 1080p x264 HDR HEVC.mkv", root=tmp_path)
    assert result.title_candidate == "Film"
    assert result.year == 2020
    assert "1080p" in result.quality_tokens
    assert "x264" in result.quality_tokens
    assert "hdr" in result.quality_tokens
    assert "hevc" in result.quality_tokens


def test_group_tag_in_leading_brackets(tmp_path: Path) -> None:
    result = _parse("[RG]The.Lighthouse.2019.HDR.HEVC-Group.mkv", root=tmp_path)
    assert result.group_tag == "RG"


def test_multi_episode_range(tmp_path: Path) -> None:
    result = _parse("Show - S01E01-E02 - Two Parter.mkv", root=tmp_path)
    assert result.season == 1
    assert result.episode == 1
    assert result.episode_end == 2


def test_multi_part_cd(tmp_path: Path) -> None:
    a = _parse("Movie - cd1.avi", root=tmp_path)
    b = _parse("Movie - cd2.avi", root=tmp_path)
    assert a.part_marker == "cd1"
    assert b.part_marker == "cd2"


def test_multi_part_pt(tmp_path: Path) -> None:
    a = _parse("Movie - pt1.avi", root=tmp_path)
    b = _parse("Movie - pt2.avi", root=tmp_path)
    assert a.part_marker == "pt1"
    assert b.part_marker == "pt2"


def test_multi_part_disc(tmp_path: Path) -> None:
    a = _parse("Movie - disc1.avi", root=tmp_path)
    assert a.part_marker == "disc1"


def test_specials_marker_s00(tmp_path: Path) -> None:
    result = _parse("Doctor Who/Specials/S00E01 - Time Crash.mp4", root=tmp_path)
    assert result.season == 0
    assert result.episode == 1


def test_specials_folder_borrows_season(tmp_path: Path) -> None:
    # No S/E in filename; parent ``Specials`` folder provides season 0.
    result = _parse("Doctor Who/Specials/Christmas Special.mp4", root=tmp_path)
    assert result.season == 0


def test_date_based_episode_classified_as_tv(tmp_path: Path) -> None:
    result = _parse("The Daily Show - 2023-04-12 - Guest Episode.mp4", root=tmp_path)
    assert result.kind == "tv"
    # The four-digit year inside the date IS still extractable; it's not
    # wrong because TMDB queries can use the year if present. Document the
    # behavior.
    assert result.year == 2023
    assert result.title_candidate is not None
    # The parser preserves the residue; the planner trims further.
    assert "Daily Show" in result.title_candidate


def test_duplicate_underscore_suffix(tmp_path: Path) -> None:
    result = _parse("Spaceballs_1.mp4", root=tmp_path)
    # Underscore collapses to space, so the duplicate marker becomes a
    # trailing " 1". The planner sees both Spaceballs.mp4 and
    # Spaceballs_1.mp4 and decides whether they're the same movie.
    assert result.title_candidate == "Spaceballs 1"


def test_edition_brace_extracted(tmp_path: Path) -> None:
    result = _parse("Some Movie (2010) {edition-Director's Cut}.mp4", root=tmp_path)
    assert result.year == 2010
    assert "Director's Cut" in result.edition_tokens
    assert result.title_candidate == "Some Movie"


def test_bare_edition_token_recognized(tmp_path: Path) -> None:
    result = _parse("Some Movie 2010 Extended Edition.mp4", root=tmp_path)
    assert result.year == 2010
    # The bare token is recognized and pulled out; titlecased label stored.
    assert any("Extended Edition" in tok for tok in result.edition_tokens)
