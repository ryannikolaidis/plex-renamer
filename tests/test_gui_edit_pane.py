"""Edit pane: manual overrides, TMDB search wiring, skip toggle.

The pane is a thin wrapper over :class:`ItemModel`; every interaction
mutates the model and emits the ``row_changed`` signal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _row(source: Path):
    from plex_renamer.gui.models import ItemRow
    from plex_renamer.parser.models import ParseResult

    parsed = ParseResult(
        source_path=source,
        kind="movie",
        title_candidate="Foo",
        year=2020,
        raw_filename=source.name,
    )
    return ItemRow(parsed=parsed)


def test_manual_override_persists_to_model(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])

    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    pane._manual_title.setText("Brand New Title")
    pane._manual_year.setValue(1999)
    pane._on_apply_overrides()

    row = model.row_for(src)
    assert row is not None
    assert row.manual_title == "Brand New Title"
    assert row.manual_year == 1999


def test_skip_toggle_propagates(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])

    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    pane._skip_checkbox.setChecked(True)
    row = model.row_for(src)
    assert row is not None
    assert row.skip is True


def test_tmdb_search_signal(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])
    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    received: list[tuple[Path, str]] = []
    pane.tmdb_search_requested.connect(lambda p, q: received.append((p, q)))

    pane._tmdb_panel.set_query_text("Some Movie")
    pane._tmdb_panel._emit_search()

    assert received == [(src, "Some Movie")]


def test_candidate_chosen_sets_model_candidate(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel
    from plex_renamer.tmdb.models import Candidate

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])
    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    cand = Candidate(
        anchor_kind="tmdb", anchor_id="42", kind="movie", title="Foo", year=2020, confidence=0.95
    )
    pane.set_tmdb_results(src, [cand])
    pane._tmdb_panel._results.setCurrentRow(0)
    pane._tmdb_panel._emit_chosen()

    row = model.row_for(src)
    assert row is not None
    assert row.candidate is not None
    assert row.candidate.anchor_id == "42"
