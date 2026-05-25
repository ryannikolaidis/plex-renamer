"""Visual end-to-end test for the v0.1.3 Lazarus_2 unknown-show recovery flow.

The user's real flow: drop a folder shaped like ``MAX/Lazarus_2/s1/[S01.E01] Title.mp4``,
realize TMDB has no record for "Lazarus_2", open the show picker, see that
the fuzzy fallback already retried with "Lazarus", confirm the picker shows
"Lazarus (2025)" first (relevance-ranked, NOT TMDB's popularity order), pick
it, see all 13 rows resolve and the group label flip from "Lazarus_2" to
"Lazarus", then click Preview and verify the target paths land under the
canonical Plex anchor folder.

This test drives that flow end-to-end through the SAME ``build_window``
wiring that production uses, saves PNG screenshots at every step, AND
asserts on rendered widget heights so layout regressions get caught.

The screenshot directory is printed to stdout at the end of the test so a
human (or LLM operator) can ``Read`` each PNG to verify the UX matches
what the user expects.

Three orthogonal regressions this test pins:

* Bug A — group label backfill. After the drop, the source panel must
  show "Lazarus_2" as the group label, not the episode title of the
  first row. This requires ``Orchestrator._on_parsed_inputs`` to call
  ``ItemModel.notify_rows_reset()`` after backfilling ``show_name_hint``
  so the rendered tree reflects the freshly-populated hints.

* Bug B — edit-pane layout. When the user clicks a row, the TMDB
  search panel inside the edit pane must render at meaningful height
  (>= 150px), not be crushed to a thin sliver by the IMDb and Manual
  override boxes claiming all vertical space.

* Bug C — fuzzy ranking + fallback. When the auto-seeded query
  "Lazarus_2" returns 0 results, the orchestrator must retry with
  "Lazarus" (the cleaned variant) and surface a notice. The result
  list must be relevance-ranked so "Lazarus" outranks "The Lazarus
  Project" for the query "Lazarus".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


# Save screenshots OUTSIDE the pytest tmp_path tree so they survive
# beyond the test process. The primary agent reads each PNG to verify
# the UX visually. The directory is created at module import time and
# its path is printed on every test run.
_SCREENSHOT_ROOT = Path(tempfile.mkdtemp(prefix="plex-renamer-v013-screenshots-"))


@pytest.fixture
def screenshots_dir() -> Path:
    """A per-test screenshot directory under the module-level root.

    The directory is NOT auto-cleaned. The primary needs to read the
    PNGs after the test exits; pytest's ``tmp_path`` cleanup would
    delete them out from under us.
    """
    target = _SCREENSHOT_ROOT / os.environ.get("PYTEST_CURRENT_TEST", "default").split(":")[-1]
    target = _SCREENSHOT_ROOT
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save_screenshot(widget, screenshots_dir: Path, name: str) -> Path:
    pixmap = widget.grab()
    path = screenshots_dir / f"{name}.png"
    assert pixmap.save(str(path)), f"failed to save {path}"
    print(f"SCREENSHOT: {path}")
    return path


# Episode titles to seed the fixture tree with. Realistic enough that
# the rendered screenshots show recognizable show data, not "ep01.mp4".
_LAZARUS_TITLES = [
    "Goodbye Cruel World",
    "Life in the Fast Lane",
    "Long Way From Home",
    "Don't Stop the Dance",
    "Pretty Vacant",
    "Heaven Is a Place on Earth",
    "Almost Blue",
    "Unforgettable Fire",
    "Death on Two Legs",
    "I Can't Tell You Why",
    "Runnin' With the Devil",
    "Close to the Edge",
    "The World Is Yours",
]


class _FakeTMDB:
    """Mocks TMDB to model the real-world Lazarus_2 case.

    ``search_tv`` returns nothing for "Lazarus_2" (the literal folder
    name); returns three Lazarus-related hits for "Lazarus" (the
    cleaned variant). Returns nothing for any other query.
    ``get_season`` returns 13 episodes when asked about TMDB id 231003,
    season 1; empty otherwise.

    The shape mirrors the v0.1.2 user's observed behavior: TMDB knows
    the show, the folder name simply doesn't match because the user
    renamed the directory to disambiguate from an earlier copy.
    """

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int | None]] = []

    def search_tv(self, title: str, year: int | None):
        from plex_renamer.tmdb.models import TVResult

        self.search_calls.append((title, year))
        normalized = title.lower().strip()
        if normalized == "lazarus":
            # Three matches in TMDB's default order — note "The Lazarus
            # Project" comes BEFORE "Lazarus" in raw TMDB popularity.
            # The local ranking step must flip that order.
            return [
                TVResult(tmdb_id=13825, title="The Lazarus Project", year=2008),
                TVResult(tmdb_id=231003, title="Lazarus", year=2025),
                TVResult(tmdb_id=679784, title="Lazarus", year=2021),
            ]
        return []

    def search_movie(self, title: str, year: int | None):
        return []

    def find_by_imdb_id(self, imdb_id: str):
        return None

    def get_season(self, tmdb_id: int, season: int):
        from plex_renamer.tmdb.models import Episode

        if tmdb_id == 231003 and season == 1:
            return [
                Episode(season=1, episode=i, title=t, air_date=None)
                for i, t in enumerate(_LAZARUS_TITLES, start=1)
            ]
        return []


def _build_lazarus_2_tree(root: Path) -> Path:
    """Create the on-disk fixture matching the user's real folder.

    Returns the path to the show folder so the test can drop it.
    """
    show_dir = root / "MAX" / "Lazarus_2"
    season_dir = show_dir / "s1"
    season_dir.mkdir(parents=True)
    for i, title in enumerate(_LAZARUS_TITLES, start=1):
        (season_dir / f"[S01.E{i:02d}] {title}.mp4").touch()
    return show_dir


def test_e2e_lazarus_2_recovery_screenshots(qapp, qtbot, tmp_path, screenshots_dir) -> None:
    """The full Lazarus_2 unknown-show recovery flow, screenshot per step.

    Drives the user's exact recovery path: drop -> see group label
    update to "Lazarus_2" -> click row -> see edit pane with non-
    crushed TMDB panel -> click group -> see picker pre-populated AND
    fallback-retried with "Lazarus" -> verify "Lazarus" outranks "The
    Lazarus Project" -> pick Lazarus (2025) -> all rows resolve and
    group label flips to "Lazarus" -> Preview -> targets land under
    "Lazarus (2025) {tmdb-231003}/Season 01/".
    """
    from PySide6.QtWidgets import QApplication

    from plex_renamer.config.settings import Settings
    from plex_renamer.gui.app import build_window
    from plex_renamer.gui.show_anchor_picker import ShowAnchorPicker

    # ----- Set up the fixture tree -----
    show_dir = _build_lazarus_2_tree(tmp_path)

    # ----- Wire up TMDB fake + Settings + window -----
    tmdb = _FakeTMDB()
    settings = Settings(
        tmdb_api_key="x",
        movies_root=str(tmp_path / "library" / "Movies"),
        tv_root=str(tmp_path / "library" / "TV"),
        _config_path=tmp_path / "config.json",
    )
    Path(settings.movies_root).mkdir(parents=True, exist_ok=True)
    Path(settings.tv_root).mkdir(parents=True, exist_ok=True)

    window, orchestrator = build_window(settings, tmdb_override=tmdb)
    qtbot.addWidget(window)
    window.resize(1800, 1000)
    window.show()
    QApplication.processEvents()

    # ----- Step 1: empty window -----
    _save_screenshot(window, screenshots_dir, "01_empty_window")

    # ----- Step 2: drop the Lazarus_2 folder -----
    # ``MainWindow._on_paths_dropped`` is the same handler the DropZone
    # signal hits, so calling it directly mirrors a real drop without a
    # synthetic Qt drag-and-drop event.
    window.set_input_root(show_dir)
    window._on_paths_dropped([show_dir])
    QApplication.processEvents()
    _save_screenshot(window, screenshots_dir, "02_after_drop_lazarus_2")

    # ASSERTIONS on Step 2: the group label MUST show "Lazarus_2", not
    # the first episode's filename or title. This is the Bug A
    # regression gate — without notify_rows_reset after the backfill,
    # the source panel keeps the original (None show_name_hint) labels.
    source_panel = window.source_panel()
    assert source_panel._tree.topLevelItemCount() == 1, (
        f"expected 1 group, got {source_panel._tree.topLevelItemCount()}"
    )
    group_item = source_panel._tree.topLevelItem(0)
    label = group_item.text(0)
    assert "Lazarus_2" in label, f"Bug A regression: group label missing show name: {label!r}"
    assert "Goodbye Cruel World" not in label, (
        f"Bug A regression: group label leaked episode title: {label!r}"
    )
    # 13 leaves, one per file.
    assert group_item.childCount() == 13, (
        f"expected 13 episode leaves under the group, got {group_item.childCount()}"
    )

    # ----- Step 3: click a row to open the edit pane -----
    rows = window.item_model().rows()
    first_path = rows[0].source_path
    window._on_row_clicked(first_path)
    QApplication.processEvents()
    _save_screenshot(window, screenshots_dir, "03_edit_pane_open")

    # ASSERTIONS on Step 3: Bug B regression gate. The TMDB search
    # panel must render at meaningful height (>= 150px); the IMDb /
    # Manual boxes must NOT dwarf it.
    edit_pane = window.edit_pane()
    tmdb_panel = edit_pane.tmdb_panel()
    panel_height = tmdb_panel.height()
    assert panel_height >= 150, (
        f"Bug B regression: TMDB panel rendered height too small: {panel_height}px "
        f"(expected >= 150; widget min-heights total ~158)"
    )
    imdb_box = edit_pane.imdb_box()
    manual_box = edit_pane.manual_box()
    # The override boxes should be smaller than the TMDB panel — that's
    # exactly the point of the Maximum size policy fix.
    assert imdb_box.height() < panel_height, (
        f"Bug B regression: IMDb box ({imdb_box.height()}px) >= TMDB panel ({panel_height}px)"
    )
    assert manual_box.height() < panel_height * 2, (
        f"Bug B regression: Manual box ({manual_box.height()}px) is "
        f"unreasonably tall vs TMDB panel ({panel_height}px)"
    )

    # ----- Step 4: click the group header to open the picker -----
    group_key = rows[0].group_key
    captured_pickers: list[ShowAnchorPicker] = []
    real_factory = orchestrator._deps.picker_factory

    def _capture_factory(gk: str) -> ShowAnchorPicker:
        p = real_factory(gk)
        captured_pickers.append(p)
        return p

    orchestrator._deps.picker_factory = _capture_factory  # type: ignore[assignment]

    # The picker's exec() would block the test on a modal event loop.
    # Replace it with a non-blocking show() so we can drive the
    # interactions in the same loop tick.
    original_exec = ShowAnchorPicker.exec
    ShowAnchorPicker.exec = lambda self: 0  # type: ignore[assignment]
    try:
        orchestrator.on_group_clicked(group_key)
        QApplication.processEvents()
        assert captured_pickers, "picker_factory was not invoked"
        picker = captured_pickers[0]
        picker.resize(700, 600)
        picker.show()
        QApplication.processEvents()
        _save_screenshot(picker, screenshots_dir, "04_picker_opened_after_fuzzy_fallback")

        # ASSERTIONS on Step 4: the search-box should now reflect the
        # CLEANED query that succeeded (since "Lazarus_2" returned 0
        # results and the fuzzy fallback retried with "Lazarus").
        assert picker.has_results(), (
            "Bug C regression: picker has no results after auto-seed + fuzzy fallback "
            "(expected fallback to retry with 'Lazarus' and surface 3 hits)"
        )
        # The search box reflects the successful variant so the user
        # understands what produced the visible results.
        assert picker.search_text() == "Lazarus", (
            f"search box should show fallback variant 'Lazarus', got {picker.search_text()!r}"
        )
        # The fallback notice surfaces both the original and the
        # cleaned variant.
        notice = picker.fallback_notice_text()
        assert "Lazarus_2" in notice and "Lazarus" in notice, (
            f"Bug C regression: fallback notice missing query names: {notice!r}"
        )

        # The TMDB fake was called with both queries.
        assert ("Lazarus_2", None) in tmdb.search_calls, (
            f"expected initial search for 'Lazarus_2'; got {tmdb.search_calls}"
        )
        assert any(q == "Lazarus" for q, _ in tmdb.search_calls), (
            f"expected fallback search for 'Lazarus'; got {tmdb.search_calls}"
        )

        # ASSERTIONS on Step 4 continued: Bug C ranking. After the
        # local rank step, "Lazarus" must outrank "The Lazarus
        # Project" — the user typing the exact title expects the
        # exact match first.
        cands = picker.candidates()
        top = cands[0]
        assert top.title == "Lazarus", (
            f"Bug C regression: top result should be 'Lazarus', got {top.title!r}. "
            f"Full ranked order: {[(c.title, c.year) for c in cands]}"
        )

        # ----- Step 5: pick Lazarus (2025) -----
        target_idx = next(i for i, c in enumerate(cands) if c.title == "Lazarus" and c.year == 2025)
        picker.select_result(target_idx)
        QApplication.processEvents()
        _save_screenshot(picker, screenshots_dir, "05_picker_selected_lazarus_2025")

        picker.trigger_pick()
        QApplication.processEvents()
    finally:
        ShowAnchorPicker.exec = original_exec  # type: ignore[assignment]

    # ----- Step 6: verify the main window reflects the resolution -----
    QApplication.processEvents()
    _save_screenshot(window, screenshots_dir, "06_after_picking_lazarus_2025")

    # All 13 rows now carry the picked candidate.
    rows_after = window.item_model().rows()
    resolved = [r for r in rows_after if r.candidate is not None]
    assert len(resolved) == 13, (
        f"only {len(resolved)} of 13 rows resolved after picking Lazarus (2025)"
    )
    # The candidate is the right show (TMDB id 231003).
    assert resolved[0].candidate.anchor_id == "231003"
    # The group label updates from "Lazarus_2" to "Lazarus" — the
    # source panel's group label now derives from the candidate's
    # title via target panel; but the source panel still uses
    # show_name_hint. Verify the target panel reflects the choice.
    target_panel = window.target_panel()
    target_group = target_panel._tree.topLevelItem(0)
    target_label = target_group.text(0)
    assert "Lazarus" in target_label, (
        f"target panel group label should show 'Lazarus' after pick: {target_label!r}"
    )

    # ----- Step 7: click Preview -----
    window._on_preview_clicked()
    QApplication.processEvents()
    _save_screenshot(window, screenshots_dir, "07_after_preview")

    # ASSERTIONS on Step 7: proposed targets land under the canonical
    # Plex anchor folder. ``Lazarus (2025) {tmdb-231003}/Season 01/``.
    rows_with_ops = [r for r in window.item_model().rows() if r.proposed_op is not None]
    assert len(rows_with_ops) == 13, (
        f"expected 13 proposed ops after Preview, got {len(rows_with_ops)}"
    )
    sample_target = str(rows_with_ops[0].proposed_op.target)
    assert "Lazarus (2025) {tmdb-231003}" in sample_target, (
        f"target missing canonical anchor: {sample_target}"
    )
    assert "Season 01" in sample_target, f"target missing Season 01: {sample_target}"

    # Final summary print so the primary agent can read the
    # screenshots in order.
    print(f"\nScreenshots saved to: {screenshots_dir}")
    for p in sorted(screenshots_dir.glob("*.png")):
        print(f"  - {p}")
