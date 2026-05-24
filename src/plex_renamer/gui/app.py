"""GUI application entry point.

Registered as ``plex-renamer-gui`` in ``pyproject.toml``. Builds a
:class:`QApplication`, loads persisted settings, constructs the engine
collaborators, wires the :class:`Orchestrator` to a fresh
:class:`MainWindow`, and runs the event loop.

The plumbing decisions live in :class:`Orchestrator`; this entry point
is a thin "load settings, prompt for missing API key, build the window"
adapter. Headless tests construct the window directly with custom
``parse_fn`` / ``apply_fn`` and skip this module entirely.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from plex_renamer.config.paths import app_config_dir
from plex_renamer.config.settings import Settings
from plex_renamer.executor.journal import JOURNAL_SUBDIR
from plex_renamer.gui.main_window import MainWindow
from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
from plex_renamer.tmdb.cache import TMDBCache
from plex_renamer.tmdb.client import TMDBClient
from plex_renamer.tmdb.fallback import IMDbFallbackResolver

_STYLESHEET = Path(__file__).parent / "styles.qss"


class _TMDBKeyPromptDialog(QDialog):
    """First-run dialog asking the user for a TMDB v3 API key.

    Shown only when ``Settings.tmdb_api_key`` is missing. The dialog
    blocks: there is no usable engine without a key, so we either get
    one or refuse to open the main window.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TMDB API key required")
        self.setModal(True)
        info = QLabel(
            "plex-renamer needs a TMDB v3 API key to identify movies and TV shows.\n"
            "Get one at https://www.themoviedb.org/settings/api and paste it below."
        )
        info.setWordWrap(True)
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("v3 API key")
        self._key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self._key_input)
        layout.addWidget(buttons)

    def key(self) -> str:
        return self._key_input.text().strip()


def _ensure_tmdb_key(settings: Settings) -> str | None:
    """Return a usable TMDB key, prompting the user when missing.

    Returns ``None`` when the user cancels — the caller refuses to open
    the main window in that case.
    """
    if settings.tmdb_api_key:
        return settings.tmdb_api_key
    dlg = _TMDBKeyPromptDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    key = dlg.key()
    if not key:
        return None
    settings.set_tmdb_api_key(key)
    return key


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

    tmdb_key = _ensure_tmdb_key(settings)
    if tmdb_key is None:
        QMessageBox.critical(
            None,
            "Cannot start plex-renamer",
            "A TMDB API key is required. Re-open the app to retry.",
        )
        return 1

    # Engine wiring. The cache decorates the bare client so identical
    # search queries don't burn the TMDB free-tier rate-limit on every
    # paint cycle.
    tmdb_client = TMDBClient(tmdb_key)
    tmdb = TMDBCache(tmdb_client)
    resolver = IMDbFallbackResolver(tmdb, omdb_api_key=settings.omdb_api_key)

    movies_root = Path(settings.movies_root) if settings.movies_root else Path.home() / "Movies"
    tv_root = Path(settings.tv_root) if settings.tv_root else Path.home() / "TV"
    journal_dir = app_config_dir() / JOURNAL_SUBDIR

    # Construct the orchestrator BEFORE the window so we can pass its
    # parse/apply methods straight into the window's constructor.
    # We bind the model later via ``MainWindow.item_model()`` after
    # construction.
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=movies_root,
        tv_root=tv_root,
        journal_dir=journal_dir,
        cleanup_enabled=settings.cleanup_enabled,
    )

    # The window needs a parse_fn that runs parse+resolve in one go so
    # the source panel renders WITH candidates. The orchestrator's
    # ``parse_and_resolve`` walks the tree, populates the model, and
    # runs resolution; we then return the ParseResults so MainWindow's
    # existing _on_paths_dropped logic stays a no-op (the rows are
    # already in the model). Returning an empty list short-circuits
    # MainWindow's row-build pass.
    orchestrator_holder: dict[str, Orchestrator] = {}

    def _parse_fn(path: Path) -> list:
        orch = orchestrator_holder["orch"]
        # Set the user's drop root explicitly so cleanup and apply use
        # it instead of guessing from the first row's parent.
        window.set_input_root(path)
        orch.parse_and_resolve(path)
        return []

    def _apply_fn(model, input_root):
        orch = orchestrator_holder["orch"]
        return orch.apply(model, input_root)

    window = MainWindow(settings, parse_fn=_parse_fn, apply_fn=_apply_fn)
    orchestrator = Orchestrator(window.item_model(), deps, main_window=window)
    orchestrator_holder["orch"] = orchestrator
    orchestrator.connect(window)

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
