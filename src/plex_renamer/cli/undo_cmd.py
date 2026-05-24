"""``plex-renamer undo`` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

from plex_renamer.executor.journal import Journal
from plex_renamer.executor.undo import undo_batch


def run_undo(args: argparse.Namespace) -> int:
    journal = Journal.load(Path(args.journal))
    result = undo_batch(journal)
    msg = (
        f"plex-renamer: undid {result.reverted} ops; "
        f"sources_recoverable={'yes' if result.sources_recoverable else 'no'}"
    )
    if result.review_dir is not None:
        msg += f"; moved {result.moved_to_review} files to {result.review_dir}"
    print(msg)
    return 0


__all__ = ["run_undo"]
