"""Deletion confirmation modal: every path listed, checkbox gates Confirm.

The modal lists EVERY scheduled deletion path and disables the Confirm
button until the user checks the "I understand" checkbox. Closing or
cancelling MUST NOT emit the ``confirmed`` signal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def test_modal_lists_all_paths(qtbot) -> None:
    from plex_renamer.gui.cleanup_confirm_modal import CleanupConfirmModal

    paths = [Path("/in/a.mkv"), Path("/in/b.mkv"), Path("/in/c.mkv")]
    modal = CleanupConfirmModal(paths)
    qtbot.addWidget(modal)
    assert modal.paths() == paths
    assert modal._list.count() == 3


def test_confirm_button_gated_by_checkbox(qtbot) -> None:
    from plex_renamer.gui.cleanup_confirm_modal import CleanupConfirmModal

    modal = CleanupConfirmModal([Path("/in/a.mkv")])
    qtbot.addWidget(modal)
    assert modal.confirm_button_enabled() is False
    modal._consent.setChecked(True)
    assert modal.confirm_button_enabled() is True
    modal._consent.setChecked(False)
    assert modal.confirm_button_enabled() is False


def test_confirm_emits_only_when_checked(qtbot) -> None:
    from plex_renamer.gui.cleanup_confirm_modal import CleanupConfirmModal

    modal = CleanupConfirmModal([Path("/in/a.mkv")])
    qtbot.addWidget(modal)
    received: list[bool] = []
    modal.confirmed.connect(lambda: received.append(True))

    # Trying to confirm without check is a no-op.
    modal._confirm()
    assert received == []

    modal._consent.setChecked(True)
    modal._confirm()
    assert received == [True]


def test_reject_does_not_emit(qtbot) -> None:
    from plex_renamer.gui.cleanup_confirm_modal import CleanupConfirmModal

    modal = CleanupConfirmModal([Path("/in/a.mkv")])
    qtbot.addWidget(modal)
    received: list[bool] = []
    modal.confirmed.connect(lambda: received.append(True))

    modal._consent.setChecked(True)
    modal.reject()
    assert received == []
