"""Collision review widget.

Surfaces every :class:`Collision` from the planner and offers three
per-collision actions:

* **Keep both** — auto-rename the second source with ``_2``. The planner
  re-runs with the modified target and emits two distinct ops.
* **Keep first only** — skip the second source's op. The planner drops it.
* **Re-anchor** — open the show-anchor picker / edit pane so the user
  can rebind one of the sources to a different identifier.

No copy proceeds for either source until the user picks an action.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.gui.models import CollisionModel


class CollisionReview(QWidget):
    """Per-collision review widget."""

    # Emitted when the user picks an action for a collision.
    action_chosen = Signal(Path, str)  # target_path, action

    # Emitted when the user requests the re-anchor flow for a specific
    # collision; the main window opens the edit pane / show-anchor picker
    # on one of the colliding sources.
    reanchor_requested = Signal(Path)  # target

    def __init__(self, model: CollisionModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self.setAccessibleName("collision-review")

        self._list = QListWidget()
        self._list.itemSelectionChanged.connect(self._on_selection_changed)

        self._action_label = QLabel("Pick an action:")
        self._keep_both = QRadioButton("Keep both (auto-rename second with _2)")
        self._keep_first = QRadioButton("Keep first only (skip second)")
        self._reanchor = QRadioButton("Re-anchor (edit identifier)")
        self._actions = QButtonGroup(self)
        self._actions.addButton(self._keep_both)
        self._actions.addButton(self._keep_first)
        self._actions.addButton(self._reanchor)
        self._keep_both.toggled.connect(self._on_action_toggled)
        self._keep_first.toggled.connect(self._on_action_toggled)
        self._reanchor.toggled.connect(self._on_action_toggled)

        self._reanchor_btn = QPushButton("Open editor for re-anchor")
        self._reanchor_btn.clicked.connect(self._emit_reanchor)
        self._reanchor_btn.setEnabled(False)

        actions_row = QHBoxLayout()
        actions_row.addWidget(self._keep_both)
        actions_row.addWidget(self._keep_first)
        actions_row.addWidget(self._reanchor)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Unresolved target collisions:"))
        layout.addWidget(self._list)
        layout.addWidget(self._action_label)
        layout.addLayout(actions_row)
        layout.addWidget(self._reanchor_btn)

        self._model.reset.connect(self._refresh)
        self._refresh()

    # ----- Population -----------------------------------------------------

    def _refresh(self) -> None:
        self._list.clear()
        for it in self._model.items():
            sources = ", ".join(str(s) for s in it.sources)
            label = f"{it.target} <- {sources}  [{it.reason}]"
            li = QListWidgetItem(label)
            li.setData(0, str(it.target))
            self._list.addItem(li)
        # Reset action selection.
        self._actions.setExclusive(False)
        for btn in self._actions.buttons():
            btn.setChecked(False)
        self._actions.setExclusive(True)

    def _current_target(self) -> Path | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._model.items()):
            return None
        return self._model.items()[row].target

    # ----- Handlers -------------------------------------------------------

    def _on_selection_changed(self) -> None:
        # Reflect the selected collision's existing action onto the radios.
        target = self._current_target()
        self._actions.setExclusive(False)
        for btn in self._actions.buttons():
            btn.setChecked(False)
        self._actions.setExclusive(True)
        if target is None:
            self._reanchor_btn.setEnabled(False)
            return
        for it in self._model.items():
            if it.target == target:
                if it.action == "keep_both":
                    self._keep_both.setChecked(True)
                elif it.action == "keep_first":
                    self._keep_first.setChecked(True)
                elif it.action == "reanchor":
                    self._reanchor.setChecked(True)
                self._reanchor_btn.setEnabled(it.action == "reanchor")
                return

    def _on_action_toggled(self, checked: bool) -> None:
        if not checked:
            return
        target = self._current_target()
        if target is None:
            return
        if self._keep_both.isChecked():
            action = "keep_both"
        elif self._keep_first.isChecked():
            action = "keep_first"
        elif self._reanchor.isChecked():
            action = "reanchor"
        else:
            return
        self._model.set_action(target, action)
        self._reanchor_btn.setEnabled(action == "reanchor")
        self.action_chosen.emit(target, action)

    def _emit_reanchor(self) -> None:
        target = self._current_target()
        if target is not None:
            self.reanchor_requested.emit(target)


__all__ = ["CollisionReview"]
