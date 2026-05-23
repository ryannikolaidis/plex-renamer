"""Tests for :class:`Settings` persistence and the app-config / cache path helpers.

The behavioral contract these tests pin:

- First run with no config.json: read ``.env`` and persist.
- Subsequent runs: config.json wins; ``.env`` is NOT consulted even if present.
- ``set_tmdb_api_key`` mutates and persists in one call.
- ``app_config_dir`` / ``app_cache_dir`` resolve to the platformdirs-derived
  paths (monkeypatched to a scratch dir for the tests).

Every test uses ``tmp_path`` plus monkeypatch on the ``platformdirs``
functions so no test ever touches the real user config dir.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest

from plex_renamer.config import Settings, app_cache_dir, app_config_dir
from plex_renamer.config.settings import CONFIG_FILENAME


@pytest.fixture
def scratch_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect platformdirs at a scratch dir for the duration of the test.

    Returns a dict with ``config`` and ``cache`` paths so the test can
    assert on them directly.
    """
    config = tmp_path / "config"
    cache = tmp_path / "cache"

    def fake_user_config_dir(name: str, appauthor: object = None) -> str:
        return str(config / name)

    def fake_user_cache_dir(name: str, appauthor: object = None) -> str:
        return str(cache / name)

    monkeypatch.setattr(platformdirs, "user_config_dir", fake_user_config_dir)
    monkeypatch.setattr(platformdirs, "user_cache_dir", fake_user_cache_dir)
    return {"config": config / "plex-renamer", "cache": cache / "plex-renamer"}


def test_app_config_dir_uses_platformdirs(scratch_dirs: dict[str, Path]) -> None:
    assert app_config_dir() == scratch_dirs["config"]


def test_app_cache_dir_uses_platformdirs(scratch_dirs: dict[str, Path]) -> None:
    assert app_cache_dir() == scratch_dirs["cache"]


def test_first_run_reads_dotenv_and_persists(scratch_dirs: dict[str, Path], tmp_path: Path) -> None:
    """No config.json + .env present: read .env, persist, and the file appears."""
    env = tmp_path / ".env"
    env.write_text("TMDB_API_KEY=k1\nOMDB_API_KEY=o1\n", encoding="utf-8")
    config_path = scratch_dirs["config"] / CONFIG_FILENAME
    assert not config_path.exists()

    settings = Settings.load(dotenv_path=env)

    assert settings.tmdb_api_key == "k1"
    assert settings.omdb_api_key == "o1"
    assert config_path.exists()


def test_subsequent_run_ignores_dotenv(scratch_dirs: dict[str, Path], tmp_path: Path) -> None:
    """Once config.json exists, .env is not consulted again."""
    # Seed the config file as a "prior run."
    config_path = scratch_dirs["config"] / CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"tmdb_api_key": "stored_key"}', encoding="utf-8")

    # Place a different value in .env. It must NOT win.
    env = tmp_path / ".env"
    env.write_text("TMDB_API_KEY=dotenv_key\n", encoding="utf-8")

    settings = Settings.load(dotenv_path=env)
    assert settings.tmdb_api_key == "stored_key"


def test_first_run_without_dotenv_persists_empty_keys(
    scratch_dirs: dict[str, Path], tmp_path: Path
) -> None:
    """No config + no .env: empty Settings is persisted with keys = None."""
    config_path = scratch_dirs["config"] / CONFIG_FILENAME
    nonexistent = tmp_path / "missing.env"
    assert not nonexistent.exists()

    settings = Settings.load(dotenv_path=nonexistent)
    assert settings.tmdb_api_key is None
    assert settings.omdb_api_key is None
    assert config_path.exists()


def test_set_tmdb_api_key_mutates_and_persists(scratch_dirs: dict[str, Path]) -> None:
    """The setter persists in one call; re-loading reflects the new value."""
    config_path = scratch_dirs["config"] / CONFIG_FILENAME
    settings = Settings.load()  # first run, no .env -> empty + persisted
    settings.set_tmdb_api_key("new_key")

    reloaded = Settings.load()
    assert reloaded.tmdb_api_key == "new_key"
    assert config_path.exists()


def test_set_omdb_api_key_mutates_and_persists(scratch_dirs: dict[str, Path]) -> None:
    settings = Settings.load()
    settings.set_omdb_api_key("omdb_new")
    reloaded = Settings.load()
    assert reloaded.omdb_api_key == "omdb_new"


def test_unknown_fields_in_config_are_dropped(scratch_dirs: dict[str, Path]) -> None:
    """Forward-compatibility: a future schema's extra fields don't crash older code."""
    config_path = scratch_dirs["config"] / CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"tmdb_api_key": "k", "future_field": "ignored"}',
        encoding="utf-8",
    )
    settings = Settings.load()
    assert settings.tmdb_api_key == "k"


def test_dotenv_empty_value_does_not_overwrite(
    scratch_dirs: dict[str, Path], tmp_path: Path
) -> None:
    """A .env with an empty key shouldn't blank out values; we treat empty as "not set"."""
    env = tmp_path / ".env"
    env.write_text("TMDB_API_KEY=\n", encoding="utf-8")
    settings = Settings.load(dotenv_path=env)
    assert settings.tmdb_api_key is None
