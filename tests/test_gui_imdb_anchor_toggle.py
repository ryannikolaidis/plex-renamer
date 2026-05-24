"""IMDb ID paste field and anchor-type toggle in the edit pane.

The user can paste an IMDb ID, select the IMDb anchor radio, and request
the resolver. The model's anchor override and IMDb id are set as a side
effect; the orchestrator is responsible for the actual resolve call.
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


def test_imdb_resolve_sets_overrides_and_emits(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])
    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    received: list[tuple[Path, str]] = []
    pane.imdb_resolve_requested.connect(lambda p, i: received.append((p, i)))

    pane._imdb_input.setText("tt1234567")
    pane._on_imdb_resolve()

    row = model.row_for(src)
    assert row is not None
    assert row.imdb_id_override == "tt1234567"
    assert row.anchor_kind_override == "imdb"
    assert received == [(src, "tt1234567")]


def test_anchor_radio_toggle_persists(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])
    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    # Default state is TMDB anchor.
    assert pane._anchor_tmdb.isChecked() is True
    # User flips to IMDb.
    pane._anchor_imdb.setChecked(True)
    row = model.row_for(src)
    assert row is not None
    assert row.anchor_kind_override == "imdb"
    # Flip back.
    pane._anchor_tmdb.setChecked(True)
    assert row.anchor_kind_override == "tmdb"


def test_empty_imdb_does_not_emit(qtbot) -> None:
    from plex_renamer.gui.edit_pane import EditPane
    from plex_renamer.gui.models import ItemModel

    model = ItemModel()
    src = Path("/in/Foo.mkv")
    model.set_rows([_row(src)])
    pane = EditPane(model)
    qtbot.addWidget(pane)
    pane.load_row(src)

    received: list[tuple[Path, str]] = []
    pane.imdb_resolve_requested.connect(lambda p, i: received.append((p, i)))

    pane._imdb_input.setText("")
    pane._on_imdb_resolve()
    assert received == []
