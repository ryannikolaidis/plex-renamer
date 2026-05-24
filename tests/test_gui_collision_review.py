"""Collision review: three actions wired per collision.

Verifies that:

* Every collision shows in the list.
* Picking ``keep_both`` / ``keep_first`` / ``reanchor`` records the
  action on the underlying model.
* ``all_resolved`` reports correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _collisions() -> list:
    from plex_renamer.planner.models import Collision

    return [
        Collision(
            target=Path("/lib/Movies/A (2020) {tmdb-1}/A (2020) {tmdb-1}.mkv"),
            sources=(Path("/in/a1.mkv"), Path("/in/a2.mkv")),
            reason="duplicate_input",
        ),
        Collision(
            target=Path("/lib/Movies/B (2020) {tmdb-2}/B (2020) {tmdb-2}.mkv"),
            sources=(Path("/in/b1.mkv"), Path("/in/b2.mkv")),
            reason="same_anchor_different_source",
        ),
    ]


def test_collision_review_lists_all(qtbot) -> None:
    from plex_renamer.gui.collision_review import CollisionReview
    from plex_renamer.gui.models import CollisionModel

    model = CollisionModel()
    model.set_collisions(_collisions())
    review = CollisionReview(model)
    qtbot.addWidget(review)
    assert review._list.count() == 2


def test_keep_both_action(qtbot) -> None:
    from plex_renamer.gui.collision_review import CollisionReview
    from plex_renamer.gui.models import CollisionModel

    model = CollisionModel()
    cols = _collisions()
    model.set_collisions(cols)
    review = CollisionReview(model)
    qtbot.addWidget(review)
    review._list.setCurrentRow(0)
    review._keep_both.setChecked(True)
    items = model.items()
    assert items[0].action == "keep_both"


def test_all_resolved_gate(qtbot) -> None:
    from plex_renamer.gui.collision_review import CollisionReview
    from plex_renamer.gui.models import CollisionModel

    model = CollisionModel()
    cols = _collisions()
    model.set_collisions(cols)
    review = CollisionReview(model)
    qtbot.addWidget(review)

    assert model.all_resolved() is False

    review._list.setCurrentRow(0)
    review._keep_both.setChecked(True)
    assert model.all_resolved() is False

    review._list.setCurrentRow(1)
    review._keep_first.setChecked(True)
    assert model.all_resolved() is True


def test_reanchor_request_emits(qtbot) -> None:
    from plex_renamer.gui.collision_review import CollisionReview
    from plex_renamer.gui.models import CollisionModel

    model = CollisionModel()
    cols = _collisions()
    model.set_collisions(cols)
    review = CollisionReview(model)
    qtbot.addWidget(review)

    received: list[Path] = []
    review.reanchor_requested.connect(received.append)

    review._list.setCurrentRow(0)
    review._reanchor.setChecked(True)
    review._emit_reanchor()

    assert received == [cols[0].target]


def test_action_signal_carries_target_and_action(qtbot) -> None:
    from plex_renamer.gui.collision_review import CollisionReview
    from plex_renamer.gui.models import CollisionModel

    model = CollisionModel()
    cols = _collisions()
    model.set_collisions(cols)
    review = CollisionReview(model)
    qtbot.addWidget(review)

    received: list[tuple[Path, str]] = []
    review.action_chosen.connect(lambda p, a: received.append((p, a)))

    review._list.setCurrentRow(1)
    review._keep_first.setChecked(True)

    assert (cols[1].target, "keep_first") in received
