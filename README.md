# plex-renamer

Cross-platform (macOS + Windows) desktop app that renames movie and TV files into the precise format Plex expects, with TMDB-mandatory anchoring and a human-in-the-loop review queue for ambiguous matches.

Drop files or directories from any source. The app builds a two-panel source-to-target preview, anchors each item to TMDB (with IMDb fallback when TMDB misses), and copies into a configured library tree under canonical Plex paths. Source cleanup is opt-in and gated by an explicit confirmation modal listing every path. Episode S/E numbers extracted from filenames are treated as hints — the planner matches by episode title against the anchored show's TMDB episode list first.

The complete product specification lives in [`INVARIANTS.md`](./INVARIANTS.md).

## Download

Each tagged release publishes installers under the [Releases](https://github.com/ryannikolaidis/plex-renamer/releases) page:

- **macOS**: `plex-renamer.dmg` (drag the `.app` into `/Applications`)
- **Windows**: `plex-renamer-setup.exe` (run the installer; the GUI lands under `Program Files\plex-renamer\gui`)

The first release is unsigned / ad-hoc-signed. On macOS, the first launch needs a right-click → Open to bypass Gatekeeper. On Windows, SmartScreen may warn; choose "More info" → "Run anyway". Code signing and notarization are deliberately out of scope for the first cut (see `INVARIANTS.md` → Out of scope).

## Develop

```
make install
make test
make check
```

Python 3.13+ is required. The project uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

Run the GUI in dev:

```
uv run python -m plex_renamer.gui.app
```

Run the CLI:

```
uv run plex-renamer --help
uv run plex-renamer plan --source <dir> --movies <dir> --tv <dir> --output plan.json
uv run plex-renamer apply --plan plan.json
uv run plex-renamer undo --journal <journal>.json
```

Run the engine sidecar (the long-running JSON-RPC daemon that future native shells talk to):

```
uv run plex-renamer-engined
```

The daemon reads newline-delimited JSON-RPC 2.0 requests on stdin and writes responses to stdout. See [`docs/win-native-bridge.md`](docs/win-native-bridge.md) for the protocol specification.

## Build

Local builds are driven by the per-OS PyInstaller specs under `packaging/`. Each spec produces both a CLI bundle and a GUI bundle so the same artifact set works for power users (CLI) and end users (GUI).

### macOS

```
make build-mac
```

Produces:

- `dist/plex-renamer.app` — the GUI `.app` bundle
- `dist/plex-renamer-cli/plex-renamer` — the CLI binary
- `dist/plex-renamer.dmg` — installer containing both, packaged by `hdiutil`

### Windows

```
make build-win
```

Produces:

- `dist/plex-renamer-cli/plex-renamer.exe` — the CLI binary
- `dist/plex-renamer-gui/plex-renamer-gui.exe` — the GUI binary

To build the NSIS installer locally (Windows only, requires NSIS on PATH):

```
makensis packaging/installer/nsis_script.nsi
```

The CI release workflow installs NSIS via chocolatey and runs the installer step automatically — see [`.github/workflows/release.yml`](.github/workflows/release.yml).

### Release

Pushing a tag of the form `v*` triggers `.github/workflows/release.yml`, which runs the per-OS build on `macos-latest` and `windows-latest`, smoke-tests the bundled CLI with `--version`, and uploads the installer artifacts to the matching GitHub Release.

## License

MIT.
