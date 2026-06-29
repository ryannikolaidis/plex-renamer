"""Command-line entry point.

Two ways to run:

* ``plex-renamer <source>`` — bare invocation launches the interactive
  TUI against ``<source>``. The TUI walks every file, lets you redirect
  TMDB anchors per row or per show group, and (with ``--movies`` /
  ``--tv``) applies the resulting plan in the same session.
* ``plex-renamer <subcommand>`` — scripted entry points for automation:
  ``plan`` (build a plan JSON), ``apply`` (consume one), ``undo``,
  ``report`` (read-only diagnostic).

Use ``--simple`` for a line-based REPL instead of the TUI.
"""

from __future__ import annotations

import argparse
import sys

from plex_renamer import __version__
from plex_renamer.cli.apply_cmd import run_apply
from plex_renamer.cli.plan_cmd import run_plan
from plex_renamer.cli.report_cmd import add_subparser as add_report_subparser
from plex_renamer.cli.undo_cmd import run_undo

_UNKNOWN_ARG_EXIT = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plex-renamer",
        description=(
            "Rename movies and TV files into Plex's expected naming format. "
            "Run `plex-renamer <source>` to launch the interactive TUI, or use "
            "one of the subcommands below for scripted runs."
        ),
        add_help=True,
    )
    parser.add_argument("--version", "-V", action="store_true", help="Print version and exit.")

    # --- top-level TUI args (no subcommand) -----------------------------
    # ``source`` is a flag rather than a positional because argparse's
    # subparser slot collides with optional positionals (a bare path
    # gets rejected as an invalid subcommand choice). The app() shim
    # pre-parses ``plex-renamer <path>`` and rewrites it to
    # ``--source <path>`` before argparse runs.
    parser.add_argument(
        "--source",
        default=None,
        help="Source directory or file. Launches the interactive TUI.",
    )
    parser.add_argument(
        "--tmdb-key",
        default=None,
        help="TMDB v3 API key. Falls back to settings/.env if omitted.",
    )
    parser.add_argument(
        "--movies",
        default=None,
        help="Movies library root. Required to apply; falls back to settings.",
    )
    parser.add_argument(
        "--tv",
        default=None,
        help="TV library root. Required to apply; falls back to settings.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of alternatives shown per row drill-in (default 5).",
    )
    parser.add_argument(
        "--show",
        choices=["all", "low-conf", "unanchored"],
        default="low-conf",
        help=("Simple-mode only — which groups to walk. The TUI uses [F] to cycle filters."),
    )
    parser.add_argument(
        "--save",
        default=None,
        help=(
            "Where to persist accumulated anchor overrides. Defaults to "
            "/tmp/plex-renamer-review-anchors-<source-name>.json."
        ),
    )
    parser.add_argument(
        "--load",
        default=None,
        help="Pre-load an anchors JSON to resume an earlier session.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Use the line-based REPL instead of the default TUI.",
    )

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

    return parser


_SUBCOMMANDS = frozenset({"plan", "apply", "undo", "report"})


def _rewrite_bare_source(args: list[str]) -> list[str]:
    """``plex-renamer /path/to/x`` -> ``plex-renamer --source /path/to/x``.

    The shim runs only when the first non-flag arg is neither a known
    subcommand name nor a flag — i.e. the user typed a bare source
    path. Subcommand invocations and explicit ``--source`` flag usage
    are passed through unchanged.
    """
    if not args:
        return args
    first = args[0]
    if first.startswith("-"):
        return args
    if first in _SUBCOMMANDS:
        return args
    return ["--source", first, *args[1:]]


def app(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the matching handler."""
    args = sys.argv[1:] if argv is None else argv

    # Direct --version short-circuit so tests don't need a subcommand.
    if args and args[0] in ("--version", "-V"):
        print(f"plex-renamer {__version__}")
        return 0

    args = _rewrite_bare_source(args)
    parser = _build_parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
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
    # ``report`` registers its own handler via set_defaults(_handler=...).
    handler = getattr(parsed, "_handler", None)
    if callable(handler):
        rc = handler(parsed)
        return rc if isinstance(rc, int) else 0

    # No subcommand. If a source path was given, launch the TUI (or the
    # line-based REPL when --simple is set). Without one, print help.
    if parsed.source:
        if parsed.simple:
            from plex_renamer.cli.review_cmd import run_review_simple

            return run_review_simple(parsed)
        from plex_renamer.cli.review_tui import run_review_tui

        return run_review_tui(parsed)

    if not args:
        print("plex-renamer: pass a source path or --help to see subcommands.")
        return 0

    print(
        f"plex-renamer: unknown argument {args[0]!r}. See --help.",
        file=sys.stderr,
    )
    return _UNKNOWN_ARG_EXIT


if __name__ == "__main__":
    raise SystemExit(app())
