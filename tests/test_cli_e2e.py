"""End-to-end CLI tests.

Spin up a fake source tree, mock the resolver layer (NOT the planner
or TMDBClient class), then drive ``plan -> apply --no-cleanup -> undo``
through ``plex-renamer``'s ``app(argv)`` and assert the filesystem
state at each step.

We mock at the TMDB-client HTTP layer using ``responses``. The CLI
constructs a real TMDBClient + TMDBCache + IMDbFallbackResolver; the
HTTP layer is the only mocked seam.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from plex_renamer.cli.main import app
from plex_renamer.config import settings as settings_module
from plex_renamer.config.settings import Settings


@pytest.fixture
def isolated_app_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect app_config_dir and app_cache_dir to a tmp scratch dir."""
    app_dir = tmp_path / "app_data"
    app_dir.mkdir(parents=True)
    cache_dir = tmp_path / "app_cache"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr("plex_renamer.config.paths.app_config_dir", lambda: app_dir)
    monkeypatch.setattr("plex_renamer.config.paths.app_cache_dir", lambda: cache_dir)
    # Also patch the ones imported into modules that already captured the
    # function at import time (settings, journal).
    monkeypatch.setattr(settings_module, "app_config_dir", lambda: app_dir)
    return app_dir


@pytest.fixture
def stub_settings(isolated_app_dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-seed a Settings file with a fake TMDB key so the CLI uses our mock."""
    s = Settings(tmdb_api_key="fake-key", omdb_api_key=None)
    s._config_path = isolated_app_dirs / "config.json"
    s.save()
    # Ensure first-run hydration is bypassed.
    monkeypatch.chdir(isolated_app_dirs)


def _seed_tmdb_responses(rsps: responses.RequestsMock) -> None:
    """Add canned TMDB HTTP responses for the fake corpus."""
    base = "https://api.themoviedb.org/3"
    # Movie search: The Matrix 1999
    rsps.add(
        method="GET",
        url=f"{base}/search/movie",
        json={
            "results": [
                {
                    "id": 603,
                    "title": "The Matrix",
                    "release_date": "1999-03-30",
                    "imdb_id": "tt0133093",
                }
            ]
        },
        match=[
            responses.matchers.query_param_matcher(
                {"query": "The Matrix", "year": "1999"}, strict_match=False
            )
        ],
    )
    # Catch-all movie search returning empty (so unknown items skip).
    rsps.add(
        method="GET",
        url=f"{base}/search/movie",
        json={"results": []},
    )
    # TV search: Test Show
    rsps.add(
        method="GET",
        url=f"{base}/search/tv",
        json={
            "results": [
                {
                    "id": 9999,
                    "name": "Test Show",
                    "first_air_date": "2020-01-01",
                }
            ]
        },
        match=[responses.matchers.query_param_matcher({"query": "Test Show"}, strict_match=False)],
    )
    rsps.add(
        method="GET",
        url=f"{base}/search/tv",
        json={"results": []},
    )
    # Season for Test Show
    rsps.add(
        method="GET",
        url=f"{base}/tv/9999/season/1",
        json={
            "episodes": [
                {"season_number": 1, "episode_number": 1, "name": "Pilot"},
                {"season_number": 1, "episode_number": 2, "name": "Setup"},
            ]
        },
    )


def test_plan_apply_undo_cli_flow(
    tmp_path: Path, stub_settings: None, isolated_app_dirs: Path
) -> None:
    """Drive plan -> apply -> undo via the CLI and assert filesystem state."""
    # Build a fake source tree.
    source = tmp_path / "source"
    movies_root = tmp_path / "Movies"
    tv_root = tmp_path / "TV"
    source.mkdir(parents=True)
    movie_file = source / "The Matrix 1999.mkv"
    movie_file.write_bytes(b"video-bytes")
    tv_file = source / "Test.Show.S01E01.Pilot.mkv"
    tv_file.write_bytes(b"episode-bytes")

    plan_path = tmp_path / "plan.json"
    journal_path = isolated_app_dirs / "journals" / "batch1.json"
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        _seed_tmdb_responses(rsps)
        code = app(
            [
                "plan",
                "--source",
                str(source),
                "--movies",
                str(movies_root),
                "--tv",
                str(tv_root),
                "--output",
                str(plan_path),
            ]
        )
        assert code == 0
        assert plan_path.exists()

    # Inspect the plan JSON.
    plan_data = json.loads(plan_path.read_text())
    assert plan_data["version"] == 1
    assert len(plan_data["ops"]) >= 1

    # Apply with --no-cleanup.
    code = app(
        [
            "apply",
            "--plan",
            str(plan_path),
            "--no-cleanup",
            "--journal",
            str(journal_path),
        ]
    )
    assert code == 0

    # The movies root has files now (tv may or may not depending on
    # whether the parser classified the .S01E01. shape).
    movie_files = list(movies_root.rglob("*.mkv"))
    assert len(movie_files) >= 1
    # Source still exists.
    assert movie_file.exists()

    # Inspect journal.
    journal_data = json.loads(journal_path.read_text())
    assert journal_data["entries"]
    assert all(e["status"] == "verified" for e in journal_data["entries"])

    # Undo.
    code = app(["undo", "--journal", str(journal_path)])
    assert code == 0
    # Targets gone.
    assert list(movies_root.rglob("*.mkv")) == []
    # Source still there.
    assert movie_file.exists()


def test_cli_unknown_subcommand_exits_two() -> None:
    """The scaffold test asserts --nope -> exit 2; subcommand-shaped errors do too."""
    code = app(["bogus-cmd"])
    assert code == 2


def test_cli_version_still_works() -> None:
    """The scaffold test must keep passing."""
    code = app(["--version"])
    assert code == 0
