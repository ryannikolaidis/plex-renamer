"""Preview button on the bottom bar.

The UX brief specifies a "Preview -> Apply" button pair. Preview builds
the plan without applying: it populates the target panel + collision
model and refreshes confidence badges. Apply (covered elsewhere)
executes the already-built plan.

These tests verify:

* The button exists in the main window's bottom bar.
* Clicking Preview invokes the configured ``preview_fn`` with the
  item model and the resolved input root.
* When the preview wires through the real orchestrator, clicking
  Preview populates the collision model.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_preview_button_exists(qtbot, gui_settings) -> None:
    """The bottom bar exposes a Preview button alongside Apply."""
    from PySide6.QtWidgets import QPushButton

    from plex_renamer.gui.main_window import MainWindow

    win = MainWindow(gui_settings)
    qtbot.addWidget(win)
    btn = win.preview_button()
    assert isinstance(btn, QPushButton)
    assert btn.text() == "Preview"


def test_preview_click_invokes_preview_fn(qtbot, tmp_path, gui_settings) -> None:
    """Clicking Preview calls preview_fn with the model + resolved root."""
    from plex_renamer.gui.main_window import MainWindow
    from plex_renamer.gui.models import ItemRow
    from plex_renamer.parser.models import ParseResult

    captured: dict = {}

    def fake_preview(model, input_root):
        captured["model"] = model
        captured["input_root"] = input_root
        return None

    win = MainWindow(gui_settings, preview_fn=fake_preview)
    qtbot.addWidget(win)

    # Seed the model with a row so the resolved input_root falls back
    # to the row's parent (no explicit set_input_root).
    src = tmp_path / "movie.mkv"
    src.touch()
    row = ItemRow(
        parsed=ParseResult(
            source_path=src,
            kind="movie",
            title_candidate="Foo",
            year=2020,
            raw_filename=src.name,
        )
    )
    win.item_model().set_rows([row])

    win.preview_button().click()

    assert captured.get("model") is win.item_model()
    assert captured.get("input_root") == tmp_path


def test_preview_populates_target_panel_and_collisions(qtbot, tmp_path, gui_settings) -> None:
    """Preview drives the real orchestrator and surfaces collisions.

    Wires the same way ``app.py`` does so the test catches divergence
    between the production preview wrapper and the orchestrator's
    behaviour.
    """
    from plex_renamer.gui.main_window import MainWindow
    from plex_renamer.gui.models import ItemRow
    from plex_renamer.gui.orchestrator import Orchestrator, OrchestratorDeps
    from plex_renamer.parser.models import ParseResult
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver
    from plex_renamer.tmdb.models import Candidate

    class _FakeTMDB:
        def search_movie(self, *_a, **_k):
            return []

        def search_tv(self, *_a, **_k):
            return []

        def find_by_imdb_id(self, *_a, **_k):
            return None

        def get_season(self, *_a, **_k):
            return []

    tmdb = _FakeTMDB()
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=IMDbFallbackResolver(tmdb, omdb_api_key=None),
        movies_root=tmp_path / "movies",
        tv_root=tmp_path / "tv",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
    )

    holder: dict = {}

    def _preview_fn(model, input_root):
        return holder["orch"].preview(model, input_root)

    win = MainWindow(gui_settings, preview_fn=_preview_fn)
    qtbot.addWidget(win)
    orch = Orchestrator(win.item_model(), deps, main_window=win)
    holder["orch"] = orch

    # Two rows with the SAME candidate (same TMDB id) and same year
    # forces a target collision in the movie planner.
    candidate = Candidate(
        anchor_kind="tmdb",
        anchor_id="603",
        kind="movie",
        title="The Matrix",
        year=1999,
        confidence=0.9,
    )
    src_a = tmp_path / "a.mkv"
    src_b = tmp_path / "b.mkv"
    src_a.touch()
    src_b.touch()
    rows = [
        ItemRow(
            parsed=ParseResult(
                source_path=src_a,
                kind="movie",
                title_candidate="The Matrix",
                year=1999,
                raw_filename=src_a.name,
            ),
            candidate=candidate,
        ),
        ItemRow(
            parsed=ParseResult(
                source_path=src_b,
                kind="movie",
                title_candidate="The Matrix",
                year=1999,
                raw_filename=src_b.name,
            ),
            candidate=candidate,
        ),
    ]
    win.item_model().set_rows(rows)
    win.set_input_root(tmp_path)

    # Pre-click sanity: no collisions yet.
    assert len(win.collision_model()) == 0

    win.preview_button().click()

    # Preview populated the collision model with the one shared-target
    # conflict.
    assert len(win.collision_model()) == 1
    # Both colliding rows are stripped from the plan, so neither has
    # a proposed_op (matches the planner's collision-stripping shape).
    rows_after = win.item_model().rows()
    assert all(r.proposed_op is None for r in rows_after)
