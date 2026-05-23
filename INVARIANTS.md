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

## Confidence and review

- The app classifies each item into one of three confidence bands and renders them with distinct colors in the review UI:
  - **Auto-accept** (green): TMDB top result's normalized title matches the parsed title, year matches ±1 if extractable from the source, and (for TV) season+episode are unambiguous against the show's episode list. The app will rename without further user action.
  - **Needs review** (yellow): one or more match criteria are weak but a plausible TMDB candidate exists. The user reviews each yellow row before any copy happens.
  - **Unresolved** (red): TMDB and IMDb both returned no usable match, or the input was excluded from media processing entirely. The user picks a candidate manually or marks the item as "skip / not a movie or TV item."
- A power-user "auto-accept top hit" toggle is available, hidden behind a settings entry, OFF by default. When on, all items with a TMDB top hit are treated as auto-accept regardless of normalized title or year match.
- Nothing on disk changes until the user clicks Apply on a reviewed plan. The plan is computed and displayed first; the user can edit individual rows; only an explicit Apply triggers filesystem operations.
- The review UI is a two-panel layout: source files on the left (grouped by detected show/movie), proposed Plex target paths on the right. Clicking any row opens an edit pane with TMDB free-text search, IMDb ID paste, manual title/year/S/E/edition override fields, and a skip toggle.

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
- The TMDB API key is read from `.env` in the working directory on first run when no app-config key is present. Once read, it persists; subsequent runs do not re-read `.env`. The user can edit the key in the settings dialog.
- TMDB API responses are cached to a per-user cache directory. Search-query responses expire after 7 days. ID lookups (movie/TV/episode by TMDB ID) are cached indefinitely — these results do not change.
- The operation journal persists in the same per-user data directory. The most recent batch's journal is readable for undo from any subsequent run, not just the run that created it. Older journals are retained for at least 30 days.

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

