"""End-to-end integration test driving the real production wiring.

The other GUI tests stub ``parse_fn`` / ``apply_fn`` directly, which
hides any divergence between the production wrappers (in
``plex_renamer.gui.app``) and the orchestrator they call. This test
constructs the SAME wrappers app.py builds, wires them into
:class:`MainWindow`, and drops a real synthetic tree through the drop
zone. It catches three regressions the headless tests miss:

1. The production ``_parse_fn`` returns ``list[ParseResult]`` so
   :meth:`MainWindow._on_paths_dropped` populates the model with rows.
2. ``Orchestrator.apply`` feeds ``plan.collisions`` into the collision
   model when the freshly-built plan has conflicts.
3. ``MainWindow._do_apply`` early-returns when the collision model has
   unresolved entries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


class _FakeTMDB:
    """In-memory TMDB stub mirroring the orchestrator's protocol."""

    def __init__(self) -> None:
        from plex_renamer.tmdb.models import Episode, MovieResult, TVResult

        self.search_movie_returns: list[MovieResult] = []
        self.search_tv_returns: list[TVResult] = []
        self.find_returns: MovieResult | TVResult | None = None
        self.get_season_returns: list[Episode] = []

    def search_movie(self, title: str, year: int | None):
        return list(self.search_movie_returns)

    def search_tv(self, title: str, year: int | None):
        return list(self.search_tv_returns)

    def find_by_imdb_id(self, imdb_id: str):
        return self.find_returns

    def get_season(self, tmdb_id: int, season: int):
        return list(self.get_season_returns)


def _build_window_with_real_wiring(tmp_path: Path, gui_settings):
    """Construct MainWindow via the SAME ``build_window`` helper app.py uses.

    Returns ``(window, fake_tmdb)`` so the test can inspect post-drop
    state and drive Apply directly. Calling through
    :func:`plex_renamer.gui.app.build_window` is load-bearing: that's
    the function the production entrypoint calls, so any future drift
    in the parse/apply/preview wrappers gets caught here.
    """
    from plex_renamer.gui.app import build_window
    from plex_renamer.gui.orchestrator import OrchestratorDeps
    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    tmdb = _FakeTMDB()
    resolver = IMDbFallbackResolver(tmdb, omdb_api_key=None)
    deps = OrchestratorDeps(
        tmdb=tmdb,
        resolver=resolver,
        movies_root=tmp_path / "movies",
        tv_root=tmp_path / "tv",
        journal_dir=tmp_path / "journals",
        cleanup_enabled=False,
    )
    window = build_window(gui_settings, deps)
    return window, tmdb


def test_drop_populates_model_via_real_parse_fn(qtbot, tmp_path, gui_settings) -> None:
    """Drop a movie file in: model has rows after the drop completes.

    Regression: production ``_parse_fn`` used to return ``[]`` and
    populate the model as a side effect; ``_on_paths_dropped`` then
    called ``set_rows([])`` which blasted the orchestrator's writes.
    After the fix, ``_parse_fn`` returns the parsed list and the model
    contains the row.
    """
    from plex_renamer.tmdb.models import MovieResult

    # Make a real on-disk movie file so parse_tree picks it up.
    movie_dir = tmp_path / "input"
    movie_dir.mkdir()
    movie_file = movie_dir / "The.Matrix.1999.mkv"
    movie_file.write_bytes(b"\x00")

    window, tmdb = _build_window_with_real_wiring(tmp_path, gui_settings)
    qtbot.addWidget(window)
    tmdb.search_movie_returns = [MovieResult(tmdb_id=603, title="The Matrix", year=1999)]

    # Drive the drop directly through the handler the DropZone calls.
    window._on_paths_dropped([movie_dir])

    rows = window.item_model().rows()
    assert len(rows) == 1
    assert rows[0].parsed.kind == "movie"
    # The orchestrator's parsed_inputs slot ran resolve_rows; the row
    # carries its TMDB candidate now.
    assert rows[0].candidate is not None
    assert rows[0].candidate.anchor_id == "603"


def test_apply_populates_collision_model_and_blocks(
    qtbot, tmp_path, gui_settings, monkeypatch
) -> None:
    """Two files resolving to the same target produce a collision the user must resolve.

    Regression: production ``Orchestrator.apply`` used to discard
    ``plan.collisions``. The collision model stayed empty even when
    the planner emitted conflicts, so the pre-apply gate never
    triggered and the user never saw the conflict in the review
    panel. After the fix, Apply populates the collision model and
    returns a zero-count RunReport WITHOUT calling apply_plan.
    """
    from plex_renamer.tmdb.models import MovieResult

    # Two distinct source files that both resolve to "The Matrix (1999)".
    # The planner targets ``<movies_root>/The Matrix (1999) {tmdb-603}/...``
    # for both; that's a same-anchor-different-source collision.
    movie_dir = tmp_path / "input"
    movie_dir.mkdir()
    (movie_dir / "Matrix.1999.mkv").write_bytes(b"\x00")
    (movie_dir / "TheMatrix.1999.1080p.mkv").write_bytes(b"\x00")

    window, tmdb = _build_window_with_real_wiring(tmp_path, gui_settings)
    qtbot.addWidget(window)
    tmdb.search_movie_returns = [MovieResult(tmdb_id=603, title="The Matrix", year=1999)]

    # Drop, populate the model.
    window._on_paths_dropped([movie_dir])

    rows = window.item_model().rows()
    assert len(rows) == 2  # proves Must-fix 1 even in the collision case

    # Click Apply through the production path.
    window._on_apply_clicked()

    # Must-fix 2: the collision model is populated and not all-resolved.
    collisions = window.collision_model()
    assert len(collisions) == 1, (
        f"expected 1 collision, got {len(collisions)} (Apply discarded plan.collisions)"
    )
    assert collisions.all_resolved() is False

    # Apply was the FIRST click; the report widget stays in its initial
    # state because the orchestrator returned a zero-count report
    # without calling apply_plan. The apply_button is still enabled —
    # the user resolves collisions and clicks Apply again.

    # Build a sentinel by inspecting that _last_journal is still None;
    # apply_plan would have populated it.
    assert window._last_journal is None

    # Must-fix 2 (continued): second click without resolving — the
    # MainWindow's pre-apply gate refuses to call apply_fn. We stub
    # QMessageBox.warning so the modal doesn't block the headless
    # event loop, and we monkey-patch apply_fn to assert it was NOT
    # called.
    warning_calls: list = []
    monkeypatch.setattr(
        "plex_renamer.gui.main_window.QMessageBox.warning",
        lambda *a, **k: warning_calls.append((a, k)) or 0,
    )
    apply_called = []
    original_apply_fn = window._apply_fn

    def _spy_apply_fn(model, root):
        apply_called.append((model, root))
        return original_apply_fn(model, root)

    window._apply_fn = _spy_apply_fn  # type: ignore[assignment]

    window._on_apply_clicked()

    # Gate fired: QMessageBox.warning was called, apply_fn was NOT.
    assert len(warning_calls) == 1
    assert apply_called == []
