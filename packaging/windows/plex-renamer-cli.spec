# PyInstaller spec for the Windows CLI.
#
# Builds the one-folder CLI bundle under ``dist/plex-renamer-cli/``:
#
#   dist/plex-renamer-cli/plex-renamer.exe
#   dist/plex-renamer-cli/_internal/...
#
# The Windows release no longer ships the Qt GUI .exe — the WPF native
# shell from ``windows-native/PlexRenamer.csproj`` is the GUI on
# Windows. The NSIS installer at
# ``packaging/installer/nsis_script.nsi`` packages this CLI bundle plus
# the WPF .exe (``dist/plex-renamer-gui/PlexRenamer.exe``) plus the
# engine sidecar binary
# (``dist/plex-renamer-engined/plex-renamer-engined.exe`` built from
# ``packaging/windows/plex-renamer-engined.spec``).
#
# Run from the project root:
#
#     uv run pyinstaller packaging/windows/plex-renamer-cli.spec --noconfirm

import importlib.util
from pathlib import Path

# See the macOS spec for an explanation of why we load the helper via
# importlib.util.spec_from_file_location rather than ``from packaging
# import ...``: the directory name collides with the PyPI ``packaging``
# library that PyInstaller depends on, so the namespace package is
# unusable here.
SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent.parent
_helper_path = PROJECT_ROOT / "packaging" / "pyinstaller_spec.py"
_spec = importlib.util.spec_from_file_location("plex_pyinstaller_helper", _helper_path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)

cli_analysis_kwargs = _helper.cli_analysis_kwargs

ICON_PATH = PROJECT_ROOT / "packaging" / "icons" / "plex-renamer.ico"
ICON_ARG = str(ICON_PATH) if ICON_PATH.exists() else None

# --- CLI executable ------------------------------------------------------

cli_a = Analysis(**cli_analysis_kwargs())
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="plex-renamer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ARG,
)
cli_coll = COLLECT(
    cli_exe,
    cli_a.binaries,
    cli_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="plex-renamer-cli",
)
