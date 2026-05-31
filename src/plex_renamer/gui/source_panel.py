"""Left-side source panel: groups detected files by show/movie.

The panel is NOT a flat table; it's a :class:`QTreeWidget` whose top
level is the group (movie or TV show) and whose children are the source
files. Each leaf carries a confidence badge.

Clicking a leaf emits :attr:`row_clicked` with the source ``Path``;
clicking a group node emits :attr:`group_clicked` with the group key.
Both signals are wired by the main window into the edit pane / show
anchor picker respectively.

Right-clicking a leaf opens a context menu with the per-row actions
(edit, set IMDb, override metadata, skip, reveal in Finder, copy path).
Multi-select (Ctrl/Shift-click) lets the user apply Skip and metadata
overrides across many rows at once.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.confidence_badge import ConfidenceBadge
from plex_renamer.gui.models import ItemModel, ItemRow

GROUP_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
SOURCE_PATH_ROLE = Qt.ItemDataRole.UserRole + 2
BADGE_CELL_ROLE = Qt.ItemDataRole.UserRole + 3


class SourcePanel(QWidget):
    """Tree view grouped by detected show / movie."""

    row_clicked = Signal(Path)
    group_clicked = Signal(str)  # group_key
    # Emitted when the user picks "Set IMDb ID…" from the context menu.
    # The MainWindow prompts for the ID and re-emits to the orchestrator.
    set_imdb_requested = Signal(Path)
    # Emitted when a row's Skip state is toggled via the context menu
    # or Delete key. The MainWindow / orchestrator mutates the model.
    skip_toggle_requested = Signal(list, bool)  # list[Path], new_skip_value

    def __init__(self, model: ItemModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self.setAccessibleName("source-panel")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Source", "Confidence"])
        self._tree.setColumnWidth(0, 480)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemActivated.connect(self._on_item_activated)

        # Empty-state caption below the tree title until rows are loaded.
        # We keep this in the normal layout flow (rather than overlaid
        # on the tree viewport) — overlay parenting to the tree caused
        # segfaults under offscreen Qt when refresh() ran during a model
        # rows_reset.
        self._empty_label = QLabel("Drop files or folders above to see parsed sources here.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: palette(mid); font-size: 12px; padding: 16px;")

        self._header_label = QLabel("Source")
        self._header_label.setStyleSheet("font-weight: 600; padding: 4px 2px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._header_label)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._tree)

        self._model.rows_reset.connect(self.refresh)
        self._model.row_changed.connect(self._refresh_row)

        # Del toggles Skip across the current selection.
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._tree)
        delete_shortcut.activated.connect(self._toggle_skip_on_selection)

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
        self._update_header_and_empty_state()

    def _update_header_and_empty_state(self) -> None:
        rows = list(self._iter_all_rows())
        total = len(rows)
        skipped = sum(1 for r in rows if r.skip)
        if total == 0:
            self._header_label.setText("Source")
            self._empty_label.setVisible(True)
        elif skipped > 0:
            self._header_label.setText(f"Source · {total} files · {skipped} skipped")
            self._empty_label.setVisible(False)
        else:
            self._header_label.setText(f"Source · {total} files")
            self._empty_label.setVisible(False)

    def _iter_all_rows(self):
        for _key, rows in self._model.groups().items():
            yield from rows

    def _refresh_row(self, _source_path: Path) -> None:
        self.refresh()

    def _make_group_item(self, group_key: str, rows: list[ItemRow]) -> QTreeWidgetItem:
        if not rows:
            label = group_key
        else:
            first = rows[0]
            if first.parsed.kind == "tv" and first.show_name_hint:
                title = first.show_name_hint
            else:
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

        # Skip-dim: skipped rows render in italic with a muted color so
        # the user sees Skip state at a glance instead of having to open
        # the edit pane to confirm.
        if row.skip:
            font = QFont(self._tree.font())
            font.setItalic(True)
            leaf.setFont(0, font)
            leaf.setForeground(0, self.palette().mid())

        badge = ConfidenceBadge(row.confidence_band)
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(2, 2, 2, 2)
        cell_layout.addWidget(badge)
        cell_layout.addStretch(1)
        leaf.setData(1, BADGE_CELL_ROLE, cell)
        return leaf

    def showEvent(self, event):  # noqa: N802 — Qt override
        super().showEvent(event)
        self._attach_badges()

    def _attach_badges(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(i)
            for j in range(group.childCount()):
                leaf = group.child(j)
                cell = leaf.data(1, BADGE_CELL_ROLE)
                if cell is not None and self._tree.itemWidget(leaf, 1) is None:
                    self._tree.setItemWidget(leaf, 1, cell)

    # ----- Selection helpers ----------------------------------------------

    def _selected_leaf_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self._tree.selectedItems():
            source_str = item.data(0, SOURCE_PATH_ROLE)
            if source_str:
                paths.append(Path(source_str))
        return paths

    def _selected_rows(self) -> list[ItemRow]:
        rows: list[ItemRow] = []
        for path in self._selected_leaf_paths():
            row = self._model.row_for(path)
            if row is not None:
                rows.append(row)
        return rows

    # ----- Click handling -------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        source_str = item.data(0, SOURCE_PATH_ROLE)
        if source_str:
            self.row_clicked.emit(Path(source_str))
            return
        group_key = item.data(0, GROUP_KEY_ROLE)
        if group_key:
            self.group_clicked.emit(group_key)

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        # Enter / double-click → load the row into the edit pane via the
        # same path as single-click. Multi-row activation isn't a thing
        # for an edit dialog, so we only handle the focused item.
        source_str = item.data(0, SOURCE_PATH_ROLE)
        if source_str:
            self.row_clicked.emit(Path(source_str))

    # ----- Context menu ---------------------------------------------------

    def _on_context_menu(self, point) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        single = rows[0] if len(rows) == 1 else None
        any_tv = any(r.parsed.kind == "tv" for r in rows)

        menu = QMenu(self)
        edit_action = QAction("Edit row…", menu)
        edit_action.setEnabled(single is not None)
        edit_action.triggered.connect(lambda: single and self.row_clicked.emit(single.source_path))
        menu.addAction(edit_action)

        imdb_action = QAction("Set IMDb ID…", menu)
        imdb_action.setEnabled(single is not None)
        imdb_action.triggered.connect(lambda: single and self._prompt_imdb_id(single.source_path))
        menu.addAction(imdb_action)

        # "Pick show for this group" — only when the single-selected row
        # is a TV row whose group has no resolved candidate on any row.
        if single is not None and single.parsed.kind == "tv":
            group_key = self._model.group_for(single.source_path)
            if group_key and self._group_is_unanchored(group_key):
                pick_action = QAction("Pick show for this group…", menu)
                pick_action.triggered.connect(lambda: self.group_clicked.emit(group_key))
                menu.addAction(pick_action)

        menu.addSeparator()

        override_menu = menu.addMenu("Override metadata")
        for kind, label in [
            ("title", "Title…"),
            ("year", "Year…"),
        ]:
            action = QAction(label, override_menu)
            action.triggered.connect(lambda _checked=False, k=kind: self._prompt_override(k, rows))
            override_menu.addAction(action)
        if any_tv:
            for kind, label in [
                ("season", "Season…"),
                ("episode", "Episode…"),
            ]:
                action = QAction(label, override_menu)
                action.triggered.connect(
                    lambda _checked=False, k=kind: self._prompt_override(k, rows)
                )
                override_menu.addAction(action)
        edition_action = QAction("Edition…", override_menu)
        edition_action.triggered.connect(lambda: self._prompt_override("edition", rows))
        override_menu.addAction(edition_action)

        menu.addSeparator()

        all_skipped = all(r.skip for r in rows)
        skip_label = "Unskip selected" if all_skipped else "Skip selected"
        skip_action = QAction(skip_label, menu)
        skip_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        skip_action.triggered.connect(self._toggle_skip_on_selection)
        menu.addAction(skip_action)

        menu.addSeparator()

        reveal_action = QAction(
            "Reveal source in Finder"
            if platform.system() == "Darwin"
            else "Reveal source in Explorer",
            menu,
        )
        reveal_action.triggered.connect(lambda: self._reveal_paths([r.source_path for r in rows]))
        menu.addAction(reveal_action)

        copy_action = QAction("Copy source path", menu)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(lambda: self._copy_paths([r.source_path for r in rows]))
        menu.addAction(copy_action)

        menu.exec(self._tree.viewport().mapToGlobal(point))

    def _group_is_unanchored(self, group_key: str) -> bool:
        groups = self._model.groups()
        if group_key not in groups:
            return False
        return not any(r.candidate is not None for r in groups[group_key])

    def _toggle_skip_on_selection(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        # Toggle on majority state: if every row is skipped, unskip all;
        # otherwise skip all.
        new_value = not all(r.skip for r in rows)
        paths = [r.source_path for r in rows]
        self.skip_toggle_requested.emit(paths, new_value)

    def _prompt_override(self, kind: str, rows: list[ItemRow]) -> None:
        labels = {
            "title": "Title override",
            "year": "Year override",
            "season": "Season override",
            "episode": "Episode override",
            "edition": "Edition override (e.g. Director's Cut)",
        }
        prompt = labels.get(kind, kind)
        if len(rows) == 1:
            note = f"Apply to '{rows[0].parsed.raw_filename}'."
        else:
            note = f"Apply to {len(rows)} selected rows."
        text, ok = QInputDialog.getText(
            self,
            f"Override {kind}",
            f"{prompt}\n\n{note}",
        )
        if not ok:
            return
        text = text.strip()
        for row in rows:
            kwargs: dict = {}
            if kind == "title":
                kwargs["title"] = text if text else None
            elif kind == "year":
                try:
                    kwargs["year"] = int(text) if text else None
                except ValueError:
                    continue
            elif kind == "season":
                try:
                    kwargs["season"] = int(text) if text else None
                except ValueError:
                    continue
            elif kind == "episode":
                try:
                    kwargs["episode"] = int(text) if text else None
                except ValueError:
                    continue
            elif kind == "edition":
                kwargs["edition"] = text if text else None
            self._model.set_manual_override(row.source_path, **kwargs)

    def _prompt_imdb_id(self, source_path: Path) -> None:
        # Defer the actual resolve to the orchestrator — emit a request
        # signal and let MainWindow pop the input dialog. Keeping the
        # prompt here would couple the panel to the orchestrator; the
        # signal pattern matches the rest of the panel's API.
        self.set_imdb_requested.emit(source_path)

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
                    # Linux: open the parent directory. xdg-open doesn't
                    # have a "highlight this file" mode that's portable.
                    subprocess.run(["xdg-open", str(path.parent)], check=False)
            except OSError:
                # Best-effort reveal; surface failures via the
                # MainWindow's error pane if needed in the future.
                continue

    def _copy_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        QApplication.clipboard().setText("\n".join(str(p) for p in paths))

    # ----- Test helpers ---------------------------------------------------

    def _leaf_count(self) -> int:
        count = 0
        for i in range(self._tree.topLevelItemCount()):
            count += self._tree.topLevelItem(i).childCount()
        return count

    def _group_count(self) -> int:
        return self._tree.topLevelItemCount()


__all__ = [
    "BADGE_CELL_ROLE",
    "GROUP_KEY_ROLE",
    "SOURCE_PATH_ROLE",
    "SourcePanel",
]
