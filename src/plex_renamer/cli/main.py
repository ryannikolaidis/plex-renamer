"""Command-line entry point.

Three subcommands:

* ``plan``: walk a source tree, resolve via TMDB, emit a JSON plan.
* ``apply``: execute a plan from JSON, write a journal.
* ``undo``: read a journal and invert the operations.

All three are wired through :func:`app`, which parses argv and
dispatches. The function returns an exit code so the same body can be
called from tests or from the script entry point.
"""

from __future__ import annotations

import argparse
import sys

from plex_renamer import __version__
from plex_renamer.cli.apply_cmd import run_apply
from plex_renamer.cli.plan_cmd import run_plan
from plex_renamer.cli.report_cmd import add_subparser as add_report_subparser
from plex_renamer.cli.review_cmd import add_subparser as add_review_subparser
from plex_renamer.cli.undo_cmd import run_undo

_UNKNOWN_ARG_EXIT = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plex-renamer",
        description="Rename movies and TV files into Plex's expected naming format.",
        add_help=True,
    )
    parser.add_argument("--version", "-V", action="store_true", help="Print version and exit.")
    sub = parser.add_subparsers(dest="command")

    # --- plan ------------------------------------------------------------
    p_plan = sub.add_parser("plan", help="Build a rename plan and write JSON.")
    p_plan.add_argument("--source", required=True, help="Input directory or file to plan.")
    p_plan.add_argument("--movies", required=True, help="Movies library root.")
    p_plan.add_argument("--tv", required=True, help="TV Shows library root.")
    p_plan.add_argument("--output", required=True, help="Plan JSON output path.")
    p_plan.add_argument(
        "--tmdb-key",
        default=None,
        help="TMDB v3 API key. Falls back to settings/.env if omitted.",
    )
    p_plan.add_argument(
        "--apply-editions",
        action="store_true",
        help="Apply parser-detected edition tokens to output paths.",
    )

    # --- apply -----------------------------------------------------------
    p_apply = sub.add_parser("apply", help="Apply a plan: copy files, write journal.")
    p_apply.add_argument("--plan", required=True, help="Plan JSON input path.")
    p_apply.add_argument(
        "--cleanup", action="store_true", help="Delete sources after verified copies."
    )
    p_apply.add_argument(
        "--no-cleanup",
        dest="cleanup",
        action="store_false",
        help="Disable cleanup (default).",
    )
    p_apply.set_defaults(cleanup=False)
    p_apply.add_argument(
        "--verify-hash",
        action="store_true",
        help="Verify with sha256 in addition to size.",
    )
    p_apply.add_argument(
        "--journal",
        default=None,
        help="Output journal path. Defaults to app-data dir.",
    )

    # --- undo ------------------------------------------------------------
    p_undo = sub.add_parser("undo", help="Undo a previously-applied batch.")
    p_undo.add_argument("--journal", required=True, help="Journal JSON path.")

    # --- report (read-only diagnostic) ----------------------------------
    add_report_subparser(sub)

    # --- review (interactive group-by-group anchor reassignment) -------
    add_review_subparser(sub)

    return parser


def app(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the matching subcommand handler."""
    args = sys.argv[1:] if argv is None else argv

    # Direct --version short-circuit so tests don't need a subcommand.
    if args and args[0] in ("--version", "-V"):
        print(f"plex-renamer {__version__}")
        return 0

    parser = _build_parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        # argparse raises SystemExit(0) when the user asked for --help; in
        # that case the help text has already been printed and we should
        # exit cleanly without appending an "unknown argument" diagnostic.
        # SystemExit(2) means a real parse error; format the message.
        code = exc.code if isinstance(exc.code, int) else _UNKNOWN_ARG_EXIT
        if code == 0:
            return 0
        bad = args[0] if args else ""
        print(
            f"plex-renamer: unknown argument {bad!r}. See --help.",
            file=sys.stderr,
        )
        return code

    if parsed.version:
        print(f"plex-renamer {__version__}")
        return 0

    if parsed.command == "plan":
        return run_plan(parsed)
    if parsed.command == "apply":
        return run_apply(parsed)
    if parsed.command == "undo":
        return run_undo(parsed)
    # The ``report`` subcommand registers its own handler on the
    # namespace via add_subparser → set_defaults(_handler=...).
    handler = getattr(parsed, "_handler", None)
    if callable(handler):
        rc = handler(parsed)
        if isinstance(rc, int):
            return rc
        return 0

    if not args:
        print("plex-renamer: pass --help to see available subcommands.")
        return 0

    print(
        f"plex-renamer: unknown argument {args[0]!r}. See --help.",
        file=sys.stderr,
    )
    return _UNKNOWN_ARG_EXIT


if __name__ == "__main__":
    raise SystemExit(app())
