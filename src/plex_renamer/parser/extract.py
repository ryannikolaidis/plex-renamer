"""Top-level parse entry points.

``parse_file`` parses a single file; ``parse_tree`` walks a directory and
emits one :class:`ParseResult` per file (video, sidecar, or skipped). Both
functions are pure — they read the filesystem but never write.

The parsing strategy is:

1. Classify skip patterns first (system files, in-progress downloads,
   non-media extensions). Skipped files still get a :class:`ParseResult` so
   the planner can report them.
2. If the file is a subtitle / NFO / artwork, classify it as a sidecar
   candidate. Sidecar pairing happens in :func:`parse_tree`, not in
   :func:`parse_file`, because pairing needs the directory listing.
3. If the file is a video, tokenize its stem and infer ``kind`` (movie vs
   TV) from the presence of S/E markers or a buried ``Season N`` token.
4. Use the parent directory chain as fallback signal: a parent named like
   a show provides the show title when the filename lacks one; a parent
   named like ``Movie (Year)`` provides the year when the filename lacks it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from plex_renamer.parser.models import ParseResult
from plex_renamer.parser.sidecars import find_sidecars
from plex_renamer.parser.skip import (
    ALL_MEDIA_EXTS,
    classify_skip,
    is_metadata,
    is_subtitle,
    is_video,
)
from plex_renamer.parser.tokens import TokenizedName, normalize_unicode, tokenize

# --- Public API ---------------------------------------------------------------


def parse_file(path: Path, input_root: Path | None = None) -> ParseResult:
    """Parse a single file into a :class:`ParseResult`.

    ``input_root`` is the user's drop / picker root. When provided, the
    returned ``parent_dirs`` list is the relative ancestor chain from
    ``input_root`` down to the file's parent; otherwise it's the absolute
    parent chain.

    Sidecar pairing is NOT performed here — call :func:`parse_tree` to get
    sidecars folded into the video's ``ParseResult``.
    """
    path = path.resolve() if path.is_absolute() else path
    parent_dirs = _parent_dirs(path, input_root)

    skip = classify_skip(path)
    if skip is not None:
        return ParseResult(
            source_path=path,
            kind="unknown",
            raw_filename=path.name,
            parent_dirs=parent_dirs,
            skip_reason=skip,
        )

    # Sidecars are classified as "unknown" here; the planner pairs them.
    if is_subtitle(path) or is_metadata(path):
        return ParseResult(
            source_path=path,
            kind="unknown",
            raw_filename=path.name,
            parent_dirs=parent_dirs,
        )

    if not is_video(path):
        # Belt-and-suspenders: classify_skip would have flagged this, but if
        # something slips through we still emit a skip.
        return ParseResult(
            source_path=path,
            kind="unknown",
            raw_filename=path.name,
            parent_dirs=parent_dirs,
            skip_reason=None,
        )

    stem = path.stem
    tokens = tokenize(stem)

    # Borrow signal from parent directory names when the filename is sparse.
    _apply_parent_hints(tokens, parent_dirs)

    kind: Literal["movie", "tv", "unknown"] = _classify_kind(tokens)
    title_candidate, episode_title = _split_title(tokens, kind)

    # Year-as-title recovery: when the filename is literally just a year
    # (``1984.mp4``) or begins with one that IS part of the title
    # (``2001 A Space Odyssey.mp4``), restore the year into the title
    # candidate and clear the year. The planner can re-derive a year later
    # from TMDB lookups; pinning a fake year here would corrupt the lookup.
    # Alternative considered: keep ``year`` set and also expose the title
    # with the year embedded. Rejected because the planner reads ``year``
    # as authoritative; leaking the year into a title that is NOT actually
    # about the year would mislead the lookup.
    if (
        kind == "movie"
        and tokens.year is not None
        and tokens.year_at_stem_start
        and (title_candidate is None or _starts_with_year(title_candidate) is False)
    ):
        # Case A: residue is empty (year was the entire stem).
        # Case B: residue does not already contain the year (year was at the
        #         start of the stem and was peeled, but the title carries it
        #         semantically — e.g. ``2001 A Space Odyssey``).
        restored_year = str(tokens.year)
        if title_candidate is None or not title_candidate.strip():
            title_candidate = restored_year
        else:
            title_candidate = f"{restored_year} {title_candidate}".strip()
        year_to_emit: int | None = None
    else:
        year_to_emit = tokens.year

    return ParseResult(
        source_path=path,
        kind=kind,
        title_candidate=title_candidate,
        year=year_to_emit,
        season=tokens.season,
        episode=tokens.episode,
        episode_end=tokens.episode_end,
        episode_title=episode_title,
        edition_tokens=list(tokens.edition_tokens),
        quality_tokens=list(tokens.quality_tokens),
        group_tag=tokens.group_tag,
        part_marker=tokens.part_marker,
        raw_filename=path.name,
        parent_dirs=parent_dirs,
    )


def parse_tree(root: Path) -> Iterator[ParseResult]:
    """Walk ``root`` and yield one :class:`ParseResult` per file.

    Directories are descended without depth limits. Sidecars are paired with
    their video sibling and folded into the video's ``ParseResult``; the
    sidecar's own ``ParseResult`` is still yielded (with ``kind="unknown"``)
    so callers see every file that exists.
    """
    root = root.resolve()
    if root.is_file():
        # Single-file input: nothing to pair against.
        yield parse_file(root, input_root=root.parent)
        return

    if not root.is_dir():
        return

    for dirpath, _dirnames, filenames in _walk(root):
        dir_files = [dirpath / name for name in filenames]
        videos = [p for p in dir_files if is_video(p)]
        # Pair sidecars per-video.
        sidecars_by_video = {v: find_sidecars(v, dir_files) for v in videos}

        for file_path in dir_files:
            result = parse_file(file_path, input_root=root)
            if file_path in sidecars_by_video:
                result.sidecars = sidecars_by_video[file_path]
            yield result


# --- Walk helper --------------------------------------------------------------


def _walk(root: Path) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Pathlib-friendly walk that yields (dirpath, dirnames, filenames).

    Sorted so output is deterministic across runs. This matters for tests.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        yield Path(dirpath), dirnames, filenames


# --- Classification helpers ---------------------------------------------------


def _classify_kind(tokens: TokenizedName) -> Literal["movie", "tv", "unknown"]:
    """Decide movie vs TV based on parsed tokens.

    TV signals (any of):

    * Season + episode pair (from S/E marker).
    * Date-based marker (``YYYY-MM-DD``) — daily shows.
    * Standalone season token (``Season 5``) AND a residue that doesn't look
      like a movie.

    Movie signal: no TV signal, OR S/E absent but year present. The default
    is ``movie`` because most ambiguous inputs (a stem with no markers at
    all) are likely user-provided movie files. The planner will downgrade
    items it can't confidently identify.
    """
    if tokens.season is not None and tokens.episode is not None:
        return "tv"
    if tokens.date is not None:
        return "tv"
    if tokens.season is not None:
        # Season buried in a flat filename (e.g. Doctor Who Classic shape).
        return "tv"
    return "movie"


def _split_title(
    tokens: TokenizedName, kind: Literal["movie", "tv", "unknown"]
) -> tuple[str | None, str | None]:
    """Split the residue into (title_candidate, episode_title).

    For movies: the residue is the title.

    For TV: if an S/E span exists, the title is the residue BEFORE the span
    and the episode title is the residue AFTER. If no S/E span exists (flat
    shape with only ``Season N``), the residue is the title and the episode
    title is None.
    """
    residue = tokens.residue
    if not residue:
        return (None, None)

    # The residue may include a sentinel ``⟦SE⟧`` placeholder where the S/E
    # marker was consumed; use it as the split point if present.
    SENTINEL = "⟦SE⟧"
    if kind == "tv" and SENTINEL in residue:
        before, _, after = residue.partition(SENTINEL)
        title = _clean_residue(before)
        episode_title = _clean_residue(after)
        return (title or None, episode_title or None)

    cleaned = _clean_residue(residue)
    if kind == "tv":
        return (cleaned or None, None)
    return (cleaned or None, None)


def _starts_with_year(text: str) -> bool:
    """Whether ``text`` begins with a 4-digit year-shaped run."""
    import re

    return bool(re.match(r"\s*(?:19|20)\d{2}(?![\d])", text))


def _clean_residue(text: str) -> str:
    """Trim, collapse dashes, and strip trailing/leading punctuation."""
    cleaned = text.replace("⟦SE⟧", " ")
    # Collapse multiple spaces.
    cleaned = " ".join(cleaned.split())
    # Strip dangling separators.
    cleaned = cleaned.strip(" -_–—.")
    return cleaned


# --- Parent-directory hints ---------------------------------------------------


def _apply_parent_hints(tokens: TokenizedName, parent_dirs: list[str]) -> None:
    """Borrow signal from parent directory names.

    Rules:

    * If we don't have a year, and the immediate parent matches
      ``<Title> (<Year>)``, take the year.
    * If we don't have a season, and the immediate parent matches
      ``Season NN`` / ``S0N`` / ``Specials``, take it.
    * If the residue is empty or trivially short, use the immediate parent
      directory name as a fallback title. (Applied later in
      :func:`_split_title`-adjacent logic; we just store the hint here.)
    """
    if not parent_dirs:
        return

    immediate = parent_dirs[-1]

    if tokens.year is None:
        year = _year_from_dir(immediate)
        if year is not None:
            tokens.year = year

    if tokens.season is None:
        season = _season_from_dir(immediate)
        if season is not None:
            tokens.season = season

    # Specials folder: season 0.
    if tokens.season is None and immediate.lower() == "specials":
        tokens.season = 0


def _year_from_dir(name: str) -> int | None:
    """Extract a four-digit year from a parent directory name like ``Movie (2010)``."""
    import re

    match = re.search(r"\b((?:19|20)\d{2})\b", name)
    if match:
        return int(match.group(1))
    return None


def _season_from_dir(name: str) -> int | None:
    """Extract a season number from a parent dir name like ``Season 5`` or ``S03``."""
    import re

    # "Season N" or "Season NN".
    match = re.fullmatch(r"\s*Season\s+(\d{1,3})\s*", name, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    # "s1", "s01", "S03".
    match = re.fullmatch(r"\s*s(\d{1,3})\s*", name, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    # "S00" → specials.
    return None


def _parent_dirs(path: Path, input_root: Path | None) -> list[str]:
    """Return the parent directory chain as a list of names.

    When ``input_root`` is provided and ``path`` is under it, the returned
    list is the path RELATIVE to ``input_root`` (excluding the file itself).
    Otherwise it's every parent up to the filesystem root.

    Both ``path`` and ``input_root`` are resolved before computing the
    relative chain. On macOS the realpath transform ``/var/folders/...`` ->
    ``/private/var/folders/...`` means an unresolved ``input_root`` would
    silently fail the ``relative_to`` check and fall back to the absolute
    parent chain. Resolving both sides keeps the relative form.
    """
    if input_root is not None:
        try:
            resolved_root = input_root.resolve()
            resolved_path = path.resolve() if path.is_absolute() else path
            rel = resolved_path.relative_to(resolved_root)
            return [normalize_unicode(p) for p in rel.parent.parts]
        except ValueError:
            pass
    return [normalize_unicode(p) for p in path.parent.parts]


__all__ = [
    "ALL_MEDIA_EXTS",
    "parse_file",
    "parse_tree",
]
