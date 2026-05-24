"""``plex-renamer apply`` subcommand.

Reads a JSON plan, copies every op, writes a journal, optionally cleans
up sources.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from plex_renamer.executor.copy import apply_plan
from plex_renamer.executor.journal import Journal
from plex_renamer.planner.models import RenamePlan


def run_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = RenamePlan.from_json(plan_path.read_text(encoding="utf-8"))

    journal: Journal | None = None
    journal_path = Path(args.journal) if args.journal else None
    if journal_path is not None:
        journal = Journal.new(
            input_root=plan.input_root,
            library_root=plan.movies_root,
            journal_dir=journal_path.parent,
            batch_id=journal_path.stem,
        )

    result = apply_plan(
        plan,
        journal=journal,
        cleanup=bool(args.cleanup),
        verify_hash=bool(args.verify_hash),
    )

    print(
        f"plex-renamer: applied {result.succeeded} ops, "
        f"{result.failed} failed, cleanup={'on' if result.cleanup_ran else 'off'}; "
        f"journal at {result.journal_path}"
    )
    return 0 if result.failed == 0 else 1


__all__ = ["run_apply"]
