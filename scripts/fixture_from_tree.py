#!/usr/bin/env python3
"""Mirror a real directory tree as empty files at a destination.

Usage:
    uv run python scripts/fixture_from_tree.py <src> <dst>
    uv run python scripts/fixture_from_tree.py <src> <dst> --include-hidden
    uv run python scripts/fixture_from_tree.py <src> <dst> --extensions mp4,mkv,srt

The source tree is READ-ONLY -- never modified. The destination is
populated with directories matching the source layout and empty files
(zero bytes) matching the source filenames.

Default exclusions match what the parser already skips:
- Hidden files (.DS_Store, ._*, Thumbs.db).
- temp_<digits>_<rest>/ directories (in-progress downloads).
- .download / .tmp / .part / .crdownload shards.

Optional --extensions argument restricts to specific file extensions
(comma-separated, no leading dot: ``--extensions mp4,mkv,srt``).
Without --extensions, every file (after default exclusions) is mirrored.

The tool refuses to write under the project's read-only reference
prefix (/Volumes/Cage/Media/CleverGet) per INVARIANTS.md.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path, PurePath

_READONLY_PREFIX_PARTS = PurePath("/Volumes/Cage/Media/CleverGet").parts
_DEFAULT_EXCLUDED_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_EXCLUDED_SUFFIXES = (".download", ".tmp", ".part", ".crdownload")
# Matches temp_<digits>_<anything> -- in-progress download directories.
_TEMP_DIR_RE = re.compile(r"^temp_\d+_.+$", re.IGNORECASE)


def _under_readonly(path: Path) -> bool:
    """True iff resolved path is under the global read-only reference dir."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = path
    parts = resolved.parts
    n = len(_READONLY_PREFIX_PARTS)
    return parts[:n] == _READONLY_PREFIX_PARTS


def _is_excluded(path: Path, include_hidden: bool, allowed_exts: set[str] | None) -> bool:
    name = path.name
    if not include_hidden and name.startswith("."):
        return True
    if name.startswith("._"):
        return True
    if name in _DEFAULT_EXCLUDED_BASENAMES:
        return True
    if path.is_file() and any(name.endswith(s) for s in _EXCLUDED_SUFFIXES):
        return True
    if path.is_file() and allowed_exts is not None:
        ext = path.suffix.lstrip(".").lower()
        if ext not in allowed_exts:
            return True
    return False


def mirror_tree(
    src: Path,
    dst: Path,
    include_hidden: bool,
    allowed_exts: set[str] | None,
) -> tuple[int, int]:
    """Mirror src into dst as empty files. Returns (n_dirs, n_files)."""
    if _under_readonly(dst):
        raise SystemExit(f"refusing to write under read-only prefix: {dst}")
    src = src.resolve()
    if not src.is_dir():
        raise SystemExit(f"source is not a directory: {src}")

    dst.mkdir(parents=True, exist_ok=True)
    n_dirs = 0
    n_files = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        # Filter directories in-place so os.walk doesn't descend into
        # excluded subtrees (temp_*/ download shards, hidden dirs).
        dirnames[:] = [
            d
            for d in dirnames
            if not _TEMP_DIR_RE.match(d)
            and not _is_excluded(Path(dirpath) / d, include_hidden, None)
        ]
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        if rel != Path("."):
            n_dirs += 1
        for fn in filenames:
            fp = Path(dirpath) / fn
            if _is_excluded(fp, include_hidden, allowed_exts):
                continue
            target = target_dir / fn
            target.touch()
            n_files += 1
    return n_dirs, n_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path, help="source directory to mirror (read-only)")
    parser.add_argument("dst", type=Path, help="destination directory for the empty-file mirror")
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="include hidden files (default: exclude)",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=None,
        help="comma-separated list of file extensions to include (default: all)",
    )
    args = parser.parse_args(argv)

    allowed_exts: set[str] | None = None
    if args.extensions:
        allowed_exts = {
            e.strip().lstrip(".").lower() for e in args.extensions.split(",") if e.strip()
        }

    n_dirs, n_files = mirror_tree(args.src, args.dst, args.include_hidden, allowed_exts)
    print(f"mirrored {n_files} files in {n_dirs} subdirectories from {args.src} to {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
