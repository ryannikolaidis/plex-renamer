"""Shared PyInstaller spec helpers.

The per-OS spec files in ``packaging/macos`` and ``packaging/windows``
are executed by PyInstaller via ``pyinstaller foo.spec``. PyInstaller
treats those ``.spec`` files as Python with a small set of injected
globals (``Analysis``, ``PYZ``, ``EXE``, ``COLLECT``, ``BUNDLE``,
``Tree``, ``DISTPATH``, ``WORKPATH``, ``SPECPATH``). To keep the two
specs in sync, this module concentrates the parameters that BOTH OSes
need to feed into ``Analysis(...)``:

* Two entry scripts: ``plex_renamer.cli.main`` (the CLI used for the
  ``--version`` smoke gate) and ``plex_renamer.gui.app`` (the GUI).
* PySide6 hidden-imports + data files. PyInstaller ships a PySide6
  hook, but for safety on macOS where Qt plugin discovery can be
  fragile we explicitly collect submodules + data files.
* ``plex_renamer``'s own data files (e.g. ``gui/styles.qss``).

The actual Analysis / PYZ / EXE / BUNDLE wiring lives in the per-OS
spec files because the structure differs:

* macOS: one PYZ + EXE for the CLI, one PYZ + EXE + BUNDLE for the
  GUI app, packaged together by ``hdiutil`` into a ``.dmg``.
* Windows: one PYZ + EXE for the CLI, one PYZ + EXE for the GUI,
  bundled into a single one-folder dist tree by ``COLLECT``, then
  packaged by NSIS into a setup ``.exe``.

The helpers below return plain dicts of constructor kwargs that the
spec files unpack into ``Analysis(**kwargs)``. This indirection keeps
the spec files small and lets us unit-test the helper contract from
:mod:`tests.test_packaging_smoke` without invoking PyInstaller.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

CLI_ENTRY = SRC_DIR / "plex_renamer" / "cli" / "main.py"
GUI_ENTRY = SRC_DIR / "plex_renamer" / "gui" / "app.py"

# Hidden imports that PyInstaller's auto-discovery sometimes misses.
# Listed conservatively; PyInstaller is happy to ignore false positives.
COMMON_HIDDEN_IMPORTS: list[str] = [
    "plex_renamer",
    "plex_renamer.cli",
    "plex_renamer.cli.main",
    "plex_renamer.cli.apply_cmd",
    "plex_renamer.cli.plan_cmd",
    "plex_renamer.cli.undo_cmd",
    "plex_renamer.config",
    "plex_renamer.config.paths",
    "plex_renamer.config.settings",
    "plex_renamer.executor",
    "plex_renamer.parser",
    "plex_renamer.planner",
    "plex_renamer.tmdb",
]

GUI_HIDDEN_IMPORTS: list[str] = [
    "plex_renamer.gui",
    "plex_renamer.gui.app",
    "plex_renamer.gui.main_window",
    "plex_renamer.gui.orchestrator",
    "plex_renamer.gui.source_panel",
    "plex_renamer.gui.target_panel",
    "plex_renamer.gui.edit_pane",
    "plex_renamer.gui.show_anchor_picker",
    "plex_renamer.gui.tmdb_search_panel",
    "plex_renamer.gui.settings_dialog",
    "plex_renamer.gui.library_roots_dialog",
    "plex_renamer.gui.cleanup_confirm_modal",
    "plex_renamer.gui.collision_review",
    "plex_renamer.gui.run_report",
    "plex_renamer.gui.drop_zone",
    "plex_renamer.gui.confidence_badge",
    "plex_renamer.gui.models",
]

# Data files (relative to the project root) that need to ship inside
# the bundle. The GUI stylesheet is the load-bearing one — without it,
# the GUI launches with default Qt styling.
GUI_DATAS: list[tuple[str, str]] = [
    (str(SRC_DIR / "plex_renamer" / "gui" / "styles.qss"), "plex_renamer/gui"),
]


def cli_analysis_kwargs() -> dict:
    """Kwargs for the CLI Analysis(...) block.

    No PySide6 collection: the CLI never imports Qt, and pulling Qt
    into the CLI bundle would balloon its size from ~10 MB to ~100 MB
    for no benefit. PyInstaller's dependency walker correctly excludes
    Qt when the CLI's import graph never touches ``plex_renamer.gui``.

    ``plex-renamer``'s package metadata is explicitly copied so
    :func:`importlib.metadata.version` (used inside
    :mod:`plex_renamer.__init__`) resolves to the real version string
    instead of the ``0.0.0+unknown`` fallback. Without this, the
    ``--version`` smoke test in :mod:`tests.test_packaging_smoke`
    sees a meaningless version and fails.
    """
    # Lazy import: ``copy_metadata`` lives behind PyInstaller's hook
    # utilities which are only on PYTHONPATH during a PyInstaller run.
    from PyInstaller.utils.hooks import copy_metadata  # noqa: PLC0415

    return {
        "scripts": [str(CLI_ENTRY)],
        "pathex": [str(SRC_DIR)],
        "binaries": [],
        "datas": copy_metadata("plex-renamer"),
        "hiddenimports": list(COMMON_HIDDEN_IMPORTS),
        "hookspath": [],
        "hooksconfig": {},
        "runtime_hooks": [],
        "excludes": [
            "PySide6",
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtWidgets",
            "shiboken6",
        ],
        "noarchive": False,
    }


def gui_analysis_kwargs() -> dict:
    """Kwargs for the GUI Analysis(...) block.

    Uses ``collect_all('PySide6')`` to pull every Qt plugin, translation,
    and submodule that the GUI might touch at runtime. This is heavier
    than necessary in some cases but avoids the "Qt platform plugin
    could not be initialized" footgun that bites every PyInstaller +
    PySide6 user on macOS the first time they ship.
    """
    # Lazy import: PyInstaller's hook utilities are only on the PYTHONPATH
    # when PyInstaller is running. Importing eagerly would make this
    # module unimportable outside a PyInstaller invocation, which would
    # break the unit test that just asserts ``cli_analysis_kwargs``'s
    # shape.
    from PyInstaller.utils.hooks import collect_all, copy_metadata  # noqa: PLC0415

    datas: list = list(GUI_DATAS) + copy_metadata("plex-renamer")
    binaries: list = []
    hiddenimports: list = list(COMMON_HIDDEN_IMPORTS) + list(GUI_HIDDEN_IMPORTS)

    pyside_datas, pyside_binaries, pyside_hidden = collect_all("PySide6")
    datas += pyside_datas
    binaries += pyside_binaries
    hiddenimports += pyside_hidden

    shiboken_datas, shiboken_binaries, shiboken_hidden = collect_all("shiboken6")
    datas += shiboken_datas
    binaries += shiboken_binaries
    hiddenimports += shiboken_hidden

    return {
        "scripts": [str(GUI_ENTRY)],
        "pathex": [str(SRC_DIR)],
        "binaries": binaries,
        "datas": datas,
        "hiddenimports": hiddenimports,
        "hookspath": [],
        "hooksconfig": {},
        "runtime_hooks": [],
        "excludes": [],
        "noarchive": False,
    }


def app_version() -> str:
    """Return the bundled app's version string.

    Reads ``plex_renamer.__version__`` so PyInstaller's BUNDLE info and
    NSIS installer metadata stay in lockstep with the package version.
    """
    sys.path.insert(0, str(SRC_DIR))
    try:
        from plex_renamer import __version__  # noqa: PLC0415

        return __version__
    finally:
        # Pop the entry we just pushed; never assume index 0 is still ours.
        with contextlib.suppress(ValueError):
            sys.path.remove(str(SRC_DIR))
