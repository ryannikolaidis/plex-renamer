"""Typer-style CLI entry point.

Slice 1 ships a stub that responds to ``--version`` and exits zero. Subsequent
slices flesh out ``plan``, ``apply``, and ``undo`` subcommands over the engine.
"""

from __future__ import annotations

import sys

from plex_renamer import __version__


def app(argv: list[str] | None = None) -> int:
    """Stub entry point. Returns the process exit code.

    Currently supports only ``--version`` / ``-V``. Any other invocation prints a
    placeholder message and exits zero so the CLI is discoverable from the
    scaffold-test suite and the packaging smoke test in slice 6.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("plex-renamer: CLI surface lands in slice 4. Run with --version.")
        return 0
    if args[0] in ("--version", "-V"):
        print(f"plex-renamer {__version__}")
        return 0
    print(f"plex-renamer: unknown argument {args[0]!r}. Slice 4 wires the full CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(app())
