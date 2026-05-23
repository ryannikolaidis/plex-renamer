"""Filename and path parser for plex-renamer.

The parser consumes a filesystem tree (files and directories) and emits
structured :class:`ParseResult` objects, one per input file. It is pure local
parsing: no TMDB calls, no network. Downstream stages (planner, executor)
consume ``ParseResult`` to make identification and emission decisions.

Show-anchor invariant
---------------------

The parser records ``season`` and ``episode`` numbers when it can detect them
in filenames, but those numbers are HINTS, not authoritative identity.
Downstream code (the planner in slice 4) treats them as tiebreakers when
matching parsed episode titles against an anchored show's TMDB episode list.
This invariant exists because filename S/E often disagrees with TMDB's
canonical numbering (regional episode splits, special-episode interleaving,
animation vs original air ordering, etc.).
"""

from __future__ import annotations

from plex_renamer.parser.extract import parse_file, parse_tree
from plex_renamer.parser.models import ParseResult, Sidecar, SkipReason

__all__ = ["ParseResult", "Sidecar", "SkipReason", "parse_file", "parse_tree"]
