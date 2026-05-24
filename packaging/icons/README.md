# Icons

This directory is reserved for `plex-renamer.icns` (macOS) and
`plex-renamer.ico` (Windows). The first cut intentionally ships
without custom icons — PyInstaller and the macOS BUNDLE step fall
back to the default Qt application icon when the files are absent.

The PyInstaller specs at `packaging/macos/plex-renamer.spec` and
`packaging/windows/plex-renamer.spec` check for the icon files at
build time and pass `icon=None` when they're not present, so adding
them later is a drop-in.

Out of scope for the first release per `INVARIANTS.md`:

- Code signing / notarization
- Custom branded iconography

Both are addressable in a follow-up without changing the spec
structure: drop `plex-renamer.icns` and `plex-renamer.ico` into this
directory and re-run `make build-mac` / `make build-win`.
