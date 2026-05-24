"""Confidence badge: band -> color mapping and band derivation from
:class:`Candidate.confidence`.

The visual color is rendered via stylesheet; we verify the band and the
hex color reported by :func:`color_for_band` rather than reading the
QPalette (which under offscreen may not reflect the stylesheet at all).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_band_thresholds() -> None:
    from plex_renamer.gui.models import confidence_band_for
    from plex_renamer.tmdb.models import Candidate

    high = Candidate(
        anchor_kind="tmdb", anchor_id="1", kind="movie", title="x", year=2020, confidence=0.9
    )
    mid = Candidate(
        anchor_kind="tmdb", anchor_id="1", kind="movie", title="x", year=2020, confidence=0.7
    )
    low = Candidate(
        anchor_kind="tmdb", anchor_id="1", kind="movie", title="x", year=2020, confidence=0.3
    )

    assert confidence_band_for(high) == "auto"
    assert confidence_band_for(mid) == "review"
    assert confidence_band_for(low) == "unresolved"
    assert confidence_band_for(None) == "unresolved"


def test_color_mapping() -> None:
    from plex_renamer.gui.models import color_for_band

    # We don't pin exact hex values — just verify each band returns a
    # distinct non-empty string.
    auto = color_for_band("auto")
    review = color_for_band("review")
    unresolved = color_for_band("unresolved")
    assert auto and review and unresolved
    assert auto != review != unresolved
    assert auto != unresolved


def test_badge_widget_updates(qtbot) -> None:
    from plex_renamer.gui.confidence_badge import ConfidenceBadge

    badge = ConfidenceBadge("auto")
    qtbot.addWidget(badge)
    assert badge.band() == "auto"
    assert badge.accessibleName() == "confidence-auto"
    badge.set_band("review")
    assert badge.band() == "review"
    assert badge.accessibleName() == "confidence-review"


def test_badge_threshold_boundaries() -> None:
    """Exactly 0.85 should be ``auto``; exactly 0.60 should be ``review``."""
    from plex_renamer.gui.models import (
        AUTO_ACCEPT_THRESHOLD,
        NEEDS_REVIEW_THRESHOLD,
        confidence_band_for,
    )
    from plex_renamer.tmdb.models import Candidate

    at_auto = Candidate(
        anchor_kind="tmdb",
        anchor_id="1",
        kind="movie",
        title="x",
        year=2020,
        confidence=AUTO_ACCEPT_THRESHOLD,
    )
    at_review = Candidate(
        anchor_kind="tmdb",
        anchor_id="1",
        kind="movie",
        title="x",
        year=2020,
        confidence=NEEDS_REVIEW_THRESHOLD,
    )
    assert confidence_band_for(at_auto) == "auto"
    assert confidence_band_for(at_review) == "review"
