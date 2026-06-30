"""Persisted settings.

The :class:`Settings` class is the canonical accessor for every persisted
preference the app holds: API keys, library roots, toggle states. The JSON
file lives at ``app_config_dir() / "config.json"``.

First-run behavior
------------------

When no config file exists yet, :meth:`Settings.load` consults ``.env``
(via ``python-dotenv``) for ``TMDB_API_KEY`` and ``OMDB_API_KEY`` and
persists whichever values it finds. The ``.env`` is read from the current
working directory by default; callers (e.g. the CLI) can pass an explicit
``dotenv_path`` to point at a different file.

Subsequent runs ignore ``.env`` even if the file still exists. The
app-config JSON is the single source of truth once it has been written.
This matches the user-visible behavior described in INVARIANTS.md: the
user edits the key in the settings dialog, not in ``.env``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from plex_renamer.config.paths import app_config_dir

CONFIG_FILENAME = "config.json"


@dataclass
class Settings:
    """Persisted user preferences.

    The fields here are intentionally a strict superset of what slice 3
    needs (only the two API keys); the remaining fields are placeholders
    so slice 4 (library roots, cleanup toggle) and slice 5 (UI toggles)
    can populate them without a schema migration.
    """

    tmdb_api_key: str | None = None
    omdb_api_key: str | None = None
    tvdb_api_key: str | None = None
    tvdb_pin: str | None = None

    # Slice-4+ placeholders. We persist them as ``None`` until those slices
    # land so the on-disk schema is stable across the whole project.
    movies_root: str | None = None
    tv_root: str | None = None
    cleanup_enabled: bool = False
    auto_accept_top_hit: bool = False

    # Internal: where this Settings object was loaded from / will save to.
    # Not persisted to JSON.
    _config_path: Path | None = field(default=None, repr=False, compare=False)

    # ----- Persistence ------------------------------------------------------

    @classmethod
    def load(
        cls,
        config_path: Path | None = None,
        dotenv_path: Path | None = None,
    ) -> Settings:
        """Load settings from disk; ``.env`` fills any unset fields.

        Resolution order:

        1. If ``config_path`` (or the default ``app_config_dir() / config.json``)
           exists, load it. For any field that is still ``None`` after the
           load, ``.env`` is consulted and a matching key copies in. This
           lets newly-introduced fields (e.g. ``TVDB_API_KEY``) land via
           ``.env`` even after the config file already exists from an
           earlier release. Filling from ``.env`` does NOT auto-save; the
           file is rewritten only when the user explicitly mutates a
           field via :meth:`save`.
        2. If no config file exists, construct an empty :class:`Settings`,
           hydrate from ``.env``, and persist.
        """
        path = config_path if config_path is not None else app_config_dir() / CONFIG_FILENAME
        if path.exists():
            settings = cls._load_from_file(path)
            settings._hydrate_from_dotenv(dotenv_path, only_unset=True)
            return settings
        # First run: pull from .env, then persist.
        settings = cls(_config_path=path)
        settings._hydrate_from_dotenv(dotenv_path)
        settings.save()
        return settings

    @classmethod
    def _load_from_file(cls, path: Path) -> Settings:
        with path.open("r", encoding="utf-8") as fp:
            data: dict[str, Any] = json.load(fp)
        kwargs = cls._filter_known_fields(data)
        settings = cls(**kwargs)
        settings._config_path = path
        return settings

    @classmethod
    def _filter_known_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Drop unknown keys; future-proofs against forward-only readers."""
        known = {f.name for f in fields(cls) if not f.name.startswith("_")}
        return {k: v for k, v in data.items() if k in known}

    def save(self) -> None:
        """Persist to ``self._config_path`` (creating parent dirs as needed)."""
        if self._config_path is None:
            self._config_path = app_config_dir() / CONFIG_FILENAME
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        with self._config_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, sort_keys=True)

    # ----- Mutators (auto-save) --------------------------------------------

    def set_tmdb_api_key(self, key: str | None) -> None:
        """Update the TMDB API key and persist immediately."""
        self.tmdb_api_key = key
        self.save()

    def set_omdb_api_key(self, key: str | None) -> None:
        """Update the OMDB API key and persist immediately."""
        self.omdb_api_key = key
        self.save()

    # ----- Internals --------------------------------------------------------

    def _hydrate_from_dotenv(
        self,
        dotenv_path: Path | None,
        *,
        only_unset: bool = False,
    ) -> None:
        """Read API keys / PINs from .env into this instance.

        Uses ``dotenv_values`` rather than ``load_dotenv`` so the values do
        NOT bleed into ``os.environ``; we want them isolated to the config
        file. Empty strings are treated as "not set."

        ``only_unset=True`` means we only copy a value from .env when
        the corresponding field on ``self`` is still ``None``. Used by
        :meth:`load` when a config file already exists, so config.json
        always wins over .env for fields the user has explicitly set.
        """
        env_path: str | None
        if dotenv_path is not None:
            env_path = str(dotenv_path)
        else:
            cwd_env = Path.cwd() / ".env"
            env_path = str(cwd_env) if cwd_env.exists() else None
        if env_path is None:
            return
        values = dotenv_values(env_path)
        mapping = [
            ("TMDB_API_KEY", "tmdb_api_key"),
            ("OMDB_API_KEY", "omdb_api_key"),
            ("TVDB_API_KEY", "tvdb_api_key"),
            ("TVDB_PIN", "tvdb_pin"),
        ]
        for env_key, attr in mapping:
            value = values.get(env_key)
            if not value:
                continue
            if only_unset and getattr(self, attr) is not None:
                continue
            setattr(self, attr, value)
