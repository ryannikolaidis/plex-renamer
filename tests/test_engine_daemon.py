"""End-to-end JSON-RPC daemon tests.

The daemon is spawned as a subprocess (``uv run plex-renamer-engined``)
with pipes for stdin / stdout. Each test writes one (or more) JSON-RPC
request lines, reads response / progress lines back, and asserts the
expected shapes.

Two sets of tests live here:

1. **In-process tests** — drive ``daemon.server._serve`` directly with
   ``io.StringIO`` so the per-test feedback loop is fast and the test
   doesn't pay subprocess startup cost. These cover every method's
   request/response shape.

2. **Subprocess smoke test** — spawn the real daemon binary via
   ``subprocess.Popen`` and round-trip a ``get_settings`` request. This
   is the closest in-tree analog of what the C# shell will do.

The :mod:`plex_renamer.daemon.methods` module exposes
``set_collaborators`` so we can substitute a ``FakeTMDB`` for the
in-process tests. The subprocess test uses the
``PLEX_RENAMER_DAEMON_BOOTSTRAP`` env-var hook that
:func:`plex_renamer.daemon.server.main` honors to import the same fake
into the subprocess before the loop starts.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from plex_renamer.daemon import methods, server
from plex_renamer.tmdb.models import Episode, MovieResult, TVResult

# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class FakeTMDB:
    """In-memory TMDB stub matching the client / cache method shape.

    Mirrors the ``FakeTMDB`` used by the Qt orchestrator tests so the
    daemon code is exercised against the same protocol the GUI is.
    """

    def __init__(self) -> None:
        self.search_movie_returns: list[MovieResult] = []
        self.search_tv_returns: list[TVResult] = []
        self.find_returns: MovieResult | TVResult | None = None
        self.get_season_returns: list[Episode] = []
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def search_movie(self, title: str, year: int | None) -> list[MovieResult]:
        self._record("search_movie", title, year)
        return list(self.search_movie_returns)

    def search_tv(self, title: str, year: int | None) -> list[TVResult]:
        self._record("search_tv", title, year)
        return list(self.search_tv_returns)

    def find_by_imdb_id(self, imdb_id: str) -> MovieResult | TVResult | None:
        self._record("find_by_imdb_id", imdb_id)
        return self.find_returns

    def get_season(self, tmdb_id: int, season: int) -> list[Episode]:
        self._record("get_season", tmdb_id, season)
        return list(self.get_season_returns)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tmdb(monkeypatch: pytest.MonkeyPatch) -> FakeTMDB:
    """Install a FakeTMDB collaborator on the methods module."""
    tmdb = FakeTMDB()

    from plex_renamer.tmdb.fallback import IMDbFallbackResolver

    def tmdb_factory(_settings):
        return tmdb

    def resolver_factory(t, _settings):
        return IMDbFallbackResolver(tmdb=t, omdb_api_key=None)

    methods.set_collaborators(
        tmdb_factory=tmdb_factory,
        resolver_factory=resolver_factory,
    )
    yield tmdb
    # Restore defaults so we don't bleed between tests.
    methods.set_collaborators(
        tmdb_factory=methods._default_tmdb_factory,
        resolver_factory=methods._default_resolver_factory,
    )


@pytest.fixture
def daemon_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the daemon at a tmp config dir so writes don't hit ~/.config."""
    monkeypatch.setenv("PLEX_RENAMER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _drive_one(request: dict, *, fake_tmdb_present: bool = True) -> dict:
    """Run a single JSON-RPC request through the in-process server.

    Returns the response dict (or raises if multiple lines came out,
    which means a streaming method was misrouted here).
    """
    line = json.dumps(request) + "\n"
    stdin = io.StringIO(line)
    stdout = io.StringIO()
    server._serve(stdin, stdout)
    out = stdout.getvalue().splitlines()
    assert len(out) == 1, f"expected exactly one line, got {len(out)}: {out!r}"
    return json.loads(out[0])


def _drive_streaming(request: dict) -> tuple[list[dict], dict]:
    """Run a streaming request; return (progress_notifications, final_response)."""
    line = json.dumps(request) + "\n"
    stdin = io.StringIO(line)
    stdout = io.StringIO()
    server._serve(stdin, stdout)
    out_lines = stdout.getvalue().splitlines()
    messages = [json.loads(line) for line in out_lines]
    progress = [m for m in messages if "id" not in m]
    finals = [m for m in messages if "id" in m]
    assert len(finals) == 1, f"expected one final response, got {finals!r}"
    return progress, finals[0]


# ---------------------------------------------------------------------------
# In-process method tests.
# ---------------------------------------------------------------------------


def test_get_settings_returns_persisted_values(
    fake_tmdb: FakeTMDB,
    daemon_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """get_settings returns the on-disk config as a dict.

    Forces ``cwd`` to a directory with no ``.env`` so ``Settings.load`` doesn't
    pick up the developer's repo-level ``.env`` (which contains a real TMDB
    key and would make this test fragile in any checkout that has one).
    """
    monkeypatch.chdir(tmp_path)
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "get_settings",
            "params": {},
        }
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["tmdb_api_key"] is None
    assert result["movies_root"] is None


def test_save_settings_persists_and_round_trips(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """save_settings writes the values; the next get_settings reads them back."""
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "save_settings",
            "params": {
                "settings": {
                    "tmdb_api_key": "abc",
                    "movies_root": "/Volumes/Movies",
                }
            },
        }
    )
    assert response["result"]["tmdb_api_key"] == "abc"
    assert (daemon_config_dir / "config.json").exists()

    response2 = _drive_one({"jsonrpc": "2.0", "id": 3, "method": "get_settings", "params": {}})
    assert response2["result"]["tmdb_api_key"] == "abc"
    assert response2["result"]["movies_root"] == "/Volumes/Movies"


