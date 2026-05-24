# PyInstaller spec for macOS.
#
# Builds two artifacts under ``dist/``:
#
#   1. ``dist/plex-renamer-cli/plex-renamer`` — a one-folder CLI bundle.
#      Used by ``tests/test_packaging_smoke.py`` (and the release pipeline)
#      to assert ``plex-renamer --version`` works inside a built binary.
#
#   2. ``dist/plex-renamer.app`` — the GUI ``.app`` bundle that the
#      ``.dmg`` installer wraps.
#
# Both are produced from a single PyInstaller invocation because
# spec files can declare multiple ``Analysis`` blocks.
#
# Run from the project root:
#
#     uv run pyinstaller packaging/macos/plex-renamer.spec --noconfirm
#
# Outputs are written to ``./dist`` and intermediate build state to
# ``./build``.

import importlib.util
from pathlib import Path

# Load the shared helper by file path rather than by import. The
# directory name ``packaging`` collides with the ``packaging`` PyPI
# library that PyInstaller's deps drag in, so ``from packaging.X
# import Y`` resolves to the wrong module. Loading via importlib.util
# bypasses sys.path / package-resolution entirely.
SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent.parent
_helper_path = PROJECT_ROOT / "packaging" / "pyinstaller_spec.py"
_spec = importlib.util.spec_from_file_location("plex_pyinstaller_helper", _helper_path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)

app_version = _helper.app_version
cli_analysis_kwargs = _helper.cli_analysis_kwargs
gui_analysis_kwargs = _helper.gui_analysis_kwargs

VERSION = app_version()
ICON_PATH = PROJECT_ROOT / "packaging" / "icons" / "plex-renamer.icns"
ICON_ARG = str(ICON_PATH) if ICON_PATH.exists() else None

# --- CLI binary ----------------------------------------------------------

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

# --- GUI .app bundle -----------------------------------------------------

gui_a = Analysis(**gui_analysis_kwargs())
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="plex-renamer-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ARG,
)
gui_coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="plex-renamer-gui",
)

# ``BUNDLE`` wraps the COLLECT output into a macOS .app. The bundle
# identifier is reverse-DNS; the user can later override this at
# notarization time but for an unsigned ad-hoc build the placeholder
# is fine.
app = BUNDLE(
    gui_coll,
    name="plex-renamer.app",
    icon=ICON_ARG,
    bundle_identifier="io.ryan.plex-renamer",
    version=VERSION,
    info_plist={
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        # Drag-and-drop folder support: declare folders as an acceptable
        # document type so Finder presents the .app as a drop target.
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Folder",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.folder"],
            }
        ],
    },
)
