"""Right-side target panel: proposed Plex paths.

Mirrors the source panel's grouping (top-level group node = show or movie)
but renders the proposed target path for each row. The panel is read-only;
the user edits via the source side and the edit pane.

Rows with no proposed op (unresolved) render the source path with a
``<unresolved>`` placeholder so the user can see what still needs work.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
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
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)

        self._header_label = QLabel("Target (Plex layout)")
        self._header_label.setStyleSheet("font-weight: 600; padding: 4px 2px;")
        self._empty_label = QLabel("Drop files or folders to see the proposed Plex layout here.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: palette(mid); font-size: 12px; padding: 16px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._header_label)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._tree)

        self._model.rows_reset.connect(self.refresh)
        self._model.row_changed.connect(self._refresh_row)

        self.refresh()

    def refresh(self) -> None:
        self._tree.clear()
        groups = self._model.groups()
        for group_key, rows in groups.items():
            group = self._make_group_item(group_key, rows)
            self._tree.addTopLevelItem(group)
            for row in rows:
                group.addChild(self._make_leaf(row))
            group.setExpanded(True)
        self._update_header_and_empty_state(groups)

    def _update_header_and_empty_state(self, groups: dict[str, list[ItemRow]]) -> None:
        total_files = sum(len(rs) for rs in groups.values())
        group_count = len(groups)
        if total_files == 0:
            self._header_label.setText("Target (Plex layout)")
            self._empty_label.setVisible(True)
        else:
            self._header_label.setText(
                f"Target (Plex layout) · {total_files} files in {group_count} groups"
            )
            self._empty_label.setVisible(False)

    def _refresh_row(self, _path: Path) -> None:
        self.refresh()

    def _make_group_item(self, group_key: str, rows: list[ItemRow]) -> QTreeWidgetItem:
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

    def _on_context_menu(self, point) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        targets: list[Path] = []
        sources: list[Path] = []
        for item in items:
            source_str = item.data(0, SOURCE_PATH_ROLE)
            if not source_str:
                continue
            row = self._model.row_for(Path(source_str))
            if row is None:
                continue
            sources.append(row.source_path)
            if row.proposed_op is not None:
                targets.append(row.proposed_op.target)
        if not sources:
            return

        menu = QMenu(self)
        reveal_label = (
            "Reveal target folder in Finder"
            if platform.system() == "Darwin"
            else "Reveal target folder in Explorer"
        )
        reveal_action = QAction(reveal_label, menu)
        reveal_action.setEnabled(bool(targets))
        reveal_action.triggered.connect(lambda: self._reveal_paths(targets))
        menu.addAction(reveal_action)

        copy_action = QAction("Copy target path", menu)
        copy_action.setEnabled(bool(targets))
        copy_action.triggered.connect(lambda: self._copy_paths(targets))
        menu.addAction(copy_action)

        menu.exec(self._tree.viewport().mapToGlobal(point))

    def _reveal_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        system = platform.system()
        for path in paths:
            try:
                if system == "Darwin":
                    subprocess.run(["open", "-R", str(path)], check=False)
                elif system == "Windows":
                    subprocess.run(["explorer", f"/select,{path}"], check=False)
                else:
                    subprocess.run(["xdg-open", str(path.parent)], check=False)
            except OSError:
                continue

    def _copy_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        QApplication.clipboard().setText("\n".join(str(p) for p in paths))


__all__ = ["TargetPanel"]
