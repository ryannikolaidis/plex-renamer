"""Multi-part movie detection.

A multi-part movie is two or more files that share a (title, year) and
differ only in a trailing part marker — ``cd1``/``cd2``, ``pt1``/``pt2``,
``part1``/``part2``, ``disc1``/``disc2``, ``disk1``/``disk2``.

The parser's tokenizer already extracts ``ParseResult.part_marker``; we
just need to group sibling ParseResults that share an identity key and
have distinct part markers.

The planner uses this groupwise: for each (title, year) key, if any
ParseResult has a part marker, the whole group is treated as multi-part
and individual files get rendered as ``- pt1``/``- pt2``/... siblings in
one per-movie folder.
"""

from __future__ import annotations

import re
from collections import defaultdict

from plex_renamer.parser.models import ParseResult

_PART_RE = re.compile(r"^(?:cd|pt|part|disc|disk)\s*(\d+)$", re.IGNORECASE)


def _identity_key(p: ParseResult) -> tuple[str, int | None]:
    return ((p.title_candidate or "").lower().strip(), p.year)


def group_multi_part(results: list[ParseResult]) -> dict[tuple[str, int | None], list[ParseResult]]:
    """Group ParseResults that share (title, year). Only returns groups that have
    at least one item with a non-empty part_marker AND at least two members.

    Single-file movies don't appear in the output. Callers handle those
    via the normal one-op-per-parse path.
    """
    by_key: dict[tuple[str, int | None], list[ParseResult]] = defaultdict(list)
    for r in results:
        if r.kind != "movie":
            continue
        if not r.title_candidate:
            continue
        by_key[_identity_key(r)].append(r)

    groups: dict[tuple[str, int | None], list[ParseResult]] = {}
    for key, items in by_key.items():
        marked = [i for i in items if i.part_marker]
        if len(marked) >= 2:
            # Keep stable order by part-marker number when available.
            items_sorted = sorted(items, key=_sort_key)
            groups[key] = items_sorted
    return groups


def _sort_key(p: ParseResult) -> tuple[int, str]:
    if p.part_marker:
        m = _PART_RE.match(p.part_marker.strip())
        if m:
            return (int(m.group(1)), p.raw_filename)
    return (9999, p.raw_filename)


__all__ = ["group_multi_part"]
