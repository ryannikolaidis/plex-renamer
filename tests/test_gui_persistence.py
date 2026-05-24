"""Persistence: settings round-trip through the dialog.

Library roots, cleanup toggle, and TMDB key are written via the settings
dialog and re-read from disk on the next ``Settings.load`` call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def test_cleanup_toggle_defaults_off(gui_settings) -> None:
    assert gui_settings.cleanup_enabled is False


def test_settings_dialog_saves_tmdb_key(qtbot, gui_settings) -> None:
    from plex_renamer.gui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(gui_settings)
    qtbot.addWidget(dlg)
    dlg.set_tmdb_key("DEADBEEF")
    dlg.set_cleanup_enabled(True)
    dlg._save()

    # In-memory state updated.
    assert gui_settings.tmdb_api_key == "DEADBEEF"
    assert gui_settings.cleanup_enabled is True

    # On-disk persistence: reload from the same path.
    from plex_renamer.config.settings import Settings

    cfg_path = gui_settings._config_path
    reloaded = Settings.load(config_path=cfg_path)
    assert reloaded.tmdb_api_key == "DEADBEEF"
    assert reloaded.cleanup_enabled is True


def test_library_roots_dialog_saves(qtbot, gui_settings, tmp_path) -> None:
    from plex_renamer.gui.library_roots_dialog import LibraryRootsDialog

    movies = tmp_path / "movies"
    tv = tmp_path / "tv"
    movies.mkdir()
    tv.mkdir()

    dlg = LibraryRootsDialog(gui_settings)
    qtbot.addWidget(dlg)
    dlg.set_paths(str(movies), str(tv))
    received: list[tuple[Path, Path]] = []
    dlg.roots_saved.connect(lambda m, t: received.append((m, t)))
    dlg._save()

    assert gui_settings.movies_root == str(movies)
    assert gui_settings.tv_root == str(tv)
    assert received == [(movies, tv)]

    from plex_renamer.config.settings import Settings

    reloaded = Settings.load(config_path=gui_settings._config_path)
    assert reloaded.movies_root == str(movies)
    assert reloaded.tv_root == str(tv)


def test_settings_dialog_does_not_persist_on_cancel(qtbot, gui_settings) -> None:
    """Reject() must NOT save anything to disk."""
    from plex_renamer.gui.settings_dialog import SettingsDialog

    initial = gui_settings.tmdb_api_key
    dlg = SettingsDialog(gui_settings)
    qtbot.addWidget(dlg)
    dlg.set_tmdb_key("CHANGED")
    dlg.reject()

    # No call to _save -> in-memory + disk both unchanged.
    assert gui_settings.tmdb_api_key == initial
