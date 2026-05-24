"""Smoke test for the PyInstaller-bundled CLI.

This test is the validation surface for slice 6's "the built binary
responds to ``--version``" AC. It runs the bundled ``plex-renamer``
binary (CLI flavour, not GUI) with ``--version`` and asserts that it
exits 0 with a sane version string.

The test is skipped outside CI because it depends on PyInstaller having
already produced a ``dist/plex-renamer-cli`` tree. In CI, the release
workflow runs the PyInstaller build step before invoking this test.
Locally, you can opt into the smoke test by setting both
``CI=true`` and ``PLEX_RENAMER_DIST_DIR`` to point at the built bundle.

The binary path is discovered in priority order:

1. ``PLEX_RENAMER_DIST_DIR`` env var — set by the release workflow.
2. ``./dist/plex-renamer-cli`` — the default PyInstaller output path
   from both per-OS specs.

This matches the discovery pattern the brief specified, with the env
var as the load-bearing knob CI uses to point at a non-default
location.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# The test is only meaningful when a built binary exists. We skip
# outside CI to keep the local ``uv run pytest`` invocation fast and
# to avoid a confusing "test not found" error when the dist folder
# isn't present.
_IS_CI = os.environ.get("CI") == "true"


def _candidate_dist_dirs() -> list[Path]:
    """Return ordered candidate directories that might contain the CLI binary."""
    env_dir = os.environ.get("PLEX_RENAMER_DIST_DIR")
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("dist") / "plex-renamer-cli")
    return candidates


def _find_binary() -> Path | None:
    """Locate the built CLI binary under the candidate dist directories."""
    name = "plex-renamer.exe" if sys.platform == "win32" else "plex-renamer"
    for candidate in _candidate_dist_dirs():
        binary = candidate / name
        if binary.exists():
            return binary
    return None


@pytest.mark.skipif(not _IS_CI, reason="Packaging smoke runs only in CI (CI=true)")
def test_built_cli_responds_to_version() -> None:
    """The bundled ``plex-renamer --version`` returns 0 with a version string.

    This is the load-bearing assertion for slice 6's "built binary on
    each OS responds to --version with exit 0 and a sane version string"
    AC. The release workflow runs this test on both macos-latest and
    windows-latest after the PyInstaller build step.
    """
    binary = _find_binary()
    if binary is None:
        pytest.fail(
            "Could not locate the built CLI binary. Looked under: "
            + ", ".join(str(c) for c in _candidate_dist_dirs())
        )

    result = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    if result.returncode != 0:
        pytest.fail(
            f"Built binary {binary} exited with {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    output = result.stdout.strip()
    if "plex-renamer" not in output:
        pytest.fail(f"--version stdout did not name the app: {output!r}")

    # Match ``plex-renamer X.Y.Z`` with X.Y.Z a dotted semver-ish number.
    # Avoids false positives on ``plex-renamer dev`` or empty strings.
    if not re.search(r"\b\d+\.\d+(?:\.\d+)?\b", output):
        pytest.fail(f"--version stdout missing a version number: {output!r}")
