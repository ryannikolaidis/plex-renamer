"""Corpus generator tests.

The corpus generator must emit a file for every entry in
:data:`CORPUS_PATTERNS`. Each category must be represented; running the
generator over an existing tree must be idempotent.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from plex_renamer.parser import parse_tree
from plex_renamer.test_corpus import CORPUS_PATTERNS, build_corpus
from plex_renamer.test_corpus.generator import READONLY_PREFIX, _refuse_readonly
from plex_renamer.test_corpus.patterns import entries_for_category


def test_build_corpus_creates_every_entry(tmp_path: Path) -> None:
    created = build_corpus(tmp_path)
    assert len(created) == len(CORPUS_PATTERNS)
    for path in created:
        assert path.exists(), f"generator returned non-existent path: {path}"


def test_build_corpus_is_idempotent(tmp_path: Path) -> None:
    first = build_corpus(tmp_path)
    second = build_corpus(tmp_path)
    assert {str(p) for p in first} == {str(p) for p in second}


def test_every_category_has_entries() -> None:
    expected_categories = {
        "tv_bracketed",
        "tv_sxxexx",
        "tv_flat_with_season",
        "movies_flat",
        "movies_folder",
        "permutations",
        "sidecars_and_artwork",
        "skip_patterns",
    }
    seen = {entry.category for entry in CORPUS_PATTERNS}
    assert expected_categories == seen


def test_each_category_has_at_least_one_entry() -> None:
    for category in {
        "tv_bracketed",
        "tv_sxxexx",
        "tv_flat_with_season",
        "movies_flat",
        "movies_folder",
        "permutations",
        "sidecars_and_artwork",
        "skip_patterns",
    }:
        entries = entries_for_category(category)  # type: ignore[arg-type]
        assert len(entries) > 0, f"category {category!r} has no entries"


def test_required_observed_patterns_present() -> None:
    """The brief locks specific patterns from the user's reference corpus."""
    relative_paths = {entry.relative_path for entry in CORPUS_PATTERNS}
    required = {
        "Game Of Thrones/s1/[S01.E01] Winter Is Coming.mp4",
        "Mad Men/s7/[S07.E14] Person to Person.mp4",
        "Recipe For Crime/s1/S01E26 - Episode 26.mp4",
        "A Field In England.mp4",
        "Men In Black 3.mp4",
        "Men In Black II.mp4",
        "Dodgeball A True Underdog Story.mp4",
        "Spaceballs.mp4",
        "Spaceballs_1.mp4",
        "Millennium Actress/Millennium Actress.mp4",
        "The.Matrix.1999.1080p.BluRay.x264.mp4",
        "Inception [2010].mp4",
        "The Godfather (1972).mp4",
        "[RG]The.Lighthouse.2019.HDR.HEVC-Group.mkv",
        "Sherlock - S01E01-E02 - A Study in Pink.mkv",
        "Kill Bill - cd1.avi",
        "Kill Bill - cd2.avi",
        "Doctor Who/Specials/S00E01 - Time Crash.mp4",
        "Doctor Who/S00/Christmas Special.mp4",
        "The Daily Show - 2023-04-12 - Guest Episode.mp4",
        "El Día De La Bestia.mp4",
        "Some Title &#8211; Subtitle_.mp4",
        "Pokémon The Movie.mp4",
        "temp_50604_pay_5/Millennium Actress.mp4_0_0.download",
        "temp_38694_pay_4/Three Amigos!.mp4_1.tmp",
        ".DS_Store",
        "Some Folder/Thumbs.db",
        "Movie/desktop.ini",
    }
    missing = required - relative_paths
    assert not missing, f"missing required patterns: {sorted(missing)}"


def test_doctor_who_classic_flat_present() -> None:
    """Exact-match the Doctor Who Classic flat-shape entry (double-spaces matter)."""
    relative_paths = {entry.relative_path for entry in CORPUS_PATTERNS}
    needle = "The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.mp4"
    assert needle in relative_paths


def test_sidecar_permutations_present() -> None:
    relative_paths = {entry.relative_path for entry in CORPUS_PATTERNS}
    required_sidecars = {
        "Sidecar Demo/Sidecar Demo.en.srt",
        "Sidecar Demo/Sidecar Demo.en.forced.srt",
        "Sidecar Demo/Sidecar Demo.en.sdh.srt",
        "Sidecar Demo/Sidecar Demo.en-GB.srt",
        "Sidecar Demo/Sidecar Demo.es.srt",
        "Movie (2020)/Movie.nfo",
        "Movie (2020)/poster.jpg",
        "Movie (2020)/fanart.jpg",
        "Movie (2020)/banner.jpg",
    }
    missing = required_sidecars - relative_paths
    assert not missing


def test_nfd_entry_written_as_nfd(tmp_path: Path) -> None:
    """The Pokémon entry is intentionally NFD-encoded on disk."""
    build_corpus(tmp_path)
    # Look for any file whose name, when normalized to NFC, becomes
    # "Pokémon The Movie.mp4". The on-disk basename must NOT already be NFC.
    matches = [p for p in tmp_path.rglob("*") if "Pok" in p.name and p.name.endswith(".mp4")]
    assert len(matches) == 1
    on_disk = matches[0].name
    assert unicodedata.normalize("NFC", on_disk) == "Pokémon The Movie.mp4"
    # And it should differ from the NFC form on disk.
    assert on_disk != "Pokémon The Movie.mp4"


def test_generator_refuses_readonly_prefix() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        build_corpus(Path(READONLY_PREFIX) / "child")


def test_generator_refuse_readonly_allows_sibling_prefix() -> None:
    """Generator's own guard must NOT false-match a sibling directory.

    ``/Volumes/Cage/Media/CleverGetExtra`` shares the string prefix
    ``/Volumes/Cage/Media/CleverGet`` but is a distinct directory. The
    guard must use a PurePath.parts comparison, matching the conftest's
    test-side check; a string-prefix check would incorrectly raise.
    """
    sibling = Path("/Volumes/Cage/Media/CleverGetExtra/whatever")
    # MUST NOT raise. The function returns None on the allow path.
    _refuse_readonly(sibling)


def test_generated_tree_is_parseable(tmp_path: Path) -> None:
    """parse_tree must traverse the generated corpus without crashing.

    Skip-pattern entries surface with skip_reason; media files surface as
    movie / tv / unknown. We don't assert specific classifications here —
    those live in the dedicated parser test modules — only that every
    generated file produces a ParseResult.
    """
    build_corpus(tmp_path)
    results = list(parse_tree(tmp_path))
    # Every generated file must appear in the parse output.
    assert len(results) == len(CORPUS_PATTERNS)
