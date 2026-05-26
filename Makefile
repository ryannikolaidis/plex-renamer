.PHONY: install tidy check test build-mac build-win clean

install:
	uv sync

tidy:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

# Per-OS packaging targets. Each invokes PyInstaller against the
# OS-specific spec file under ``packaging/`` and then drops an
# installer artifact into ``dist/``. The same specs run on CI's
# macos-latest / windows-latest runners via ``.github/workflows/release.yml``.
#
# ``build-mac`` produces:
#   - dist/plex-renamer.app             (GUI .app bundle)
#   - dist/plex-renamer-cli/            (CLI one-folder bundle)
#   - dist/plex-renamer.dmg             (installer wrapping both)
#
# ``build-win`` produces three artifact directories:
#   - dist/plex-renamer-cli/            (CLI one-folder bundle, PyInstaller)
#   - dist/plex-renamer-engined/        (engine sidecar daemon binary, PyInstaller)
#   - dist/plex-renamer-gui/PlexRenamer.exe  (WPF .NET 8 GUI, dotnet publish)
# The Windows installer (``plex-renamer-setup.exe``) is built by CI;
# local-Windows users with NSIS + .NET 8 SDK installed can run
# ``makensis packaging/installer/nsis_script.nsi`` after this target.
# The legacy Qt GUI .exe no longer ships on Windows — replaced by the
# WPF native shell under ``windows-native/PlexRenamer.csproj``.
build-mac:
	uv run pyinstaller packaging/macos/plex-renamer.spec --distpath dist --workpath build --noconfirm
	mkdir -p dist/dmg-staging
	cp -R dist/plex-renamer.app dist/dmg-staging/
	cp -R dist/plex-renamer-cli dist/dmg-staging/
	hdiutil create -volname plex-renamer -srcfolder dist/dmg-staging -ov -format UDZO dist/plex-renamer.dmg

build-win:
	uv run pyinstaller packaging/windows/plex-renamer-cli.spec --distpath dist --workpath build --noconfirm
	uv run pyinstaller packaging/windows/plex-renamer-engined.spec --distpath dist --workpath build --noconfirm
	dotnet publish windows-native/PlexRenamer.sln -c Release -r win-x64 --self-contained false -o dist/plex-renamer-gui
	@echo "build-win: artifacts under dist/. To produce the NSIS installer, run:"
	@echo "    makensis -DAPP_VERSION=\$$(uv run python -c \"import importlib.util; s=importlib.util.spec_from_file_location('h','packaging/pyinstaller_spec.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.app_version())\") packaging/installer/nsis_script.nsi"
	@echo "(requires NSIS on PATH; CI installs it via chocolatey)."

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