def test_parse_inputs_returns_rows_and_groups(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """parse_inputs walks the tree and emits parsed rows + presentation groups."""
    show_dir = tmp_path / "Lazarus"
    s1 = show_dir / "s1"
    s1.mkdir(parents=True)
    (s1 / "Lazarus.S01E01.mkv").write_bytes(b"")
    (s1 / "Lazarus.S01E02.mkv").write_bytes(b"")

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "parse_inputs",
            "params": {"paths": [str(show_dir)]},
        }
    )
    result = response["result"]
    assert len(result["rows"]) == 2
    assert len(result["groups"]) == 1
    assert result["groups"][0]["kind"] == "tv"
    # Both rows share the same group key.
    group_keys = {r["group_key"] for r in result["rows"]}
    assert len(group_keys) == 1


def test_parse_inputs_round_trips_sidecars(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """Sidecars the parser pairs with a video survive the JSON wire shape.

    The planner consumes ``parsed.sidecars`` to rename subtitle / NFO /
    artwork files alongside their video. If the wire shape dropped the
    field, every paired sidecar would silently disappear from the rename
    plan and INVARIANTS.md's "Sidecars and adjacent files" guarantee
    would be broken on the daemon-driven shell. This test pins the
    round-trip via a tree the parser actually pairs (a movie video next
    to a ``poster.jpg`` artwork file).
    """
    movie_dir = tmp_path / "Foo (2020)"
    movie_dir.mkdir()
    (movie_dir / "Foo.2020.mkv").write_bytes(b"")
    (movie_dir / "poster.jpg").write_bytes(b"")

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "parse_inputs",
            "params": {"paths": [str(movie_dir)]},
        }
    )
    rows = response["result"]["rows"]
    video_rows = [r for r in rows if r["parsed"]["kind"] == "movie"]
    assert len(video_rows) == 1
    parsed = video_rows[0]["parsed"]
    sidecars = parsed.get("sidecars")
    # Must be present on the wire shape (not dropped by the encoder).
    assert sidecars is not None, "parsed.sidecars MUST be in the wire shape"
    # The artwork pairing the parser produces survives encoding.
    artwork = [sc for sc in sidecars if sc["kind"] == "artwork"]
    assert len(artwork) == 1
    assert artwork[0]["path"].endswith("poster.jpg")
    # Sidecar shape carries kind/language/modifiers fields per the protocol doc.
    assert "language" in artwork[0]
    assert "modifiers" in artwork[0]


