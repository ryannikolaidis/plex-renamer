"""``plex-renamer report`` subcommand.

Read-only diagnostic walker: parses a source tree, runs the resolver,
and prints per-row + per-group matching state with the top candidate
plus top-N alternatives. Used to spot-check accuracy and identify
where the parser / TMDB ranking needs tightening.

Never writes or moves files. The output is the same shape regardless
of how much of the tree resolves cleanly.

Output formats:

* Default (no ``--json``): human-readable table grouped by group_key.
  Each row prints kind, parsed title/year, top match, and any
  diagnostic flags. Alternatives are shown indented under each row
  unless ``--no-alternatives`` is set.
* ``--json``: dump the structured report dict to stdout or to a path
  via ``--output``. The dict is JSON-stable across runs so a user can
  diff before/after a parser change.

Filters scope the printed rows. The full report is always computed
(so the summary line is accurate); filters only affect what gets
printed:

* ``--show all`` (default): print every row.
* ``--show low-conf``: print rows with no top candidate OR a top
  candidate below 0.85 confidence.
* ``--show unanchored``: print only rows where no candidate resolved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plex_renamer.config.settings import Settings
from plex_renamer.diagnostics.report import (
    ReportArtifact,
    RowReport,
    build_report,
    report_to_dict,
)
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.errors import TMDBAuthError
from plex_renamer.tmdb.fallback import IMDbFallbackResolver


def add_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``report`` subcommand into the top-level CLI parser."""
    p = sub.add_parser(
        "report",
        help="Run the resolver against a tree and print per-file matches + alternatives. Read-only.",
    )
    p.add_argument("--source", required=True, help="Source directory or file to walk.")
    p.add_argument(
        "--tmdb-key",
        default=None,
        help="TMDB v3 API key. Falls back to settings/.env if omitted.",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of alternative candidates to surface per row (default 5).",
    )
    p.add_argument(
        "--show",
        choices=["all", "low-conf", "unanchored"],
        default="all",
        help="Filter printed rows. Summary always reflects the full tree.",
    )
    p.add_argument(
        "--no-alternatives",
        action="store_true",
        help="Hide the alternatives list under each row.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON dump instead of the human-readable table.",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Write JSON output here instead of stdout (requires --json).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-row progress to stderr while running.",
    )
    p.set_defaults(_handler=run_report)


def run_report(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.exists():
        print(f"plex-renamer: source not found: {source}", file=sys.stderr)
        return 2

    settings = Settings.load()
    tmdb_key = args.tmdb_key or settings.tmdb_api_key
    if not tmdb_key:
        print(
            "plex-renamer: no TMDB key (pass --tmdb-key or set TMDB_API_KEY).",
            file=sys.stderr,
        )
        return 2

    try:
        client = TMDBClient(api_key=tmdb_key)
    except TMDBAuthError as exc:
        print(f"plex-renamer: TMDB auth failed: {exc}", file=sys.stderr)
        return 2
    cache = TMDBCache(client)
    resolver = IMDbFallbackResolver(tmdb=cache, omdb_api_key=settings.omdb_api_key)

    progress = None if args.quiet else _progress_to_stderr

    artifact = build_report(
        source,
        search_movie=resolver.search_movie_pooled,
        search_tv=resolver.search_tv_pooled,
        top_n=int(args.top_n),
        progress=progress,
    )

    # Always dump the ignored-paths list to a tmp file so the user
    # can audit what got filtered out (download shards, system files,
    # non-media). Deterministic name keyed on the source's basename so
    # re-runs overwrite the prior dump.
    ignored_path = _ignored_dump_path(source)
    if artifact.ignored:
        ignored_path.parent.mkdir(parents=True, exist_ok=True)
        with ignored_path.open("w", encoding="utf-8") as fp:
            fp.write(f"# plex-renamer ignored paths for {source}\n")
            fp.write(f"# {len(artifact.ignored)} entries\n")
            for ig in artifact.ignored:
                detail = f" ({ig.detail})" if ig.detail else ""
                fp.write(f"{ig.reason}{detail}\t{ig.source_path}\n")
        print(
            f"plex-renamer: {len(artifact.ignored)} ignored path(s) listed in {ignored_path}",
            file=sys.stderr,
        )

    if args.json:
        payload = json.dumps(report_to_dict(artifact), indent=2)
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload, encoding="utf-8")
            print(f"plex-renamer: wrote JSON report to {out_path}", file=sys.stderr)
        else:
            print(payload)
        return 0

    _print_human(artifact, show=args.show, hide_alternatives=args.no_alternatives)
    return 0


