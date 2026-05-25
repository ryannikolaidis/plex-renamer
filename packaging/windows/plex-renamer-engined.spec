# PyInstaller spec for the engine sidecar daemon.
#
# Builds one artifact under ``dist/``:
#
#   ``dist/plex-renamer-engined/plex-renamer-engined.exe`` — the
#   long-running JSON-RPC daemon used by native shells (the WPF Windows
#   app and any future native frontends). The bundle is one-folder so
#   PyInstaller's ``_internal/`` directory carries the Python runtime
#   and the project's modules.
#
# The spec deliberately excludes PySide6 / shiboken6 to keep the binary
# small. The daemon never imports Qt; pulling Qt into the sidecar would
# add ~90 MB to the installer for no benefit.
#
# Run from the project root:
#
#     uv run pyinstaller packaging/windows/plex-renamer-engined.spec --noconfirm

import importlib.util
from pathlib import Path

# Load the shared helper via importlib because the directory name
# ``packaging`` collides with the PyPI ``packaging`` library PyInstaller
# depends on. The same trick is used by ``plex-renamer.spec``.
SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent.parent
_helper_path = PROJECT_ROOT / "packaging" / "pyinstaller_spec.py"
_spec = importlib.util.spec_from_file_location("plex_pyinstaller_helper", _helper_path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)

# Lazy import of PyInstaller's hook helpers; only available during a
# PyInstaller run.
from PyInstaller.utils.hooks import copy_metadata  # noqa: E402

SRC_DIR = PROJECT_ROOT / "src"
DAEMON_ENTRY = SRC_DIR / "plex_renamer" / "daemon" / "server.py"

ICON_PATH = PROJECT_ROOT / "packaging" / "icons" / "plex-renamer.ico"
ICON_ARG = str(ICON_PATH) if ICON_PATH.exists() else None

# Hidden imports for the daemon. The COMMON_HIDDEN_IMPORTS list from the
# shared helper already covers the engine modules; we add the daemon
# package explicitly so PyInstaller doesn't drop it when the dependency
# walker misses the relative imports inside the methods table.
DAEMON_HIDDEN_IMPORTS = list(_helper.COMMON_HIDDEN_IMPORTS) + [
    "plex_renamer.daemon",
    "plex_renamer.daemon.server",
    "plex_renamer.daemon.methods",
    "plex_renamer.daemon.orchestrator",
    "plex_renamer.daemon.schemas",
]

daemon_analysis_kwargs = {
    "scripts": [str(DAEMON_ENTRY)],
    "pathex": [str(SRC_DIR)],
    "binaries": [],
    "datas": copy_metadata("plex-renamer"),
    "hiddenimports": DAEMON_HIDDEN_IMPORTS,
    "hookspath": [],
    "hooksconfig": {},
    "runtime_hooks": [],
    # Daemon is Qt-free. Exclude PySide6 explicitly so a stray import
    # path through ``plex_renamer.gui`` doesn't pull the whole Qt
    # runtime into the sidecar's _internal/.
    "excludes": [
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "shiboken6",
        "plex_renamer.gui",
    ],
    "noarchive": False,
}

daemon_a = Analysis(**daemon_analysis_kwargs)
daemon_pyz = PYZ(daemon_a.pure)
daemon_exe = EXE(
    daemon_pyz,
    daemon_a.scripts,
    [],
    exclude_binaries=True,
    name="plex-renamer-engined",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # ``console=True``: the sidecar reads JSON-RPC over stdin/stdout, so
    # it MUST be a console subsystem binary. Setting console=False on
    # Windows would detach the process from its parent's stdio handles
    # and the shell's pipes would never get bytes back.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ARG,
)
daemon_coll = COLLECT(
    daemon_exe,
    daemon_a.binaries,
    daemon_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="plex-renamer-engined",
)
