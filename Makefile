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
# ``build-win`` produces:
#   - dist/plex-renamer-cli/            (CLI one-folder bundle with .exe)
#   - dist/plex-renamer-gui/            (GUI one-folder bundle with .exe)
# The NSIS installer (``plex-renamer-setup.exe``) is built by CI;
# local-Windows users with NSIS installed can run ``makensis
# packaging/installer/nsis_script.nsi`` themselves.
build-mac:
	uv pip install "pyinstaller>=6.11"
	uv run pyinstaller packaging/macos/plex-renamer.spec --distpath dist --workpath build --noconfirm
	mkdir -p dist/dmg-staging
	cp -R dist/plex-renamer.app dist/dmg-staging/
	cp -R dist/plex-renamer-cli dist/dmg-staging/
	hdiutil create -volname plex-renamer -srcfolder dist/dmg-staging -ov -format UDZO dist/plex-renamer.dmg

build-win:
	uv pip install "pyinstaller>=6.11"
	uv run pyinstaller packaging/windows/plex-renamer.spec --distpath dist --workpath build --noconfirm
	@echo "build-win: PyInstaller bundles built under dist/. To produce the NSIS installer, run:"
	@echo "    makensis packaging/installer/nsis_script.nsi"
	@echo "(requires NSIS on PATH; CI installs it via chocolatey)."

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
