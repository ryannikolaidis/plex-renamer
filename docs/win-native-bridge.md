# plex-renamer engine bridge: JSON-RPC protocol

The Python sidecar (`plex-renamer-engined`) talks JSON-RPC 2.0 over `stdin` / `stdout` to native shells (the WPF Windows app, future native shells on other platforms). One JSON object per line, newline-delimited (`\n`). Responses are unbuffered — the daemon flushes `stdout` after every write.

This document is the **source of truth** for the protocol. The C# shell mirrors these shapes as POCO records. The Python daemon's test suite (`tests/test_engine_daemon.py`) exercises every shape documented here; if the doc and the implementation disagree, file a bug against whichever is wrong.

## Lifecycle

1. **Spawn.** The shell starts the sidecar as a child process (`plex-renamer-engined.exe` on Windows, `plex-renamer-engined` script via `uv run` in dev). The sidecar runs continuously until told otherwise.
2. **Requests / responses.** Each line of `stdin` is one JSON-RPC 2.0 request:
   ```
   {"jsonrpc":"2.0","id":<int|str>,"method":"<name>","params":{...}}
   ```
   The daemon writes one response line to `stdout` for every request:
   ```
   {"jsonrpc":"2.0","id":<same>,"result":{...}}
   ```
   or, on error:
   ```
   {"jsonrpc":"2.0","id":<same>,"error":{"code":<int>,"message":"...","data":{...}}}
   ```
   The `id` round-trips verbatim so the shell can match responses to its pending promises.
3. **Streaming.** The `apply_plan` method may emit zero or more `progress` notifications BEFORE the final `result` response. Notifications have no `id`:
   ```
   {"jsonrpc":"2.0","method":"progress","params":{"id":<original_req_id>,...}}
   ```
   The shell uses `params.id` to associate the notification with the original request. Exactly one `result` response (with matching `id`) closes the request. See the `apply_plan` section below for the specific event types and the current (post-hoc) timing.
4. **Shutdown.** Two clean exits:
   - Send `{"jsonrpc":"2.0","id":<n>,"method":"shutdown"}`. Daemon responds `{"result":{"ok":true}}` and returns.
   - Close the daemon's stdin (EOF). Daemon returns with no extra output.

   Additional path: SIGINT (Ctrl+C) is caught by Python's default `KeyboardInterrupt` handler; the loop exits with code 0. SIGTERM is NOT installed by the daemon — on SIGTERM the OS terminates the process without an orderly shutdown. The shell is expected to send `shutdown` or close stdin before terminating the child.

## Error codes

Standard JSON-RPC 2.0 plus one local extension:

| Code     | Symbol                  | Meaning                                              |
|----------|-------------------------|------------------------------------------------------|
| `-32700` | `ERR_PARSE`             | Request line was not valid JSON.                     |
| `-32600` | `ERR_INVALID_REQUEST`   | JSON parsed but not a JSON-RPC request object.       |
| `-32601` | `ERR_METHOD_NOT_FOUND`  | Unknown method name.                                 |
| `-32602` | `ERR_INVALID_PARAMS`    | Params dict missing a required key or wrong shape.   |
| `-32603` | `ERR_INTERNAL`          | Daemon-side bug.                                     |
| `-32000` | `ERR_APP`               | Application error from the engine (e.g. TMDB 503).   |

## Common shapes

### `Settings`

```json
{
  "tmdb_api_key": "string",
  "omdb_api_key": "string",
  "movies_root": "/absolute/path",
  "tv_root": "/absolute/path",
  "cleanup_enabled": false,
  "auto_accept_top_hit": false
}
```

### `ParseResult`

```json
{
  "source_path": "/absolute/path/to/file.mkv",
  "kind": "movie | tv | unknown",
  "title_candidate": "Foo Bar",
  "year": 2018,
  "season": 1,
  "episode": 4,
  "episode_end": null,
  "episode_title": "Pilot",
  "edition_tokens": ["Director's Cut"],
  "quality_tokens": ["1080p", "x264"],
  "group_tag": "RARBG",
  "part_marker": null,
  "raw_filename": "Foo.Bar.2018.S01E04.Pilot.1080p.x264-RARBG.mkv",
  "parent_dirs": ["Foo Bar", "Season 1"],
  "skip_reason": null,
  "sidecars": [<Sidecar>, ...]
}
```

`skip_reason` (when non-null): `{"reason": "<short>", "detail": "<long>"}`. Reasons: `not_a_media_file`, `in_progress_download`, `excluded_extension`.

`sidecars` lists the subtitle / NFO / artwork files the parser paired with this video. The shell carries these through `parse_inputs` → `edit_row` → `build_plan` so the planner can rename sidecars alongside their video. Dropping the field on the wire silently loses every sidecar from the rename plan.

### `Sidecar`

