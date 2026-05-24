"""GUI application entry point.

Registered as ``plex-renamer-gui`` in ``pyproject.toml``. Builds a
:class:`QApplication`, loads persisted settings, applies the stylesheet,
and constructs the :class:`MainWindow`.

The entry point is intentionally minimal: every real plumbing decision
lives in :class:`MainWindow` so the headless tests can construct that
class directly without the application loop.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from plex_renamer.config.settings import Settings
from plex_renamer.gui.main_window import MainWindow

_STYLESHEET = Path(__file__).parent / "styles.qss"


def main(argv: list[str] | None = None) -> int:
    """Build the QApplication and run the event loop.

    Returns the process exit code.
    """
    args = sys.argv if argv is None else argv
    app = QApplication(args)
    if _STYLESHEET.exists():
        # If the stylesheet can't be read for some reason, fall back
        # to native styling rather than failing the launch.
        with contextlib.suppress(OSError):
            app.setStyleSheet(_STYLESHEET.read_text(encoding="utf-8"))
    settings = Settings.load()
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
