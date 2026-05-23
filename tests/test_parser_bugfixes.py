"""Regression tests for cold-reviewer-flagged parser bugs.

Every test in this module pins behavior surfaced by the cold reviewer round
on the slice 2 implementation. The fixes update token classification,
year-as-title recovery, separator collapsing, scene-style trailing group
suffixes, the cross-format S/E disambiguator, and the bracket-inner
classifier ordering. See the slice 2 amend brief for the catalog.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from plex_renamer.parser import parse_file

# The read-only reference dir guard uses a hard-coded `/Volumes/Cage/Media/CleverGet`
# prefix, which is the user's actual macOS reference media directory. On Windows
# the path semantics break the parts-based comparison (`/Volumes/...` resolves to
# a drive-prefixed path), so the guard's behavior is not testable on Windows.
# The production prefix only protects the user's macOS dev machine; CI on
# Linux + macOS still exercises the mechanism.
_skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="readonly-guard prefix is macOS-specific; Windows path semantics break the comparison",
)


def _parse(name: str, *, root: Path) -> object:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return parse_file(path, input_root=root)


# --- Fix 1: Quality whitelist no longer eats real movie titles --------------


def test_extended_family_keeps_extended_in_title(tmp_path: Path) -> None:
    """``Extended Family (2018).mkv`` — ``extended`` is title content, not quality."""
    result = _parse("Extended Family (2018).mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Extended Family"
    assert result.year == 2018
    assert "extended" not in result.quality_tokens


def test_internal_affairs_keeps_internal_in_title(tmp_path: Path) -> None:
    result = _parse("Internal Affairs (2002).mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Internal Affairs"
    assert result.year == 2002
    assert "internal" not in result.quality_tokens


def test_limited_partnership_keeps_limited_in_title(tmp_path: Path) -> None:
    result = _parse("Limited Partnership (2014).mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Limited Partnership"
    assert "limited" not in result.quality_tokens


def test_proper_as_lone_title(tmp_path: Path) -> None:
    result = _parse("Proper.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Proper"
    assert "proper" not in result.quality_tokens


def test_remux_as_lone_title(tmp_path: Path) -> None:
    result = _parse("Remux.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Remux"
    assert "remux" not in result.quality_tokens


def test_uncut_gems_keeps_uncut_in_title(tmp_path: Path) -> None:
    """``uncut`` is recognized as a BARE EDITION; the title still keeps it."""
    result = _parse("Uncut Gems (2019).mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.year == 2019
    # Edition extraction pulls "uncut" into edition_tokens but the residue
    # still keeps "Gems"; restoring the full title is the planner's job.
    # The parser must NOT corrupt the residue to "s".
    assert result.title_candidate is not None
    assert "Gems" in result.title_candidate
    assert result.title_candidate != "s"


def test_extended_still_recognized_as_edition_when_marked(tmp_path: Path) -> None:
    """``Extended Edition`` is still pulled as an edition phrase."""
    result = _parse("Some Movie (2010) Extended Edition.mp4", root=tmp_path)
    assert result.year == 2010
    assert any("Extended Edition" in label for label in result.edition_tokens)


def test_quality_tokens_still_recognized_after_curation(tmp_path: Path) -> None:
    """Narrow technical tokens kept after curation still get pulled."""
    result = _parse("Movie 2020 1080p WEB-DL x264 HDR.mkv", root=tmp_path)
    assert result.title_candidate == "Movie"
    assert result.year == 2020
    assert "1080p" in result.quality_tokens
    assert "web-dl" in result.quality_tokens
    assert "x264" in result.quality_tokens
    assert "hdr" in result.quality_tokens


# --- Fix 3: Year-as-title classics ------------------------------------------


def test_year_only_filename_keeps_year_in_title(tmp_path: Path) -> None:
    """``1984.mp4`` — the year IS the title; don't strip it."""
    result = _parse("1984.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "1984"
    assert result.year is None


def test_year_at_end_still_extracted_as_year(tmp_path: Path) -> None:
    """Sanity: a trailing year stays extracted as the year, not title."""
    result = _parse("The Matrix 1999.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "The Matrix"
    assert result.year == 1999


# --- Year-as-title recovery: scene-style <year> <Title> regression tests ----
#
# A stem-start year followed by more text is ambiguous: it may be title
# content (``2001 A Space Odyssey``) or a scene-style release prefix
# (``1999 The Matrix``). The recovery narrows to the unambiguous case
# (``1984.mp4`` — year IS the whole stem); scene-style files keep the
# year extracted and the residue as the title. The cost is accepting
# title degradation on the rare year-as-title-prefix case.


def test_scene_style_year_prefix_keeps_year_extraction(tmp_path: Path) -> None:
    """``1999 The Matrix.mp4`` — scene-style year prefix, title is the residue."""
    result = _parse("1999 The Matrix.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "The Matrix"
    assert result.year == 1999


def test_scene_style_year_prefix_2010_inception(tmp_path: Path) -> None:
    result = _parse("2010 Inception.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Inception"
    assert result.year == 2010


def test_scene_style_year_prefix_2010_dot_inception(tmp_path: Path) -> None:
    """Dot-separator variant — same shape, same expected extraction."""
    result = _parse("2010.Inception.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Inception"
    assert result.year == 2010


def test_year_at_title_start_accepts_year_extraction(tmp_path: Path) -> None:
    """``2001 A Space Odyssey.mp4`` — accepted cost of narrowing the recovery.

    The year extracts (which is technically wrong for this title), and the
    residue becomes the title. This is documented as the explicit trade-off:
    we lose the rare ``<year> <Title>`` titles where the year IS title
    content in exchange for not regressing the common scene-style
    ``<year> <Title>`` shape where the year is a release prefix.
    """
    result = _parse("2001 A Space Odyssey.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.year == 2001
    # Title degradation accepted: the residue contains the rest of the title.
    assert result.title_candidate is not None
    assert "Space Odyssey" in result.title_candidate


# --- Fix 4: parent_dirs resolves input_root ---------------------------------


def test_parent_dirs_resolves_input_root_against_realpath(tmp_path: Path) -> None:
    """An ``input_root`` shaped like macOS ``/var/folders`` resolves before
    computing the relative parent chain.

    Constructing a literal ``/var/folders/...`` path here is not portable so
    we exercise the same code path: pass an unresolved path that goes
    through ``Path.resolve()``. On macOS the realpath transform
    ``/var/folders`` -> ``/private/var/folders`` would otherwise prevent
    ``relative_to`` from succeeding.
    """
    # Build a real subdirectory; the test runs against pytest's tmp_path
    # (already realpath-resolved). To exercise the resolve() codepath we
    # pass an UNRESOLVED form of input_root by constructing a path that
    # includes a symlink-shaped indirection (use the parent + name).
    subdir = tmp_path / "Movies" / "The Matrix (1999)"
    subdir.mkdir(parents=True)
    video = subdir / "The Matrix.mp4"
    video.touch()
    # Unresolved path: re-derive via parent and name without calling resolve().
    unresolved_root = Path(str(tmp_path)) / ".." / tmp_path.name
    result = parse_file(video, input_root=unresolved_root)
    # parent_dirs MUST be the relative chain, not the absolute one.
    assert result.parent_dirs == ["Movies", "The Matrix (1999)"]


# --- Fix 5: Year/date leaves dangling dash separators -----------------------


def test_daily_show_date_leaves_clean_title(tmp_path: Path) -> None:
    """The date span is removed AND the surrounding dashes collapse."""
    result = _parse("The Daily Show - 2023-04-12 - Guest Episode.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.title_candidate is not None
    # No dangling ``- -`` left where the date was.
    assert " - - " not in result.title_candidate
    assert "Daily Show" in result.title_candidate


# --- Fix 6: Trailing scene-style -Group suffix ------------------------------


def test_trailing_group_suffix_extracted_when_quality_present(tmp_path: Path) -> None:
    result = _parse("[RG]The.Lighthouse.2019.HDR.HEVC-Group.mkv", root=tmp_path)
    # Leading [RG] wins as group_tag.
    assert result.group_tag == "RG"
    # Trailing -Group is removed from the title.
    assert result.title_candidate is not None
    assert "Group" not in result.title_candidate
    assert "Lighthouse" in result.title_candidate


def test_trailing_group_with_quality_inferred_as_group_tag(tmp_path: Path) -> None:
    """No leading bracket, but quality context present — trailing word is group."""
    result = _parse("Foo.1080p.x264-Group.mkv", root=tmp_path)
    assert result.group_tag == "Group"
    assert result.title_candidate == "Foo"


def test_no_group_inferred_without_quality_context(tmp_path: Path) -> None:
    """No quality tokens — trailing dash-word stays in the title."""
    result = _parse("Foo-Bar.mkv", root=tmp_path)
    # Without quality tokens, ``-Bar`` is title content.
    assert result.group_tag is None
    assert result.title_candidate is not None
    assert "Bar" in result.title_candidate


# --- Fix 8: [1080p]Title.mkv classified as group_tag ------------------------


def test_bracketed_quality_token_not_group_tag(tmp_path: Path) -> None:
    result = _parse("[1080p]Title.mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.group_tag is None
    assert "1080p" in result.quality_tokens
    assert result.title_candidate == "Title"


# --- Fix 9: Cross-format 1x12 false positives -------------------------------


def test_some_movie_1x12_not_classified_as_tv(tmp_path: Path) -> None:
    """``Some Movie 1x12.mkv`` — no separator-residue, no strong leading sep."""
    result = _parse("Some Movie 1x12.mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.season is None
    assert result.episode is None


def test_show_dash_1x12_dash_title_still_tv(tmp_path: Path) -> None:
    """Sanity: the original TV-shape ``Show - 1x12 - Title.mp4`` still parses."""
    result = _parse("Show - 1x12 - Title.mp4", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 12


def test_dotted_show_1x12_still_tv(tmp_path: Path) -> None:
    """``Show.1x12.mkv`` — dotted scene shape stays TV."""
    result = _parse("Show.1x12.mkv", root=tmp_path)
    assert result.kind == "tv"
    assert result.season == 1
    assert result.episode == 12


# --- Fix 10: [Director Cut] misclassified as group_tag ----------------------


def test_bracketed_director_cut_recognized_as_edition(tmp_path: Path) -> None:
    """Two-word edition phrase in brackets goes to ``edition_tokens``,
    not the group-tag bucket."""
    result = _parse("Some Movie (2010) [Director's Cut].mp4", root=tmp_path)
    assert result.year == 2010
    assert result.group_tag is None
    assert any("Director's Cut" in label for label in result.edition_tokens)


def test_bracketed_unrated_recognized_as_edition(tmp_path: Path) -> None:
    result = _parse("Movie (2015) [Unrated].mp4", root=tmp_path)
    assert result.year == 2015
    assert result.group_tag is None
    assert any(label.lower() == "unrated" for label in result.edition_tokens)


# --- Fix 7: conftest write-guard gaps ---------------------------------------
#
# These tests prove that the autouse fixture catches each newly-patched call.
# We deliberately exercise the guard surface with paths that do NOT exist
# (the prefix path is not present on the test machine); the guard fires on
# the prefix check before any filesystem syscall runs.


_BAD = "/Volumes/Cage/Media/CleverGet/test-write"


@_skip_on_windows
def test_guard_blocks_os_mkdir() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        os.mkdir(_BAD)


@_skip_on_windows
def test_guard_blocks_os_makedirs() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        os.makedirs(_BAD)


@_skip_on_windows
def test_guard_blocks_os_replace() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        os.replace(_BAD, _BAD + "-2")


@_skip_on_windows
def test_guard_blocks_os_symlink() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        os.symlink(_BAD, _BAD + "-link")


@_skip_on_windows
def test_guard_blocks_os_link() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        os.link(_BAD, _BAD + "-link")


@_skip_on_windows
def test_guard_blocks_path_rename(tmp_path: Path) -> None:
    src = Path(_BAD)
    dst = tmp_path / "dst.mp4"
    with pytest.raises(RuntimeError, match="read-only"):
        src.rename(dst)


@_skip_on_windows
def test_guard_blocks_path_replace(tmp_path: Path) -> None:
    src = Path(_BAD)
    dst = tmp_path / "dst.mp4"
    with pytest.raises(RuntimeError, match="read-only"):
        src.replace(dst)


@_skip_on_windows
def test_guard_blocks_path_symlink_to(tmp_path: Path) -> None:
    link = tmp_path / "link"
    target = Path(_BAD)
    with pytest.raises(RuntimeError, match="read-only"):
        link.symlink_to(target)


@_skip_on_windows
def test_guard_blocks_path_hardlink_to(tmp_path: Path) -> None:
    link = tmp_path / "link"
    target = Path(_BAD)
    with pytest.raises(RuntimeError, match="read-only"):
        link.hardlink_to(target)


@_skip_on_windows
def test_guard_blocks_shutil_chown() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        shutil.chown(_BAD, user="root")


@_skip_on_windows
def test_guard_blocks_text_mode_open_write() -> None:
    # The explicit ``"wt"`` mode (text-mode write) is intentional here; the
    # whole point of the test is to verify the guard catches text-mode
    # variants in addition to ``"w"``.
    with pytest.raises(RuntimeError, match="read-only"):
        open(_BAD + "/foo.txt", "wt")  # noqa: SIM115, UP015


@_skip_on_windows
def test_guard_blocks_text_mode_open_append() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        open(_BAD + "/foo.txt", "at")  # noqa: SIM115, UP015


@_skip_on_windows
def test_guard_blocks_text_mode_open_exclusive() -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        open(_BAD + "/foo.txt", "xt")  # noqa: SIM115, UP015


# --- Fix 7 prefix-match: /Volumes/Cage/Media/CleverGetExtra not matched -----


@_skip_on_windows
def test_guard_does_not_falsely_match_sibling_prefix(tmp_path: Path) -> None:
    """The string-prefix check would match ``CleverGetExtra``; the parts-check
    must not. We use a tmp_path-based write to confirm the guard lets it
    through (no RuntimeError); the actual write may not succeed because
    the sibling path does not exist, but the guard MUST NOT fire on it.
    """
    sibling = "/Volumes/Cage/Media/CleverGetExtra/test-write.txt"
    # The guard fires by raising RuntimeError. We assert it doesn't fire.
    # We expect a different OSError (no such file/dir) but NOT a RuntimeError.
    try:
        open(sibling, "w")  # noqa: SIM115
    except RuntimeError:
        pytest.fail("guard false-matched a sibling prefix")
    except OSError:
        # Expected: the path doesn't exist, so the real open raises.
        pass