def test_sidecar_schema_round_trip() -> None:
    """sidecar_to_dict / sidecar_from_dict are inverse operations.

    The wire-shape contract is end-to-end: the daemon encodes a Sidecar to
    JSON, the shell can either pass it back verbatim or reconstruct from
    its POCO record. Either way, the daemon's decoder must accept what its
    encoder produced.
    """
    from plex_renamer.daemon import schemas
    from plex_renamer.parser.models import Sidecar

    original = Sidecar(
        path=Path("/abs/path/Foo.en.forced.srt"),
        kind="subtitle",
        language="en",
        modifiers=["forced"],
    )
    encoded = schemas.sidecar_to_dict(original)
    decoded = schemas.sidecar_from_dict(encoded)
    assert decoded.path == original.path
    assert decoded.kind == original.kind
    assert decoded.language == original.language
    assert decoded.modifiers == original.modifiers


def test_parse_result_schema_round_trip_with_sidecars() -> None:
    """parse_result_to_dict / parse_result_from_dict preserve sidecars.

    Specifically pins the field that was dropped in the round-1 wire shape.
    """
    from plex_renamer.daemon import schemas
    from plex_renamer.parser.models import ParseResult, Sidecar

    original = ParseResult(
        source_path=Path("/abs/Foo.2020.mkv"),
        kind="movie",
        title_candidate="Foo",
        year=2020,
        sidecars=[
            Sidecar(
                path=Path("/abs/Foo.2020.en.srt"),
                kind="subtitle",
                language="en",
                modifiers=[],
            ),
            Sidecar(
                path=Path("/abs/poster.jpg"),
                kind="artwork",
                language=None,
                modifiers=[],
            ),
        ],
    )
    encoded = schemas.parse_result_to_dict(original)
    assert "sidecars" in encoded
    assert len(encoded["sidecars"]) == 2
    decoded = schemas.parse_result_from_dict(encoded)
    assert len(decoded.sidecars) == 2
    decoded_subtitle = next(sc for sc in decoded.sidecars if sc.kind == "subtitle")
    assert decoded_subtitle.language == "en"
    decoded_artwork = next(sc for sc in decoded.sidecars if sc.kind == "artwork")
    assert decoded_artwork.language is None


def test_parse_and_resolve_runs_tmdb(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """parse_and_resolve hits TMDB and attaches candidates to TV rows."""
    fake_tmdb.search_tv_returns = [TVResult(tmdb_id=42, title="Lazarus", year=2024)]
    fake_tmdb.get_season_returns = [
        Episode(season=1, episode=1, title="Pilot"),
        Episode(season=1, episode=2, title="Second"),
    ]

    show_dir = tmp_path / "Lazarus"
    s1 = show_dir / "s1"
    s1.mkdir(parents=True)
    (s1 / "Lazarus.S01E01.mkv").write_bytes(b"")
    (s1 / "Lazarus.S01E02.mkv").write_bytes(b"")

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "parse_and_resolve",
            "params": {"paths": [str(show_dir)]},
        }
    )
    result = response["result"]
    assert len(result["rows"]) == 2
    for row in result["rows"]:
        assert row["candidate"] is not None
        assert row["candidate"]["anchor_id"] == "42"
        assert row["candidate"]["kind"] == "tv"
    # Both rows carry the merged episode list.
    assert len(result["rows"][0]["candidate"]["episode_list"]) == 2
    # Errors list is present and empty.
    assert result["errors"] == []


def test_search_tmdb_free_returns_combined_candidates(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """search_tmdb_free combines movie + tv results when kind=any."""
    fake_tmdb.search_movie_returns = [MovieResult(tmdb_id=1, title="Foo", year=2020)]
    fake_tmdb.search_tv_returns = [TVResult(tmdb_id=2, title="Foo (TV)", year=2020)]
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "search_tmdb_free",
            "params": {"query": "Foo", "kind": "any"},
        }
    )
    candidates = response["result"]["candidates"]
    assert len(candidates) == 2
    kinds = {c["kind"] for c in candidates}
    assert kinds == {"movie", "tv"}


