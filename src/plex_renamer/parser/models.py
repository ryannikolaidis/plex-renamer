"""Parser data shapes.

These dataclasses are the public contract between the parser stage and every
downstream stage. They are frozen-ish (regular dataclasses, but treated as
immutable in practice) and carry only data, no behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SidecarKind = Literal["subtitle", "nfo", "artwork"]
"""Kinds of sidecar files the parser can pair with a video."""

ItemKind = Literal["movie", "tv", "unknown"]
"""Top-level classification of a parsed file."""

SkipReasonValue = Literal[
    "in_progress_download",
    "system_file",
    "non_media_extension",
    "empty_directory",
    "corrupted_archive_remnant",
]
"""Closed set of reasons a file is excluded from media processing."""


@dataclass
class Sidecar:
    """A file paired with a recognized video by basename stem.

    Pairing matches by basename stem before the first language code or
    extension. ``Foo.en.srt``, ``Foo.en.forced.srt``, ``Foo.en.sdh.srt``, and
    ``Foo.en-GB.srt`` all pair with ``Foo.mp4``. ``poster.jpg`` / ``fanart.jpg``
    / ``banner.jpg`` and ``.nfo`` files in the same directory as a single video
    also pair when their stem matches the video or matches a Plex artwork
    convention name.
    """

    path: Path
    kind: SidecarKind
    language: str | None = None
    """Two-letter or BCP-47-ish language tag (``en``, ``en-GB``, ``es``)."""

    modifiers: list[str] = field(default_factory=list)
    """Subtitle modifiers such as ``forced`` or ``sdh``."""


@dataclass
class SkipReason:
    """Why a file is excluded from media processing.

    ``reason`` is the categorical bucket; ``detail`` is a free-form explanation
    surfaced in the run report (e.g. the matched basename or the offending
    extension).
    """

    reason: SkipReasonValue
    detail: str = ""


@dataclass
class ParseResult:
    """Structured output of parsing a single filesystem entry.

    All fields use ``None`` to mean "not extractable" and an empty list to mean
    "checked but found none." Downstream code MUST treat ``season`` and
    ``episode`` as HINTS, not as authoritative identity. See the module
    docstring on :mod:`plex_renamer.parser` for the rationale.
    """

    source_path: Path
    """Absolute path to the input file."""

    kind: ItemKind
    """Top-level classification: ``movie``, ``tv``, or ``unknown``."""

    title_candidate: str | None = None
    """Cleaned title string with year, quality, and group tokens stripped."""

    year: int | None = None
    """Four-digit year if extractable from the filename or parent folder."""

    season: int | None = None
    """Season number HINT only; not authoritative. See module docstring."""

    episode: int | None = None
    """Episode number HINT only; not authoritative. See module docstring."""

    episode_end: int | None = None
    """For multi-episode files like ``S01E01-E02``; the end of the range."""

    episode_title: str | None = None
    """Cleaned episode title for TV items only."""

    edition_tokens: list[str] = field(default_factory=list)
    """Edition labels like ``"Director's Cut"`` or ``"Extended"``."""

    quality_tokens: list[str] = field(default_factory=list)
    """Resolution / codec / HDR tokens like ``1080p``, ``x264``, ``HDR``."""

    group_tag: str | None = None
    """Release-group tag extracted from a leading or trailing ``[Group]``."""

    part_marker: str | None = None
    """Multi-part movie marker such as ``pt1``, ``cd2``, ``disc1``."""

    sidecars: list[Sidecar] = field(default_factory=list)
    """Sidecar files paired with this video by basename stem."""

    skip_reason: SkipReason | None = None
    """Set when this file is excluded from media processing entirely."""

    raw_filename: str = ""
    """Original filename, basename only."""

    parent_dirs: list[str] = field(default_factory=list)
    """Ancestors up to the input root; useful for show-folder hints."""
