"""Skip-pattern detection.

This module decides whether a filesystem entry should be excluded from media
processing entirely. It is intentionally narrow: it does not classify
movies vs TV, only "is this even media that we care about?".

The decision is based on filename and ancestor-directory patterns, not on
file contents (we don't read bytes here).
"""

from __future__ import annotations

import re
from pathlib import PurePath

from plex_renamer.parser.models import SkipReason

# Extension whitelists — sourced from INVARIANTS.md "Inputs" section.
VIDEO_EXTS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mkv",
        ".m4v",
        ".avi",
        ".mov",
        ".wmv",
        ".mpg",
        ".mpeg",
        ".ts",
        ".m2ts",
        ".webm",
    }
)

SUBTITLE_EXTS: frozenset[str] = frozenset(
    {
        ".srt",
        ".vtt",
        ".ass",
        ".ssa",
        ".sub",
        ".idx",
        ".sup",
    }
)

METADATA_EXTS: frozenset[str] = frozenset(
    {
        ".nfo",
        ".jpg",
        ".jpeg",
        ".png",
    }
)

ALL_MEDIA_EXTS: frozenset[str] = VIDEO_EXTS | SUBTITLE_EXTS | METADATA_EXTS

# System file basenames that are always skipped regardless of extension.
SYSTEM_BASENAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
    }
)

# Parent-directory names that mark in-progress downloads.
_TEMP_DIR_PATTERN = re.compile(r"^temp_\d+_.+$")

# Filename patterns that mark in-progress / shard / remnant downloads.
_DOWNLOAD_SHARD_PATTERN = re.compile(r"\.(download|tmp)$", re.IGNORECASE)
_PART_SHARD_PATTERN = re.compile(r"\.(part|crdownload)$", re.IGNORECASE)


def classify_skip(path: PurePath) -> SkipReason | None:
    """Return a :class:`SkipReason` if ``path`` should be excluded.

    ``path`` may be relative or absolute; only the basename and ancestor names
    are inspected. Returns ``None`` when the path is a normal media candidate.
    """
    name = path.name

    # System cruft — match before extension checks so case-sensitive matches win.
    if name in SYSTEM_BASENAMES:
        return SkipReason(reason="system_file", detail=name)

    # In-progress download shards: .download, .tmp, .part, .crdownload suffixes.
    if _DOWNLOAD_SHARD_PATTERN.search(name) or _PART_SHARD_PATTERN.search(name):
        return SkipReason(reason="in_progress_download", detail=name)

    # Parent directory matching temp_<digits>_<anything> is the CleverGet
    # in-progress convention. Any file inside such a directory is skipped.
    for parent_name in path.parts[:-1]:
        if _TEMP_DIR_PATTERN.match(parent_name):
            return SkipReason(
                reason="in_progress_download",
                detail=f"under {parent_name}/",
            )

    # Extension whitelist: anything not on it is non-media noise.
    ext = path.suffix.lower()
    if ext == "":
        return SkipReason(reason="non_media_extension", detail="(no extension)")
    if ext not in ALL_MEDIA_EXTS:
        return SkipReason(reason="non_media_extension", detail=ext)

    return None


def is_video(path: PurePath) -> bool:
    """Return True if ``path`` has a recognized video extension."""
    return path.suffix.lower() in VIDEO_EXTS


def is_subtitle(path: PurePath) -> bool:
    """Return True if ``path`` has a recognized subtitle extension."""
    return path.suffix.lower() in SUBTITLE_EXTS


def is_metadata(path: PurePath) -> bool:
    """Return True if ``path`` has a recognized NFO/artwork extension."""
    return path.suffix.lower() in METADATA_EXTS