def _row_dict_for_test(
    *,
    source_path: str = "/tmp/Foo.2020.mkv",
    title: str = "Foo",
    year: int = 2020,
    kind: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> dict:
    """Build a minimal Row dict that the daemon's edit/find methods accept."""
    return {
        "row_id": source_path,
        "source_path": source_path,
        "parsed": {
            "source_path": source_path,
            "kind": kind,
            "title_candidate": title,
            "year": year,
            "season": season,
            "episode": episode,
            "episode_end": None,
            "episode_title": None,
            "edition_tokens": [],
            "quality_tokens": [],
            "group_tag": None,
            "part_marker": None,
            "raw_filename": source_path.rsplit("/", 1)[-1],
            "parent_dirs": [],
            "skip_reason": None,
        },
        "candidate": None,
        "show_name_hint": None,
        "group_key": f"movie::{source_path}" if kind == "movie" else f"tv::{title}",
        "skip": False,
        "manual_title": None,
        "manual_year": None,
        "manual_season": None,
        "manual_episode": None,
        "manual_edition": None,
        "imdb_id_override": None,
        "anchor_kind_override": None,
    }


def test_find_by_imdb_returns_tmdb_candidate_on_hit(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """find_by_imdb wraps the TMDB /find hit as a TMDB-anchored Candidate."""
    fake_tmdb.find_returns = MovieResult(tmdb_id=42, title="X", year=2010)
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "find_by_imdb",
            "params": {
                "imdb_id": "tt0000042",
                "row": _row_dict_for_test(),
            },
        }
    )
    candidate = response["result"]["candidate"]
    assert candidate["anchor_kind"] == "tmdb"
    assert candidate["anchor_id"] == "42"
    assert candidate["kind"] == "movie"


def test_find_by_imdb_synthesizes_imdb_anchor_on_miss(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """When TMDB has no /find hit, the daemon synthesizes an IMDb-anchored Candidate.

    This mirrors the Qt orchestrator's ``on_imdb_resolve`` behavior so a WPF
    user pasting an unknown IMDb id sees the same placeholder result as the
    macOS Qt user. Without this, the WPF flow would silently drop the IMDb id.
    """
    fake_tmdb.find_returns = None
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "find_by_imdb",
            "params": {
                "imdb_id": "tt9999999",
                "row": _row_dict_for_test(title="Unknown Film", year=1999, kind="movie"),
            },
        }
    )
    candidate = response["result"]["candidate"]
    # The Qt path produces an imdb-anchored Candidate at 0.55 confidence; the
    # daemon must do the same so the user can still proceed with an
    # {imdb-tt9999999} folder anchor.
    assert candidate is not None
    assert candidate["anchor_kind"] == "imdb"
    assert candidate["anchor_id"] == "tt9999999"
    assert candidate["title"] == "Unknown Film"
    assert candidate["year"] == 1999
    assert abs(candidate["confidence"] - 0.55) < 0.001


def test_iterate_anchor_search_returns_variant_used(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """iterate_anchor_search retries cleaned variants on zero results."""
    # Original returns empty; cleaned variant ("Lazarus") returns a hit.
    call_count = {"n": 0}

    def search_tv_side_effect(title, year):
        call_count["n"] += 1
        if title == "Lazarus_2":
            return []
        return [TVResult(tmdb_id=42, title="Lazarus", year=2024)]

    # Monkey-patch search_tv on the fake to switch behavior per call.
    fake_tmdb.search_tv = search_tv_side_effect  # type: ignore[assignment]

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 40,
            "method": "iterate_anchor_search",
            "params": {"query": "Lazarus_2"},
        }
    )
    result = response["result"]
    assert result["variant_used"] == "Lazarus"
    assert result["variant_original"] == "Lazarus_2"
    assert len(result["candidates"]) == 1


def test_select_anchor_propagates_and_hydrates(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """select_anchor applies the chosen candidate to every row in the group."""
    fake_tmdb.get_season_returns = [
        Episode(season=1, episode=1, title="Pilot"),
        Episode(season=1, episode=2, title="Second"),
    ]
    show_dir = tmp_path / "Lazarus"
    s1 = show_dir / "s1"
    s1.mkdir(parents=True)
    (s1 / "Lazarus.S01E01.mkv").write_bytes(b"")
    (s1 / "Lazarus.S01E02.mkv").write_bytes(b"")
    parse_response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "parse_inputs",
            "params": {"paths": [str(show_dir)]},
        }
    )
    rows = parse_response["result"]["rows"]
    group_key = rows[0]["group_key"]

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "select_anchor",
            "params": {
                "rows": rows,
                "group_key": group_key,
                "candidate": {
                    "anchor_kind": "tmdb",
                    "anchor_id": "42",
                    "kind": "tv",
                    "title": "Lazarus",
                    "year": 2024,
                    "confidence": 0.95,
                },
            },
        }
    )
    updated = response["result"]["rows"]
    for row in updated:
        assert row["candidate"] is not None
        assert row["candidate"]["anchor_id"] == "42"
        assert len(row["candidate"]["episode_list"]) == 2


