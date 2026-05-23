"""Scaffold-slice tests.

Verify that the project skeleton is intact:
- ``INVARIANTS.md`` exists at the repo root with the eight required H2 sections.
- ``pyproject.toml`` parses, names the project, and declares Python >= 3.13.
- The CLI entry point is importable and responds to ``--version``.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_INVARIANT_SECTIONS = [
    "Inputs",
    "Outputs",
    "Identification",
    "Confidence and review",
    "Sidecars and adjacent files",
    "Safety",
    "Persistence",
    "Out of scope",
]

H2_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def _invariants_sections() -> dict[str, str]:
    """Return {section_title: body} for every H2 in INVARIANTS.md, robust to reordering."""
    text = (REPO_ROOT / "INVARIANTS.md").read_text()
    matches = list(H2_HEADER_RE.finditer(text))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[title] = text[body_start:body_end].strip()
    return sections


def test_invariants_file_exists() -> None:
    invariants = REPO_ROOT / "INVARIANTS.md"
    assert invariants.exists(), f"INVARIANTS.md missing at {invariants}"
    text = invariants.read_text()
    assert len(text) > 500, "INVARIANTS.md is suspiciously short"


def test_invariants_required_sections_present() -> None:
    sections = _invariants_sections()
    for required in REQUIRED_INVARIANT_SECTIONS:
        assert required in sections, (
            f"INVARIANTS.md is missing required H2 section: {required!r}. Found: {sorted(sections)}"
        )


def test_invariants_required_sections_non_empty() -> None:
    sections = _invariants_sections()
    for required in REQUIRED_INVARIANT_SECTIONS:
        body = sections[required]
        assert len(body) > 80, f"Section {required!r} is too short ({len(body)} chars)"


def test_pyproject_parses_and_declares_python_313() -> None:
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml missing"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["name"] == "plex-renamer"
    requires = data["project"]["requires-python"]
    assert requires.startswith(">=3.13"), f"expected Python >=3.13 floor, got {requires!r}"


def test_cli_entry_point_importable() -> None:
    from plex_renamer.cli.main import app

    assert callable(app)


def test_cli_version_flag_exits_zero(capsys) -> None:
    from plex_renamer.cli.main import app

    code = app(["--version"])
    captured = capsys.readouterr()
    assert code == 0
    assert "plex-renamer" in captured.out
    # Sanity: the printed version is the package's declared __version__.
    from plex_renamer import __version__

    assert __version__ in captured.out


def test_cli_unknown_arg_exits_nonzero(capsys) -> None:
    from plex_renamer.cli.main import app

    code = app(["--nope"])
    captured = capsys.readouterr()
    assert code != 0, "unknown argument must not report success"
    assert "unknown argument" in captured.err


def test_python_runtime_meets_floor() -> None:
    assert sys.version_info >= (3, 13), (
        f"runtime Python {sys.version_info} is below the declared 3.13 floor"
    )
