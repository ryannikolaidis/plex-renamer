"""Rename planner.

Consumes :class:`~plex_renamer.parser.ParseResult` lists and resolved
:class:`~plex_renamer.tmdb.Candidate` objects and produces a
:class:`RenamePlan` of copy operations targeting the user-configured
Movies and TV roots.

The planner is pure: it reads no filesystem state, makes no HTTP calls
directly (it consumes pre-resolved Candidates), and emits a data
structure the executor then realizes. Show-anchor matching (fuzzy
episode title -> TMDB episode) is the one place the planner CAN call
back into the TMDB layer via a :class:`_TMDBLike` protocol; tests
inject a fake.
"""

from __future__ import annotations

from plex_renamer.planner.build import build_plan
from plex_renamer.planner.models import Collision, EditionMatch, RenameOp, RenamePlan

__all__ = [
    "Collision",
    "EditionMatch",
    "RenameOp",
    "RenamePlan",
    "build_plan",
]
