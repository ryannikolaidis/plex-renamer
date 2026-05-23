"""Persistent app configuration.

The TMDB API key (and the optional OMDB API key) is read from ``.env`` on
first run and then persisted to the OS-appropriate app-config directory.
After the first persist, the ``.env`` file is no longer consulted; the
app-config JSON is the source of truth. The user can edit it via the GUI
in slice 5 or via the :class:`Settings` API.

Library roots and toggle states live alongside the keys in the same JSON
file (see :class:`Settings`); slice 5 fills them in.
"""

from __future__ import annotations

from plex_renamer.config.paths import app_cache_dir, app_config_dir
from plex_renamer.config.settings import Settings

__all__ = ["Settings", "app_cache_dir", "app_config_dir"]
