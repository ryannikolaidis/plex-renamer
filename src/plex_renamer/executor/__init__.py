"""Plan executor.

Consumes a :class:`~plex_renamer.planner.RenamePlan` and:

* Copies every op's source to its target, creating parent dirs.
* Verifies (size match; optional sha256).
* Writes a JSON write-ahead journal entry BEFORE each filesystem call.
* On opt-in, cleans up source files (with hard safety guards) after all
  ops verified.
* Provides an undo path that reads the journal and inverts the ops.
"""

from __future__ import annotations

from plex_renamer.executor.cleanup import cleanup_sources
from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal, JournalEntry
from plex_renamer.executor.undo import undo_batch

__all__ = [
    "Journal",
    "JournalEntry",
    "apply_plan",
    "cleanup_sources",
    "undo_batch",
]
