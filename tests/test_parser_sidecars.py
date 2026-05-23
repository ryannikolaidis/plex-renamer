"""Sidecar pairing tests.

Sidecars (subtitle, NFO, artwork) pair with a video by basename stem with
language and modifier tokens (``forced``, ``sdh``) extracted. Plex-named
artwork files (``poster.jpg``, ``fanart.jpg``, ``banner.jpg``) pair with any
video in the same directory regardless of stem.
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser import parse_tree
from plex_renamer.parser.sidecars import find_sidecars, parse_sidecar_name


def _build_tree(root: Path, names: list[str]) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        full = root / name
        full.parent.mkdir(parents=True, exist_ok=True)
        full.touch()
        paths.append(full)
    return paths


# --- Sidecar name parsing ---------------------------------------------------


def test_parse_plain_subtitle() -> None:
    parsed = parse_sidecar_name(Path("Foo.srt"))
    assert parsed == ("Foo", None, [])


def test_parse_english_subtitle() -> None:
    parsed = parse_sidecar_name(Path("Foo.en.srt"))
    assert parsed == ("Foo", "en", [])


def test_parse_english_forced() -> None:
    parsed = parse_sidecar_name(Path("Foo.en.forced.srt"))
    assert parsed == ("Foo", "en", ["forced"])


def test_parse_english_sdh() -> None:
    parsed = parse_sidecar_name(Path("Foo.en.sdh.srt"))
    assert parsed == ("Foo", "en", ["sdh"])


def test_parse_region_tagged_subtitle() -> None:
    parsed = parse_sidecar_name(Path("Foo.en-GB.srt"))
    assert parsed == ("Foo", "en-GB", [])


def test_parse_region_tagged_lowercased_subtitle() -> None:
    parsed = parse_sidecar_name(Path("Foo.en-gb.srt"))
    assert parsed == ("Foo", "en-GB", [])


def test_parse_nfo_has_no_language() -> None:
    parsed = parse_sidecar_name(Path("Foo.nfo"))
    assert parsed == ("Foo", None, [])


def test_parse_artwork_has_no_language() -> None:
    parsed = parse_sidecar_name(Path("poster.jpg"))
    assert parsed == ("poster", None, [])


# --- Pairing inside a directory --------------------------------------------


def test_subtitle_paired_by_stem(tmp_path: Path) -> None:
    files = _build_tree(
        tmp_path,
        [
            "Movie/Movie.mp4",
            "Movie/Movie.en.srt",
            "Movie/Movie.en.forced.srt",
        ],
    )
    video = files[0]
    sidecars = find_sidecars(video, files)
    assert len(sidecars) == 2
    kinds = {sc.kind for sc in sidecars}
    assert kinds == {"subtitle"}
    languages = {sc.language for sc in sidecars}
    assert languages == {"en"}
    modifiers = sorted([m for sc in sidecars for m in sc.modifiers])
    assert modifiers == ["forced"]


def test_multiple_languages(tmp_path: Path) -> None:
    files = _build_tree(
        tmp_path,
        [
            "Movie/Movie.mp4",
            "Movie/Movie.en.srt",
            "Movie/Movie.es.srt",
            "Movie/Movie.en-GB.srt",
        ],
    )
    video = files[0]
    sidecars = find_sidecars(video, files)
    languages = sorted({sc.language for sc in sidecars if sc.language})
    assert languages == ["en", "en-GB", "es"]


def test_plex_artwork_pairs_in_same_dir(tmp_path: Path) -> None:
    files = _build_tree(
        tmp_path,
        [
            "Movie (2020)/Movie.mp4",
            "Movie (2020)/Movie.nfo",
            "Movie (2020)/poster.jpg",
            "Movie (2020)/fanart.jpg",
            "Movie (2020)/banner.jpg",
        ],
    )
    video = files[0]
    sidecars = find_sidecars(video, files)
    kinds = [sc.kind for sc in sidecars]
    # 1 nfo + 3 artwork (poster, fanart, banner).
    assert kinds.count("artwork") == 3
    assert kinds.count("nfo") == 1


def test_parse_tree_folds_sidecars_into_video(tmp_path: Path) -> None:
    _build_tree(
        tmp_path,
        [
            "Movie (2020)/Movie.mp4",
            "Movie (2020)/Movie.en.srt",
            "Movie (2020)/Movie.en.forced.srt",
            "Movie (2020)/poster.jpg",
        ],
    )
    results = list(parse_tree(tmp_path))
    video_results = [r for r in results if r.raw_filename == "Movie.mp4"]
    assert len(video_results) == 1
    video = video_results[0]
    assert len(video.sidecars) == 3
    assert {sc.kind for sc in video.sidecars} == {"subtitle", "artwork"}


def test_unrelated_subtitle_does_not_pair(tmp_path: Path) -> None:
    files = _build_tree(
        tmp_path,
        [
            "Mix/Alpha.mp4",
            "Mix/Beta.en.srt",  # no Beta.mp4 — unpaired
        ],
    )
    video = files[0]
    sidecars = find_sidecars(video, files)
    assert sidecars == []
