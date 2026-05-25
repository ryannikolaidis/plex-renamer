"""Right-side target panel: proposed Plex paths.

Mirrors the source panel's grouping (top-level group node = show or movie)
but renders the proposed target path for each row. The panel is read-only;
the user edits via the source side and the edit pane.

Rows with no proposed op (unresolved) render the source path with a
``<unresolved>`` placeholder so the user can see what still needs work.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.models import ItemModel, ItemRow
from plex_renamer.gui.source_panel import GROUP_KEY_ROLE, SOURCE_PATH_ROLE


class TargetPanel(QWidget):
    """Tree view showing the proposed Plex target for each source."""

    row_clicked = Signal(Path)

    def __init__(self, model: ItemModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self.setAccessibleName("target-panel")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Proposed Plex path"])
        self._tree.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)

        self._model.rows_reset.connect(self.refresh)
        self._model.row_changed.connect(self._refresh_row)

        self.refresh()

    def refresh(self) -> None:
        self._tree.clear()
        for group_key, rows in self._model.groups().items():
            group = self._make_group_item(group_key, rows)
            self._tree.addTopLevelItem(group)
            for row in rows:
                group.addChild(self._make_leaf(row))
            group.setExpanded(True)

    def _refresh_row(self, _path: Path) -> None:
        self.refresh()

    def _make_group_item(self, group_key: str, rows: list[ItemRow]) -> QTreeWidgetItem:
        # Mirror source_panel._make_group_item: when the group has no
        # candidate yet (resolver returned nothing), prefer show_name_hint
        # for TV rows so the user sees "Lazarus_2 — 13 item(s)" instead
        # of "[S01.E01] Goodbye Cruel World.mp4". Falling back to
        # title_candidate lands us on the EPISODE title for filenames
        # like "[S01.E01] Episode.mp4"; falling back to raw_filename
        # puts the .mp4 filename in the group header. Neither is the show.
        if not rows:
            label = group_key
        else:
            first = rows[0]
            if first.candidate is not None:
                title = first.candidate.title
                year = f" ({first.candidate.year})" if first.candidate.year else ""
            elif first.parsed.kind == "tv" and first.show_name_hint:
                title = first.show_name_hint
                year = f" ({first.parsed.year})" if first.parsed.year else ""
            else:
                title = first.parsed.title_candidate or first.parsed.raw_filename
                year = f" ({first.parsed.year})" if first.parsed.year else ""
            count = f" — {len(rows)} item(s)" if len(rows) > 1 else ""
            label = f"{title}{year}{count}"
        item = QTreeWidgetItem([label])
        item.setData(0, GROUP_KEY_ROLE, group_key)
        return item

    def _make_leaf(self, row: ItemRow) -> QTreeWidgetItem:
        if row.skip:
            label = "<skipped>"
        elif row.proposed_op is not None:
            label = str(row.proposed_op.target)
        else:
            label = "<unresolved>"
        leaf = QTreeWidgetItem([label])
        leaf.setData(0, SOURCE_PATH_ROLE, str(row.source_path))
        leaf.setToolTip(0, label)
        return leaf

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        source_str = item.data(0, SOURCE_PATH_ROLE)
        if source_str:
            self.row_clicked.emit(Path(source_str))


__all__ = ["TargetPanel"]
