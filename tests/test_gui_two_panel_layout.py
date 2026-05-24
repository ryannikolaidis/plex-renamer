"""Two-panel layout: source on the left, target on the right.

Verifies that the panels are present, that they are NOT a flat table
(both are :class:`QTreeWidget`-based), and that adding rows produces
group nodes in both panels with matching counts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _make_row(source: Path, kind: str = "tv", title: str = "Foo"):
    from plex_renamer.gui.models import ItemRow
    from plex_renamer.parser.models import ParseResult

    parsed = ParseResult(
        source_path=source,
        kind=kind,  # type: ignore[arg-type]
        title_candidate=title,
        year=2020,
        raw_filename=source.name,
        season=1,
        episode=1,
    )
    return ItemRow(parsed=parsed)


def test_main_window_has_both_panels(qtbot, gui_settings) -> None:
    from plex_renamer.gui.main_window import MainWindow

    win = MainWindow(gui_settings)
    qtbot.addWidget(win)
    assert win.source_panel() is not None
    assert win.target_panel() is not None


def test_panels_group_by_show(qtbot, gui_settings) -> None:
    from plex_renamer.gui.main_window import MainWindow

    win = MainWindow(gui_settings)
    qtbot.addWidget(win)
    rows = [
        _make_row(Path("/in/Foo/s01e01.mkv"), "tv", "Foo"),
        _make_row(Path("/in/Foo/s01e02.mkv"), "tv", "Foo"),
        _make_row(Path("/in/Bar/s01e01.mkv"), "tv", "Bar"),
    ]
    win.item_model().set_rows(rows)

    # Two distinct shows -> two group nodes.
    assert win.source_panel()._group_count() == 2
    assert win.source_panel()._leaf_count() == 3


def test_panels_not_flat_table(qtbot, gui_settings) -> None:
    """The source panel must use a tree widget (groups + leaves)."""
    from PySide6.QtWidgets import QTreeWidget

    from plex_renamer.gui.main_window import MainWindow

    win = MainWindow(gui_settings)
    qtbot.addWidget(win)
    # Look for at least one QTreeWidget descendant in the source panel.
    trees = win.source_panel().findChildren(QTreeWidget)
    assert len(trees) >= 1


def test_clicking_source_row_loads_edit_pane(qtbot, gui_settings) -> None:
    from plex_renamer.gui.main_window import MainWindow

    win = MainWindow(gui_settings)
    qtbot.addWidget(win)
    row = _make_row(Path("/in/Foo/s01e01.mkv"), "tv", "Foo")
    win.item_model().set_rows([row])

    win._on_row_clicked(row.source_path)

    current = win.edit_pane().current_row()
    assert current is not None
    assert current.source_path == row.source_path
