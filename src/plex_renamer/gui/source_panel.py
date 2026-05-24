"""Left-side source panel: groups detected files by show/movie.

The panel is NOT a flat table; it's a :class:`QTreeWidget` whose top
level is the group (movie or TV show) and whose children are the source
files. Each leaf carries a confidence badge.

Clicking a leaf emits :attr:`row_clicked` with the source ``Path``;
clicking a group node emits :attr:`group_clicked` with the group key.
Both signals are wired by the main window into the edit pane / show
anchor picker respectively.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.confidence_badge import ConfidenceBadge
from plex_renamer.gui.models import ItemModel, ItemRow

GROUP_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
SOURCE_PATH_ROLE = Qt.ItemDataRole.UserRole + 2


class SourcePanel(QWidget):
    """Tree view grouped by detected show / movie."""

    row_clicked = Signal(Path)
    group_clicked = Signal(str)  # group_key

    def __init__(self, model: ItemModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self.setAccessibleName("source-panel")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Source", "Confidence"])
        self._tree.setColumnWidth(0, 480)
        self._tree.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)

        self._model.rows_reset.connect(self.refresh)
        self._model.row_changed.connect(self._refresh_row)

        self.refresh()

    # ----- Population -----------------------------------------------------

    def refresh(self) -> None:
        self._tree.clear()
        for group_key, rows in self._model.groups().items():
            group_item = self._make_group_item(group_key, rows)
            self._tree.addTopLevelItem(group_item)
            for row in rows:
                leaf = self._make_leaf_item(row)
                group_item.addChild(leaf)
            group_item.setExpanded(True)

    def _refresh_row(self, _source_path: Path) -> None:
        # Simple strategy: re-render. A future optimization could find
        # the specific leaf and rebuild only that one widget.
        self.refresh()

    def _make_group_item(self, group_key: str, rows: list[ItemRow]) -> QTreeWidgetItem:
        # Group label: derive from the first row's metadata; the parser
        # cleaned the title already.
        if not rows:
            label = group_key
        else:
            first = rows[0]
            title = first.parsed.title_candidate or first.parsed.raw_filename
            year = f" ({first.parsed.year})" if first.parsed.year else ""
            count = f" — {len(rows)} item(s)" if len(rows) > 1 else ""
            label = f"{title}{year}{count}"
        item = QTreeWidgetItem([label, ""])
        item.setData(0, GROUP_KEY_ROLE, group_key)
        return item

    def _make_leaf_item(self, row: ItemRow) -> QTreeWidgetItem:
        leaf = QTreeWidgetItem([row.parsed.raw_filename, ""])
        leaf.setData(0, SOURCE_PATH_ROLE, str(row.source_path))
        leaf.setToolTip(0, str(row.source_path))

        # Render the confidence badge inside column 1.
        badge = ConfidenceBadge(row.confidence_band)
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(2, 2, 2, 2)
        cell_layout.addWidget(badge)
        cell_layout.addStretch(1)
        # Defer setItemWidget until the leaf is attached to the tree —
        # caller handles that. We expose the cell widget through the
        # leaf's data so the tree can hook it up.
        leaf.setData(1, Qt.ItemDataRole.UserRole + 3, cell)
        return leaf

    def showEvent(self, event):  # noqa: N802 — Qt override
        # Hook up the deferred itemWidget pairs once the tree is realized.
        super().showEvent(event)
        self._attach_badges()

    def _attach_badges(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                leaf = group.child(j)
                cell = leaf.data(1, Qt.ItemDataRole.UserRole + 3)
                if cell is not None and self._tree.itemWidget(leaf, 1) is None:
                    self._tree.setItemWidget(leaf, 1, cell)

    # ----- Click handling -------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        source_str = item.data(0, SOURCE_PATH_ROLE)
        if source_str:
            self.row_clicked.emit(Path(source_str))
            return
        group_key = item.data(0, GROUP_KEY_ROLE)
        if group_key:
            self.group_clicked.emit(group_key)

    # ----- Test helpers ---------------------------------------------------

    def _leaf_count(self) -> int:
        count = 0
        for i in range(self._tree.topLevelItemCount()):
            count += self._tree.topLevelItem(i).childCount()
        return count

    def _group_count(self) -> int:
        return self._tree.topLevelItemCount()


__all__ = ["GROUP_KEY_ROLE", "SOURCE_PATH_ROLE", "SourcePanel"]
