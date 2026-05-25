# plex-renamer engine bridge: JSON-RPC protocol

The Python sidecar (`plex-renamer-engined`) talks JSON-RPC 2.0 over `stdin` / `stdout` to native shells (the WPF Windows app, future native shells on other platforms). One JSON object per line, newline-delimited (`\n`). Responses are unbuffered — the daemon flushes `stdout` after every write.

This document is the **source of truth** for the protocol. The C# shell (and any future shell) mirrors these shapes as POCO records. If a shell's POCO record drifts from this document, this document wins; update the POCO.

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
   The shell uses `params.id` to associate the notification with the original request. Exactly one `result` response (with matching `id`) closes the request.
4. **Shutdown.** Three clean exits:
   - Send `{"jsonrpc":"2.0","id":<n>,"method":"shutdown"}`. Daemon responds `{"result":{"ok":true}}` and returns.
   - Close the daemon's stdin (EOF). Daemon returns with no extra output.
   - SIGTERM / SIGINT. Daemon catches `KeyboardInterrupt` and returns.

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
  "skip_reason": null
}
```

`skip_reason` (when non-null): `{"reason": "<short>", "detail": "<long>"}`. Reasons: `not_a_media_file`, `in_progress_download`, `excluded_extension`.

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

`group_key` shape: `movie::<source_path>` for movies (one row per group), `tv::<show_hint>` for TV (1..N rows). `row_id` is stable across calls — the shell uses it to refer to a specific row in `edit_row`.

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

## Methods

### `get_settings`

Returns the current `Settings` from the OS-appropriate config location (`%APPDATA%\plex-renamer\config.json` on Windows, `~/Library/Application Support/plex-renamer/config.json` on macOS).

**Request**: `{}` (no params)

**Result**:
```json
{"settings": <Settings>}
```

### `save_settings`

Persists the given `Settings` to disk and returns the persisted shape.

**Request**:
```json
{"settings": <Settings>}
```

**Result**:
```json
{"settings": <Settings>}
```

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
  "settings": <Settings>
}
```

**Result**:
```json
{
  "rows": [<Row>, ...],
  "groups": [<Group>, ...],
  "input_root": "/abs/common-parent",
  "errors": [
    {"row_id": "...", "message": "..."}
  ]
}
```

`errors` is per-row; a row failing to resolve does not abort the call. The Row's `candidate` is `null` in that case.

### `search_tmdb_free`

Free-text search for the per-row edit pane. The shell debounces user keystrokes; each round-trip is a single search call.

**Request**:
```json
{
  "query": "foo bar",
  "kind": "movie | tv",
  "settings": <Settings>
}
```

**Result**:
```json
{"candidates": [<Candidate>, ...]}
```

### `find_by_imdb`

Resolves an IMDb ID (`ttNNNNNNN`) via TMDB's `/find/{external_id}` endpoint, with OMDB fallback if configured.

**Request**:
```json
{
  "imdb_id": "tt1234567",
  "settings": <Settings>
}
```

**Result**:
```json
{"candidate": <Candidate | null>}
```

### `iterate_anchor_search`

Runs TMDB search for a TV show with the zero-result cleaned-variant retries (strips trailing `_<digits>`, parenthesized suffixes, leading `"The "`). The picker dialog drives this on every search-box change.

**Request**:
```json
{
  "query": "Foo Bar",
  "settings": <Settings>
}
```

**Result**:
```json
{
  "candidates": [<Candidate>, ...],
  "variant_note": "Tried 'Foo Bar (UK)' first; results below are for 'Foo Bar'."
}
```

`variant_note` is `null` when the original query returned results without retry.

### `select_anchor`

Propagates a picked anchor to every row in the group. Fetches the season episode lists for the TV show and hydrates per-row title matches against the TMDB episode list (fuzzy match first, S/E numbers as tiebreaker, per `INVARIANTS.md`'s Identification rule).

**Request**:
```json
{
  "rows": [<Row>, ...],
  "group_key": "tv::Foo Bar",
  "candidate": <Candidate>,
  "settings": <Settings>
}
```

**Result**:
```json
{
  "rows": [<Row>, ...]
}
```

The returned `rows` contains the updated rows for the entire group (others are unchanged).

### `edit_row`

Applies per-row overrides (title / year / S / E / edition / IMDb-ID / anchor-type-toggle / skip) and recomputes the row's `Candidate` + target path.

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
    "skip": false
  },
  "settings": <Settings>
}
```

`overrides` keys are all optional; only the keys present apply changes. Pass `null` for a key to clear an existing override.

**Result**:
```json
{
  "row": <Row>
}
```

### `build_plan`

Assembles the current resolved state (rows + their candidates + overrides) into a `RenamePlan` with collisions detected.

**Request**:
```json
{
  "rows": [<Row>, ...],
  "settings": <Settings>
}
```

**Result**:
```json
{"plan": <RenamePlan>}
```

### `apply_plan` (streaming)

Executes the plan, copying source files to their canonical Plex paths, optionally cleaning up sources, and writing the journal. Emits zero or more `progress` notifications before the final `result`.

**Request**:
```json
{
  "plan": <RenamePlan>,
  "cleanup": false,
  "verify_hash": false,
  "settings": <Settings>
}
```

**Notifications** (zero or more, in order):
```json
{"jsonrpc":"2.0","method":"progress","params":{
  "id": <original_req_id>,
  "stage": "copying | verifying | cleaning",
  "index": 3,
  "total": 12,
  "source": "/abs/src",
  "target": "/abs/target"
}}
```

**Final result**:
```json
{"report": <RunReport>}
```

### `undo_batch`

Reads a journal and inverts every operation. When cleanup did not run, undo restores fully (deletes the new copy; source still exists). When cleanup ran, undo restores copied targets to a "review" folder under the library root and reports that sources are non-recoverable.

**Request**:
```json
{"journal_path": "/abs/journals/2025-05-25T12-34-56.json"}
```

**Result**:
```json
{"report": {
  "reverted": 12,
  "moved_to_review": 0,
  "review_dir": null,
  "sources_recoverable": true
}}
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

## Bootstrap hook (test-only)

The daemon honors an optional `PLEX_RENAMER_DAEMON_BOOTSTRAP` environment variable pointing to a Python file. When set, the daemon `runpy.run_path`s that file before entering the dispatch loop. Tests use this to swap the TMDB factory with a `FakeTMDB`. Production shells must never set this variable.

## Versioning

This is protocol v1. Breaking changes will bump the version in this file's H1 and the daemon will respond to a future `info` method with `{"protocol_version": "1"}`. Until that method exists, both sides assume v1.
