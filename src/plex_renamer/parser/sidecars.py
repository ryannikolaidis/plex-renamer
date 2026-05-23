"""Sidecar pairing.

Sidecars are subtitle, NFO, and Plex artwork files that travel with a video
through rename. The parser matches them by basename stem (before the first
language code or extension) and extracts language and modifier tokens
(``forced``, ``sdh``).

Pairing rules (sourced from INVARIANTS.md "Sidecars and adjacent files"):

* ``Foo.en.srt``, ``Foo.en.forced.srt``, ``Foo.en.sdh.srt``, ``Foo.en-GB.srt``
  all pair with ``Foo.mp4``.
* ``poster.jpg`` / ``fanart.jpg`` / ``banner.jpg`` / ``Foo.nfo`` sitting in
  the same directory as the video pair with it. Artwork files in unrelated
  locations are ignored upstream.
* A sidecar that cannot be paired with any video is excluded from copying
  upstream (the planner handles surfacing it as "skipped sidecar"; the parser
  only emits the pairing data).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

from plex_renamer.parser.models import Sidecar, SidecarKind
from plex_renamer.parser.skip import (
    METADATA_EXTS,
    SUBTITLE_EXTS,
    is_metadata,
    is_subtitle,
    is_video,
)

# Plex artwork basenames that pair with any video in the same directory.
PLEX_ARTWORK_NAMES: frozenset[str] = frozenset(
    {
        "poster",
        "fanart",
        "banner",
        "background",
        "clearlogo",
        "logo",
        "thumb",
    }
)

# Subtitle modifier tokens. Lowercase comparison.
SUBTITLE_MODIFIERS: frozenset[str] = frozenset(
    {
        "forced",
        "sdh",
        "cc",
        "hi",  # hearing-impaired
        "default",
    }
)

# Language tag pattern: 2-3 letter primary + optional region (BCP-47-ish).
# Matches en, eng, en-GB, pt-BR, zh-Hans (no script subtag support beyond simple).
_LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?$")


def parse_sidecar_name(path: PurePath) -> tuple[str, str | None, list[str]] | None:
    """Parse a sidecar filename into (basename_stem, language, modifiers).

    Returns ``None`` when ``path`` is not a recognizable sidecar at all.

    The basename stem is the leading portion before the FIRST language code
    or modifier. For ``Foo.en.forced.srt`` the stem is ``Foo``, language is
    ``en``, modifiers are ``["forced"]``. For ``Foo.srt`` (no language) the
    stem is ``Foo`` and language is ``None``.

    Token-ordering constraint: modifiers MUST come AFTER the language code.
    ``Foo.en.forced.srt`` parses cleanly; ``Foo.forced.en.srt`` does not —
    the leading ``forced`` is consumed as a modifier and ``en`` is taken as
    the language, leaving the stem as ``Foo``. The reverse-ordered form
    ``Foo.forced.en.srt`` produced by some unusual tools is recognized but
    the modifier-before-language path will keep the modifier consumed and
    the stem clean. The single shape that is NOT supported is a stem that
    contains the modifier token in a non-suffix position (e.g.
    ``forced.en.srt`` where the leading ``forced`` is the entire stem). For
    pairing with the video, the calling code matches on stem; modifiers
    after the language guarantee that the stem matches the video's stem.
    """
    ext = path.suffix.lower()
    if ext not in SUBTITLE_EXTS and ext not in METADATA_EXTS:
        return None

    # NFO and artwork: language and modifiers never apply. The whole stem
    # is the basename.
    if ext in METADATA_EXTS:
        return (path.stem, None, [])

    # Subtitle: peel language and modifier tokens off the right side.
    parts = path.stem.split(".")
    if len(parts) == 1:
        return (parts[0], None, [])

    language: str | None = None
    modifiers: list[str] = []
    stem_end = len(parts)

    # Walk left-to-right after the FIRST part; only take consecutive
    # recognized tokens. Once we hit a non-token, stop (the rest is part of
    # the stem).
    cursor = 1
    while cursor < len(parts):
        token = parts[cursor]
        token_lower = token.lower()
        if language is None and _LANG_TAG_RE.match(token):
            # Don't grab a 3-letter word that looks like a language if it's
            # actually a modifier. Modifiers take priority when ambiguous.
            if token_lower in SUBTITLE_MODIFIERS:
                modifiers.append(token_lower)
            else:
                language = _canonicalize_language(token)
            stem_end = cursor
            cursor += 1
            continue
        if token_lower in SUBTITLE_MODIFIERS:
            modifiers.append(token_lower)
            if stem_end == len(parts):
                stem_end = cursor
            cursor += 1
            continue
        # Unrecognized — the rest belongs to the stem.
        break

    stem = ".".join(parts[:stem_end])
    return (stem, language, modifiers)


def _canonicalize_language(raw: str) -> str:
    """Canonicalize a raw language tag: lower-case primary, upper-case region.

    ``en-gb`` → ``en-GB``; ``EN`` → ``en``; ``en_US`` → ``en-US``.
    """
    norm = raw.replace("_", "-")
    if "-" not in norm:
        return norm.lower()
    primary, _, region = norm.partition("-")
    return f"{primary.lower()}-{region.upper()}"


def find_sidecars(video_path: Path, directory_files: list[Path]) -> list[Sidecar]:
    """Pair sidecars from ``directory_files`` with the given ``video_path``.

    ``directory_files`` should be every file in the video's parent directory.
    The function returns a list of :class:`Sidecar` for every paired file.

    Pairing rules:

    1. Subtitle / NFO files whose basename stem equals the video's stem.
    2. Plex-artwork basenames (``poster``, ``fanart``, etc.) in the same
       directory — they pair with the video regardless of its stem.
    3. Multiple sidecars per video are allowed (e.g. ``en`` + ``en.forced``).
    """
    if not is_video(video_path):
        return []

    video_stem = video_path.stem
    sidecars: list[Sidecar] = []

    for candidate in directory_files:
        if candidate == video_path:
            continue
        ext = candidate.suffix.lower()
        if ext not in SUBTITLE_EXTS and ext not in METADATA_EXTS:
            continue

        parsed = parse_sidecar_name(candidate)
        if parsed is None:
            continue
        stem, language, modifiers = parsed

        # Stem match → direct pairing.
        if stem == video_stem:
            sidecars.append(
                Sidecar(
                    path=candidate,
                    kind=_kind_for_ext(ext),
                    language=language,
                    modifiers=modifiers,
                )
            )
            continue

        # Plex artwork name → pairs with any video in the same dir.
        if ext in METADATA_EXTS and stem.lower() in PLEX_ARTWORK_NAMES:
            sidecars.append(
                Sidecar(
                    path=candidate,
                    kind="artwork",
                    language=None,
                    modifiers=[],
                )
            )

    return sidecars


def _kind_for_ext(ext: str) -> SidecarKind:
    """Map a sidecar extension to its :class:`SidecarKind`."""
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    if ext == ".nfo":
        return "nfo"
    if ext in METADATA_EXTS:
        return "artwork"
    # Should be unreachable given the filter in find_sidecars.
    return "artwork"


# Re-export for convenience.
__all__ = [
    "PLEX_ARTWORK_NAMES",
    "SUBTITLE_MODIFIERS",
    "find_sidecars",
    "is_metadata",
    "is_subtitle",
    "is_video",
    "parse_sidecar_name",
]
