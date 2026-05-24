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
