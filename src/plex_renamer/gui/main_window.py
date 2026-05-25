"""Top-level main window.

Composes the drop zone, the two panels, the edit pane (in a dock-like
side area), the run-report widget, and a bottom action bar. The window
is a THIN orchestrator: it owns the :class:`ItemModel` and
:class:`CollisionModel`, wires panel signals to the model and to engine
callables, and dispatches Apply / Undo to the executor.

Engine plumbing
---------------

The main window does not import the executor directly when running
under tests; callers can inject a custom ``apply_callback`` /
``resolve_movie`` / ``resolve_tv`` so headless tests don't need TMDB or
a real disk. The default ``main()`` entrypoint wires the real engine.

State machine
-------------

1. Idle (nothing dropped).
2. Parsing — drop zone fired; the orchestrator walks ``parse_tree`` and
   resolves candidates per row.
3. Reviewing — the panels show rows; the user edits / picks shows.
4. Applying — the user clicked Apply; the cleanup modal pops if
   cleanup is enabled.
5. Reporting — the run-report widget shows results + undo button.

Slices 1-4 own the engine surfaces; this window is the assembly layer.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from plex_renamer.config.settings import Settings
from plex_renamer.executor.cleanup import deletion_preview
from plex_renamer.gui.cleanup_confirm_modal import CleanupConfirmModal
from plex_renamer.gui.collision_review import CollisionReview
from plex_renamer.gui.drop_zone import DropZone
from plex_renamer.gui.edit_pane import EditPane
from plex_renamer.gui.models import CollisionModel, ItemModel, ItemRow, RunReport
from plex_renamer.gui.run_report import RunReportWidget
from plex_renamer.gui.settings_dialog import SettingsDialog
from plex_renamer.gui.source_panel import SourcePanel
from plex_renamer.gui.target_panel import TargetPanel
from plex_renamer.parser.extract import parse_tree
from plex_renamer.parser.models import ParseResult

# A function that takes a list of input paths and yields ParseResults.
# Tests inject a deterministic one; production wires ``parse_tree``.
ParseFn = Callable[[Path], list[ParseResult]]

# Apply callback signature: receives the list of (parsed, candidate)-derived
# RenameOps; returns a RunReport summarizing the executor's output. Kept
# abstract so headless tests can stub the executor entirely.
ApplyFn = Callable[[ItemModel, Path], RunReport]

# Preview callback signature: builds the plan without applying. Populates
# the target panel + collision model as a side effect; the return value
# is opaque (the planner's RenamePlan) for tests that want to inspect it.
PreviewFn = Callable[[ItemModel, Path], object]


def _default_parse(input_root: Path) -> list[ParseResult]:
    return list(parse_tree(input_root))


class MainWindow(QMainWindow):
    """Composed main window for the review UI."""

    # Test hooks: emitted at major phase transitions so tests can wait
    # without driving full engine plumbing.
    parsed_inputs = Signal(int)  # number of rows
    applied = Signal()
    undone = Signal(Path)  # journal_path

    # Orchestrator-facing signals: re-emitted from the contained widgets
    # so the main_window is the single seam an external orchestrator
    # subscribes to. The orchestrator runs TMDB / IMDb resolution, opens
    # the show-anchor picker, and re-routes the edit-pane on a re-anchor
    # request; none of that lives in MainWindow itself.
    tmdb_search_requested = Signal(Path, str)  # source_path, query
    imdb_resolve_requested = Signal(Path, str)  # source_path, imdb_id
    group_clicked = Signal(str)  # group_key
    reanchor_requested = Signal(Path)  # collision target

    def __init__(
        self,
        settings: Settings,
        *,
        parse_fn: ParseFn | None = None,
        apply_fn: ApplyFn | None = None,
        preview_fn: PreviewFn | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("plex-renamer")
        self.setMinimumSize(1100, 700)
        # Default window geometry. The minimum keeps the layout
        # functional on small screens; the resize gives the right pane
        # enough room not to clip the run-report's Errors list and the
        # collision review widget on a fresh launch.
        self.resize(1800, 1000)
        self._settings = settings
        self._parse_fn: ParseFn = parse_fn or _default_parse
        self._apply_fn: ApplyFn | None = apply_fn
        self._preview_fn: PreviewFn | None = preview_fn

        # Models.
        self._item_model = ItemModel(self)
        self._collision_model = CollisionModel(self)
        self._last_journal: Path | None = None
        # Explicit input root set by the orchestrator when the user drops
        # a folder; falls back to the first row's parent if unset.
        self._input_root: Path | None = None

        # Widgets.
        self._drop_zone = DropZone()
        self._drop_zone.paths_dropped.connect(self._on_paths_dropped)

        self._source_panel = SourcePanel(self._item_model)
        self._target_panel = TargetPanel(self._item_model)
        self._edit_pane = EditPane(self._item_model)
        self._collision_review = CollisionReview(self._collision_model)
        self._run_report = RunReportWidget()
        self._run_report.undo_requested.connect(self._on_undo_requested)

        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setObjectName("preview-btn")
        self._preview_btn.clicked.connect(self._on_preview_clicked)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("apply-btn")
        self._apply_btn.clicked.connect(self._on_apply_clicked)

        self._settings_btn = QPushButton("Settings...")
        self._settings_btn.clicked.connect(self._open_settings)

        # Wire panels to edit pane.
        self._source_panel.row_clicked.connect(self._on_row_clicked)
        self._target_panel.row_clicked.connect(self._on_row_clicked)

        # Re-emit child-widget signals at the main_window level so a
        # single orchestrator subscribes once instead of reaching into
        # every panel. The MainWindow itself does nothing with these
        # signals; it only forwards them.
        self._edit_pane.tmdb_search_requested.connect(self.tmdb_search_requested)
        self._edit_pane.imdb_resolve_requested.connect(self.imdb_resolve_requested)
        self._source_panel.group_clicked.connect(self.group_clicked)
        self._collision_review.reanchor_requested.connect(self.reanchor_requested)

        self._build_layout()

    # ----- Layout ---------------------------------------------------------

    def _build_layout(self) -> None:
        # Two-panel splitter: source | target.
        panels = QSplitter()
        panels.addWidget(self._source_panel)
        panels.addWidget(self._target_panel)
        panels.setSizes([700, 700])

        # Right side: edit pane stacked above collision review and run
        # report. We use a splitter so the user can resize.
        side = QSplitter()
        side.setOrientation(Qt.Orientation.Vertical)
        side.addWidget(self._edit_pane)
        side.addWidget(self._collision_review)
        side.addWidget(self._run_report)

        # Body splitter: panels on the left, side on the right.
        body = QSplitter()
        body.addWidget(panels)
        body.addWidget(side)
        body.setSizes([1100, 600])

        # Bottom bar: settings on the left, Preview + Apply on the right.
        bottom = QHBoxLayout()
        bottom.addWidget(self._settings_btn)
        bottom.addStretch(1)
        bottom.addWidget(self._preview_btn)
        bottom.addWidget(self._apply_btn)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._drop_zone)
        layout.addWidget(body, stretch=1)
        layout.addLayout(bottom)
        self.setCentralWidget(central)

    # ----- Drop handling --------------------------------------------------

    def _on_paths_dropped(self, paths: list[Path]) -> None:
        rows: list[ItemRow] = []
        for p in paths:
            for parsed in self._parse_fn(p):
                if parsed.kind == "unknown" or parsed.skip_reason is not None:
                    continue
                rows.append(ItemRow(parsed=parsed))
        self._item_model.set_rows(rows)
        self.parsed_inputs.emit(len(rows))

    # ----- Row click ------------------------------------------------------

    def _on_row_clicked(self, source_path: Path) -> None:
        self._edit_pane.load_row(source_path)

    # ----- Settings -------------------------------------------------------

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, parent=self)
        dlg.exec()

    # ----- Preview --------------------------------------------------------

    def _on_preview_clicked(self) -> None:
        """Build the plan via the orchestrator without applying.

        Populates the target panel + collision model as a side effect
        through ``preview_fn``. The button is a no-op when no preview_fn
        is wired (e.g., headless tests that don't exercise this flow).
        """
        if self._preview_fn is None:
            return
        self._preview_fn(self._item_model, self._settings_root_or_default())

    # ----- Apply ----------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        if self._settings.cleanup_enabled:
            sources = [r.parsed.source_path for r in self._item_model.rows() if not r.skip]
            # The modal must show EVERY path that will disappear, not just
            # the source files. ``deletion_preview`` adds the parent dirs
            # the executor's cleanup pass will prune up the chain so the
            # user's consent matches the actual deletion set.
            preview = deletion_preview(sources, self._settings_root_or_default())
            paths_to_show = preview if preview else sources
            modal = CleanupConfirmModal(paths_to_show, parent=self)
            modal.confirmed.connect(self._do_apply)
            modal.exec()
        else:
            self._do_apply()

    def _do_apply(self) -> None:
        if (
            self._collision_review
            and not self._collision_model.all_resolved()
            and len(self._collision_model) > 0
        ):
            QMessageBox.warning(
                self,
                "Unresolved collisions",
                "Resolve all collisions in the review panel before applying.",
            )
            return
        if self._apply_fn is None:
            # No engine wired (e.g. running in headless test without an
            # apply_fn). Emit the signal but skip the actual call.
            self.applied.emit()
            return
        # The apply_fn is the engine-side adapter; it returns a RunReport
        # for the GUI to render.
        report = self._apply_fn(self._item_model, self._settings_root_or_default())
        self._last_journal = report.journal_path
        self._run_report.set_report(report)
        self.applied.emit()

    def _settings_root_or_default(self) -> Path:
        """Return a usable input_root for the apply pass.

        The orchestrator's apply_fn typically wants the user's drop root
        as input_root. We default to the first row's parent when nothing
        else is available.
        """
        if self._input_root is not None:
            return self._input_root
        rows = self._item_model.rows()
        if rows:
            return rows[0].source_path.parent
        return Path.cwd()

    def set_input_root(self, input_root: Path) -> None:
        """Set the input_root explicitly.

        Called by the orchestrator when the user drops a folder so the
        cleanup preview and apply pass operate on the user-supplied root
        rather than a first-row-parent heuristic.
        """
        self._input_root = input_root

    def input_root(self) -> Path | None:
        return self._input_root

    # ----- Undo -----------------------------------------------------------

    def _on_undo_requested(self, journal_path: Path) -> None:
        # The undo execution itself is engine code; the main window only
        # surfaces the request via :attr:`undone`. The default app entry
        # point connects this to ``undo_batch``.
        self.undone.emit(journal_path)

    # ----- Test accessors -------------------------------------------------

    def item_model(self) -> ItemModel:
        return self._item_model

    def collision_model(self) -> CollisionModel:
        return self._collision_model

    def drop_zone(self) -> DropZone:
        return self._drop_zone

    def source_panel(self) -> SourcePanel:
        return self._source_panel

    def target_panel(self) -> TargetPanel:
        return self._target_panel

    def edit_pane(self) -> EditPane:
        return self._edit_pane

    def collision_review(self) -> CollisionReview:
        return self._collision_review

    def run_report_widget(self) -> RunReportWidget:
        return self._run_report

    def apply_button(self) -> QPushButton:
        return self._apply_btn

    def preview_button(self) -> QPushButton:
        return self._preview_btn


__all__ = ["ApplyFn", "MainWindow", "ParseFn", "PreviewFn"]