```json
{
  "path": "/absolute/path/to/sidecar.srt",
  "kind": "subtitle | nfo | artwork",
  "language": "en",
  "modifiers": ["forced"]
}
```

`language` is a two-letter or BCP-47-ish tag (`en`, `en-GB`, `es`), `null` when no language was extractable (typical for `.nfo` and artwork). `modifiers` are subtitle-only attributes like `"forced"` or `"sdh"`; empty list for non-subtitle sidecars.

### `Candidate`

```json
{
  "anchor_kind": "tmdb | imdb",
  "anchor_id": "12345",
  "kind": "movie | tv",
  "title": "Foo Bar",
  "year": 2018,
  "confidence": 0.92,
  "episode_list": [
    {"season": 1, "episode": 1, "title": "Pilot", "air_date": "2018-09-25"},
    ...
  ]
}
```

`confidence` is `[0.0, 1.0]`; the bands the GUI uses are >= 0.85 auto-accept (green), >= 0.60 needs-review (yellow), < 0.60 unresolved (red). The shell renders the band; the daemon returns the raw float.

### `Row`

A source-row carried across the wire. The shell holds these in its own state and passes them back to the daemon on every method call that needs row context.

```json
{
  "row_id": "/absolute/path/to/file.mkv",
  "parsed": <ParseResult>,
  "candidate": <Candidate | null>,
  "show_name_hint": "Foo Bar",
  "group_key": "tv::Foo Bar",
  "skip": false,
  "manual_title": null,
  "manual_year": null,
  "manual_season": null,
  "manual_episode": null,
  "manual_edition": null,
  "imdb_id_override": null,
  "anchor_kind_override": null
}
```

`group_key` shape: `movie::<source_path>` for movies (one row per group), `tv::<show_hint>` for TV (1..N rows). `row_id` is stable across calls — the shell uses it to refer to a specific row in `edit_row`. The row's source path lives at `parsed.source_path`.

### `Group`

```json
{
  "group_key": "tv::Foo Bar",
  "kind": "movie | tv",
  "label": "Foo Bar",
  "row_ids": ["row_id_a", "row_id_b", ...]
}
```

Group `label` follows the panel-label rule from `INVARIANTS.md`: derived from `show_name_hint` when unresolved, from the Candidate title-with-year when resolved.

### `RenamePlan`

```json
{
  "ops": [<RenameOp>, ...],
  "collisions": [<Collision>, ...],
  "skipped": [{"path": "/abs", "reason": "short"}, ...],
  "movies_root": "/abs/movies",
  "tv_root": "/abs/tv",
  "input_root": "/abs/input",
  "apply_editions": false,
  "warnings": ["..."]
}
```

### `RenameOp`

```json
{
  "source": "/absolute/source",
  "target": "/absolute/target",
  "kind": "movie | tv",
  "anchor": "tmdb-12345 | imdb-tt67890",
  "edition": "Director's Cut",
  "confidence": 0.92,
  "sidecars": [["/abs/src.srt", "/abs/dst.en.srt"], ...],
  "warnings": ["..."],
  "detected_editions": ["..."]
}
```

### `Collision`

```json
{
  "target": "/abs/target/path",
  "sources": ["/abs/src1", "/abs/src2"],
  "reason": "duplicate_input | existing_target | within_batch_conflict"
}
```

### `RunReport`

```json
{
  "succeeded": 12,
  "failed": 0,
  "skipped": 1,
  "cleanup_ran": false,
  "journal_path": "/abs/journals/2025-05-25T12-34-56.json",
  "error_messages": []
}
```

### `Error` (per-row resolver errors carried in some method results)

```json
{"source_path": "/abs/src", "message": "TMDB 503: ..."}
```

The shell renders these verbatim in its Errors pane. Keyed on `source_path` because `row_id` and `source_path` are equivalent in current shapes and matching the planner's `skipped` list (also keyed on path) keeps the shell's error-rendering uniform.

## Methods

All response shapes below are the **literal `result` value** of the JSON-RPC response (i.e. they are NOT wrapped in an outer `{settings: ...}` or `{report: ...}` envelope). The shell can deserialize the `result` field directly into the documented shape.

### `get_settings`

Returns the current settings from the OS-appropriate config location (`%APPDATA%\plex-renamer\config.json` on Windows, `~/Library/Application Support/plex-renamer/config.json` on macOS). The daemon also honors a `PLEX_RENAMER_CONFIG_DIR` env var override (test/install-time only); production shells should never set it.

**Request**: `{}` (no params)

**Result**: A `Settings` dict (flat).

### `save_settings`

Persists the given settings to disk and returns the persisted shape. Cached TMDB client(s) keyed on the prior credential pair are dropped so the next TMDB-touching call rebuilds with the new key.

**Request**:
```json
{"settings": <Settings>}
```

