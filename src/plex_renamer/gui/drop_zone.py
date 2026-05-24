"""Drag-and-drop / file-picker zone for selecting input paths.

Accepts both directories and individual files via drag-and-drop. The
zone also exposes a button-driven file picker for users who prefer it.

Emits :attr:`paths_dropped` with a list of absolute :class:`Path`
objects; callers wire this to the planning pipeline.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DropZone(QFrame):
    """A drop target widget for source files and directories."""

    paths_dropped = Signal(list)  # list[Path]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(80)
        self.setAccessibleName("drop-zone")

        self._label = QLabel("Drop folders or files here, or click Choose...")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._choose_btn = QPushButton("Choose folder...")
        self._choose_btn.clicked.connect(self._open_picker)

        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._choose_btn)
        row.addStretch(1)
        layout.addLayout(row)

    # ----- Drag and drop --------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 — Qt override
        mime: QMimeData = event.mimeData()
        if mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 — Qt override
        urls = event.mimeData().urls()
        paths: list[Path] = []
        for u in urls:
            local = u.toLocalFile()
            if local:
                paths.append(Path(local))
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    # ----- File picker fallback ------------------------------------------

    def _open_picker(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if directory:
            self.paths_dropped.emit([Path(directory)])

    # ----- Test helpers ---------------------------------------------------

    def simulate_drop(self, paths: list[Path]) -> None:
        """Inject a drop result without a real Qt drag event.

        Intended for tests; production code uses :meth:`dropEvent`.
        """
        self.paths_dropped.emit(list(paths))


__all__ = ["DropZone"]
