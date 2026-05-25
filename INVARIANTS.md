# plex-renamer — Product Invariants

This file is the source of truth for what the shipped product must do, regardless of how it is built. Every change to behavior visible to the user updates this file before the corresponding code lands. The agent-coding brief (in `~/.agent-coding/projects/plex-renamer/`) describes the implementation plan; this file describes the product.

These are product invariants, not implementation requirements. They survive refactors, language changes, and packaging changes.

## Inputs

- The user invokes the app and provides input as any combination of individual files and individual directories via either a system file picker or drag-and-drop onto the application window.
- Inputs may be deeply nested or flat. The app walks the tree without depth limits.
- Input format is unrestricted. The app handles, at minimum, every naming convention observed in the user's reference corpus at `/Volumes/Cage/Media/CleverGet`, plus a broad set of plausible permutations including dot-separated tokens, bracketed years, quality tags (`1080p`, `x264`, `HDR`, `HEVC`), release-group tags in square brackets, multi-episode files (`S01E01-E02`), multi-part movies (`cd1`/`cd2`, `pt1`/`pt2`, `part1`/`part2`, `disc1`/`disc2`), specials (`S00Exx`, `Specials/`), date-based episodes (`YYYY-MM-DD`), accented characters, HTML entities, Unicode NFD vs NFC encodings, sidecar permutations (`.en.srt`, `.en.forced.srt`, `.en.sdh.srt`), NFO and Plex-named artwork files, duplicates with `_1` / `_2` suffixes.
- The app excludes from processing: in-progress download directories matching `temp_*` containing `.download` or `.tmp` shards; `.DS_Store`; `Thumbs.db`; any other non-media artifact that does not match the media-file extension whitelist.
- Media extensions in scope: video — `mp4`, `mkv`, `m4v`, `avi`, `mov`, `wmv`, `mpg`, `mpeg`, `ts`, `m2ts`, `webm`; subtitle — `srt`, `vtt`, `ass`, `ssa`, paired `sub`+`idx`, `sup`; metadata — `nfo` and Plex-named `jpg`/`jpeg`/`png` artwork (`poster.jpg`, `fanart.jpg`, `banner.jpg`, etc.).
- The reference directory `/Volumes/Cage/Media/CleverGet` is READ-ONLY. No code path in the project writes to, moves, renames, or deletes anything under that prefix.

## Outputs

- The app produces output by COPY into a user-configured pair of library roots: a `Movies/` root and a `TV Shows/` root. Copy is the default operation; the source files remain in place unless source cleanup is explicitly enabled (see Safety).
- Output paths conform to Plex's expected naming, verbatim:
  - **Movies** (per-movie folder always): `<movies_root>/<Title> (<Year>) {<anchor>}/<Title> (<Year>) {<anchor>}.<ext>`. Edition tag optional: `<Title> (<Year>) {edition-Director's Cut} {<anchor>}.<ext>`. Multi-part siblings in the same folder: `<Title> (<Year>) {<anchor>} - pt1.<ext>`, `- pt2.<ext>`. Subtitle sidecars share the basename: `<Title> (<Year>) {<anchor>}.en.srt`, `.en.forced.srt`, `.en.sdh.srt`.
  - **TV** (year on every show folder): `<tv_root>/<Show Name> (<Year>) {<anchor>}/Season <NN>/<Show Name> (<Year>) - S<NN>E<NN> - <Episode Title>.<ext>`. Season and episode numbers are two-digit zero-padded. Specials route to `Season 00/`. Multi-episode files use `S01E01-E02`. The folder carries the TMDB or IMDb anchor; episode files do not.
- `<anchor>` is `tmdb-<id>` when TMDB returned a confident match for the title, and `imdb-tt<id>` when only an IMDb identification was available.
- Output paths are NFC-normalized Unicode.
- Output paths strip Windows-reserved characters (`<`, `>`, `:`, `"`, `/`, `\`, `|`, `?`, `*`) and Windows-reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`).
- Output paths longer than 240 characters surface a warning on the plan; the user can shorten the title or accept the warning to proceed.
- A per-movie folder is always emitted. Flat-file movie output is not supported.
- A separate `MOVE` mode is not supported. The model is COPY plus optional source cleanup; cleanup is gated by safety rules in the Safety section.

## Identification

