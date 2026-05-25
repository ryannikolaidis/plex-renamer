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


def build_window(
    settings: Settings,
    deps: OrchestratorDeps | None = None,
    *,
    tmdb_override: object | None = None,
) -> tuple[MainWindow, Orchestrator]:
    """Assemble :class:`MainWindow` + :class:`Orchestrator` + wrappers.

    Returns ``(window, orchestrator)`` so callers can drive the
    orchestrator directly (integration tests bypass the parsed_inputs
    signal to inspect intermediate state). Extracted from :func:`main`
    so an integration test can exercise the EXACT same wrappers
    production builds — any divergence between the test's wiring and
    production's wiring would otherwise let regressions sneak through
    the headless tests (e.g., a ``_parse_fn`` shape drift).

    Two construction styles:

    * Production: caller builds an :class:`OrchestratorDeps` with a real
      TMDB cache / resolver / library roots and passes it via ``deps``.
    * Integration tests: caller passes ``tmdb_override`` (a fake
      implementing the ``_TMDBLike`` protocol). The function constructs
      a default deps bundle pointed at the settings' library roots and
      uses the override in place of a real TMDB client.

    The wrappers themselves are intentionally trivial: they exist to
    bind the orchestrator's ``parse`` / ``apply`` / ``preview`` methods
    into the ``MainWindow`` constructor.
    """
    if deps is None:
        if tmdb_override is None:
            raise ValueError("build_window requires either deps or tmdb_override")
        # Build a default deps bundle so integration tests can pass
        # only the TMDB fake. The library roots come from settings;
        # journal_dir is namespaced under the movies root so the
        # filesystem layout the test sees mirrors production.
        movies_root = Path(settings.movies_root) if settings.movies_root else Path.home() / "Movies"
        tv_root = Path(settings.tv_root) if settings.tv_root else Path.home() / "TV"
        resolver = IMDbFallbackResolver(tmdb_override, omdb_api_key=settings.omdb_api_key)
        deps = OrchestratorDeps(
            tmdb=tmdb_override,  # type: ignore[arg-type]
            resolver=resolver,
            movies_root=movies_root,
            tv_root=tv_root,
            journal_dir=movies_root / ".plex-renamer-journals",
            cleanup_enabled=settings.cleanup_enabled,
        )

    orchestrator_holder: dict[str, Orchestrator] = {}

    # The window's ``parse_fn`` is typed as
    # ``Callable[[Path], list[ParseResult]]``. ``MainWindow._on_paths_dropped``
    # wraps each non-skipped result in an ``ItemRow`` and calls
    # ``item_model.set_rows(rows)`` exactly once, then emits
    # ``parsed_inputs``. The orchestrator subscribes to that signal in
    # :meth:`Orchestrator.connect` and runs resolution against the
    # seated rows; the production flow and the headless tests therefore
    # use the SAME parse_fn shape (return-the-list).
    def _parse_fn(path: Path) -> list:
        # Set the user's drop root explicitly so cleanup and apply use
        # it instead of guessing from the first row's parent.
        window.set_input_root(path)
        orch = orchestrator_holder["orch"]
        return orch.parse(path)

    def _apply_fn(model, input_root):
        orch = orchestrator_holder["orch"]
        return orch.apply(model, input_root)

    def _preview_fn(model, input_root):
        orch = orchestrator_holder["orch"]
        return orch.preview(model, input_root)

    window = MainWindow(settings, parse_fn=_parse_fn, apply_fn=_apply_fn, preview_fn=_preview_fn)
    orchestrator = Orchestrator(window.item_model(), deps, main_window=window)
    orchestrator_holder["orch"] = orchestrator
    orchestrator.connect(window)
    return window, orchestrator


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

    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=movies_root,
        tv_root=tv_root,
        journal_dir=journal_dir,
        cleanup_enabled=settings.cleanup_enabled,
    )

    window, _orchestrator = build_window(settings, deps)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
