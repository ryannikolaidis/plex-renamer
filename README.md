# plex-renamer

Cross-platform (macOS + Windows) desktop app that renames movie and TV files into the precise format Plex expects, with TMDB-mandatory anchoring and a human-in-the-loop review queue for ambiguous matches.

Drop files or directories from any source. The app builds a two-panel source-to-target preview, anchors each item to TMDB (with IMDb fallback when TMDB misses), and copies into a configured library tree under canonical Plex paths. Source cleanup is opt-in and gated by an explicit confirmation modal listing every path. Episode S/E numbers extracted from filenames are treated as hints — the planner matches by episode title against the anchored show's TMDB episode list first.

The complete product specification lives in [`INVARIANTS.md`](./INVARIANTS.md).

## Status

Greenfield. Project scaffold landed in slice 1. Subsequent slices ship the parser + test corpus, the TMDB client + cache, the planner + executor + CLI, the PySide6 GUI, and the cross-platform packaging.

## Develop

```
make install
make test
make check
```

Python 3.13+ is required. The project uses [`uv`](https://docs.astral.sh/uv/) for dependency management.

## Build (placeholders)

```
make build-mac    # filled in by the packaging slice
make build-win    # filled in by the packaging slice
```

## License

MIT.
