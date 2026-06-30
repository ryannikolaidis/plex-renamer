"""``plex-renamer review`` (default mode) — paginated TUI.

A textual-based interactive surface that lists every file under
``--source`` with its proposed Plex target path. The user pages
through with arrow keys (or ``j``/``k``), drills into a row to pick a
different anchor, filters by status, and optionally applies the plan
in the same session.

The line-based REPL implementation lives at
:func:`plex_renamer.cli.review_cmd.run_review_simple` and is reached
via ``--simple``; this module is the default.

Apply requires both ``--movies`` and ``--tv`` (or settings carrying
them). Without those roots, the TUI runs in review-only mode — every
override still persists to the JSON anchors file at quit, but ``[A]``
is disabled with a tooltip explaining why.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from plex_renamer.config.settings import Settings
from plex_renamer.diagnostics.report import (
    RowReport,
)
from plex_renamer.executor.copy import apply_plan_iter
from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult
from plex_renamer.planner.build import build_plan_from_pairs
from plex_renamer.planner.movie_path import movie_target_path
from plex_renamer.planner.show_anchor import match_episode
from plex_renamer.planner.tv_path import tv_target_path
from plex_renamer.tmdb.anchor_parse import AnchorParseError, parse_anchor
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.errors import TMDBAuthError
from plex_renamer.tmdb.fallback import IMDbFallbackResolver
from plex_renamer.tmdb.models import Candidate, Episode
from plex_renamer.tvdb import TVDBClient, TVDBSeasonType
from plex_renamer.tvdb.errors import TVDBError

LOW_CONF_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PlanRow:
    """One displayable row: report state + parsed + computed target path."""

    row: RowReport
    parsed: ParseResult
    target: Path | None  # None when unresolved or missing TV episode mapping

    @property
    def confidence(self) -> float | None:
        c = self.row.top_candidate
        return c.confidence if c is not None else None

    @property
    def is_low_conf(self) -> bool:
        c = self.confidence
        if c is None:
            return True
        return c < LOW_CONF_THRESHOLD

    @property
    def is_unanchored(self) -> bool:
        return self.row.top_candidate is None


@dataclass
class TUIState:
    source: Path
    movies_root: Path | None
    tv_root: Path | None
    save_path: Path
    plan_rows: list[PlanRow] = field(default_factory=list)
    overrides_groups: dict[str, str] = field(default_factory=dict)
    overrides_rows: dict[str, str] = field(default_factory=dict)
    # Episode-source overrides. ``"tmdb"`` (default) uses TMDB's per-season
    # episode list; ``"tvdb:<season-type>"`` (e.g. ``"tvdb:official"``)
    # fetches the corresponding ordering from TheTVDB and rematches the
    # affected rows against it. Group-scope is keyed by group_key (one
    # entry per show); row-scope is keyed by the file's source path.
    group_episode_source: dict[str, str] = field(default_factory=dict)
    row_episode_source: dict[Path, str] = field(default_factory=dict)
    filter_mode: str = "all"  # all | low-conf | unanchored
    search: str = ""

    def visible_indices(self) -> list[int]:
        out: list[int] = []
        for i, pr in enumerate(self.plan_rows):
            if self.filter_mode == "low-conf" and not pr.is_low_conf:
                continue
            if self.filter_mode == "unanchored" and not pr.is_unanchored:
                continue
            if self.search:
                hay = (pr.parsed.raw_filename or "").lower()
                if self.search.lower() not in hay:
                    continue
            out.append(i)
        return out

    def can_apply(self) -> bool:
        if self.movies_root is None or self.tv_root is None:
            return False
        return any(pr.target is not None for pr in self.plan_rows)


# ---------------------------------------------------------------------------
# Path/display helpers
# ---------------------------------------------------------------------------


def _relative_display(path: Path, root: Path) -> str:
    """Display ``path`` relative to ``root`` when reachable, else basename."""
    try:
        rel = path.relative_to(root)
        return str(rel)
    except ValueError:
        # Source root is the file itself, or path is outside.
        return path.name


def _compute_target(
    parsed: ParseResult,
    candidate: Candidate | None,
    matched_episode: Episode | None,
    movies_root: Path | None,
    tv_root: Path | None,
) -> Path | None:
    """Compute the Plex target path for this row, or None if unresolvable."""
    if candidate is None:
        return None
    if parsed.kind == "movie":
        if movies_root is None:
            return None
        ext = parsed.source_path.suffix
        return movie_target_path(candidate, movies_root, edition=None, part_marker=None, ext=ext)
    if parsed.kind == "tv":
        if tv_root is None or matched_episode is None:
            return None
        ext = parsed.source_path.suffix
        return tv_target_path(
            candidate,
            tv_root,
            matched_episode.season,
            matched_episode.episode,
            matched_episode.title,
            ext,
            episode_end=parsed.episode_end,
        )
    return None


def _shorten(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return "…" + s[-(n - 1) :]


def _candidate_browser_url(c: Candidate) -> str | None:
    """Build a public web URL for a Candidate's anchor.

    TMDB uses ``/movie/<id>`` or ``/tv/<id>``; IMDb uses
    ``/title/tt<id>``. Returns ``None`` for unknown anchor kinds.
    """
    if c.anchor_kind == "tmdb":
        kind_path = "movie" if c.kind == "movie" else "tv"
        return f"https://www.themoviedb.org/{kind_path}/{c.anchor_id}"
    if c.anchor_kind == "imdb":
        return f"https://www.imdb.com/title/{c.anchor_id}/"
    return None


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------


class RowDetailScreen(ModalScreen[dict | None]):
    """Drill-in modal for one row.

    Result returned via ``dismiss``: ``None`` (no change) or a dict with::

        {"action": "set_candidate", "candidate": Candidate, "scope": "row" | "group"}
        {"action": "clear", "scope": "row" | "group"}
        {"action": "remove"}         # drop the row from the in-memory list
        {"action": "delete_source"}  # unlink the source file on disk (confirmed)
        {"action": "switch_episode_source", "source": "tmdb" | "tvdb:<type>",
         "scope": "row" | "group"}  # re-resolve episode mapping from another source
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
    ]

    DEFAULT_CSS = """
    RowDetailScreen {
        align: center middle;
    }
    #row_detail_box {
        width: 90%;
        height: 80%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    #row_actions Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        plan_row: PlanRow,
        resolver: IMDbFallbackResolver,
        cache: TMDBCache,
        group_size: int,
    ) -> None:
        super().__init__()
        self._plan_row = plan_row
        self._resolver = resolver
        self._cache = cache
        self._group_size = group_size

    def compose(self) -> ComposeResult:
        pr = self._plan_row
        row = pr.row
        with Vertical(id="row_detail_box"):
            yield Label(f"[b]{pr.parsed.raw_filename}[/b]")
            yield Static(f"source: {pr.parsed.source_path}")
            yield Static(f"kind: {row.kind}    flags: {', '.join(row.flags) or '—'}")
            if row.top_candidate is not None:
                c = row.top_candidate
                yield Static(
                    f"current → {c.title} ({c.year}) "
                    f"[{c.anchor_kind}-{c.anchor_id}]  conf={c.confidence:.2f}"
                )
            else:
                yield Static("current → (unanchored)")
            if row.matched_episode is not None:
                ep = row.matched_episode
                yield Static(f"episode → s{ep.season:02d}e{ep.episode:02d}  {ep.title or '—'}")
            if pr.target is not None:
                yield Static(f"[b]destination[/b] → {pr.target}")
            else:
                yield Static(
                    "[b]destination[/b] → (unresolved — no movies/tv roots or no episode match)"
                )
            yield Static("")
            yield Static("[b]Alternatives[/b]  (Enter row index to select)")
            alt_table: DataTable = DataTable(id="alt_table", cursor_type="row")
            alt_table.add_columns("#", "Title", "Year", "Anchor", "Conf")
            for n, alt in enumerate(row.alternatives, start=1):
                alt_table.add_row(
                    str(n),
                    alt.title,
                    str(alt.year) if alt.year else "",
                    f"{alt.anchor_kind}-{alt.anchor_id}",
                    f"{alt.confidence:.2f}",
                )
            yield alt_table
            yield Static("")
            yield Input(
                placeholder="paste TMDB id or themoviedb.org URL (e.g. tmdb-231003) then Enter",
                id="id_input",
            )
            yield Input(
                placeholder="search TMDB by query then Enter (re-ranks)",
                id="search_input",
            )
            yield Static("")
            scope_hint = (
                f"Scope: group ({self._group_size} files)"
                if pr.parsed.kind == "tv"
                else "Scope: this row"
            )
            yield Static(scope_hint)
            with Horizontal(id="row_actions"):
                yield Button("Accept current", id="accept_current", variant="default")
                yield Button("Pick alt #", id="pick_alt", variant="primary")
                yield Button("Clear anchor", id="clear_anchor", variant="warning")
                yield Button("Remove from list", id="remove_row", variant="warning")
                yield Button("Delete source file", id="delete_source", variant="error")
                yield Button("Switch to TVDB…", id="switch_tvdb", variant="default")
                yield Button("Cancel", id="cancel_btn", variant="default")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on an alternative row commits it.
        alts = self._plan_row.row.alternatives
        idx = event.cursor_row
        if 0 <= idx < len(alts):
            self._dismiss_with_candidate(alts[idx])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "id_input":
            self._handle_id_input(event.value)
        elif event.input.id == "search_input":
            self._handle_search_input(event.value)

    def _handle_id_input(self, value: str) -> None:
        v = value.strip()
        if not v:
            return
        try:
            ref = parse_anchor(v)
        except AnchorParseError as exc:
            self.notify(f"invalid anchor: {exc}", severity="error")
            return
        cand = self._resolve_anchor_to_candidate(ref)
        if cand is None:
            self.notify("could not fetch TMDB record for that id", severity="error")
            return
        self._dismiss_with_candidate(cand)

    def _handle_search_input(self, value: str) -> None:
        q = value.strip()
        if not q:
            return
        kind = self._plan_row.parsed.kind
        if kind == "movie":
            results = self._resolver.search_movie_pooled(q, None)
        else:
            results = self._resolver.search_tv_pooled(q, None)
        if not results:
            self.notify("no TMDB results for that query", severity="warning")
            return
        # Rewrite the alt_table with these new results.
        table = self.query_one("#alt_table", DataTable)
        table.clear()
        for n, alt in enumerate(results[:9], start=1):
            table.add_row(
                str(n),
                alt.title,
                str(alt.year) if alt.year else "",
                f"{alt.anchor_kind}-{alt.anchor_id}",
                f"{alt.confidence:.2f}",
            )
        # Stash the new alternatives so on_data_table_row_selected can
        # commit them. We mutate the underlying RowReport's alternatives
        # in-place — fine for this transient UI state since the row will
        # be rebuilt on dismiss anyway.
        object.__setattr__(self._plan_row.row, "alternatives", list(results[:9]))

    def _resolve_anchor_to_candidate(self, ref) -> Candidate | None:
        from plex_renamer.diagnostics.overrides import resolve_anchor_to_candidate

        try:
            return resolve_anchor_to_candidate(
                ref,
                parsed_kind=self._plan_row.parsed.kind,
                get_movie=self._cache.get_movie,
                get_tv=self._cache.get_tv,
                get_season=self._cache.get_season,
            )
        except Exception as exc:
            self.notify(f"TMDB lookup failed: {exc}", severity="error")
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid in ("cancel_btn", "accept_current"):
            self.dismiss(None)
        elif bid == "pick_alt":
            table = self.query_one("#alt_table", DataTable)
            idx = table.cursor_row
            alts = self._plan_row.row.alternatives
            if 0 <= idx < len(alts):
                self._dismiss_with_candidate(alts[idx])
        elif bid == "clear_anchor":
            scope = "group" if self._plan_row.parsed.kind == "tv" else "row"
            self.dismiss({"action": "clear", "scope": scope})
        elif bid == "remove_row":
            self.dismiss({"action": "remove"})
        elif bid == "delete_source":
            self.dismiss({"action": "delete_source"})
        elif bid == "switch_tvdb":
            self.dismiss({"action": "open_tvdb_search"})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _dismiss_with_candidate(self, candidate: Candidate) -> None:
        scope = "group" if self._plan_row.parsed.kind == "tv" else "row"
        self.dismiss({"action": "set_candidate", "candidate": candidate, "scope": scope})


class TVDBSearchScreen(ModalScreen[dict | None]):
    """Search TVDB for a show + pick an episode ordering.

    Result returned via ``dismiss``: ``None`` on cancel, else a dict::

        {"tvdb_id": int, "tvdb_title": str, "tvdb_year": int | None,
         "season_type": "default" | "official" | "dvd" | "absolute"
                        | "alternate" | "regional",
         "scope": "row" | "group"}

    The caller (the App) then fetches that TVDB id's episode list under
    the chosen ordering and re-matches the affected row(s) against it.
    The folder anchor becomes ``{tvdb-<id>}``.
    """

    DEFAULT_CSS = """
    TVDBSearchScreen {
        align: center middle;
    }
    #tvdb_search_box {
        width: 90%;
        height: 90%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    #tvdb_actions Button {
        margin: 0 1;
    }
    """

    _SEASON_TYPES: list[TVDBSeasonType] = [
        "default",
        "official",
        "dvd",
        "absolute",
        "alternate",
        "regional",
    ]

    def __init__(
        self,
        initial_query: str,
        is_group_capable: bool,
        group_size: int,
        tvdb_client: TVDBClient,
    ) -> None:
        super().__init__()
        self._initial_query = initial_query
        self._is_group_capable = is_group_capable
        self._group_size = group_size
        self._tvdb_client = tvdb_client
        self._results: list = []  # list[TVDBSeriesResult]

    def compose(self) -> ComposeResult:
        with Vertical(id="tvdb_search_box"):
            yield Label("[b]Search TVDB for show[/b]")
            yield Static("")
            yield Input(
                value=self._initial_query,
                placeholder="show title (Enter to search)",
                id="tvdb_query",
            )
            yield Static("")
            results_table: DataTable = DataTable(
                id="tvdb_results_table", cursor_type="row", zebra_stripes=True
            )
            results_table.add_columns("#", "Title", "Year", "TVDB id", "Overview")
            yield results_table
            yield Static("")
            yield Static(
                "[b]Episode ordering[/b]  (TVDB's per-show alternate orderings; "
                "'official' usually matches aired order)"
            )
            with Horizontal():
                for st in self._SEASON_TYPES:
                    yield Button(st, id=f"season_type_{st}", variant="default")
            yield Static("")
            if self._is_group_capable:
                yield Static(
                    f"[b]Scope[/b]  apply to: 'group' = all {self._group_size} "
                    f"file(s) in this show; 'row' = only the highlighted row"
                )
                with Horizontal():
                    yield Button("Apply to group", id="scope_group", variant="primary")
                    yield Button("Apply to row", id="scope_row", variant="default")
            else:
                # Movies (or single-file groups) — group/row distinction is moot.
                with Horizontal():
                    yield Button("Apply", id="scope_row", variant="primary")
            yield Static("")
            with Horizontal(id="tvdb_actions"):
                yield Button("Cancel", id="tvdb_cancel", variant="default")

    def on_mount(self) -> None:
        # Auto-run the search with the initial query (the show name).
        self.query_one("#tvdb_query", Input).focus()
        if self._initial_query.strip():
            self._run_search(self._initial_query)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "tvdb_query":
            self._run_search(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "tvdb_cancel":
            self.dismiss(None)
            return
        if bid.startswith("season_type_"):
            # Track selected season type by re-styling: just stash it.
            self._selected_season_type = bid.removeprefix("season_type_")
            # Visually mark the chosen one. We toggle variant.
            for st in self._SEASON_TYPES:
                btn = self.query_one(f"#season_type_{st}", Button)
                btn.variant = "primary" if st == self._selected_season_type else "default"
            return
        if bid in ("scope_group", "scope_row"):
            self._commit(scope="group" if bid == "scope_group" else "row")

    def _run_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        try:
            self._results = self._tvdb_client.search_series(query, limit=12)
        except Exception as exc:
            self.notify(f"TVDB search failed: {exc}", severity="error")
            return
        table = self.query_one("#tvdb_results_table", DataTable)
        table.clear()
        for i, r in enumerate(self._results, start=1):
            overview = (r.overview or "").replace("\n", " ")
            table.add_row(
                str(i),
                r.title,
                str(r.year) if r.year else "",
                str(r.tvdb_id),
                overview[:80],
                key=str(i - 1),
            )
        if not self._results:
            self.notify("no TVDB results", severity="warning")

    def _commit(self, *, scope: str) -> None:
        if not self._results:
            self.notify("pick a show first (run a search)", severity="warning")
            return
        table = self.query_one("#tvdb_results_table", DataTable)
        row_idx = table.cursor_row
        if not (0 <= row_idx < len(self._results)):
            self.notify("highlight a search result first", severity="warning")
            return
        season_type = getattr(self, "_selected_season_type", "official")
        chosen = self._results[row_idx]
        self.dismiss(
            {
                "tvdb_id": chosen.tvdb_id,
                "tvdb_title": chosen.title,
                "tvdb_year": chosen.year,
                "season_type": season_type,
                "scope": scope,
            }
        )


class EditTargetsScreen(ModalScreen[dict | None]):
    """Edit movies/tv library roots. Result is a dict or None on cancel."""

    DEFAULT_CSS = """
    EditTargetsScreen {
        align: center middle;
    }
    #targets_box {
        width: 80%;
        height: auto;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    """

    def __init__(self, movies: Path | None, tv: Path | None) -> None:
        super().__init__()
        self._movies = movies
        self._tv = tv

    def compose(self) -> ComposeResult:
        with Vertical(id="targets_box"):
            yield Label("[b]Library roots[/b]")
            yield Static("")
            yield Label("Movies root:")
            yield Input(
                value=str(self._movies) if self._movies else "",
                placeholder="/path/to/Movies",
                id="movies_input",
            )
            yield Static("")
            yield Label("TV root:")
            yield Input(
                value=str(self._tv) if self._tv else "",
                placeholder="/path/to/TV",
                id="tv_input",
            )
            yield Static("")
            yield Static("Saved to the app's settings file; persists across runs.")
            yield Static("")
            with Horizontal():
                yield Button("Save", id="targets_save", variant="primary")
                yield Button("Cancel", id="targets_cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "targets_cancel":
            self.dismiss(None)
            return
        if event.button.id == "targets_save":
            movies = self.query_one("#movies_input", Input).value.strip()
            tv = self.query_one("#tv_input", Input).value.strip()
            self.dismiss(
                {
                    "movies": Path(movies) if movies else None,
                    "tv": Path(tv) if tv else None,
                }
            )


class DeleteConfirmScreen(ModalScreen[bool]):
    """Confirmation modal before unlinking a source file from disk.

    This is destructive. The dialog shows the absolute path + size so
    the user can sanity-check before committing.
    """

    DEFAULT_CSS = """
    DeleteConfirmScreen {
        align: center middle;
    }
    #delete_box {
        width: 80;
        height: auto;
        background: $panel;
        border: solid $error;
        padding: 1 2;
    }
    """

    def __init__(self, source_path: Path, size_bytes: int | None) -> None:
        super().__init__()
        self._source_path = source_path
        self._size_bytes = size_bytes

    def compose(self) -> ComposeResult:
        with Vertical(id="delete_box"):
            yield Label("[b red]Delete source file?[/b red]")
            yield Static("")
            yield Static(f"  path: {self._source_path}")
            if self._size_bytes is not None:
                yield Static(f"  size: {self._size_bytes:,} bytes")
            yield Static("")
            yield Static("[b]This permanently removes the file from disk.[/b]")
            yield Static("There is no undo here.")
            yield Static("")
            with Horizontal():
                yield Button("Delete", id="delete_yes", variant="error")
                yield Button("Cancel", id="delete_no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete_yes")


class ApplyConfirmScreen(ModalScreen[bool]):
    """Confirmation modal before applying. Shows op counts + collision count."""

    DEFAULT_CSS = """
    ApplyConfirmScreen {
        align: center middle;
    }
    #apply_box {
        width: 60;
        height: auto;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    """

    def __init__(self, op_count: int, collision_count: int, skipped_count: int) -> None:
        super().__init__()
        self._op_count = op_count
        self._collision_count = collision_count
        self._skipped_count = skipped_count

    def compose(self) -> ComposeResult:
        with Vertical(id="apply_box"):
            yield Label("[b]Apply plan?[/b]")
            yield Static("")
            yield Static(f"  files to move:      {self._op_count}")
            yield Static(f"  skipped/unresolved: {self._skipped_count}")
            yield Static(f"  collisions:         {self._collision_count}")
            yield Static("")
            yield Static(
                "Sources are MOVED to library roots (copy + verify, then "
                "the original is deleted). Use `undo` against the journal "
                "if anything goes wrong."
            )
            yield Static("")
            with Horizontal():
                yield Button("Apply", id="apply_yes", variant="primary")
                yield Button("Cancel", id="apply_no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "apply_yes")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


class ReviewApp(App):
    """Top-level textual app for `plex-renamer review`."""

    BINDINGS = [
        # Enter is handled via DataTable.RowSelected (the table consumes
        # the key for its own selection event). The binding here is kept
        # for the footer hint only.
        Binding("enter", "open_detail", "details", show=True),
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("slash", "focus_search", "search"),
        Binding("f", "cycle_filter", "filter"),
        Binding("a", "apply", "apply"),
        Binding("c", "convert_row", "convert row"),
        Binding("w", "open_in_browser", "open match"),
        Binding("x", "remove_row", "exclude row"),
        # Capital D so the user can't trigger a destructive delete with
        # a single stray keystroke. Lowercase d is intentionally not a
        # binding.
        Binding("D", "delete_source", "delete source file"),
        Binding("t", "edit_targets", "set library roots"),
        Binding("q", "quit", "quit"),
        Binding("?", "show_help", "help"),
    ]

    DEFAULT_CSS = """
    #status_bar {
        height: 2;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #search_input {
        height: 3;
        margin: 0;
    }
    #file_table {
        height: 1fr;
    }
    """

    def __init__(
        self,
        state: TUIState,
        cache: TMDBCache,
        resolver: IMDbFallbackResolver,
        defer_resolve: bool = False,
        tvdb_client: TVDBClient | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._cache = cache
        self._resolver = resolver
        # When True, plan_rows are loaded lazily inside on_mount via a
        # worker thread so the App's main loop starts up immediately
        # (the alternative is paying the full parse + TMDB-resolve cost
        # before the App even appears, which feels like a hang).
        self._defer_resolve = defer_resolve
        self._loading_status = "resolving…"
        # Timestamps of recent toasts. Used to cap visible notifications
        # (see ``_capped_notify``) so rapid converts don't pile up an
        # unbounded stack of "applied 1 file(s)" toasts.
        self._recent_toasts: deque[float] = deque()
        self._tvdb_client = tvdb_client
        # Per-process episode-list cache so re-applying TVDB to multiple
        # rows of one show doesn't re-fetch on every row.
        self._tvdb_episode_cache: dict[tuple[int, str], tuple[Episode, ...]] = {}

    _TOAST_CAP = 3

    def _capped_notify(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 4.0,
    ) -> None:
        """Like ``self.notify`` but never lets more than ``_TOAST_CAP``
        toasts stack on screen at once. Newest fires, oldest get cleared
        when we'd overflow."""
        now = time.monotonic()
        while self._recent_toasts and now - self._recent_toasts[0] > timeout:
            self._recent_toasts.popleft()
        if len(self._recent_toasts) >= self._TOAST_CAP:
            # Textual can't dismiss a single notification by id; clear
            # all and start a fresh batch with this one as the first.
            self.clear_notifications()
            self._recent_toasts.clear()
        self.notify(message, severity=severity, timeout=timeout)
        self._recent_toasts.append(now)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(self._status_text(), id="status_bar")
        yield Input(placeholder="filter by filename (Enter applies, ESC clears)", id="search_input")
        table: DataTable = DataTable(id="file_table", cursor_type="row", zebra_stripes=True)
        table.add_columns("#", "Before", "After", "Conf", "Flags")
        yield table
        yield Footer()

    def on_mount(self) -> None:
        # Move focus off the search Input. Without this textual focuses
        # the first focusable widget (the Input), which made typing land
        # in the filter box on startup instead of triggering table keys.
        self.query_one("#file_table", DataTable).focus()
        self._refresh_table()
        if self._defer_resolve:
            self._loader()

    @work(thread=True)
    def _loader(self) -> None:
        """Resolve rows one at a time and stream them into the table.

        Each completed row is appended to ``plan_rows`` and the table
        re-renders, so the user can navigate / convert / exclude rows
        that have landed while the rest of the tree is still resolving.
        """
        # Inlined replica of ``build_report``'s loop so we can yield
        # per-row results to the UI instead of returning a finished
        # artifact at the end.
        from plex_renamer.diagnostics.report import (
            _attach_episode_matches,
            _report_one_row,
        )

        try:
            # Iterate parse_tree lazily so the first row appears as soon
            # as the parser yields it — on a slow network share the walk
            # itself is dozens of round-trips, and consuming it eagerly
            # left the user staring at "resolving…" with no rows.
            idx = 0
            for parsed in parse_tree(self._state.source):
                if parsed.skip_reason is not None:
                    continue
                idx += 1
                self._loading_status = f"resolving {idx}  {parsed.source_path.name[:48]}"
                self.app.call_from_thread(self._update_status)

                row = _report_one_row(
                    parsed,
                    self._state.source,
                    self._resolver.search_movie_pooled,
                    self._resolver.search_tv_pooled,
                    5,
                )
                # Attach the TMDB episode match in the same pass so the
                # row arrives with its destination path computable.
                row = _attach_episode_matches(
                    [row],
                    parsed_lookup={parsed.source_path: parsed},
                    get_season=self._cache.get_season,
                )[0]
                target = _compute_target(
                    parsed,
                    row.top_candidate,
                    row.matched_episode,
                    self._state.movies_root,
                    self._state.tv_root,
                )
                plan_row = PlanRow(row=row, parsed=parsed, target=target)
                self.app.call_from_thread(self._append_plan_row, plan_row)

            self._loading_status = ""
            self.app.call_from_thread(self._update_status)
        except Exception as exc:
            self._loading_status = f"resolve failed: {exc}"
            self.app.call_from_thread(self._update_status)

    def _append_plan_row(self, plan_row: PlanRow) -> None:
        """Append a single resolved row to the in-memory state + table.

        Called from ``_loader`` via ``call_from_thread`` so this runs
        only on the UI thread.
        """
        self._state.plan_rows.append(plan_row)
        self._refresh_table()

    # --- table helpers --------------------------------------------------

    def _refresh_table(self) -> None:
        table = self.query_one("#file_table", DataTable)
        # Preserve cursor position across the rebuild. ``clear()`` resets
        # cursor_row to 0; without this an action that removes a row
        # (exclude / convert / delete) would yank focus back to the top.
        try:
            prev_cursor_row = table.cursor_row
        except Exception:
            prev_cursor_row = 0
        table.clear()
        visible = self._state.visible_indices()
        for i in visible:
            pr = self._state.plan_rows[i]
            before = _relative_display(pr.parsed.source_path, self._state.source)
            if pr.target is not None and pr.parsed.kind == "movie" and self._state.movies_root:
                after = _relative_display(pr.target, self._state.movies_root.parent)
            elif pr.target is not None and pr.parsed.kind == "tv" and self._state.tv_root:
                after = _relative_display(pr.target, self._state.tv_root.parent)
            elif pr.target is not None:
                after = str(pr.target)
            else:
                after = "<unresolved>"
            conf = f"{pr.confidence:.2f}" if pr.confidence is not None else "—"
            flags = ", ".join(pr.row.flags) or "—"
            table.add_row(
                str(i + 1),
                _shorten(before, 60),
                _shorten(after, 80),
                conf,
                _shorten(flags, 28),
                key=str(i),
            )
        # Restore cursor — clamp into the new row range so removing the
        # last row still leaves the cursor on a valid spot.
        row_count = len(visible)
        if row_count:
            clamped = max(0, min(prev_cursor_row, row_count - 1))
            with contextlib.suppress(Exception):
                table.move_cursor(row=clamped)
        self._update_status()

    def _status_text(self) -> str:
        s = self._state
        total = len(s.plan_rows)
        visible = len(s.visible_indices())
        overrides = len(s.overrides_groups) + len(s.overrides_rows)
        movies = str(s.movies_root) if s.movies_root else "—"
        tv = str(s.tv_root) if s.tv_root else "—"
        suffix = f"    [{self._loading_status}]" if self._loading_status else ""
        return (
            f"source: {s.source}    filter: {s.filter_mode}    "
            f"visible: {visible}/{total}    overrides: {overrides}{suffix}\n"
            f"movies: {movies}    tv: {tv}    [t]=edit"
        )

    def _update_status(self) -> None:
        bar = self.query_one("#status_bar", Static)
        bar.update(self._status_text())

    # --- actions --------------------------------------------------------

    def action_cursor_down(self) -> None:
        table = self.query_one("#file_table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#file_table", DataTable)
        table.action_cursor_up()

    def action_focus_search(self) -> None:
        self.query_one("#search_input", Input).focus()

    def action_cycle_filter(self) -> None:
        order = ["all", "low-conf", "unanchored"]
        idx = order.index(self._state.filter_mode)
        self._state.filter_mode = order[(idx + 1) % len(order)]
        self._refresh_table()

    def action_show_help(self) -> None:
        self.notify(
            "Enter=details · /=search · F=filter · A=apply all · "
            "c=convert · w=open in browser · x=exclude · D=delete file · Q=quit",
            timeout=6,
        )

    def action_edit_targets(self) -> None:
        def _after_edit(result: dict | None) -> None:
            if result is None:
                return
            movies = result.get("movies")
            tv = result.get("tv")
            self._state.movies_root = movies
            self._state.tv_root = tv
            # Persist to settings so future runs pick this up.
            try:
                settings = Settings.load()
                settings.movies_root = str(movies) if movies else None
                settings.tv_root = str(tv) if tv else None
                settings.save()
            except Exception as exc:
                self.notify(f"settings save failed: {exc}", severity="error")
            # Recompute every row's target with the new roots.
            for pr in self._state.plan_rows:
                pr.target = _compute_target(
                    pr.parsed,
                    pr.row.top_candidate,
                    pr.row.matched_episode,
                    movies,
                    tv,
                )
            self._refresh_table()
            self.notify("library roots updated", timeout=3)

        self.push_screen(
            EditTargetsScreen(self._state.movies_root, self._state.tv_root),
            _after_edit,
        )

    def action_open_in_browser(self) -> None:
        idx = self._cursor_row_index()
        if idx is None or not (0 <= idx < len(self._state.plan_rows)):
            return
        pr = self._state.plan_rows[idx]
        cand = pr.row.top_candidate
        if cand is None:
            self.notify("no match to open — anchor the row first", severity="warning")
            return
        url = _candidate_browser_url(cand)
        if url is None:
            self.notify(f"don't know how to open {cand.anchor_kind} anchors", severity="warning")
            return
        webbrowser.open(url)
        self.notify(f"opened {url}", timeout=3)

    def action_remove_row(self) -> None:
        idx = self._cursor_row_index()
        if idx is None:
            return
        self._remove_row(idx, notify=True)

    def action_delete_source(self) -> None:
        idx = self._cursor_row_index()
        if idx is None:
            return
        self._prompt_delete_source(idx)

    def _cursor_row_index(self) -> int | None:
        table = self.query_one("#file_table", DataTable)
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None
        if row_key is None:
            return None
        try:
            return int(row_key)
        except (TypeError, ValueError):
            return None

    def _remove_row(self, row_idx: int, *, notify: bool) -> None:
        if not (0 <= row_idx < len(self._state.plan_rows)):
            return
        pr = self._state.plan_rows[row_idx]
        # Drop any saved override for this row so the JSON stays in sync.
        self._state.overrides_rows.pop(str(pr.row.source_path), None)
        self._state.plan_rows.pop(row_idx)
        self._persist_overrides()
        self._refresh_table()
        if notify:
            self._capped_notify(f"excluded {pr.parsed.raw_filename} from apply")

    def _prompt_delete_source(self, row_idx: int) -> None:
        if not (0 <= row_idx < len(self._state.plan_rows)):
            return
        pr = self._state.plan_rows[row_idx]
        path = pr.parsed.source_path
        try:
            size = path.stat().st_size
        except OSError:
            size = None

        def _after_confirm(yes: bool | None) -> None:
            if not yes:
                return
            try:
                path.unlink()
            except OSError as exc:
                self.notify(f"delete failed: {exc}", severity="error", timeout=8)
                return
            # Re-resolve the row's current index — _remove_row would
            # mis-target if other rows were removed in the meantime.
            try:
                fresh_idx = self._state.plan_rows.index(pr)
            except ValueError:
                # Already gone from the list somehow; just refresh.
                self._refresh_table()
                return
            self._remove_row(fresh_idx, notify=False)
            self._capped_notify(f"deleted {path.name}", severity="warning")

        self.push_screen(DeleteConfirmScreen(path, size), _after_confirm)

    def action_open_detail(self) -> None:
        # Triggered by the global Enter binding when focus is NOT on the
        # DataTable. When the table has focus, on_data_table_row_selected
        # below handles it instead (the table consumes Enter for its own
        # RowSelected event).
        table = self.query_one("#file_table", DataTable)
        self._open_detail_for_cursor(table)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Fires when the user presses Enter (or clicks) on a row in the
        # main file table. The same handler ignores events from other
        # DataTables (e.g. the alternatives table inside the modal — but
        # that table only exists while the modal is mounted, and modals
        # have their own message scope).
        if event.data_table.id != "file_table":
            return
        row_key = event.row_key.value
        if row_key is None:
            return
        self._open_detail_for_row_index(int(row_key))

    def _open_detail_for_cursor(self, table: DataTable) -> None:
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return
        if row_key is None:
            return
        self._open_detail_for_row_index(int(row_key))

    def _open_detail_for_row_index(self, row_idx: int) -> None:
        if not (0 <= row_idx < len(self._state.plan_rows)):
            return
        plan_row = self._state.plan_rows[row_idx]
        group_size = sum(
            1 for pr in self._state.plan_rows if pr.row.group_key == plan_row.row.group_key
        )

        def _after_modal(result: dict | None) -> None:
            if result is None:
                return
            self._apply_override_decision(plan_row, result)
            self._refresh_table()

        self.push_screen(
            RowDetailScreen(plan_row, self._resolver, self._cache, group_size),
            _after_modal,
        )

    def _apply_override_decision(self, plan_row: PlanRow, decision: dict) -> None:
        action = decision.get("action")
        scope = decision.get("scope", "row")
        if action == "clear":
            self._mutate_anchor(plan_row, scope, None)
        elif action == "set_candidate":
            cand: Candidate = decision["candidate"]
            self._mutate_anchor(plan_row, scope, cand)
        elif action == "remove":
            try:
                idx = self._state.plan_rows.index(plan_row)
            except ValueError:
                return
            self._remove_row(idx, notify=True)
        elif action == "delete_source":
            try:
                idx = self._state.plan_rows.index(plan_row)
            except ValueError:
                return
            self._prompt_delete_source(idx)
        elif action == "open_tvdb_search":
            self._open_tvdb_search(plan_row)

    def _open_tvdb_search(self, plan_row: PlanRow) -> None:
        """Open the TVDB-search modal pre-filled with the row's show name."""
        if self._tvdb_client is None:
            self._capped_notify(
                "TVDB not configured — set TVDB_API_KEY in settings to enable",
                severity="warning",
                timeout=8,
            )
            return
        initial = ""
        if plan_row.row.top_candidate is not None:
            initial = plan_row.row.top_candidate.title
        if not initial:
            initial = plan_row.parsed.title_candidate or plan_row.parsed.raw_filename
        group_size = sum(
            1 for pr in self._state.plan_rows if pr.row.group_key == plan_row.row.group_key
        )
        is_group_capable = plan_row.parsed.kind == "tv" and group_size > 1

        def _after_search(result: dict | None) -> None:
            if result is None:
                return
            self._apply_tvdb_switch(plan_row, result)

        self.push_screen(
            TVDBSearchScreen(
                initial_query=initial,
                is_group_capable=is_group_capable,
                group_size=group_size,
                tvdb_client=self._tvdb_client,
            ),
            _after_search,
        )

    def _apply_tvdb_switch(self, plan_row: PlanRow, picked: dict) -> None:
        """Re-resolve a row (or whole show) against a TVDB show + ordering.

        Fetches the chosen TVDB series' episode list for the chosen
        ``season_type``, replaces the affected rows' ``top_candidate``
        with a tvdb-anchored :class:`Candidate` carrying that episode
        list, and reruns ``match_episode`` so the file gets the right
        S/E + title under the new ordering.
        """
        if self._tvdb_client is None:
            return
        tvdb_id = int(picked["tvdb_id"])
        season_type = picked["season_type"]
        scope = picked["scope"]
        cache_key = (tvdb_id, season_type)
        episodes = self._tvdb_episode_cache.get(cache_key)
        if episodes is None:
            try:
                result = self._tvdb_client.get_series_episodes(tvdb_id, season_type)
            except TVDBError as exc:
                self._capped_notify(f"TVDB fetch failed: {exc}", severity="error", timeout=8)
                return
            episodes = result.episodes
            self._tvdb_episode_cache[cache_key] = episodes
        if not episodes:
            self._capped_notify(
                f"TVDB returned 0 episodes for tvdb-{tvdb_id} ({season_type})",
                severity="warning",
            )
            return

        # Build the TVDB-anchored Candidate that will replace each row's
        # top_candidate. ``episode_list`` is pre-populated so the planner
        # at apply time uses TVDB data directly and avoids any TMDB
        # round-trip for episode titles.
        new_candidate = Candidate(
            anchor_kind="tvdb",
            anchor_id=str(tvdb_id),
            kind="tv",
            title=str(picked.get("tvdb_title") or ""),
            year=picked.get("tvdb_year"),
            confidence=1.0,  # user explicitly chose this
            episode_list=tuple(episodes),
        )

        if scope == "group":
            targets = [
                pr for pr in self._state.plan_rows if pr.row.group_key == plan_row.row.group_key
            ]
            self._state.group_episode_source[plan_row.row.group_key] = f"tvdb:{season_type}"
        else:
            targets = [plan_row]
            self._state.row_episode_source[plan_row.row.source_path] = f"tvdb:{season_type}"

        for pr in targets:
            try:
                new_episode = match_episode(pr.parsed, new_candidate, fetch_season=None)
            except Exception:
                new_episode = None
            new_flags = [
                f
                for f in pr.row.flags
                if f
                not in {
                    "no-anchor",
                    "low-confidence",
                    "ambiguous",
                    "empty-search",
                    "year-mismatch",
                    "episode-renumbered",
                    "episode-title-mismatch",
                    "episode-synthesized",
                    "episode-unknown",
                }
            ]
            new_flags.append("tvdb-source")
            new_row = RowReport(
                source_path=pr.row.source_path,
                raw_filename=pr.row.raw_filename,
                kind=pr.row.kind,
                parsed_title=pr.row.parsed_title,
                parsed_year=pr.row.parsed_year,
                parsed_season=pr.row.parsed_season,
                parsed_episode=pr.row.parsed_episode,
                parsed_episode_title=pr.row.parsed_episode_title,
                group_key=pr.row.group_key,
                top_candidate=new_candidate,
                alternatives=pr.row.alternatives,
                queries_tried=pr.row.queries_tried,
                flags=new_flags,
                matched_episode=new_episode,
            )
            pr.row = new_row
            pr.target = _compute_target(
                pr.parsed,
                new_candidate,
                new_episode,
                self._state.movies_root,
                self._state.tv_root,
            )

        self._refresh_table()
        self._capped_notify(
            f"switched {len(targets)} row(s) to tvdb-{tvdb_id} ({season_type})",
            timeout=4,
        )

    def _mutate_anchor(self, plan_row: PlanRow, scope: str, candidate: Candidate | None) -> None:
        """Replace the anchor on this row (or every row in its group)."""
        if scope == "group":
            targets = [
                pr for pr in self._state.plan_rows if pr.row.group_key == plan_row.row.group_key
            ]
        else:
            targets = [plan_row]
        # Persist into override accumulators (used at apply + save time).
        anchor_str: str | None = None
        if candidate is not None:
            anchor_str = self._candidate_to_anchor(candidate)
        for pr in targets:
            # Recompute matched_episode for TV anchors before recomputing target.
            new_episode: Episode | None = None
            if pr.parsed.kind == "tv" and candidate is not None:
                try:
                    new_episode = match_episode(
                        pr.parsed, candidate, fetch_season=self._cache.get_season
                    )
                except Exception:
                    new_episode = None
            new_flags = [
                f
                for f in pr.row.flags
                if f
                not in {
                    "no-anchor",
                    "low-confidence",
                    "ambiguous",
                    "empty-search",
                    "year-mismatch",
                }
            ]
            if candidate is not None:
                new_flags.append("anchor-override")
            new_row = RowReport(
                source_path=pr.row.source_path,
                raw_filename=pr.row.raw_filename,
                kind=pr.row.kind,
                parsed_title=pr.row.parsed_title,
                parsed_year=pr.row.parsed_year,
                parsed_season=pr.row.parsed_season,
                parsed_episode=pr.row.parsed_episode,
                parsed_episode_title=pr.row.parsed_episode_title,
                group_key=pr.row.group_key,
                top_candidate=candidate,
                alternatives=pr.row.alternatives,
                queries_tried=pr.row.queries_tried,
                flags=new_flags,
                matched_episode=new_episode,
            )
            pr.row = new_row
            pr.target = _compute_target(
                pr.parsed,
                candidate,
                new_episode,
                self._state.movies_root,
                self._state.tv_root,
            )
        # Store in the accumulators. Group scope persists by group_key;
        # row scope persists by source_path. Clearing scrubs the entry.
        if scope == "group":
            key = plan_row.row.group_key
            if anchor_str is None:
                self._state.overrides_groups.pop(key, None)
            else:
                self._state.overrides_groups[key] = anchor_str
        else:
            key = str(plan_row.row.source_path)
            if anchor_str is None:
                self._state.overrides_rows.pop(key, None)
            else:
                self._state.overrides_rows[key] = anchor_str
        self._persist_overrides()

    def _candidate_to_anchor(self, c: Candidate) -> str:
        if c.anchor_kind == "imdb":
            return f"imdb-{c.anchor_id}"
        return f"tmdb-{c.kind}-{c.anchor_id}"

    def _persist_overrides(self) -> None:
        payload = {
            "groups": dict(self._state.overrides_groups),
            "rows": dict(self._state.overrides_rows),
        }
        try:
            self._state.save_path.parent.mkdir(parents=True, exist_ok=True)
            self._state.save_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            self.notify(f"could not save overrides: {exc}", severity="error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search_input":
            self._state.search = event.value
            self._refresh_table()
            self.query_one("#file_table", DataTable).focus()

    def action_apply(self) -> None:
        if not self._state.can_apply():
            self.notify(
                "apply needs --movies and --tv roots; restart with them set or use the "
                "saved anchors JSON via `plex-renamer plan` to build a plan",
                severity="warning",
                timeout=8,
            )
            return
        plan = self._build_apply_plan_from_rows(self._state.plan_rows)
        if not plan.ops:
            self.notify(
                "no resolvable ops to apply (every row is unresolved or unanchored)",
                severity="warning",
            )
            return

        def _after_confirm(yes: bool | None) -> None:
            if not yes:
                return
            converted_rows = [
                pr
                for pr in self._state.plan_rows
                if pr.row.top_candidate is not None and pr.target is not None
            ]
            self._apply_in_background(plan, converted_rows)

        # The confirm screen is the one popup we KEEP for bulk apply —
        # it shows what's about to happen (op count, collisions, skipped)
        # and is the only place to bail out before changing the disk.
        self.push_screen(
            ApplyConfirmScreen(
                op_count=len(plan.ops),
                collision_count=len(plan.collisions),
                skipped_count=len(plan.skipped),
            ),
            _after_confirm,
        )

    def action_convert_row(self) -> None:
        """Apply the currently-highlighted row only (move file + remove from list)."""
        if self._state.movies_root is None or self._state.tv_root is None:
            self.notify(
                "convert needs --movies and --tv roots set",
                severity="warning",
                timeout=6,
            )
            return
        idx = self._cursor_row_index()
        if idx is None:
            return
        if not (0 <= idx < len(self._state.plan_rows)):
            return
        pr = self._state.plan_rows[idx]
        if pr.row.top_candidate is None or pr.target is None:
            self.notify(
                "row has no resolved target — anchor it first",
                severity="warning",
            )
            return
        plan = self._build_apply_plan_from_rows([pr])
        if not plan.ops:
            self.notify("planner produced no ops for this row", severity="error")
            return
        # Per-row convert skips both the confirm AND the progress popup.
        # Same-FS moves are instant (os.rename); cross-FS shows progress
        # in the status bar so the file table stays visible.
        #
        # Skip ``prune_empty_parents``: on a slow network share each
        # ``iterdir()`` to check whether the parent is now empty is a
        # round-trip the server may also be servicing in serial behind
        # its own indexer, and we've measured per-row cleanup taking
        # 8+ seconds while the actual rename was 4. The bulk apply
        # keeps pruning on; per-row gives that up to keep latency low.
        self._apply_in_background(plan, [pr], prune_empty_parents=False)

    @work(thread=True)
    def _apply_in_background(
        self,
        plan,
        converted_rows: list[PlanRow],
        *,
        prune_empty_parents: bool = True,
    ) -> None:
        """Run apply_plan_iter on a worker thread.

        Progress events go to the status bar (not a modal) so the file
        table stays interactive. On done, refreshes the table and drops
        rows whose source no longer exists.
        """
        succeeded = 0
        failed = 0
        last_error: str | None = None
        try:
            for event in apply_plan_iter(
                plan, cleanup=True, prune_empty_parents=prune_empty_parents
            ):
                ev = event.get("event")
                if ev == "op_started":
                    total = int(event.get("total_ops") or 0)
                    idx = int(event.get("op_index") or 0)
                    src = event.get("source") or ""
                    name = Path(src).name if src else ""
                    self._loading_status = f"applying {idx + 1}/{total}  {name[:40]}"
                    self.app.call_from_thread(self._update_status)
                elif ev == "op_verified":
                    succeeded += 1
                elif ev == "op_failed":
                    failed += 1
                    last_error = str(event.get("error") or "")
        except Exception as exc:
            failed += 1
            last_error = str(exc)
        self._loading_status = ""

        def _finish() -> None:
            self._after_apply_complete(converted_rows)
            if failed:
                self._capped_notify(
                    f"applied with {failed} failure(s): {last_error or '?'}",
                    severity="error",
                    timeout=8,
                )
            else:
                self._capped_notify(f"applied {succeeded} file(s)", timeout=4)

        self.app.call_from_thread(_finish)

    def _after_apply_complete(self, converted: list[PlanRow]) -> None:
        """Drop successfully-moved rows from the list + refresh the table.

        Best-effort heuristic: a row is "converted" if its source path no
        longer exists on disk after the apply (cleanup deleted it). Any
        row whose source survived gets to stay in the list so the user
        can retry. The caller (``_finish``) emits the user-facing toast;
        we just mutate state here.
        """
        for pr in list(converted):
            if not pr.parsed.source_path.exists():
                try:
                    actual_idx = self._state.plan_rows.index(pr)
                except ValueError:
                    continue
                self._remove_row(actual_idx, notify=False)
        self._refresh_table()

    def _build_apply_plan_from_rows(self, plan_rows: list[PlanRow]):
        assert self._state.movies_root is not None
        assert self._state.tv_root is not None
        pairs: list[tuple[ParseResult, Candidate | None]] = [
            (pr.parsed, pr.row.top_candidate) for pr in plan_rows
        ]
        return build_plan_from_pairs(
            pairs,
            movies_root=self._state.movies_root,
            tv_root=self._state.tv_root,
            input_root=self._state.source,
            fetch_season=self._cache.get_season,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_save_path(explicit: str | None, source: Path) -> Path:
    if explicit:
        return Path(explicit).resolve()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source.name)
    return Path("/tmp") / f"plex-renamer-review-anchors-{safe or 'root'}.json"


def _preload_overrides(state: TUIState, path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    groups = payload.get("groups") or {}
    rows = payload.get("rows") or {}
    for k, v in groups.items():
        state.overrides_groups[str(k)] = str(v)
    for k, v in rows.items():
        state.overrides_rows[str(k)] = str(v)


def run_review_tui(args: argparse.Namespace) -> int:
    """Default ``plex-renamer review`` entry: paginated TUI."""
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

    movies_root = Path(args.movies).resolve() if args.movies else None
    tv_root = Path(args.tv).resolve() if args.tv else None
    if movies_root is None and settings.movies_root:
        movies_root = Path(settings.movies_root)
    if tv_root is None and settings.tv_root:
        tv_root = Path(settings.tv_root)

    try:
        client = TMDBClient(api_key=tmdb_key)
    except TMDBAuthError as exc:
        print(f"plex-renamer: TMDB auth failed: {exc}", file=sys.stderr)
        return 2
    cache = TMDBCache(client)
    resolver = IMDbFallbackResolver(tmdb=cache, omdb_api_key=settings.omdb_api_key)

    # TVDB is optional — used only when the user picks the "Switch to
    # TVDB" action in the row detail modal. Without a key, that button
    # just notifies "not configured" and the rest of the TUI runs fine.
    tvdb_client: TVDBClient | None = None
    if settings.tvdb_api_key:
        try:
            tvdb_client = TVDBClient(
                api_key=settings.tvdb_api_key,
                pin=settings.tvdb_pin,
            )
        except Exception as exc:
            print(f"plex-renamer: TVDB client init failed: {exc}", file=sys.stderr)
            tvdb_client = None

    save_path = _resolve_save_path(args.save, source)
    state = TUIState(
        source=source,
        movies_root=movies_root,
        tv_root=tv_root,
        save_path=save_path,
        plan_rows=[],
    )
    if args.load:
        _preload_overrides(state, Path(args.load))

    # The App launches with an empty table and resolves on a worker
    # thread so the UI appears immediately. Status bar shows resolution
    # progress; the table populates when done.
    app = ReviewApp(
        state=state,
        cache=cache,
        resolver=resolver,
        defer_resolve=True,
        tvdb_client=tvdb_client,
    )
    app.run()

    if state.overrides_groups or state.overrides_rows:
        print(
            f"\nWrote {len(state.overrides_groups) + len(state.overrides_rows)} "
            f"override(s) to {state.save_path}",
            file=sys.stderr,
        )
    return 0


__all__ = ["run_review_tui"]
