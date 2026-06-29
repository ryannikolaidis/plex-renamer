"""Derive a TV show name from a parsed item's path tree.

The parser produces a per-file :class:`ParseResult` that knows its own
parent directories. For episode files shaped like
``[S01.E02] Episode Title.mp4`` the ``title_candidate`` is the episode
title, not the show — the show name lives in a parent directory.

This helper is the single source of truth for that derivation. The
Qt GUI orchestrator backfills the show name onto each ItemRow at
parse time; the diagnostics CLI reads it inline. Both call here.

The "season folder" regex matches the conventional layouts:

* ``s1``, ``S01``, ``Season 5``, ``Series 2``: season directories.
* ``Specials``: bonus content directory.

Anything else is treated as the show name candidate, with a fallback
to ``input_root.name`` when every parent_dirs entry looks season-like
(the user dropped the show directory itself).
"""

from __future__ import annotations

import re
from pathlib import Path

_SEASON_FOLDER_RE = re.compile(
    r"^(s|season\s*|series\s*)\d{1,2}$|^specials$",
    re.IGNORECASE,
)


def derive_show_name(input_root: Path, parent_dirs: list[str]) -> str:
    """Find the most likely TV show name from the path tree.

    Walks ``parent_dirs`` left-to-right (closest to ``input_root`` first),
    returning the first entry that does NOT look like a season folder.
    Falls back to ``input_root.name`` when every entry is season-like —
    covers the "user dropped a show directory whose contents are season
    folders" shape.
    """
    for d in parent_dirs:
        if not _SEASON_FOLDER_RE.match(d.strip()):
            return d
    return input_root.name


def looks_like_season_folder(name: str) -> bool:
    """True when ``name`` resembles a season directory ('s1', 'Season 02', etc.)."""
    return bool(_SEASON_FOLDER_RE.match(name.strip()))


__all__ = ["derive_show_name", "looks_like_season_folder"]
