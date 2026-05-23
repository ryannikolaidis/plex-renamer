"""Catalog of patterns the corpus generator emits.

Each :class:`CorpusEntry` is a single relative path that the generator writes
as an empty file under the output directory. The catalog is grouped by
category so test assertions can reference categories.

Adding a new pattern: append a :class:`CorpusEntry` to the appropriate list.
Tests in ``tests/test_corpus_generator.py`` assert that every entry's file
gets created.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CategoryName = Literal[
    "tv_bracketed",
    "tv_sxxexx",
    "tv_flat_with_season",
    "movies_flat",
    "movies_folder",
    "permutations",
    "sidecars_and_artwork",
    "skip_patterns",
]


@dataclass(frozen=True)
class CorpusEntry:
    """A single mock-tree path the generator will emit as an empty file."""

    category: CategoryName
    relative_path: str
    note: str = ""
    """Human-readable description; not used for assertions."""


# --- TV: bracketed [Sxx.Eyy] shape (Amazon / MAX) ----------------------------

TV_BRACKETED: list[CorpusEntry] = [
    CorpusEntry(
        category="tv_bracketed",
        relative_path="Game Of Thrones/s1/[S01.E01] Winter Is Coming.mp4",
        note="Amazon/MAX bracketed S/E with episode title.",
    ),
    CorpusEntry(
        category="tv_bracketed",
        relative_path="Mad Men/s7/[S07.E14] Person to Person.mp4",
        note="Same shape, season 7 episode 14.",
    ),
]

# --- TV: plain SxxExx (Tubitv shape) -----------------------------------------

TV_SXXEXX: list[CorpusEntry] = [
    CorpusEntry(
        category="tv_sxxexx",
        relative_path="Recipe For Crime/s1/S01E26 - Episode 26.mp4",
        note="Plain SxxExx with dash-separated episode title.",
    ),
]

# --- TV: flat with season buried (Doctor Who Classic shape) -------------------

TV_FLAT_WITH_SEASON: list[CorpusEntry] = [
    CorpusEntry(
        category="tv_flat_with_season",
        relative_path=(
            "The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.mp4"
        ),
        note=("Double-spaces, show name at end, season buried in title, no episode number."),
    ),
    CorpusEntry(
        category="tv_flat_with_season",
        relative_path=(
            "The Tomb of the Cybermen  ANIMATED FULL EPISODES  Season 5  Doctor Who Classic.en.srt"
        ),
        note="Sidecar companion to the flat-shape video above.",
    ),
]

# --- Movies: flat with no year -----------------------------------------------

MOVIES_FLAT: list[CorpusEntry] = [
    CorpusEntry(
        category="movies_flat",
        relative_path="A Field In England.mp4",
        note="Simple movie, no year, no other tokens.",
    ),
    CorpusEntry(
        category="movies_flat",
        relative_path="Men In Black 3.mp4",
        note="Sequel with Arabic numeral.",
    ),
    CorpusEntry(
        category="movies_flat",
        relative_path="Men In Black II.mp4",
        note="Sequel with Roman numeral.",
    ),
    CorpusEntry(
        category="movies_flat",
        relative_path="Dodgeball A True Underdog Story.mp4",
        note="Colon-stripped subtitle.",
    ),
    CorpusEntry(
        category="movies_flat",
        relative_path="Spaceballs.mp4",
        note="Plain title baseline.",
    ),
    CorpusEntry(
        category="movies_flat",
        relative_path="Spaceballs_1.mp4",
        note="Duplicate variant with _1 suffix.",
    ),
]

# --- Movies: folder-with-title shape ----------------------------------------

MOVIES_FOLDER: list[CorpusEntry] = [
    CorpusEntry(
        category="movies_folder",
        relative_path="Millennium Actress/Millennium Actress.mp4",
        note="Folder name matches video stem.",
    ),
]

# --- Plausible permutations (not in user corpus but parser must handle) ------

PERMUTATIONS: list[CorpusEntry] = [
    CorpusEntry(
        category="permutations",
        relative_path="The.Matrix.1999.1080p.BluRay.x264.mp4",
        note="Dot-separated tokens with year + quality.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Inception [2010].mp4",
        note="Year in square brackets.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="The Godfather (1972).mp4",
        note="Year in parentheses.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="[RG]The.Lighthouse.2019.HDR.HEVC-Group.mkv",
        note="Release group + quality + HDR + HEVC.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Sherlock - S01E01-E02 - A Study in Pink.mkv",
        note="Multi-episode range.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Kill Bill - cd1.avi",
        note="Multi-part movie, cd1.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Kill Bill - cd2.avi",
        note="Multi-part movie, cd2.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Doctor Who/Specials/S00E01 - Time Crash.mp4",
        note="Specials/ folder + S00Exx.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Doctor Who/S00/Christmas Special.mp4",
        note="S00 folder, no S/E in filename.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="The Daily Show - 2023-04-12 - Guest Episode.mp4",
        note="Date-based daily show.",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="El Día De La Bestia.mp4",
        note="Accented characters (NFC).",
    ),
    CorpusEntry(
        category="permutations",
        relative_path="Some Title &#8211; Subtitle_.mp4",
        note="HTML entity for en-dash; trailing underscore.",
    ),
    CorpusEntry(
        category="permutations",
        # NFD-decomposed "Pokémon" (e + combining acute).
        relative_path="Pokémon The Movie.mp4",
        note="Unicode NFD-encoded accented character.",
    ),
]

# --- Sidecars and artwork ----------------------------------------------------

SIDECARS_AND_ARTWORK: list[CorpusEntry] = [
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.mp4",
        note="Base video for sidecar pairing.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.en.srt",
        note="Plain English subtitle.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.en.forced.srt",
        note="Forced English subtitle.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.en.sdh.srt",
        note="SDH English subtitle.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.en-GB.srt",
        note="Region-tagged subtitle.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Sidecar Demo/Sidecar Demo.es.srt",
        note="Spanish subtitle.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Movie (2020)/Movie.mp4",
        note="Movie with year for artwork pairing.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Movie (2020)/Movie.nfo",
        note="NFO sidecar.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Movie (2020)/poster.jpg",
        note="Plex artwork: poster.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Movie (2020)/fanart.jpg",
        note="Plex artwork: fanart.",
    ),
    CorpusEntry(
        category="sidecars_and_artwork",
        relative_path="Movie (2020)/banner.jpg",
        note="Plex artwork: banner.",
    ),
]

# --- Skip patterns -----------------------------------------------------------

SKIP_PATTERNS: list[CorpusEntry] = [
    CorpusEntry(
        category="skip_patterns",
        relative_path="temp_50604_pay_5/Millennium Actress.mp4_0_0.download",
        note="In-progress CleverGet download shard.",
    ),
    CorpusEntry(
        category="skip_patterns",
        relative_path="temp_38694_pay_4/Three Amigos!.mp4_1.tmp",
        note="In-progress CleverGet temp shard.",
    ),
    CorpusEntry(
        category="skip_patterns",
        relative_path=".DS_Store",
        note="macOS system file at corpus root.",
    ),
    CorpusEntry(
        category="skip_patterns",
        relative_path="Some Folder/Thumbs.db",
        note="Windows thumbnail cache.",
    ),
    CorpusEntry(
        category="skip_patterns",
        relative_path="Movie/desktop.ini",
        note="Windows folder customization file.",
    ),
]


# --- Catalog ----------------------------------------------------------------

CORPUS_PATTERNS: list[CorpusEntry] = [
    *TV_BRACKETED,
    *TV_SXXEXX,
    *TV_FLAT_WITH_SEASON,
    *MOVIES_FLAT,
    *MOVIES_FOLDER,
    *PERMUTATIONS,
    *SIDECARS_AND_ARTWORK,
    *SKIP_PATTERNS,
]
"""Flat list of every pattern. ``len(CORPUS_PATTERNS)`` is the number of
files the generator will write."""


def entries_for_category(category: CategoryName) -> list[CorpusEntry]:
    """Return all entries for a given category."""
    return [e for e in CORPUS_PATTERNS if e.category == category]
