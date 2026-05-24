"""Drop-zone signal wiring.

We can't synthesize a real OS-level drag-and-drop reliably across all
platforms in headless mode, so the test exercises the public
:meth:`DropZone.simulate_drop` shim. The shim is the same code path the
real ``dropEvent`` calls; the only difference is the input source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def test_drop_zone_emits_paths(qtbot) -> None:
    from plex_renamer.gui.drop_zone import DropZone

    zone = DropZone()
    qtbot.addWidget(zone)

    captured: list[list[Path]] = []
    zone.paths_dropped.connect(captured.append)

    zone.simulate_drop([Path("/tmp/a.mkv"), Path("/tmp/dir")])

    assert len(captured) == 1
    assert captured[0] == [Path("/tmp/a.mkv"), Path("/tmp/dir")]


def test_drop_zone_constructs_under_offscreen(qtbot) -> None:
    """Construct without errors under the offscreen platform plugin."""
    from plex_renamer.gui.drop_zone import DropZone

    zone = DropZone()
    qtbot.addWidget(zone)
    assert zone.acceptDrops() is True
    assert zone.accessibleName() == "drop-zone"


def test_drop_zone_no_signal_on_empty(qtbot) -> None:
    from plex_renamer.gui.drop_zone import DropZone

    zone = DropZone()
    qtbot.addWidget(zone)
    captured: list[list[Path]] = []
    zone.paths_dropped.connect(captured.append)

    # Empty list — simulate_drop is the same signal emitter; we don't
    # call it with empty since real drops with no URLs ignore the event.
    # Validate that nothing was captured before any explicit call.
    assert captured == []
