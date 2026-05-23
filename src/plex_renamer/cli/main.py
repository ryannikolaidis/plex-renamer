"""CLI entry point.

Slice 1 ships a stub that responds to ``--version`` / ``-V``. Subsequent slices
replace this with the full subcommand surface (``plan``, ``apply``, ``undo``).
"""

from __future__ import annotations

import sys

from plex_renamer import __version__

_UNKNOWN_ARG_EXIT = 2  # POSIX convention; matches Click/Typer default.


def app(argv: list[str] | None = None) -> int:
    """Stub entry point. Returns the process exit code."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("plex-renamer: CLI surface lands in slice 4. Run with --version.")
        return 0
    if args[0] in ("--version", "-V"):
        print(f"plex-renamer {__version__}")
        return 0
    print(
        f"plex-renamer: unknown argument {args[0]!r}. Slice 4 wires the full CLI.",
        file=sys.stderr,
    )
    return _UNKNOWN_ARG_EXIT


if __name__ == "__main__":
    raise SystemExit(app())