- TMDB is the mandatory primary identifier. The app holds a TMDB API key (read once from `.env` on first run, then persisted to the OS-appropriate app-config location) and queries TMDB for every input item it intends to copy.
- When TMDB returns no confident match, the app falls back to IMDb identification. The user can paste an IMDb ID directly in the per-item edit pane, and the resolver looks the ID up via TMDB's `/find/{external_id}` endpoint or via an optional OMDB API key (if configured) to obtain the canonical title and year. When TMDB has no record at all, the output anchor becomes `imdb-tt<id>` instead of `tmdb-<id>`.
- The user can override the anchor type per item in the edit pane (TMDB vs IMDb), independent of which identifier the resolver picked.
- Episode season and episode numbers extracted from filenames are recorded as HINTS, never as authoritative identity. For TV episodes, the engine matches each file's parsed title against the anchored show's TMDB episode list by fuzzy title match FIRST, and uses the filename's S/E numbers only as a tiebreaker when fuzzy matching is ambiguous. This rule exists because filename S/E often disagrees with TMDB's canonical numbering (regional episode splits, special-episode interleaving, animation vs original air orderings).
- When a directory contains multiple files that appear to be episodes of the same show but the show identification is itself ambiguous (multiple plausible TMDB candidates, or low confidence on the top result), the app surfaces a GROUP-level prompt: the user picks the show once on TMDB, and every episode in the group then resolves against that anchored show's episode list. This is more accurate than per-episode lookup and amortizes one TMDB call over many files.
- When the auto-seeded TMDB search for a group returns no results (the show name in the path doesn't match a known title), the group-level picker exposes a search box where the user types the correct title. The new query re-fires against TMDB and the picker updates in place. The user can iterate until a viable candidate appears, then picks it; the chosen candidate propagates to every row in the group.

## Confidence and review

- The app classifies each item into one of three confidence bands and renders them with distinct colors in the review UI:
  - **Auto-accept** (green): TMDB top result's normalized title matches the parsed title, year matches ±1 if extractable from the source, and (for TV) season+episode are unambiguous against the show's episode list. The app will rename without further user action.
  - **Needs review** (yellow): one or more match criteria are weak but a plausible TMDB candidate exists. The user reviews each yellow row before any copy happens.
  - **Unresolved** (red): TMDB and IMDb both returned no usable match, or the input was excluded from media processing entirely. The user picks a candidate manually or marks the item as "skip / not a movie or TV item."
- A power-user "auto-accept top hit" toggle is available, hidden behind a settings entry, OFF by default. When on, all items with a TMDB top hit are treated as auto-accept regardless of normalized title or year match.
- Nothing on disk changes until the user clicks Apply on a reviewed plan. The plan is computed and displayed first; the user can edit individual rows; only an explicit Apply triggers filesystem operations.
- The review UI is a two-panel layout: source files on the left (grouped by detected show/movie), proposed Plex target paths on the right. Clicking any row opens an edit pane with TMDB free-text search, IMDb ID paste, manual title/year/S/E/edition override fields, and a skip toggle.
- The source and target panels group by the same key and render the same group label for every state. When the group is unresolved (no Candidate yet), the label is the show-name hint derived from the path tree, never the first file's episode title or raw filename. When the group is resolved, the label is the Candidate title with year. Both panels apply this rule in lock-step — they never disagree about what a group is called.
- The show-anchor picker surfaces a hyperlink to every candidate's canonical record on themoviedb.org (or imdb.com for IMDb anchors) plus a "View on TMDB" / "View on IMDb" button that opens the link in the system browser. The user can validate every suggestion against the source before committing the pick. The picker is also relevance-ranked locally: exact and prefix matches outrank distant fuzzy matches regardless of TMDB's popularity ordering, so the literal title the user typed wins over similarly-named shows. When the auto-seeded query returns zero results, the orchestrator retries with cleaned variants of the query (strip trailing `_<digits>`, parenthesized suffixes, leading "The ") and surfaces a notice naming the variant that produced the visible results.

## Sidecars and adjacent files

- Subtitle, NFO, and Plex-named artwork files that share a basename with a recognized video file are paired with that video and renamed alongside it into the same per-movie or per-episode location, preserving language and modifier tokens.
- Sidecar pairing matches by basename stem before the first language code or extension. `Foo.en.srt`, `Foo.en.forced.srt`, and `Foo.en.sdh.srt` all pair with `Foo.mp4`.
- NFO and adjacent JPG/PNG artwork files (`poster.jpg`, `fanart.jpg`, `banner.jpg`) sitting in the same directory as a recognized video are moved with it. Artwork files in unrelated locations are ignored.
- A sidecar that cannot be paired with any video (the video was excluded or unmatchable) is excluded from copying and surfaced as a "skipped sidecar" in the run report.

## Safety

- The app never writes, moves, renames, or deletes any path under the user's reference media directory `/Volumes/Cage/Media/CleverGet`. The test suite enforces this with a write-prefix guard that fails any test that crosses the boundary.
- Source cleanup is OFF by default. The user must explicitly enable a "delete sources after successful copy" toggle in the bottom bar. The toggle state persists across runs.
- When cleanup is enabled and the user clicks Apply, a confirmation modal pops up listing EVERY path scheduled for deletion (source files plus now-empty parent directories that will be removed up the chain). The user must tick an explicit "I understand, delete these" checkbox before the deletion proceeds. Closing the modal or unchecking the box cancels the deletion entirely.
- The cleanup never deletes:
  - The user's input root (the directory or file path that was dropped onto the window or selected via the picker).
  - Any path with fewer than 3 components below the filesystem root. So `/`, `/Users`, `/Users/ryan`, `/Volumes`, `/Volumes/MyDisk` all refuse deletion regardless of how the planner arrived there.
  - Any path matching the always-disallowed prefix list, regardless of depth: `/`, `/Users`, `/Users/<any>`, `/Volumes`, `/Volumes/<any>`, `/private`, `/System`, `/Library`, `/Applications`, `/tmp`, `/var`, `C:\`, `C:\Users`, `C:\Users\<any>`, `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`.
- Cleanup only fires when every operation in the batch verified successfully (size match between source and destination as the minimum check). A single verification failure aborts the entire cleanup pass; the user sees a run-report error.
- The executor writes a JSON write-ahead journal entry before every filesystem operation. A crashed batch leaves a journal that the next run can read for recovery.
- Undo: after a batch, the user has a one-click "Undo this batch" action in the post-run report. Undo reads the journal and inverts every operation. When cleanup did not run, undo restores fully (delete the new copy, source still exists). When cleanup ran, undo restores the copied targets back to a "review" folder under the library root and reports that the deleted sources are non-recoverable from this app — the source bytes are gone.

## Persistence

- The TMDB API key, OMDB API key (optional), library roots (`Movies/` and `TV Shows/` paths), the source-cleanup toggle state, and the auto-accept-top-hit toggle state all persist to the OS-appropriate app-config location across runs (macOS: `~/Library/Application Support/plex-renamer/config.json`; Windows: `%APPDATA%\plex-renamer\config.json`). Settings can be edited from the UI at any time.
- Library roots are LIVE-mutable. Changing a root via the bottom-bar Change... button updates the orchestrator's view of those paths before the next Preview or Apply. The planner does not snapshot roots at startup; every plan build reads the current value. The same rule holds for the cleanup-enabled toggle and the TMDB / OMDB API keys: changes propagate to the next operation without restarting the app.
- The TMDB API key is read from `.env` in the working directory on first run when no app-config key is present. Once read, it persists; subsequent runs do not re-read `.env`. The user can edit the key in the settings dialog.
- TMDB API responses are cached to a per-user cache directory. Search-query responses expire after 7 days. ID lookups (movie/TV/episode by TMDB ID) are cached indefinitely — these results do not change.
- The operation journal persists in the same per-user data directory. The most recent batch's journal is readable for undo from any subsequent run, not just the run that created it. Older journals are retained for at least 30 days.

## Testing discipline

End-to-end pipeline correctness on the corpus generator's output is a load-bearing CI gate. Every change that touches the parser, the TMDB resolver, the orchestrator's resolve flow, the planner's path emission, or the GUI's group/edit-pane logic must keep `tests/test_integration_corpus_pipeline.py` green. The test runs the slice-2 corpus generator (every observed input pattern + every plausible permutation) through the full parse → resolve → plan pipeline with a hermetic mock TMDB, asserting that:

- Every TV episode under a recognized show in the corpus produces a Candidate with `anchor_kind="tmdb"` (no silent failures to `<unresolved>`).
- Group labels in the source panel are the show name, not the first episode's filename or a season folder name.
- Show-anchor picker queries TMDB with the show name derived from the path tree, not with the first row's episode title.
- Proposed Plex paths match the canonical shape end-to-end.

The corpus generator is the source of truth for the input patterns the app must handle. Per-layer unit tests (parser tests, planner path tests, GUI widget tests) verify individual components; the corpus pipeline test verifies they compose correctly. Both layers are mandatory.

Visual end-to-end tests use `widget.grab()` to capture PNG screenshots at each step of the user's flow. Tests assert rendered widget heights / structure, not just widget construction. Screenshots are saved to a known path so a human (or LLM) can inspect them when the test fails or when changing UI code. The Lazarus_2 recovery flow (`tests/test_visual_e2e_lazarus_recovery.py`) is the canonical example: it drives drop → group-label assertion (BOTH panels) → row click → edit-pane layout assertion → group click → picker assertion (including URL hyperlink + view button) → search → pick → resolution → preview → library-root change → re-preview, taking screenshots that together depict the user's full recovery journey.

Visual tests assert that widgets render at AT LEAST their `sizeHint().height()`. A widget rendered below its sizeHint means Qt was forced to compress it, and a compressed `QFormLayout` / `QGroupBox` causes its inner rows to overlap visually. Asserting on `widget.height() >= widget.sizeHint().height()` is the regression gate that catches the "squished" UX class. The TMDB search panel additionally has an explicit minimum-height assertion (`>= 150px`) because its own min-heights sum to ~158px and any value below that means the layout policy is broken.

Visual tests run at a mid-sized window (1400×850), not the developer's full screen. Larger windows accidentally hide layout bugs by giving every widget room. The 1400×850 window puts the right-column splitter under enough pressure that broken size policies actually fail the assertions, while still being a realistic shape a user would have.

Visual tests cover BOTH the resolved AND unresolved states. Asserting only on the post-pick happy path lets bugs that only manifest before resolution slip through. The drop → unresolved-group-label assertion is its own step, separate from the post-pick assertions.

When the UI has two parallel widgets (source panel + target panel), tests assert behavior on BOTH. Mirror logic in two places means tests need to mirror in two places too — fixing only one and tests passing is how the v0.1.3 target-panel label bug shipped.

Configuration that the user can change from the UI gets a regression test for the change → re-operation path: change a library root mid-session, re-run Preview, verify the new operation reflects the change. This is the "live-mutable settings" coverage; without it the user reports "I changed the root but it didn't take effect" and we ship a snapshot bug.

See [`docs/testing-retrospective-v0.1.0.md`](docs/testing-retrospective-v0.1.0.md) for the original (v0.1.0 → v0.1.1) retrospective, [`docs/decisions.md`](docs/decisions.md) for the WHY behind every load-bearing architectural choice, and [`docs/issue-retrospective.md`](docs/issue-retrospective.md) for the full inventory of bugs we hit during development plus the prompt-modification proposals each one motivates.

## Out of scope

The following are explicitly not in scope. The product does not do these things.

- Music libraries, audiobook libraries, and Plex's "Other Videos" / "Home Videos" categories. The app handles Movies and TV only.
- Anime absolute-numbering format. Anime is treated as standard TV with season-and-episode (`S<NN>E<NN>`) numbering.
- Date-based daily shows (e.g. The Daily Show with `Show - YYYY-MM-DD - Title.ext`) in the first release. The parser detects them and surfaces them to the review queue as "needs manual review"; full date-based emit is a follow-up feature.
- Direct Plex server integration. The app does not trigger library scans, log in to a Plex account, or communicate with a Plex Media Server in any way. Plex picks up the renamed files when it scans the library on its own schedule.
- Music videos.
- Auto-update mechanism. The user manually downloads new releases.
- Telemetry, analytics, and crash reporting. The app sends no usage data anywhere.
- Code signing or notarization for macOS distribution. The first release ships unsigned / ad-hoc-signed binaries.
- Localization. The UI is English only.
- Plex extras placement beyond the per-movie folder. Behind The Scenes / Featurettes subfolders are recognized but not auto-routed.
- 4K and HDR quality-tag preservation in output paths. Quality tokens (`1080p`, `x264`, `HDR`, `HEVC`, etc.) are parsed from input filenames and discarded; they do not appear in output names.
- Network discovery of Plex servers.