def test_edit_row_applies_overrides(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """edit_row mutates only the targeted row + returns the full list."""
    movie = tmp_path / "Foo.2020.mkv"
    movie.write_bytes(b"")
    parse_response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 60,
            "method": "parse_inputs",
            "params": {"paths": [str(movie)]},
        }
    )
    rows = parse_response["result"]["rows"]
    row_id = rows[0]["row_id"]

    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 61,
            "method": "edit_row",
            "params": {
                "rows": rows,
                "row_id": row_id,
                "overrides": {"manual_title": "Foo (Director's Cut)", "skip": True},
            },
        }
    )
    updated = response["result"]["rows"]
    assert len(updated) == 1
    assert updated[0]["manual_title"] == "Foo (Director's Cut)"
    assert updated[0]["skip"] is True


def test_build_plan_returns_plan_dict(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """build_plan assembles a RenamePlan from the row + candidate state."""
    fake_tmdb.search_tv_returns = [TVResult(tmdb_id=42, title="Lazarus", year=2024)]
    fake_tmdb.get_season_returns = [Episode(season=1, episode=1, title="Pilot")]

    show_dir = tmp_path / "Lazarus"
    s1 = show_dir / "s1"
    s1.mkdir(parents=True)
    (s1 / "Lazarus.S01E01.mkv").write_bytes(b"x")

    resolved = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 70,
            "method": "parse_and_resolve",
            "params": {
                "paths": [str(show_dir)],
                "settings": {
                    "movies_root": str(tmp_path / "movies"),
                    "tv_root": str(tmp_path / "tv"),
                },
            },
        }
    )
    rows = resolved["result"]["rows"]
    plan_response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 71,
            "method": "build_plan",
            "params": {
                "rows": rows,
                "input_root": str(show_dir),
                "settings": {
                    "movies_root": str(tmp_path / "movies"),
                    "tv_root": str(tmp_path / "tv"),
                },
            },
        }
    )
    plan = plan_response["result"]["plan"]
    assert plan["ops"]
    op = plan["ops"][0]
    assert "Lazarus" in op["target"]
    assert op["kind"] == "tv"
    assert op["anchor"] == "tmdb-42"


