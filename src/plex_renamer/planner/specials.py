"""Specials detection.

A parsed item is a "special" when any of:

* ``ParseResult.season == 0``.
* Any parent directory name (case-insensitive) equals ``Specials``.

Specials always route to ``Season 00/``. Season-0 episodes still emit
under the show folder; we just override the season number when building
the target path.
"""

from __future__ import annotations

from plex_renamer.parser.models import ParseResult


def is_special(parsed: ParseResult) -> bool:
    if parsed.season == 0:
        return True
    return any(ancestor.lower() == "specials" for ancestor in parsed.parent_dirs)


__all__ = ["is_special"]
