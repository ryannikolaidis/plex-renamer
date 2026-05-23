"""Skip-pattern detection tests.

Files matching skip patterns are still parsed (so callers see them), but
``skip_reason`` is set and the kind is ``unknown``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plex_renamer.parser import parse_file


def _make(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_download_shard_skipped(tmp_path: Path) -> None:
    p = tmp_path / "temp_50604_pay_5" / "Millennium Actress.mp4_0_0.download"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "in_progress_download"
    assert result.kind == "unknown"


def test_tmp_shard_skipped(tmp_path: Path) -> None:
    p = tmp_path / "temp_38694_pay_4" / "Three Amigos!.mp4_1.tmp"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "in_progress_download"


def test_any_file_under_temp_dir_skipped(tmp_path: Path) -> None:
    p = tmp_path / "temp_99_foo" / "thing.mp4"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    # Files under temp_<digits>_<anything> are skipped even when their
    # extension is on the whitelist.
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "in_progress_download"


def test_ds_store_skipped(tmp_path: Path) -> None:
    p = tmp_path / ".DS_Store"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "system_file"


def test_thumbs_db_skipped(tmp_path: Path) -> None:
    p = tmp_path / "Some Folder" / "Thumbs.db"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "system_file"


def test_desktop_ini_skipped(tmp_path: Path) -> None:
    p = tmp_path / "Movie" / "desktop.ini"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "system_file"


def test_non_media_extension_skipped(tmp_path: Path) -> None:
    p = tmp_path / "random.zip"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "non_media_extension"


def test_no_extension_skipped(tmp_path: Path) -> None:
    p = tmp_path / "no_extension_here"
    _make(p)
    result = parse_file(p, input_root=tmp_path)
    assert result.skip_reason is not None
    assert result.skip_reason.reason == "non_media_extension"


# --- Read-only fixture self-test --------------------------------------------


def test_readonly_fixture_blocks_writes_under_prefix(tmp_path: Path) -> None:
    """The conftest autouse fixture must raise on writes under the CleverGet prefix.

    We don't actually create the prefix path — the fixture's string-prefix
    check fires regardless of whether the path exists, which is the whole
    point.
    """
    bad = Path("/Volumes/Cage/Media/CleverGet/Movie/should_not_be_written.mp4")
    with pytest.raises(RuntimeError, match="read-only"):
        bad.touch()


def test_readonly_fixture_blocks_open_under_prefix() -> None:
    """``builtins.open`` is also patched for writable modes under the prefix.

    The bare ``open()`` call is intentional — we are exercising the
    write-guard path and the guarded ``open`` raises before any file handle
    is constructed. A ``with`` block here would never reach ``__enter__``.
    """
    with pytest.raises(RuntimeError, match="read-only"):
        open("/Volumes/Cage/Media/CleverGet/foo.txt", "w")  # noqa: SIM115


def test_readonly_fixture_allows_tmp_writes(tmp_path: Path) -> None:
    """The fixture must not break ordinary writes outside the prefix."""
    p = tmp_path / "ok.txt"
    p.write_text("hello")
    assert p.read_text() == "hello"
