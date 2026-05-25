"""Show-anchor flow: an ambiguous TV group is anchored once and all
episodes inherit the chosen show.

The flow is:

1. Source panel shows a group node for the ambiguous show.
2. User opens the show-anchor picker on the group.
3. Picker fetches TMDB results (caller-injected here).
4. User picks a show; orchestrator pushes the candidate onto every row
   in the group.

We verify the wiring end-to-end against the GUI models without calling
real TMDB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _make_tv_row(source: Path, title: str = "Foo"):
    from plex_renamer.gui.models import ItemRow
    from plex_renamer.parser.models import ParseResult

    parsed = ParseResult(
        source_path=source,
        kind="tv",
        title_candidate=title,
        year=2020,
        raw_filename=source.name,
        season=1,
        episode=int(source.stem[-1]) if source.stem[-1].isdigit() else 1,
    )
    return ItemRow(parsed=parsed)


def test_show_anchor_picker_emits_choice(qtbot) -> None:
    from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker
    from plex_renamer.tmdb.models import Candidate

    picker = ShowAnchorPicker(group_key="tv::Foo")
    qtbot.addWidget(picker)

    candidates = [
        Candidate(
            anchor_kind="tmdb",
            anchor_id="100",
            kind="tv",
            title="Foo (US)",
            year=2020,
            confidence=0.8,
        ),
        Candidate(
            anchor_kind="tmdb",
            anchor_id="200",
            kind="tv",
            title="Foo (UK)",
            year=2019,
            confidence=0.7,
        ),
    ]
    picker.set_results(candidates)

    received: list[tuple[str, Candidate]] = []
    picker.show_chosen.connect(lambda g, c: received.append((g, c)))

    picker._results.setCurrentRow(1)
    picker._emit_chosen()

    assert received == [("tv::Foo", candidates[1])]


def test_group_anchor_propagates_to_all_rows() -> None:
    """When a show is picked for a group, every row in the group inherits it."""
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.tmdb.models import Candidate

    model = ItemModel()
    rows = [
        _make_tv_row(Path("/in/Foo/s01e01.mkv")),
        _make_tv_row(Path("/in/Foo/s01e02.mkv")),
    ]
    model.set_rows(rows)

    show = Candidate(
        anchor_kind="tmdb",
        anchor_id="555",
        kind="tv",
        title="Foo",
        year=2020,
        confidence=0.95,
    )
    # Simulate the orchestrator's group-anchor step: push the show onto
    # every row in the group.
    for r in rows:
        if r.group_key == "tv::Foo":
            model.set_candidate(r.source_path, show)

    for r in model.rows():
        assert r.candidate is not None
        assert r.candidate.anchor_id == "555"


def test_picker_emits_search_requested_when_user_searches(qtbot) -> None:
    """Typing a new query and triggering search emits ``search_requested``.

    Pins the interactive-search wiring on the picker itself: the
    orchestrator's recovery flow depends on the picker emitting a
    signal the orchestrator subscribes to. A regression to a silent
    text input would re-introduce the v0.1.1 dead-end behavior where
    an empty result list had no recourse.
    """
    from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker

    picker = ShowAnchorPicker(group_key="tv::Lazarus_2")
    qtbot.addWidget(picker)

    captured: list[tuple[str, str]] = []
    picker.search_requested.connect(lambda g, q: captured.append((g, q)))

    picker.set_search_text("Lazarus")
    picker.trigger_search()
    assert captured == [("tv::Lazarus_2", "Lazarus")]


def test_picker_set_results_shows_empty_hint_when_no_matches(qtbot) -> None:
    """An empty result list with a current query surfaces the hint label.

    The empty-state hint is what tells the user "your search returned
    nothing -- type a different name and try again." Without it the
    dialog reads as broken (which is exactly what the v0.1.1 user
    reported).
    """
    from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker

    picker = ShowAnchorPicker(group_key="tv::Lazarus_2")
    qtbot.addWidget(picker)

    picker.set_search_text("Lazarus_2")
    picker.set_results([])
    # The hint label is configured as visible (isVisible() returns False
    # until the dialog itself is shown, so we check the property the
    # picker controls directly).
    assert not picker._empty_label.isHidden()
    assert "Lazarus_2" in picker._empty_label.text()


def test_orchestrator_re_searches_picker_on_search_requested(qapp, qtbot) -> None:
    """When the picker emits ``search_requested``, the orchestrator re-queries TMDB.

    The orchestrator's :meth:`on_picker_search` runs ``tmdb.search_tv``
    with the new query and pushes the results back into the picker via
    :meth:`set_results`. Pins the wiring between picker and engine for
    the unknown-show recovery flow.
    """
    from pathlib import Path as P

    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
    from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.models import TVResult

    class _FakeTMDB:
        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []

        def search_movie(self, title, year):
            return []

        def search_tv(self, title, year):
            self.calls.append((title, year))
            if title == "Lazarus":
                return [TVResult(tmdb_id=42, title="Lazarus", year=2024)]
            return []

        def find_by_imdb_id(self, imdb_id):
            return None

        def get_season(self, tmdb_id, season):
            return []

    class _FakeResolver:
        def resolve_movie(self, *_args, **_kwargs):
            return None

        def resolve_tv(self, *_args, **_kwargs):
            return None

    tmdb = _FakeTMDB()
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=_FakeResolver(),
        movies_root=P("/tmp/Movies"),
        tv_root=P("/tmp/TV"),
        journal_dir=P("/tmp/journals"),
        cleanup_enabled=False,
    )
    model = ItemModel()
    # Seed a row so _rows_in_group returns something for the group key.
    parsed = ParseResult(
        source_path=P("/in/Lazarus_2/s1/[S01.E01] Test.mp4"),
        kind="tv",
        title_candidate="",
        year=None,
        raw_filename="[S01.E01] Test.mp4",
        season=1,
        episode=1,
        parent_dirs=["Lazarus_2", "s1"],
    )
    from plex_renamer.gui.models import ItemRow

    model.set_rows([ItemRow(parsed=parsed, show_name_hint="Lazarus_2")])

    orch = Orchestrator(model, deps)
    picker = ShowAnchorPicker(group_key="tv::Lazarus_2")
    qtbot.addWidget(picker)
    orch._open_picker = picker

    orch.on_picker_search("tv::Lazarus_2", "Lazarus")

    assert ("Lazarus", None) in tmdb.calls
    # The picker received the new candidate.
    assert picker._candidates, "Picker should have received candidates"
    assert picker._candidates[0].title == "Lazarus"


def test_source_panel_group_click_emits_group_key(qtbot) -> None:
    """Clicking a group node in the source panel emits ``group_clicked``."""
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.gui.source_panel import SourcePanel

    model = ItemModel()
    model.set_rows([_make_tv_row(Path("/in/Foo/s01e01.mkv"))])
    panel = SourcePanel(model)
    qtbot.addWidget(panel)

    received: list[str] = []
    panel.group_clicked.connect(received.append)

    # Find the group node and click via the internal handler.
    tree = panel._tree
    group_item = tree.topLevelItem(0)
    panel._on_item_clicked(group_item, 0)

    assert received == ["tv::Foo"]
