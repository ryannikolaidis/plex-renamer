"""Drag-and-drop / file-picker zone for selecting input paths.

Accepts both directories and individual files via drag-and-drop. The
zone also exposes a button-driven file picker for users who prefer it.

Emits :attr:`paths_dropped` with a list of absolute :class:`Path`
objects; callers wire this to the planning pipeline.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Style applied during a drag-over so the user gets visual confirmation
# that the drop will be accepted. The default frame style is restored on
# DragLeave / Drop. Kept as a module-level constant so the test suite
# can assert the stylesheet swap if needed.
_DEFAULT_DROPZONE_STYLE = ""
_HOVER_DROPZONE_STYLE = (
    "QFrame#drop-zone { border: 2px solid #2d7dd2; background: rgba(45, 125, 210, 0.08); }"
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
        # An ObjectName is required so the hover stylesheet targets only
        # this frame and not every QFrame in the window.
        self.setObjectName("drop-zone")
        self._default_label_text = "Drop folders or files here, or click Choose..."

        self._label = QLabel(self._default_label_text)
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
            self._set_hover(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 — Qt override
        self._set_hover(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 — Qt override
        self._set_hover(False)
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

    def _set_hover(self, hovered: bool) -> None:
        if hovered:
            self.setStyleSheet(_HOVER_DROPZONE_STYLE)
            self._label.setText("Release to drop")
        else:
            self.setStyleSheet(_DEFAULT_DROPZONE_STYLE)
            self._label.setText(self._default_label_text)

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
