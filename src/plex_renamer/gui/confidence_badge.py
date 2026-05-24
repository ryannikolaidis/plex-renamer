"""Color-coded confidence indicator.

Renders a small filled circle next to a row whose color is one of:

* green (``auto`` band, confidence >= 0.85)
* yellow (``review`` band, confidence >= 0.60)
* red (``unresolved`` band, no usable candidate)

The widget is a thin :class:`QLabel` subclass so it works in any list
view delegate path or as a sibling in a row layout. The accessible text
falls back to ``"auto"`` / ``"review"`` / ``"unresolved"`` for screen
readers; the color alone is not the only signal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from plex_renamer.gui.models import ConfidenceBand, color_for_band

BADGE_SIZE_PX = 12


class ConfidenceBadge(QLabel):
    """Small colored dot indicating the row's confidence band.

    The badge size is fixed at :data:`BADGE_SIZE_PX` to read cleanly
    next to one-line text. The QSS background-color drives the rendered
    color; we set it via inline stylesheet so the widget works without
    a global stylesheet loaded.
    """

    def __init__(self, band: ConfidenceBand = "unresolved", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._band: ConfidenceBand = band
        self.setFixedSize(BADGE_SIZE_PX, BADGE_SIZE_PX)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_style()
        self.setAccessibleName(f"confidence-{band}")

    # ----- Public API -----------------------------------------------------

    def set_band(self, band: ConfidenceBand) -> None:
        """Update the band and re-render."""
        self._band = band
        self._apply_style()
        self.setAccessibleName(f"confidence-{band}")

    def band(self) -> ConfidenceBand:
        return self._band

    # ----- Internals -------------------------------------------------------

    def _apply_style(self) -> None:
        color = color_for_band(self._band)
        radius = BADGE_SIZE_PX // 2
        self.setStyleSheet(
            f"background-color: {color}; border-radius: {radius}px; border: 1px solid #444;"
        )


__all__ = ["BADGE_SIZE_PX", "ConfidenceBadge"]