**Result**: A `Settings` dict (flat) — the persisted result.

### `parse_inputs`

Walks each input path with the engine's `parse_tree`, returning parsed rows + group keys but NOT running TMDB resolve. Use this when the shell wants parse-only output (rare; typically use `parse_and_resolve`).

**Request**:
```json
{"paths": ["/abs1", "/abs2"]}
```

**Result**:
```json
{
  "rows": [<Row>, ...],
  "groups": [<Group>, ...],
  "input_root": "/abs/common-parent"
}
```

### `parse_and_resolve`

Walks the input paths, parses, runs per-row TMDB resolve (with IMDb fallback), and assigns confidence bands. This is the high-level method the shell calls on drop.

**Request**:
```json
{
  "paths": ["/abs1", "/abs2"],
  "settings": <Settings>  // optional; falls back to on-disk config
}
```

**Result**:
```json
{
  "rows": [<Row>, ...],
  "groups": [<Group>, ...],
  "input_root": "/abs/common-parent",
  "errors": [<Error>, ...]
}
```

`errors` is per-row; a row failing to resolve does not abort the call. The Row's `candidate` is `null` in that case.

### `search_tmdb_free`

Free-text search for the per-row edit pane. The shell debounces user keystrokes; each round-trip is a single search call.

**Request**:
```json
{
  "query": "foo bar",
  "kind": "movie | tv | any",   // "any" hits both endpoints; defaults to "any"
  "settings": <Settings>  // optional; falls back to on-disk config
}
```

**Result**:
```json
{
  "candidates": [<Candidate>, ...],
  "error": "..."  // optional; present when one of the search endpoints raised
}
```

If `kind="any"` and one endpoint raises while the other succeeds, the result includes whatever candidates came back from the successful endpoint plus an `error` field describing the failure. The shell can render partial results.

### `find_by_imdb`

Resolves an IMDb ID (`ttNNNNNNN`) via TMDB's `/find/{external_id}` endpoint. When TMDB has no hit, the daemon synthesizes an IMDb-anchored Candidate from the supplied row's parsed title/year/kind/season at confidence 0.55 — same shape the Qt orchestrator produces — so the user can still proceed with an `{imdb-tt...}` folder anchor.

**Request**:
```json
{
  "imdb_id": "tt1234567",
  "row": <Row>,           // the row the user pasted the id on
  "settings": <Settings>  // optional
}
```

**Result**:
```json
{
  "candidate": <Candidate>,   // anchor_kind="tmdb" on TMDB hit; anchor_kind="imdb" on TMDB miss (synthesized at confidence 0.55)
  "errors": [<Error>, ...]    // typically empty; season-hydration failures appear here when the TMDB hit is a TV show
}
```

The `candidate` is never `null` — the daemon always returns either a TMDB-anchored Candidate (on hit) or an IMDb-anchored Candidate synthesized from the row (on miss). The shell renders both bands appropriately.

### `iterate_anchor_search`

Runs TMDB search for a TV show with the zero-result cleaned-variant retries (strips trailing `_<digits>`, parenthesized suffixes, leading `"The "`). The picker dialog drives this on every search-box change.

**Request**:
```json
{
  "query": "Foo Bar",
  "year": 2024,           // optional; passed to TMDB search
  "settings": <Settings>  // optional
}
```

**Result**:
```json
{
  "candidates": [<Candidate>, ...],
  "variant_used": "Foo Bar",       // the query string that actually produced results (may differ from `query`)
  "variant_original": "Foo Bar",   // the literal request query
  "variants_tried": ["Foo Bar (UK)", "Foo Bar"]  // the cleaned-variant chain in order
}
```

When `variant_used == variant_original`, the original query returned results without retry. When they differ, the shell renders a fallback notice in the picker naming the variant used.

### `select_anchor`

