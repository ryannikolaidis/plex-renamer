"""Path-safety tests.

NFC normalization, Windows-reserved chars / names, length warnings,
and the always-disallowed prefix list.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pytest

from plex_renamer.planner.path_safety import (
    has_at_least_three_components,
    is_always_disallowed,
    path_length_warning,
    sanitize_component,
    sanitize_path,
)

_skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX path semantics don't translate to Windows",
)


def test_sanitize_component_strips_windows_chars() -> None:
    """Each of ``<>:"\\|?*`` becomes ``_``."""
    raw = 'bad<>:"\\|?*name'
    safe = sanitize_component(raw)
    for ch in '<>:"\\|?*':
        assert ch not in safe


def test_sanitize_component_reserved_name() -> None:
    """``CON`` and ``COM1`` etc. get a ``_`` appended to the stem."""
    assert sanitize_component("CON.txt").upper().startswith("CON_")
    assert sanitize_component("COM1.mkv").upper().startswith("COM1_")
    assert sanitize_component("LPT9").upper() == "LPT9_"


def test_sanitize_component_reserved_name_case_insensitive() -> None:
    """Reserved-name check is case-insensitive."""
    assert sanitize_component("con.txt").lower().startswith("con_")
    assert sanitize_component("Aux") == "Aux_"


def test_sanitize_component_keeps_non_reserved() -> None:
    """A non-reserved component passes through unchanged (after NFC)."""
    raw = "Breaking Bad (2008)"
    assert sanitize_component(raw) == raw


def test_sanitize_component_nfc_normalizes() -> None:
    """NFD input becomes NFC."""
    nfd = unicodedata.normalize("NFD", "Pokémon")
    safe = sanitize_component(nfd)
    assert unicodedata.is_normalized("NFC", safe)


def test_sanitize_component_trailing_dots_and_spaces_stripped() -> None:
    """Windows strips trailing dots / spaces; we anticipate that."""
    assert sanitize_component("File.") == "File"
    assert sanitize_component("File ") == "File"
    assert sanitize_component("File...") == "File"


def test_sanitize_component_empty_falls_back_to_underscore() -> None:
    assert sanitize_component("") == "_"
    assert sanitize_component("...") == "_"


@_skip_on_windows
def test_sanitize_path_preserves_anchor() -> None:
    p = Path("/Users/ryan/Movies/CON.mkv")
    safe = sanitize_path(p)
    assert safe.anchor == "/"
    # CON gets sanitized.
    assert safe.name.startswith("CON_")


def test_path_length_warning_above_threshold() -> None:
    """Warn when the full path exceeds the threshold."""
    long_path = Path("/tmp/" + "a" * 300 + ".mkv")
    assert path_length_warning(long_path) is not None


def test_path_length_warning_below_threshold() -> None:
    short = Path("/tmp/movie.mkv")
    assert path_length_warning(short) is None


@_skip_on_windows
def test_is_always_disallowed_posix() -> None:
    """The POSIX always-disallowed list refuses cleanup."""
    for guarded in (
        "/",
        "/Users",
        "/Users/ryan",
        "/Volumes",
        "/Volumes/MyDisk",
        "/tmp",
        "/var",
        "/System",
        "/Library",
        "/Applications",
        "/private",
    ):
        assert is_always_disallowed(Path(guarded)), guarded


def test_is_always_disallowed_negative_for_descendants() -> None:
    """A descendant of a guarded prefix is not itself guarded."""
    assert not is_always_disallowed(Path("/Users/ryan/scratch"))
    assert not is_always_disallowed(Path("/Volumes/MyDisk/Movies"))


@_skip_on_windows
def test_has_at_least_three_components() -> None:
    assert not has_at_least_three_components(Path("/"))
    assert not has_at_least_three_components(Path("/Users"))
    assert not has_at_least_three_components(Path("/Users/ryan"))
    assert has_at_least_three_components(Path("/Users/ryan/scratch"))
    assert has_at_least_three_components(Path("/Users/ryan/scratch/Movies"))


def test_path_safety_warnings_surface_on_op(tmp_path: Path) -> None:
    """An overlong target path surfaces a warning on the RenameOp."""
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.planner.build import build_plan_from_pairs
    from plex_renamer.tmdb.models import Candidate

    long_title = "x" * 250
    source = tmp_path / "in" / "x.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate=long_title,
        year=2010,
        raw_filename=source.name,
    )
    cand = Candidate(
        anchor_kind="tmdb",
        anchor_id="1",
        kind="movie",
        title=long_title,
        year=2010,
        confidence=0.9,
    )
    plan = build_plan_from_pairs(
        [(parsed, cand)],
        movies_root=tmp_path / "Movies",
        tv_root=tmp_path / "TV",
        input_root=tmp_path / "in",
    )
    assert len(plan.ops[0].warnings) >= 1
    assert "exceeds" in plan.ops[0].warnings[0]
