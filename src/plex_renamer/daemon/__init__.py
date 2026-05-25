"""JSON-RPC sidecar daemon for native (non-Qt) shells.

The daemon wraps the engine modules (``parser``, ``tmdb``, ``planner``,
``executor``, ``config``) and ports the orchestration flow logic from
:mod:`plex_renamer.gui.orchestrator` into pure-Python helpers under
:mod:`plex_renamer.daemon.orchestrator`. Native shells (the WPF Windows
app slice, future GTK/Cocoa shells, etc.) speak JSON-RPC 2.0 to the
daemon over stdin/stdout — newline-delimited, one object per line.

The Qt path keeps its own in-process orchestrator and is unmodified by
this layer. The daemon is additive.

Public entry points:

* :func:`plex_renamer.daemon.server.main` — the script entry referenced
  by ``[project.scripts] plex-renamer-engined``.
* :mod:`plex_renamer.daemon.schemas` — request / response shape
  definitions.
"""

from __future__ import annotations

from plex_renamer.daemon import schemas
from plex_renamer.daemon.server import main

__all__ = ["main", "schemas"]
