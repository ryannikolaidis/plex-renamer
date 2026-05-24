"""Build Plex-canonical movie target paths.

Path shape (from INVARIANTS.md):

* Single movie file::

      <movies_root>/<Title> (<Year>) {<anchor>}/<Title> (<Year>) {<anchor>}.<ext>

* Multi-part movie (cd1/cd2/pt1/pt2/...)::

      <movies_root>/<Title> (<Year>) {<anchor>}/<Title> (<Year>) {<anchor>} - pt1.<ext>

* With edition (only when the caller opts in via ``apply_edition=True``)::

      <movies_root>/<Title> (<Year>) {edition-Director's Cut} {<anchor>}/<Title> (<Year>) {edition-Director's Cut} {<anchor>}.<ext>

* Subtitle sidecar in the same per-movie folder::

      <movies_root>/<Title> (<Year>) {<anchor>}/<Title> (<Year>) {<anchor>}.en.srt
"""

from __future__ import annotations

from pathlib import Path

from plex_renamer.planner.path_safety import sanitize_component
from plex_renamer.tmdb.models import Candidate


def render_anchor(candidate: Candidate) -> str:
    """Render the Plex anchor token: ``tmdb-<id>`` or ``imdb-tt<id>``.

    The IMDb anchor convention requires the ``tt`` prefix to already be in
    ``anchor_id``; we render it verbatim. The synthesizer in
    :mod:`plex_renamer.tmdb.fallback` returns IDs with the ``tt`` prefix.
    """
    if candidate.anchor_kind == "tmdb":
        return f"tmdb-{candidate.anchor_id}"
    return f"imdb-{candidate.anchor_id}"


def movie_folder_name(candidate: Candidate, edition: str | None) -> str:
    """Return the per-movie folder name with optional edition stamp.

    ``edition`` is None unless the caller explicitly opts in to applying
    the parser-detected edition token; the planner defaults to off.
    """
    title = candidate.title
    year = candidate.year
    anchor = render_anchor(candidate)
    year_segment = f"({year})" if year is not None else ""
    parts = [title]
    if year_segment:
        parts.append(year_segment)
    if edition:
        parts.append("{edition-" + edition + "}")
    parts.append("{" + anchor + "}")
    return sanitize_component(" ".join(parts))


def movie_base_stem(candidate: Candidate, edition: str | None) -> str:
    """Return the base filename stem (no extension, no part suffix).

    Identical to :func:`movie_folder_name`: Plex's convention has the file
    share the folder's name verbatim.
    """
    return movie_folder_name(candidate, edition)


def movie_target_path(
    candidate: Candidate,
    movies_root: Path,
    edition: str | None,
    part_marker: str | None,
    ext: str,
) -> Path:
    """Build the full target path for a single movie file.

    ``ext`` is the leading-dot extension (``.mkv``); we pass it through
    verbatim, lower-cased.

    ``part_marker`` is normalized to ``pt<N>`` regardless of whether the
    source was ``cd1``, ``pt1``, ``part1``, or ``disc1``. The convention
    keeps multi-part siblings sorted lexicographically.
    """
    folder = movie_folder_name(candidate, edition)
    stem = movie_base_stem(candidate, edition)
    if part_marker:
        normalized = _normalize_part_marker(part_marker)
        stem = f"{stem} - {normalized}"
    safe_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    filename = sanitize_component(f"{stem}{safe_ext}")
    return movies_root / folder / filename


def movie_sidecar_target(
    candidate: Candidate,
    movies_root: Path,
    edition: str | None,
    part_marker: str | None,
    sidecar_suffix: str,
) -> Path:
    """Build the target path for a sidecar that pairs with this movie.

    ``sidecar_suffix`` is everything after the base stem, e.g.
    ``.en.srt``, ``.en.forced.srt``, ``.nfo``, ``-poster.jpg``. We don't
    inspect the sidecar shape; the caller computed it from the
    :class:`~plex_renamer.parser.Sidecar` data.
    """
    folder = movie_folder_name(candidate, edition)
    stem = movie_base_stem(candidate, edition)
    if part_marker:
        normalized = _normalize_part_marker(part_marker)
        stem = f"{stem} - {normalized}"
    filename = sanitize_component(f"{stem}{sidecar_suffix}")
    return movies_root / folder / filename


def _normalize_part_marker(marker: str) -> str:
    """Normalize any of cd1/pt1/part1/disc1/disk1 to ``pt<N>``."""
    import re

    m = re.match(r"^(?:cd|pt|part|disc|disk)\s*(\d+)$", marker.strip().lower())
    if m:
        return f"pt{int(m.group(1))}"
    # Fallback: pass through, normalized.
    return marker.strip().lower()


__all__ = [
    "movie_base_stem",
    "movie_folder_name",
    "movie_sidecar_target",
    "movie_target_path",
    "render_anchor",
]