def test_apply_plan_streams_progress_and_done(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """apply_plan emits progress notifications then a final result."""
    # Build a minimal plan: one movie copy from tmp_path to tmp_path.
    src = tmp_path / "src" / "Matrix.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"hello")
    target = (
        tmp_path / "movies" / "The Matrix (1999) {tmdb-603}" / "The Matrix (1999) {tmdb-603}.mkv"
    )
    plan_dict = {
        "ops": [
            {
                "source": str(src),
                "target": str(target),
                "kind": "movie",
                "anchor": "tmdb-603",
                "edition": None,
                "confidence": 0.9,
                "sidecars": [],
                "warnings": [],
                "detected_editions": [],
            }
        ],
        "collisions": [],
        "skipped": [],
        "movies_root": str(tmp_path / "movies"),
        "tv_root": str(tmp_path / "tv"),
        "input_root": str(tmp_path / "src"),
        "apply_editions": False,
        "warnings": [],
    }

    progress, final = _drive_streaming(
        {
            "jsonrpc": "2.0",
            "id": 80,
            "method": "apply_plan",
            "params": {
                "plan": plan_dict,
                "cleanup": False,
                "verify_hash": False,
            },
        }
    )
    # At least one op_started + one op_verified + one final.
    events = [p["params"].get("event") for p in progress]
    assert "op_started" in events
    assert "op_verified" in events
    assert final["id"] == 80
    report = final["result"]
    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert Path(report["journal_path"]).exists()
    # Progress events carry the total_ops + op_index pair the shell needs
    # for a progress bar; the engine sets these on every emit.
    started = [p for p in progress if p["params"].get("event") == "op_started"][0]
    assert started["params"]["op_index"] == 0
    assert started["params"]["total_ops"] == 1


def test_apply_plan_interleaves_started_and_verified(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path
) -> None:
    """For an N-op plan the daemon emits op_started_i BEFORE op_verified_i,
    not all op_started_* before any op_verified_*. Interleaving is the
    load-bearing property that lets the shell render live progress
    during a multi-minute video-file copy instead of waiting for the
    whole batch to finish.
    """
    ops: list[dict[str, object]] = []
    for i in range(3):
        src = tmp_path / "src" / f"Matrix-{i}.mkv"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"hello")
        target = (
            tmp_path
            / "movies"
            / f"The Matrix ({1999 + i}) {{tmdb-603}}"
            / f"The Matrix ({1999 + i}) {{tmdb-603}}.mkv"
        )
        ops.append(
            {
                "source": str(src),
                "target": str(target),
                "kind": "movie",
                "anchor": "tmdb-603",
                "edition": None,
                "confidence": 0.9,
                "sidecars": [],
                "warnings": [],
                "detected_editions": [],
            }
        )
    plan_dict = {
        "ops": ops,
        "collisions": [],
        "skipped": [],
        "movies_root": str(tmp_path / "movies"),
        "tv_root": str(tmp_path / "tv"),
        "input_root": str(tmp_path / "src"),
        "apply_editions": False,
        "warnings": [],
    }

    progress, _final = _drive_streaming(
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "apply_plan",
            "params": {"plan": plan_dict, "cleanup": False, "verify_hash": False},
        }
    )
    # Walk the event stream and assert started_i appears BEFORE verified_i
    # for every op_index, AND that at least one started_j (j > i) appears
    # AFTER verified_i — i.e. the events are properly interleaved op-by-op
    # rather than batched as "all starts then all verifieds".
    seen_started: set[int] = set()
    seen_verified: set[int] = set()
    interleaved = False
    for p in progress:
        params = p["params"]
        kind = params.get("event")
        idx = params.get("op_index")
        if kind == "op_started":
            assert idx is not None and idx not in seen_verified, (
                f"op_started {idx} arrived after op_verified {idx}"
            )
            if seen_verified:
                interleaved = True
            seen_started.add(idx)
        elif kind == "op_verified":
            assert idx is not None and idx in seen_started, (
                f"op_verified {idx} arrived before op_started {idx}"
            )
            seen_verified.add(idx)
    assert seen_started == {0, 1, 2}
    assert seen_verified == {0, 1, 2}
    assert interleaved, (
        "all op_started events arrived before any op_verified — the daemon "
        "is still in batch-emit mode, not per-op streaming"
    )


def test_undo_batch_reverts(fake_tmdb: FakeTMDB, daemon_config_dir: Path, tmp_path: Path) -> None:
    """undo_batch reads a journal and inverts the applied operations."""
    # First apply a plan so there's a journal to undo.
    src = tmp_path / "src" / "Matrix.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"hello")
    target = (
        tmp_path / "movies" / "The Matrix (1999) {tmdb-603}" / "The Matrix (1999) {tmdb-603}.mkv"
    )
    plan_dict = {
        "ops": [
            {
                "source": str(src),
                "target": str(target),
                "kind": "movie",
                "anchor": "tmdb-603",
                "edition": None,
                "confidence": 0.9,
                "sidecars": [],
                "warnings": [],
                "detected_editions": [],
            }
        ],
        "collisions": [],
        "skipped": [],
        "movies_root": str(tmp_path / "movies"),
        "tv_root": str(tmp_path / "tv"),
        "input_root": str(tmp_path / "src"),
        "apply_editions": False,
        "warnings": [],
    }
    _, final = _drive_streaming(
        {
            "jsonrpc": "2.0",
            "id": 90,
            "method": "apply_plan",
            "params": {"plan": plan_dict, "cleanup": False, "verify_hash": False},
        }
    )
    journal_path = final["result"]["journal_path"]
    assert target.exists()

    # Now undo.
    response = _drive_one(
        {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "undo_batch",
            "params": {"journal_path": journal_path},
        }
    )
    assert response["result"]["reverted"] == 1
    assert not target.exists()