def _ignored_dump_path(source: Path) -> Path:
    """Stable tmp-dir location for the ignored-paths dump.

    Keyed on the source's basename so a user running the report against
    multiple sources doesn't keep clobbering one shared file.
    """
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in source.name)
    return Path("/tmp") / f"plex-renamer-ignored-{safe_name or 'root'}.txt"


def _progress_to_stderr(idx: int, total: int, source_path: Path) -> None:
    # One line per row, overwritable. The user runs this against trees
    # with hundreds of files and needs to know how far through it is.
    sys.stderr.write(f"\rresolving {idx + 1}/{total}: {source_path.name[:60]:<60}")
    sys.stderr.flush()
    if idx + 1 == total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def _print_human(artifact: ReportArtifact, *, show: str, hide_alternatives: bool) -> None:
    summary_lines = [
        "",
        f"plex-renamer report for {artifact.source}",
        "=" * 72,
        f"  rows:                {artifact.total_rows}",
        f"  anchored:            {artifact.anchored_rows} "
        f"({_pct(artifact.anchored_rows, artifact.total_rows)})",
        f"  high confidence:     {artifact.high_confidence_rows} (>= 0.85)",
        f"  needs review:        {artifact.review_rows} (0.60 - 0.85)",
        f"  low confidence:      {artifact.low_confidence_rows} (< 0.60)",
        f"  unknown / no parse:  {artifact.unknown_rows}",
        f"  groups:              {len(artifact.groups)}",
        "",
    ]
    for line in summary_lines:
        print(line)

    for group in artifact.groups:
        # Filter at the group level first — if no row in the group
        # passes the filter, skip the group header entirely.
        group_rows = [r for r in group.rows if _row_passes(r, show)]
        if not group_rows:
            continue
        anchor_label = (
            f"{group.anchor_kind}-{group.anchor_id}" if group.anchored else "(unanchored)"
        )
        print(
            f"[{group.kind.upper()}] {group.label}  →  {anchor_label}  · {group.row_count} row(s)"
        )
        for row in group_rows:
            _print_row(row, hide_alternatives=hide_alternatives)
        print("")


def _print_row(row: RowReport, *, hide_alternatives: bool) -> None:
    if row.top_candidate is None:
        flag_str = f"  [{','.join(row.flags)}]" if row.flags else ""
        print(f"  · {row.raw_filename:<54}  →  (no anchor){flag_str}")
    else:
        c = row.top_candidate
        flag_str = f"  [{','.join(row.flags)}]" if row.flags else ""
        print(
            f"  · {row.raw_filename:<54}  →  {c.title} ({c.year})  "
            f"[{c.anchor_kind}-{c.anchor_id}]  conf={c.confidence:.2f}{flag_str}"
        )
    if hide_alternatives or not row.alternatives:
        return
    for alt in row.alternatives:
        print(
            f"      alt: {alt.title} ({alt.year})  "
            f"[{alt.anchor_kind}-{alt.anchor_id}]  conf={alt.confidence:.2f}"
        )


def _row_passes(row: RowReport, show: str) -> bool:
    if show == "all":
        return True
    if show == "unanchored":
        return row.top_candidate is None
    if show == "low-conf":
        if row.top_candidate is None:
            return True
        return row.top_candidate.confidence < 0.85
    return True


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{(part / whole) * 100:.1f}%"


__all__ = ["add_subparser", "run_report"]
