"""Anchor-override application for the diagnostics report.

The user runs the report, sees three rows the resolver couldn't anchor,
looks up the correct TMDB ids by hand, then re-runs the report with
``--anchor`` flags to verify those rows now resolve correctly. This
module owns the override-parsing + override-application logic so the
report can stay focused on resolution.

Override key shapes:

* ``tv|<show_name>`` — override every row in this TV group. The
  show_name matches what the report's group key produced
  (``derive_show_name(input_root, parent_dirs)``).
* ``movie|<title>|<year>`` — override a movie group. Empty year is
  ``<title>|``.
* ``row:<absolute_path>`` — override one specific source path. Wins
  over a group override on the same row.

Values are TMDB / IMDb anchor strings parsable by
:func:`plex_renamer.tmdb.anchor_parse.parse_anchor` — accepts canonical
short forms AND TMDB / IMDb URLs.

The override applies by fetching the canonical record from TMDB
(``get_movie`` / ``get_tv``), so the report shows the real title /
year for the override, not just the bare id.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from plex_renamer.tmdb.anchor_parse import AnchorParseError, AnchorRef, parse_anchor
from plex_renamer.tmdb.models import Candidate, Episode, MovieResult, TVResult


@dataclass(frozen=True)
class OverrideSet:
    """Parsed override map. Empty ``OverrideSet()`` means no overrides."""

    groups: dict[str, AnchorRef]
    rows: dict[str, AnchorRef]

    def is_empty(self) -> bool:
        return not self.groups and not self.rows


class OverrideParseError(ValueError):
    """Raised when an override key or value can't be parsed."""


def parse_override_flags(flags: list[str]) -> OverrideSet:
    """Parse a list of ``KEY=VALUE`` strings into an :class:`OverrideSet`.

    Empty input returns an empty :class:`OverrideSet`. Whitespace
    around the ``=`` and around the key / value is ignored. Each key
    must start with ``tv|``, ``movie|``, or ``row:``.
    """
    groups: dict[str, AnchorRef] = {}
    rows: dict[str, AnchorRef] = {}
    for raw in flags:
        if "=" not in raw:
            raise OverrideParseError(
                f"invalid override {raw!r}: expected KEY=VALUE (e.g. tv|Lazarus_2=tmdb-tv-231003)"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise OverrideParseError(f"invalid override {raw!r}: empty key or value")
        try:
            ref = parse_anchor(value)
        except AnchorParseError as exc:
            raise OverrideParseError(f"override {raw!r}: {exc}") from exc
        if key.startswith("row:"):
            rows[key[len("row:") :].strip()] = ref
        elif key.startswith("tv|") or key.startswith("movie|"):
            groups[key] = ref
        else:
            raise OverrideParseError(
                f"override key {key!r} must start with 'tv|', 'movie|', or 'row:'"
            )
    return OverrideSet(groups=groups, rows=rows)


def load_override_file(path: Path) -> OverrideSet:
    """Parse a JSON override file into an :class:`OverrideSet`.

    File shape::

        {
          "groups": {
            "tv|Lazarus_2": "tmdb-tv-231003",
            "movie|Spaceballs|": "tmdb-movie-957"
          },
          "rows": {
            "/abs/path/to/file.mp4": "https://www.themoviedb.org/movie/12345"
          }
        }
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OverrideParseError(f"override file {path}: top-level must be an object")
    groups: dict[str, AnchorRef] = {}
    rows: dict[str, AnchorRef] = {}
    for key, value in (payload.get("groups") or {}).items():
        try:
            groups[key] = parse_anchor(str(value))
        except AnchorParseError as exc:
            raise OverrideParseError(
                f"override file {path}: groups[{key!r}] = {value!r}: {exc}"
            ) from exc
    for key, value in (payload.get("rows") or {}).items():
        try:
            rows[key] = parse_anchor(str(value))
        except AnchorParseError as exc:
            raise OverrideParseError(
                f"override file {path}: rows[{key!r}] = {value!r}: {exc}"
            ) from exc
    return OverrideSet(groups=groups, rows=rows)


def merge_overrides(*sets: OverrideSet) -> OverrideSet:
    """Merge multiple override sets. Later sets win on conflicting keys."""
    groups: dict[str, AnchorRef] = {}
    rows: dict[str, AnchorRef] = {}
    for s in sets:
        groups.update(s.groups)
        rows.update(s.rows)
    return OverrideSet(groups=groups, rows=rows)


# --- candidate resolution from AnchorRef ----------------------------------

GetMovieFn = Callable[[int], MovieResult]
GetTVFn = Callable[[int], TVResult]
GetSeasonFn = Callable[[int, int], list[Episode]]


def resolve_anchor_to_candidate(
    ref: AnchorRef,
    *,
    parsed_kind: str,
    get_movie: GetMovieFn,
    get_tv: GetTVFn,
    get_season: GetSeasonFn | None = None,
) -> Candidate:
    """Fetch the canonical TMDB record for ``ref`` and synthesize a Candidate.

    ``parsed_kind`` is the parser's classification of the row receiving
    the override; used as the tiebreaker when ``ref.item_kind`` is
    None (the user wrote a bare ``tmdb-12345`` instead of
    ``tmdb-movie-12345`` / ``tmdb-tv-12345``).

    ``get_season`` (optional) fills the episode list for TV overrides
    when a season was specified on the ref. Without it the candidate
    has no episode mapping; the planner can still emit folder names.

    IMDb refs are not fetched here — the report falls back to a stub
    candidate (no title / year) and the user is expected to use the
    TMDB anchor in practice. We can extend this with a TMDB
    ``find_by_imdb_id`` round-trip later if it proves common.
    """
    if ref.kind == "imdb":
        return Candidate(
            anchor_kind="imdb",
            anchor_id=ref.id,
            kind="movie" if parsed_kind == "movie" else "tv",
            title=f"(imdb anchor {ref.id})",
            year=None,
            confidence=1.0,
        )

    item_kind = ref.item_kind or parsed_kind
    if item_kind == "movie":
        record = get_movie(int(ref.id))
        return Candidate(
            anchor_kind="tmdb",
            anchor_id=str(record.tmdb_id),
            kind="movie",
            title=record.title,
            year=record.year,
            confidence=1.0,
        )
    # tv
    record_tv = get_tv(int(ref.id))
    episode_list: tuple[Episode, ...] | None = None
    if get_season is not None and ref.season is not None:
        try:
            episode_list = tuple(get_season(int(ref.id), ref.season))
        except Exception:
            episode_list = None
    return Candidate(
        anchor_kind="tmdb",
        anchor_id=str(record_tv.tmdb_id),
        kind="tv",
        title=record_tv.title,
        year=record_tv.year,
        confidence=1.0,
        episode_list=episode_list,
    )


__all__ = [
    "GetMovieFn",
    "GetSeasonFn",
    "GetTVFn",
    "OverrideParseError",
    "OverrideSet",
    "load_override_file",
    "merge_overrides",
    "parse_override_flags",
    "resolve_anchor_to_candidate",
]