Propagates a picked anchor to every row in a group. Fetches the season episode lists for the TV show and hydrates per-row title matches against the TMDB episode list (fuzzy match first, S/E numbers as tiebreaker, per `INVARIANTS.md`'s Identification rule).

**Request**:
```json
{
  "rows": [<Row>, ...],            // the current row state — every row, not just the group's
  "group_key": "tv::Foo Bar",      // which group the anchor applies to
  "candidate": <Candidate>,        // the picked Candidate
  "settings": <Settings>           // optional
}
```

**Result**:
```json
{
  "rows": [<Row>, ...],            // the full updated row list (rows outside the group are unchanged)
  "errors": [<Error>, ...]         // typically empty; season-hydration failures land here
}
```

### `edit_row`

Applies per-row overrides (title / year / S / E / edition / IMDb-ID / anchor-type-toggle / skip) and recomputes the row's `Candidate` + target path. The shell passes the full current row list back, identifies the target by `row_id`, and receives the full list with one row updated.

**Request**:
```json
{
  "rows": [<Row>, ...],
  "row_id": "/abs/source/path",
  "overrides": {
    "manual_title": "Foo Bar",
    "manual_year": 2018,
    "manual_season": 1,
    "manual_episode": 4,
    "manual_edition": "Director's Cut",
    "imdb_id_override": "tt1234567",
    "anchor_kind_override": "imdb | tmdb | null",
    "show_name_hint": "Foo Bar",
    "skip": false,
    "candidate": <Candidate>     // optional: shell can attach a fully-formed candidate from a search pick
  },
  "settings": <Settings>           // optional
}
```

`overrides` keys are all optional; only the keys present apply changes. Pass `null` for a key to clear an existing override.

**Result**:
```json
{
  "rows": [<Row>, ...]             // the full updated row list (one row mutated)
}
```

### `build_plan`

Assembles the current resolved state (rows + their candidates + overrides) into a `RenamePlan` with collisions detected.

**Request**:
```json
{
  "rows": [<Row>, ...],
  "input_root": "/abs/input",      // optional; defaults to the common parent of the rows' source paths
  "apply_editions": false,         // optional
  "settings": <Settings>           // optional
}
```

**Result**:
```json
{"plan": <RenamePlan>}
```

### `apply_plan` (streaming)

Executes the plan, copying source files to their canonical Plex paths, optionally cleaning up sources, and writing the journal. Emits progress notifications around the executor call (see "Current timing" below), then exactly one `result` response.

**Request**:
```json
{
  "plan": <RenamePlan>,
  "cleanup": false,
  "verify_hash": false,
  "settings": <Settings>           // optional
}
```

**Notifications** (zero or more, in order):
```json
{"jsonrpc":"2.0","method":"progress","params":{
  "id": <original_req_id>,
  "event": "op_started | op_verified | op_failed",
  "op_index": 3,                   // 0-based; present on every event
  "total_ops": 12,                 // plan size; present on every event
  "source": "/abs/src",
  "target": "/abs/target",
  "total_bytes": 12345,            // present on op_started; source size or null if stat failed
  "bytes": 12345,                  // present on op_verified only; target size after copy
  "error": "..."                   // present on op_failed only
}}
```

**Per-op streaming.** The daemon emits `op_started` for op N immediately BEFORE its `shutil.copy2` runs, then `op_verified` (or `op_failed`) for the same N AFTER the copy + verification completes, then `op_started` for op N+1, etc. This interleaved cadence is the load-bearing property that lets the shell render a live progress bar during multi-minute video-file copies. Pairing rule: a `op_verified` / `op_failed` event with `op_index == N` ALWAYS follows the matching `op_started` with `op_index == N`, and a later `op_started` with `op_index > N` cannot appear until the prior op resolved. Use the `total_ops` field to size the progress UI; use `op_index + 1` against it for percent-complete.

**Final result**: A `RunReport` dict (flat).

### `undo_batch`

Reads a journal and inverts every operation. When cleanup did not run, undo restores fully (deletes the new copy; source still exists). When cleanup ran, undo restores copied targets to a "review" folder under the library root and reports that sources are non-recoverable.

**Request**:
```json
{"journal_path": "/abs/journals/2025-05-25T12-34-56.json"}
```

**Result** (flat — no `report` envelope):
```json
{
  "reverted": 12,
  "moved_to_review": 0,
  "review_dir": null,
  "sources_recoverable": true
}
```

### `shutdown`

Special-cased in the dispatch loop (not in the public method table). Closes the daemon cleanly.

**Request**:
```json
{"jsonrpc":"2.0","id":<n>,"method":"shutdown"}
```

**Result**:
```json
{"ok": true}
```

## Environment overrides

The daemon honors three environment variables. Production shells should leave all three unset; tests and installer scripts use them.

| Env var                              | Honored when           | Purpose                                                                                 |
|--------------------------------------|------------------------|-----------------------------------------------------------------------------------------|
| `PLEX_RENAMER_CONFIG_DIR`            | always                 | Override the config directory (`config.json` location). Tests redirect to `tmp_path`.   |
| `PLEX_RENAMER_JOURNAL_DIR`           | always                 | Override the journals directory. Tests redirect; production uses the default.           |
| `PLEX_RENAMER_DAEMON_BOOTSTRAP`      | dev/source builds only | Runs a Python file before the dispatch loop. **Hard-disabled** in PyInstaller builds (`sys.frozen` is set), so the shipped binary cannot be tricked into executing arbitrary code via this var. Tests use it to install a `FakeTMDB` collaborator into the subprocess. When the hook fires, the daemon writes a one-line stderr trace naming the bootstrap path for forensics. |

## Versioning

This is protocol v1. Breaking changes will bump the version in this file's H1 and the daemon will respond to a future `info` method with `{"protocol_version": "1"}`. Until that method exists, both sides assume v1.
