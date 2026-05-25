"""Library roots are surfaced on the main window.

Before v0.1.2 the Movies / TV destinations lived two levels deep
(Settings... -> Library roots...) and the user had no visual cue
what the current values were. The main window now hosts a labels +
"Change..." buttons row so the destination is always visible.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_main_window_labels_reflect_unset_roots(qtbot, gui_settings) -> None:
    """When both roots are unset, the labels render the 'Not set' hint."""
    from plex_renamer.gui.main_window import MainWindow

    gui_settings.movies_root = None
    gui_settings.tv_root = None

    window = MainWindow(gui_settings)
    qtbot.addWidget(window)

    movies_label = window.movies_root_label()
    tv_label = window.tv_root_label()
    assert "Not set" in movies_label.text()
    assert "Not set" in tv_label.text()
    # Style applied for the unset state (yellow tint + italic).
    assert "italic" in movies_label.styleSheet()
    assert "italic" in tv_label.styleSheet()


def test_main_window_labels_reflect_configured_roots(qtbot, gui_settings, tmp_path) -> None:
    movies = tmp_path / "Movies"
    tv = tmp_path / "TV"
    movies.mkdir()
    tv.mkdir()
    gui_settings.movies_root = str(movies)
    gui_settings.tv_root = str(tv)

    from plex_renamer.gui.main_window import MainWindow

    window = MainWindow(gui_settings)
    qtbot.addWidget(window)

    assert window.movies_root_label().text() == str(movies)
    assert window.tv_root_label().text() == str(tv)
    # No italic style on configured roots.
    assert "italic" not in window.movies_root_label().styleSheet()


def test_change_movies_root_updates_settings(qtbot, gui_settings, tmp_path, monkeypatch) -> None:
    """Clicking Change... and picking a folder persists to Settings.

    We monkeypatch ``QFileDialog.getExistingDirectory`` so the test
    doesn't pop a real picker.
    """
    from PySide6.QtWidgets import QFileDialog

    from plex_renamer.gui.main_window import MainWindow

    new_movies = tmp_path / "NewMovies"
    new_movies.mkdir()
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(new_movies),
    )

    window = MainWindow(gui_settings)
    qtbot.addWidget(window)
    window._change_movies_root()

    assert gui_settings.movies_root == str(new_movies)
    assert window.movies_root_label().text() == str(new_movies)
    # Persisted to disk.
    from plex_renamer.config.settings import Settings

    reloaded = Settings.load(config_path=gui_settings._config_path)
    assert reloaded.movies_root == str(new_movies)


def test_change_tv_root_updates_settings(qtbot, gui_settings, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    from plex_renamer.gui.main_window import MainWindow

    new_tv = tmp_path / "NewTV"
    new_tv.mkdir()
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(new_tv),
    )

    window = MainWindow(gui_settings)
    qtbot.addWidget(window)
    window._change_tv_root()

    assert gui_settings.tv_root == str(new_tv)
    assert window.tv_root_label().text() == str(new_tv)


def test_change_root_cancelled_does_not_mutate(qtbot, gui_settings, monkeypatch) -> None:
    """Cancelling the file picker (returns empty string) is a no-op."""
    from PySide6.QtWidgets import QFileDialog

    from plex_renamer.gui.main_window import MainWindow

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: "",
    )

    gui_settings.movies_root = None
    window = MainWindow(gui_settings)
    qtbot.addWidget(window)
    window._change_movies_root()

    assert gui_settings.movies_root is None
    assert "Not set" in window.movies_root_label().text()
