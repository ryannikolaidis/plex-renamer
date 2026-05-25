"""Tests for scripts/fixture_from_tree.py.

The fixture-from-tree helper mirrors a real directory structure into
empty files at a destination so a user can build a test fixture from
their actual media tree without copying any bytes. The helper exposes
``mirror_tree`` as a library function (the integration tests import it
directly) plus a CLI ``main`` entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to path so the test can import the helper. Pop it back
# off afterwards so the rest of the test session is unaffected.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from fixture_from_tree import mirror_tree  # type: ignore[import-not-found]
finally:
    sys.path.pop(0)


def test_mirrors_directory_structure(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Lazarus" / "s1").mkdir(parents=True)
    (src / "Lazarus" / "s1" / "[S01.E01] Goodbye Cruel World.mp4").write_bytes(b"video data")
    (src / "Lazarus" / "s1" / "[S01.E01] Goodbye Cruel World.en.srt").write_bytes(b"subtitle")

    dst = tmp_path / "dst"
    n_dirs, n_files = mirror_tree(src, dst, include_hidden=False, allowed_exts=None)

    # Lazarus + s1 directories are counted (root dst itself is not).
    assert n_dirs >= 2
    assert n_files == 2
    assert (dst / "Lazarus" / "s1" / "[S01.E01] Goodbye Cruel World.mp4").exists()
    assert (dst / "Lazarus" / "s1" / "[S01.E01] Goodbye Cruel World.en.srt").exists()
    # Zero bytes -- no real data copied.
    assert (dst / "Lazarus" / "s1" / "[S01.E01] Goodbye Cruel World.mp4").stat().st_size == 0


def test_skips_temp_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "Show" / "temp_50604_pay_5").mkdir(parents=True)
    (src / "Show" / "temp_50604_pay_5" / "Movie.mp4_0_0.download").touch()
    (src / "Show" / "s1").mkdir()
    (src / "Show" / "s1" / "[S01.E01] Episode.mp4").touch()

    dst = tmp_path / "dst"
    mirror_tree(src, dst, include_hidden=False, allowed_exts=None)

    assert (dst / "Show" / "s1" / "[S01.E01] Episode.mp4").exists()
    assert not (dst / "Show" / "temp_50604_pay_5").exists()


def test_skips_hidden_by_default(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".DS_Store").touch()
    (src / "Movie.mp4").touch()

    dst = tmp_path / "dst"
    _, n = mirror_tree(src, dst, include_hidden=False, allowed_exts=None)
    assert (dst / "Movie.mp4").exists()
    assert not (dst / ".DS_Store").exists()
    assert n == 1


def test_extension_filter(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Movie.mp4").touch()
    (src / "Movie.srt").touch()
    (src / "Movie.nfo").touch()
    (src / "Movie.jpg").touch()

    dst = tmp_path / "dst"
    mirror_tree(src, dst, include_hidden=False, allowed_exts={"mp4", "srt"})
    assert (dst / "Movie.mp4").exists()
    assert (dst / "Movie.srt").exists()
    assert not (dst / "Movie.nfo").exists()
    assert not (dst / "Movie.jpg").exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX readonly prefix doesn't translate to Windows path semantics",
)
def test_refuses_to_write_under_readonly_prefix(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Movie.mp4").touch()

    with pytest.raises(SystemExit, match="read-only prefix"):
        mirror_tree(
            src,
            Path("/Volumes/Cage/Media/CleverGet/test"),
            include_hidden=False,
            allowed_exts=None,
        )
