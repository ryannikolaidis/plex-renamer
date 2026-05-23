"""Movie-shape parsing tests."""

from __future__ import annotations

from pathlib import Path

from plex_renamer.parser import parse_file


def _parse(name: str, *, root: Path | None = None) -> object:
    """Parse a synthetic filename. ``root`` controls the input root for parent_dirs."""
    if root is None:
        path = Path(f"/tmp/_corpus/{name}")
        return parse_file(path)
    path = root / name
    return parse_file(path, input_root=root)


def test_flat_movie_no_year(tmp_path: Path) -> None:
    result = _parse("A Field In England.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "A Field In England"
    assert result.year is None
    assert result.season is None
    assert result.episode is None
    assert result.quality_tokens == []
    assert result.skip_reason is None


def test_flat_movie_sequel_arabic(tmp_path: Path) -> None:
    result = _parse("Men In Black 3.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Men In Black 3"
    assert result.year is None


def test_flat_movie_sequel_roman(tmp_path: Path) -> None:
    result = _parse("Men In Black II.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Men In Black II"


def test_flat_movie_colon_stripped(tmp_path: Path) -> None:
    result = _parse("Dodgeball A True Underdog Story.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Dodgeball A True Underdog Story"


def test_flat_movie_duplicate_suffix(tmp_path: Path) -> None:
    a = _parse("Spaceballs.mp4", root=tmp_path)
    b = _parse("Spaceballs_1.mp4", root=tmp_path)
    assert a.kind == "movie"
    assert a.title_candidate == "Spaceballs"
    assert b.kind == "movie"
    # Underscores collapse to spaces; the _1 suffix is preserved as " 1".
    assert b.title_candidate == "Spaceballs 1"


def test_dotted_movie_with_year_and_quality(tmp_path: Path) -> None:
    result = _parse("The.Matrix.1999.1080p.BluRay.x264.mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "The Matrix"
    assert result.year == 1999
    assert "1080p" in result.quality_tokens
    assert "bluray" in result.quality_tokens
    assert "x264" in result.quality_tokens


def test_year_in_brackets(tmp_path: Path) -> None:
    result = _parse("Inception [2010].mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "Inception"
    assert result.year == 2010


def test_year_in_parens(tmp_path: Path) -> None:
    result = _parse("The Godfather (1972).mp4", root=tmp_path)
    assert result.kind == "movie"
    assert result.title_candidate == "The Godfather"
    assert result.year == 1972


def test_group_tag_quality_and_hdr(tmp_path: Path) -> None:
    result = _parse("[RG]The.Lighthouse.2019.HDR.HEVC-Group.mkv", root=tmp_path)
    assert result.kind == "movie"
    assert result.year == 2019
    assert result.group_tag == "RG"
    assert "hdr" in result.quality_tokens
    assert "hevc" in result.quality_tokens
    assert result.title_candidate is not None
    assert "Lighthouse" in result.title_candidate


def test_folder_with_title_movie(tmp_path: Path) -> None:
    root = tmp_path / "input"
    folder = root / "Millennium Actress"
    folder.mkdir(parents=True)
    video = folder / "Millennium Actress.mp4"
    video.touch()

    result = parse_file(video, input_root=root)
    assert result.kind == "movie"
    assert result.title_candidate == "Millennium Actress"
    assert result.parent_dirs == ["Millennium Actress"]


def test_movie_folder_year_borrowed_from_parent(tmp_path: Path) -> None:
    root = tmp_path / "input"
    folder = root / "Movie (2020)"
    folder.mkdir(parents=True)
    video = folder / "Movie.mp4"
    video.touch()

    result = parse_file(video, input_root=root)
    assert result.kind == "movie"
    assert result.year == 2020
    assert result.title_candidate == "Movie"


def test_part_marker_movie(tmp_path: Path) -> None:
    result_a = _parse("Kill Bill - cd1.avi", root=tmp_path)
    result_b = _parse("Kill Bill - cd2.avi", root=tmp_path)
    assert result_a.part_marker == "cd1"
    assert result_b.part_marker == "cd2"
    assert result_a.title_candidate is not None
    assert "Kill Bill" in result_a.title_candidate
