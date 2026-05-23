"""Mock-tree builder for the parser test corpus.

Run as a script::

    python -m plex_renamer.test_corpus.generator /tmp/plex_corpus

Or call :func:`build_corpus` directly from Python. The generator writes empty
files at every relative path in :data:`CORPUS_PATTERNS`, creating parent
directories as needed.

Safety: the generator REFUSES to write under
``/Volumes/Cage/Media/CleverGet`` regardless of whether the caller asks for
that prefix. The user's reference corpus is read-only and the project-wide
conftest fixture enforces the same boundary for the test suite.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

from plex_renamer.test_corpus.patterns import CORPUS_PATTERNS, CorpusEntry

READONLY_PREFIX = "/Volumes/Cage/Media/CleverGet"

# Entries whose relative path should be NFD-encoded on disk. NFD-encoded
# basenames are a real-world corpus quirk on macOS HFS+ that the parser
# must handle. We mark them by exact match against the pattern catalog.
_NFD_TARGETS: frozenset[str] = frozenset({"Pokémon The Movie.mp4"})


def build_corpus(out_dir: Path | str) -> list[Path]:
    """Build the mock tree under ``out_dir`` and return every created path.

    Existing files at the same location are NOT overwritten (the generator
    is idempotent: re-running is a no-op when the tree is already present).
    Returns the absolute paths of every entry — created or pre-existing.
    """
    out = Path(out_dir).resolve()
    _refuse_readonly(out)

    out.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    for entry in CORPUS_PATTERNS:
        rel = _maybe_nfd(entry)
        full = out / rel
        _refuse_readonly(full)

        full.parent.mkdir(parents=True, exist_ok=True)
        # Empty file. We use open() rather than Path.touch() so the conftest
        # write-guard fixture treats it like any other writable open.
        if not full.exists():
            with full.open("wb"):
                pass
        created.append(full)

    return created


def _maybe_nfd(entry: CorpusEntry) -> str:
    """Return the entry's relative path, NFD-encoded when the pattern demands it."""
    # Patterns are authored as plain Python strings; Python source files are
    # NFC by default. For the NFD targets we explicitly decompose the path.
    if Path(entry.relative_path).name in _NFD_TARGETS:
        return unicodedata.normalize("NFD", entry.relative_path)
    return entry.relative_path


def _refuse_readonly(path: Path) -> None:
    """Raise if ``path`` is under the user's read-only reference directory."""
    try:
        resolved = path.resolve()
    except OSError:
        # Path may not exist yet; resolve(strict=False) is implicit in 3.13.
        resolved = path

    if str(resolved).startswith(READONLY_PREFIX):
        raise RuntimeError(f"refusing to write under read-only reference prefix: {resolved}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m plex_renamer.test_corpus.generator",
        description="Build the plex-renamer parser test corpus.",
    )
    parser.add_argument(
        "out_dir",
        type=Path,
        help="Directory to populate. Created if missing. Existing files are kept.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    paths = build_corpus(args.out_dir)
    print(f"wrote {len(paths)} entries under {args.out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
