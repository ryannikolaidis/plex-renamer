"""Line-based REPL fallback for ``plex-renamer <source> --simple``.

The default invocation (``plex-renamer <source>``) launches the
textual TUI in :mod:`plex_renamer.cli.review_tui`. This module is the
no-deps alternative: a group-by-group prompt that lets you redirect
TMDB anchors without ever touching the filesystem.

UX:

::

    [GROUP 3 / 21]  [TV ]  Lazarus  · 13 file(s)
      current → Lazarus (2025)  [tmdb-231003]  conf=0.91
      alternatives:
        [1] The Lazarus Project (2008) [tmdb-13825]  conf=0.71
        [2] Lazarus (2021)             [tmdb-679784] conf=0.65
        ...
      [a]ccept  [1-N] pick alt  [i] id/url  [s] search  [k] skip  [b] back  [q] save+quit
      ?

Actions:

* ``Enter`` or ``a``: keep the current match, advance.
* digit ``N`` (1-9): replace the anchor with alternative N.
* ``i``: prompt for a TMDB id or themoviedb.org URL.
* ``s``: prompt for a new query, run a TMDB search, show ranked
  results, then re-prompt.
* ``k``: mark this group unanchored (drop the current match).
* ``b``: move back one group.
* ``q``: save accumulated overrides to a JSON file and exit.
* ``?``: print the actions list.

Output: a JSON anchors file (``--save`` path, default
``/tmp/plex-renamer-review-anchors-<source-name>.json``) that the
``report`` subcommand can replay via ``--anchors``. Empty when the
user accepted every group as-is.

Read-only — never writes to or copies the source tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from plex_renamer.config.settings import Settings
from plex_renamer.diagnostics.report import (
    GroupReport,
    ReportArtifact,
    build_report,
)
from plex_renamer.tmdb.anchor_parse import AnchorParseError, parse_anchor
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.errors import TMDBAuthError
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate


def run_review_simple(args: argparse.Namespace) -> int:
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

    # Build the initial report. Progress goes to stderr so the
    # interactive prompts can use stdout cleanly.
    print(f"Resolving {source} ...", file=sys.stderr)
    artifact = build_report(
        source,
        search_movie=resolver.search_movie_pooled,
        search_tv=resolver.search_tv_pooled,
        top_n=int(args.top_n),
        progress=_progress_to_stderr,
        get_movie=cache.get_movie,
        get_tv=cache.get_tv,
        get_season=cache.get_season,
    )

    groups_to_review = _filter_groups(artifact, args.show)
    if not groups_to_review:
        print(
            f"plex-renamer: nothing to review (filter={args.show!r}). "
            f"Try --show all to walk every group.",
            file=sys.stderr,
        )
        return 0

    # Load existing anchors as the starting state if asked.
    state = _ReviewState(
        accumulated={},
        save_path=_resolve_save_path(args.save, source),
    )
    if args.load:
        _preload_state(state, Path(args.load))

    print()
    print(f"plex-renamer review — {len(groups_to_review)} group(s) to walk")
    print(f"  source: {source}")
    print(f"  save:   {state.save_path}")
    print("  ? for help, q to save and quit at any time")
    print()

    rc = _walk_groups(groups_to_review, state, cache=cache, resolver=resolver)

    if state.accumulated:
        _persist_state(state)
        print(
            f"\nWrote {len(state.accumulated)} anchor override(s) to {state.save_path}",
            file=sys.stderr,
        )
        print(
            f"  Replay via: plex-renamer report --source <tree> --anchors {state.save_path}",
            file=sys.stderr,
        )
    else:
        print("\nNo overrides recorded.", file=sys.stderr)
    return rc


# --- internals ----------------------------------------------------------


@dataclass
class _ReviewState:
    """Accumulator for user overrides across the review loop."""

    accumulated: dict[str, str]  # group_key -> anchor short-form
    save_path: Path


def _filter_groups(artifact: ReportArtifact, show: str) -> list[GroupReport]:
    if show == "all":
        return list(artifact.groups)
    if show == "unanchored":
        return [g for g in artifact.groups if not g.anchored]
    # "low-conf": unanchored OR any row in the group below 0.85
    out: list[GroupReport] = []
    for g in artifact.groups:
        if not g.anchored:
            out.append(g)
            continue
        if any(r.top_candidate is not None and r.top_candidate.confidence < 0.85 for r in g.rows):
            out.append(g)
    return out


def _walk_groups(
    groups: list[GroupReport],
    state: _ReviewState,
    *,
    cache: TMDBCache,
    resolver: IMDbFallbackResolver,
) -> int:
    idx = 0
    while 0 <= idx < len(groups):
        group = groups[idx]
        _print_group_summary(idx, len(groups), group, state)
        action = _prompt("> ")
        result = _handle_action(action, group=group, state=state, cache=cache, resolver=resolver)
        if result == "next":
            idx += 1
        elif result == "back":
            idx = max(0, idx - 1)
        elif result == "quit":
            return 0
        elif result == "stay":
            # Loop again on the same group (re-prompt).
            continue
        else:
            idx += 1
    print(f"\nReached end of review queue ({len(groups)} groups).", file=sys.stderr)
    return 0


def _print_group_summary(idx: int, total: int, group: GroupReport, state: _ReviewState) -> None:
    print()
    print(
        f"[GROUP {idx + 1} / {total}]  [{group.kind.upper():5}]  "
        f"{group.label}  · {group.row_count} file(s)"
    )
    # If the user has already overridden this group in the current
    # session, surface that instead of the resolver's pick.
    override_anchor = state.accumulated.get(group.group_key)
    if override_anchor:
        print(f"  override (this session) → {override_anchor}")

    # Pull the top candidate + alternatives from the group's first row
    # (they're identical across rows of one group at the show level).
    if not group.rows:
        print("  (no rows in group)")
        return
    first = group.rows[0]
    top = first.top_candidate
    if top is None:
        print("  current → (unanchored)")
    else:
        print(
            f"  current → {top.title} ({top.year})  "
            f"[{top.anchor_kind}-{top.anchor_id}]  conf={top.confidence:.2f}"
        )
    if first.alternatives:
        print("  alternatives:")
        for n, alt in enumerate(first.alternatives, start=1):
            print(
                f"    [{n}] {alt.title} ({alt.year})  "
                f"[{alt.anchor_kind}-{alt.anchor_id}]  conf={alt.confidence:.2f}"
            )
    print(
        "  [a]ccept · [1-N] pick alt · [i] id/url · [s] search · "
        "[k] skip · [b] back · [q] save+quit · [?] help"
    )


def _handle_action(
    raw: str,
    *,
    group: GroupReport,
    state: _ReviewState,
    cache: TMDBCache,
    resolver: IMDbFallbackResolver,
) -> str:
    action = raw.strip().lower()
    if action in ("", "a"):
        return "next"
    if action == "?":
        _print_help()
        return "stay"
    if action == "q":
        return "quit"
    if action == "b":
        return "back"
    if action == "k":
        # Skip == clear any prior override and leave the resolver pick
        # in place. Pop from accumulated if present.
        if group.group_key in state.accumulated:
            del state.accumulated[group.group_key]
        print("  (skipped — keeping resolver's current pick)")
        return "next"
    if action.isdigit():
        n = int(action)
        first = group.rows[0] if group.rows else None
        if first is None or n < 1 or n > len(first.alternatives):
            print(f"  no alternative #{n}")
            return "stay"
        chosen = first.alternatives[n - 1]
        anchor = _candidate_to_anchor(chosen)
        state.accumulated[group.group_key] = anchor
        print(f"  ✓ anchored to {chosen.title} ({chosen.year})  [{anchor}]")
        _persist_state(state)  # incremental save in case of crash
        return "next"
    if action == "i":
        return _prompt_for_id(group, state)
    if action == "s":
        return _prompt_for_search(group, state, resolver=resolver)
    print(f"  unknown action: {raw!r}. Type ? for help.")
    return "stay"


def _prompt_for_id(group: GroupReport, state: _ReviewState) -> str:
    raw = _prompt("    TMDB id or URL: ")
    if not raw.strip():
        return "stay"
    try:
        ref = parse_anchor(raw)
    except AnchorParseError as exc:
        print(f"    invalid anchor: {exc}")
        return "stay"
    # Render to canonical short form. Keep ``tmdb-12345`` (no kind)
    # when the user gave a bare numeric id; the report's override
    # apply will infer kind from the parsed row.
    if ref.kind == "imdb":
        anchor = f"imdb-{ref.id}"
    elif ref.item_kind is None:
        anchor = f"tmdb-{ref.id}"
    else:
        anchor = f"tmdb-{ref.item_kind}-{ref.id}"
    if ref.season is not None:
        anchor += f"/season/{ref.season}"
    state.accumulated[group.group_key] = anchor
    print(f"  ✓ anchored to {anchor}")
    _persist_state(state)
    return "next"


def _prompt_for_search(
    group: GroupReport,
    state: _ReviewState,
    *,
    resolver: IMDbFallbackResolver,
) -> str:
    raw = _prompt("    new TMDB query: ")
    query = raw.strip()
    if not query:
        return "stay"
    if group.kind == "movie":
        results = resolver.search_movie_pooled(query, None)
    else:
        results = resolver.search_tv_pooled(query, None)
    if not results:
        print("    (no TMDB results)")
        return "stay"
    print(f"    {len(results)} result(s):")
    for n, c in enumerate(results[:9], start=1):
        print(
            f"      [{n}] {c.title} ({c.year})  "
            f"[{c.anchor_kind}-{c.anchor_id}]  conf={c.confidence:.2f}"
        )
    pick = _prompt("    pick number (or empty to cancel): ").strip()
    if not pick.isdigit():
        return "stay"
    n = int(pick)
    if n < 1 or n > min(9, len(results)):
        print(f"    no result #{n}")
        return "stay"
    chosen = results[n - 1]
    anchor = _candidate_to_anchor(chosen)
    state.accumulated[group.group_key] = anchor
    print(f"  ✓ anchored to {chosen.title} ({chosen.year})  [{anchor}]")
    _persist_state(state)
    return "next"


def _candidate_to_anchor(c: Candidate) -> str:
    if c.anchor_kind == "imdb":
        return f"imdb-{c.anchor_id}"
    # Include the item kind so the report's override apply doesn't
    # have to infer it from the parsed row.
    return f"tmdb-{c.kind}-{c.anchor_id}"


def _print_help() -> None:
    print(
        "\n  Actions:\n"
        "    [a]ccept / Enter — keep current match, next group\n"
        "    [1-9]            — pick the numbered alternative\n"
        "    [i]              — paste a TMDB id (tmdb-12345) or URL\n"
        "    [s]              — search TMDB with a new query\n"
        "    [k]              — skip this group (clear any prior override)\n"
        "    [b]              — go back to the previous group\n"
        "    [q]              — save accumulated overrides + quit\n"
        "    [?]              — show this help\n"
    )


def _prompt(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return "q"
    except KeyboardInterrupt:
        return "q"


def _resolve_save_path(explicit: str | None, source: Path) -> Path:
    if explicit:
        return Path(explicit).resolve()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source.name)
    return Path("/tmp") / f"plex-renamer-review-anchors-{safe or 'root'}.json"


def _preload_state(state: _ReviewState, path: Path) -> None:
    """Pre-populate accumulated overrides from a prior review's JSON.

    Accepts the same shape :mod:`plex_renamer.diagnostics.overrides`
    consumes — ``{"groups": {...}, "rows": {...}}``. Only ``groups``
    is loaded here (the review walks groups, not rows). ``rows``
    overrides remain in the file and pass through to the next
    ``--anchors`` invocation untouched.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"plex-renamer: --load skipped ({exc})", file=sys.stderr)
        return
    if not isinstance(payload, dict):
        return
    groups = payload.get("groups") or {}
    for key, value in groups.items():
        state.accumulated[str(key)] = str(value)
    if state.accumulated:
        print(
            f"plex-renamer: loaded {len(state.accumulated)} prior override(s) from {path}",
            file=sys.stderr,
        )


def _persist_state(state: _ReviewState) -> None:
    """Write the current accumulator to the save path.

    Called after every successful override so a crash / Ctrl-C
    doesn't lose progress. The file shape matches what the report's
    ``--anchors`` flag consumes, so the user can immediately replay
    the session against the same tree (or any tree with the same
    group keys).
    """
    payload = {
        "groups": dict(state.accumulated),
        "rows": {},
    }
    state.save_path.parent.mkdir(parents=True, exist_ok=True)
    state.save_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _progress_to_stderr(idx: int, total: int, source_path: Path) -> None:
    sys.stderr.write(f"\rresolving {idx + 1}/{total}: {source_path.name[:60]:<60}")
    sys.stderr.flush()
    if idx + 1 == total:
        sys.stderr.write("\n")
        sys.stderr.flush()


__all__ = ["run_review_simple"]