# ---------------------------------------------------------------------------
# Error path tests.
# ---------------------------------------------------------------------------


def test_unknown_method_returns_error(fake_tmdb: FakeTMDB, daemon_config_dir: Path) -> None:
    response = _drive_one({"jsonrpc": "2.0", "id": 100, "method": "no_such_method", "params": {}})
    assert "error" in response
    assert response["error"]["code"] == -32601


def test_invalid_json_yields_parse_error(fake_tmdb: FakeTMDB, daemon_config_dir: Path) -> None:
    stdin = io.StringIO("not json\n")
    stdout = io.StringIO()
    server._serve(stdin, stdout)
    out = stdout.getvalue().splitlines()
    assert len(out) == 1
    response = json.loads(out[0])
    assert response["error"]["code"] == -32700
    assert response["id"] is None


def test_request_that_is_not_object_yields_invalid_request_error(
    fake_tmdb: FakeTMDB, daemon_config_dir: Path
) -> None:
    """A JSON-valid request that's an array or string is rejected with -32600."""
    stdin = io.StringIO(json.dumps([1, 2, 3]) + "\n")
    stdout = io.StringIO()
    server._serve(stdin, stdout)
    out = stdout.getvalue().splitlines()
    assert len(out) == 1
    response = json.loads(out[0])
    assert response["error"]["code"] == -32600
    assert response["id"] is None


def test_shutdown_method_ends_loop(fake_tmdb: FakeTMDB, daemon_config_dir: Path) -> None:
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "get_settings", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
        + "\n"
        # This third request is past the shutdown; the server should NOT consume it.
        + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "get_settings", "params": {}})
        + "\n"
    )
    stdout = io.StringIO()
    server._serve(stdin, stdout)
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["id"] == 2
    assert second["result"]["ok"] is True


# ---------------------------------------------------------------------------
# Subprocess smoke test.
# ---------------------------------------------------------------------------


def test_subprocess_round_trip_get_settings(tmp_path: Path) -> None:
    """Spawn the daemon binary, send get_settings, parse the response."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env = os.environ.copy()
    env["PLEX_RENAMER_CONFIG_DIR"] = str(config_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "plex_renamer.daemon.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "get_settings",
            "params": {},
        }
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        response_line = proc.stdout.readline()
        response = json.loads(response_line)
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        # Clean shutdown.
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "shutdown"}) + "\n")
        proc.stdin.flush()
        shutdown_line = proc.stdout.readline()
        shutdown_response = json.loads(shutdown_line)
        assert shutdown_response["id"] == 2
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_subprocess_bootstrap_hook_loads_fake_tmdb(tmp_path: Path) -> None:
    """The bootstrap hook lets a subprocess swap collaborators without code edits."""
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        textwrap.dedent(
            """\
            from plex_renamer.daemon import methods
            from plex_renamer.tmdb.fallback import IMDbFallbackResolver
            from plex_renamer.tmdb.models import MovieResult, TVResult, Episode


            class _Fake:
                def search_movie(self, title, year):
                    return [MovieResult(tmdb_id=1, title=title, year=year)]

                def search_tv(self, title, year):
                    return [TVResult(tmdb_id=2, title=title, year=year)]

                def find_by_imdb_id(self, imdb_id):
                    return None

                def get_season(self, tmdb_id, season):
                    return []


            _tmdb = _Fake()

            methods.set_collaborators(
                tmdb_factory=lambda settings: _tmdb,
                resolver_factory=lambda t, s: IMDbFallbackResolver(tmdb=t, omdb_api_key=None),
            )
            """
        )
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env = os.environ.copy()
    env["PLEX_RENAMER_CONFIG_DIR"] = str(config_dir)
    env["PLEX_RENAMER_DAEMON_BOOTSTRAP"] = str(bootstrap)

    proc = subprocess.Popen(
        [sys.executable, "-m", "plex_renamer.daemon.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "search_tmdb_free",
            "params": {"query": "Lazarus", "kind": "tv"},
        }
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        response = json.loads(proc.stdout.readline())
        assert response["id"] == 1
        candidates = response["result"]["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["title"] == "Lazarus"
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "shutdown"}) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
