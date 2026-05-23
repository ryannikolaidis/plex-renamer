"""OS-appropriate directories for persistent state.

We use :mod:`platformdirs` rather than rolling our own per-OS logic. The
app name is ``plex-renamer``; the appauthor is intentionally suppressed
(``False``) so we get ``~/Library/Application Support/plex-renamer`` on
macOS and ``%APPDATA%\\plex-renamer`` on Windows, not
``%APPDATA%\\<Author>\\plex-renamer``.

The functions are thin wrappers because tests need a single seam to
monkeypatch when redirecting state to a scratch dir. Patching at the
call-site level rather than the ``platformdirs`` module level keeps the
tests insulated from upstream API changes.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

APP_NAME = "plex-renamer"


def app_config_dir() -> Path:
    """Return the OS-appropriate app-config directory.

    macOS: ``~/Library/Application Support/plex-renamer``
    Windows: ``%APPDATA%/plex-renamer``
    Linux: ``~/.config/plex-renamer``

    The directory is NOT auto-created here; callers (``Settings.save``)
    create it on first write.
    """
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def app_cache_dir() -> Path:
    """Return the OS-appropriate per-user cache directory.

    macOS: ``~/Library/Caches/plex-renamer``
    Windows: ``%LOCALAPPDATA%/plex-renamer/Cache``
    Linux: ``~/.cache/plex-renamer``
    """
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
