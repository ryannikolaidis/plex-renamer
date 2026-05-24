"""PySide6 desktop UI for plex-renamer.

This package is a THIN CLIENT over the engine. The engine
(``plex_renamer.parser``, ``plex_renamer.tmdb``, ``plex_renamer.planner``,
``plex_renamer.executor``) never imports anything from here; the dependency
is one-way.

The entry point is :func:`plex_renamer.gui.app.main`, registered as a
gui-script in ``pyproject.toml`` (``plex-renamer-gui``).

Headless tests run with ``QT_QPA_PLATFORM=offscreen`` and exercise widget
construction, signal/slot wiring, and model state machine transitions.
Visual smoke (color rendering, drag-drop visuals, HiDPI, fonts) is out of
scope for the test suite and reserved for the user's manual smoke pass.
"""
