# Decisions

Every load-bearing decision in plex-renamer's design, with the reasoning behind each one. This is not an API reference and not a changelog. It is the WHY layer: if you want to know why we built the show-anchor picker the way we did, or why settings are live-mutable rather than snapshotted, or why the engine and the GUI are split the way they are, the answer is here.

Decisions are grouped by surface. Within each surface, decisions are listed in roughly the order they were made; later decisions sometimes reference earlier ones.

---

## Platform and tooling

### Python 3.13 + PySide6 + uv

The app needs to be cross-platform (macOS + Windows) with a desktop GUI and ship as installable single-file artifacts. The realistic choices were Electron + a Node bundle, Tauri + Rust, or Python + Qt.

We picked Python + Qt (via PySide6) because the parsing / TMDB / planning surfaces are best expressed in Python — the parser already wants regex-heavy work, rapidfuzz for fuzzy matching, and the Python TMDB ecosystem is mature. Qt is the only GUI framework that ships on both platforms with native widget rendering and a stable Python binding (PySide6 is Qt-for-Python, BSD-licensed). PyInstaller bundles a Python interpreter + dependencies into a single distributable. The dev cycle is faster than Rust and the runtime footprint is smaller than Electron.

We picked Python 3.13 specifically (not 3.10 or 3.11) because we wanted modern union syntax (`X | Y` instead of `Optional[X]`), structural pattern matching where useful, and the freshest type-system improvements. The user explicitly asked "why not 3.13 or 3.14?" — we picked the latest stable that PySide6 supports.

We picked `uv` over `pip` + `pip-tools` because uv's lockfile is the only fully-deterministic Python lockfile that survives a multi-platform CI matrix. Lock once on Linux, install identically on macOS and Windows.

### PyInstaller + NSIS + native DMG

Each platform gets its own installable: a `.dmg` containing a `.app` bundle on macOS, an NSIS-built `.exe` installer on Windows. We considered shipping just the PyInstaller-bundled folder on both platforms; the user explicitly asked "where does a user get the .exe file?" which made it clear that the artifact has to look like a normal app on each OS, not a folder of unpacked Python.

We do NOT code-sign. The first release ships unsigned binaries with the macOS Gatekeeper bypass documented (`xattr -dr com.apple.quarantine /Applications/plex-renamer.app`). Code signing on macOS requires a paid Apple Developer account; we'd rather keep the project free and document the bypass than add a recurring cost. INVARIANTS.md explicitly carves this out of scope.

### Trust the lockfile, not the index

The repository contains a private Azure DevOps package mirror in some developer environments (`UV_EXTRA_INDEX_URL`). Running `uv lock` with that env set poisons the lockfile with `pkgs.dev.azure.com` URLs that nobody outside that environment can resolve. We learned this the hard way (twice). Every `uv lock` invocation in scripts, CI, and docs now strips the env var:

```bash
env -u UV_EXTRA_INDEX_URL uv lock --index https://pypi.org/simple
```

CI runs `grep -c 'pkgs.dev.azure.com' uv.lock` as a guard. A poisoned lockfile fails the build before anyone tries to install from it.

---

## Engine architecture

### Parser → Resolver → Planner → Executor

The data flow is one direction. The parser walks a tree and emits `ParseResult` per recognized media file. The resolver takes parse results and queries TMDB (with IMDb fallback) to produce `Candidate` records. The planner takes `(ParseResult, Candidate)` pairs and produces a `RenamePlan` (the proposed file operations). The executor takes a `RenamePlan` and performs the copies/deletes against the filesystem, journaling each operation.

Each layer is independently testable and composable. The slice-2 corpus generator feeds the parser; the parser's output feeds resolver tests; both feed the planner; the planner feeds executor tests. A change at any layer is a focused change.

The orchestrator is the GUI's binding to the engine. It owns the `ItemModel`, subscribes to widget signals, drives the engine, and pushes results back into the model. The GUI widgets do not import the engine directly; they receive `parse_fn`, `apply_fn`, `preview_fn` callables. This makes headless tests trivial — they bind their own callables, no real disk or network involved.

### TMDB anchor is mandatory; IMDb is fallback; nothing else is identification

`INVARIANTS.md` requires every output path to carry either a `{tmdb-<id>}` or `{imdb-tt<id>}` anchor. We considered allowing un-anchored output (just title + year) so the app could rename without a TMDB key, but Plex specifically uses the anchor to disambiguate identical titles (e.g. *The Office* US vs UK). Anchored output is the only output that Plex matches reliably.

This is a load-bearing decision and the source of several downstream rules:
- The TMDB API key is required at launch (`build_window` refuses to construct without one).
- The show-anchor picker is a first-class UI element because some shows can only be identified by anchoring once and propagating to siblings.
- Episode S/E numbers from filenames are HINTS only; the canonical numbering comes from TMDB's episode list (regional splits, double-episodes, animation orderings often disagree).

### Group-by-show, anchor-once

Episodes of the same show resolve as a group. The orchestrator clusters parse results by show-name hint (derived from the path tree), issues ONE TMDB search per group, and propagates the picked candidate to every row. This amortizes TMDB calls (13 episodes = 1 search + 1 season hydration), produces consistent anchors across siblings, and surfaces the show-anchor picker once per ambiguous group rather than per row.

The group key is `tv::<show-name>` for TV or `movie::<title>::<year>` for movies. The model groups internally so the source panel, target panel, and resolver all agree on grouping.

### `show_name_hint` is derived at parse time, not at resolve time

For filenames shaped `[S01.E01] Title.mp4`, the parser correctly extracts the EPISODE title (`Title`), not the SHOW title — there's no show name in the filename. The show name sits on a parent directory (`Lazarus/s1/[S01.E01] Title.mp4`).

We considered making the parser walk the parent dirs itself and produce a `show_name_hint` field on `ParseResult`. We rejected this because:
- The parser must be a PURE function of input path → ParseResult. Adding parent-dir traversal puts filesystem semantics inside the parser surface.
- The same file dropped from different roots gives different parent dirs; the parser's output should be deterministic per file.

Instead, the orchestrator's `derive_show_name(input_root, parent_dirs)` walks the parent dirs (skipping season-shaped folders) at drop time and seats the result on `ItemRow.show_name_hint`. The hint is GUI-layer state, not engine-layer state. This kept the parser pure and let the GUI compose path semantics with parse semantics correctly.

### Settings are live-mutable

The user can change the TV root mid-session via the bottom-bar Change... button. The first implementation snapshotted `movies_root` / `tv_root` into `OrchestratorDeps` at construction; changes never propagated. The user reported "I set the new TV root and clicked Preview and nothing happened."

We now treat library roots (and any other user-editable setting) as live values. `MainWindow.library_roots_changed` fires on change; `Orchestrator.update_library_roots` refreshes `_deps`. Every `Preview` / `Apply` reads the current value.

The principle: anything the user can edit from the UI must propagate to the next operation without a restart. INVARIANTS.md codifies this so we don't reintroduce the snapshot bug elsewhere.

---

## GUI architecture

### Two panels, source-on-left, target-on-right

The user's mental model is "files I have" → "files Plex will see." Two panels make that explicit and physical. We considered a single table view with source + target columns; it scaled badly when target paths got long (200+ chars in Plex-canonical form) and lost the grouping affordance.

Grouping is by show / movie. Each group is a top-level tree node; rows are leaves. The user can collapse groups they're confident about and focus on the ones that need review. The badge-per-row (auto-accept green / needs-review yellow / unresolved red) communicates per-row confidence; the group label communicates show-level identification.

Both panels honor the SAME group label rule: when the group has no Candidate yet (unresolved), the label uses `show_name_hint`. When resolved, the label uses the Candidate's title + year. Both panels must agree because the user reads them side-by-side; a disagreement reads as a bug.

### Edit pane lives in a `QScrollArea`

The edit pane hosts a TMDB search panel + IMDb override + Manual override + skip + Done. Total content is ~600px tall. On a typical desktop window with a 700px right column, the content doesn't fit.

We previously tried compensating with `QSizePolicy.Fixed` on the override boxes to "lock them at sizeHint." Qt's layout engine VIOLATED Fixed when the column was short, compressing the boxes BELOW their sizeHint and causing the inner `QFormLayout` rows to overlap visually. The user reported "fields are squished" / "labels overlap" — that's what they were seeing.

A QScrollArea is the right answer: when content doesn't fit, the user scrolls instead of seeing overlapping widgets. Override boxes use default Preferred policy (their sizeHint stands). The scroll area handles the overflow.

This decision came late (v0.1.3). The lesson generalizes: any pane hosting variable-size content gets a QScrollArea wrapper, not a Fixed/Maximum size policy hack.

### Picker shows clickable TMDB / IMDb URLs

The user explicitly asked for a way to validate suggestions before committing. The show-anchor picker now renders a clickable hyperlink (`https://www.themoviedb.org/tv/231003`) below the result list when a row is selected, plus a "View on TMDB" / "View on IMDb" button that opens the URL via `QDesktopServices.openUrl`.

We considered embedded poster thumbnails. Deferred: the TMDB result models don't currently carry `poster_path`, and fetching + caching poster images is meaningful work (rate-limited HTTP, image cache directory, asynchronous painting). Adding it is a feature for a later release, not a hotfix.

The URL hyperlink is the minimum viable validation affordance. It costs nothing — no extra HTTP, no async painting, no cache directory.

### Local relevance ranking + fuzzy fallback

TMDB returns search results in its own popularity order, which doesn't match user intent: query "Lazarus" returns "The Lazarus Project (2008)" before "Lazarus (2025)" because the older show has more votes. The user typing "Lazarus" expects an exact-match result first.

The orchestrator now runs every TMDB result list through a `rank_candidates` step (rapidfuzz `WRatio` + a prefix-match bonus). Exact and prefix matches outrank distant fuzzy matches. The picker's top entry is the literal query when it exists.

When the auto-seeded query returns 0 results, the orchestrator walks a fixed list of `cleaned_query_variants` (strip trailing `_<digits>` / `-<digits>`, strip parenthesized suffixes, strip leading "The ") until one variant returns results. The successful variant becomes the search-box text and the picker surfaces a notice: "No matches for 'Lazarus_2' — showing results for 'Lazarus'."

The fallback exists because users rename folders to disambiguate copies (`Lazarus`, `Lazarus_2`) and the disambiguation suffix is never a TMDB match. Surfacing the variant in the notice makes the behavior transparent so the user doesn't wonder why the results disagree with the box's original content.

### `ItemModel.notify_rows_reset()` for post-mutation refresh

The orchestrator's `_on_parsed_inputs` callback runs AFTER the drop handler has called `set_rows(rows)`. The drop handler builds `ItemRow` with `show_name_hint=None`; the orchestrator backfills the hint after the model is already populated. Direct mutation of `ItemRow.show_name_hint` doesn't fire any signal — the source/target panels never see the updated value.

We added `ItemModel.notify_rows_reset()` that re-emits `rows_reset` without mutating data. The orchestrator calls it after backfilling. The panels rebuild and pick up the new hints.

The principle: any code path that mutates model state directly must follow up with a signal emit. We considered making `show_name_hint` a property setter that fires a signal automatically; rejected because `ItemRow` is a plain dataclass and we don't want signal infrastructure inside data records. The model owns the signaling.

### Headless Qt testing via `QT_QPA_PLATFORM=offscreen`

All Qt tests run with `QT_QPA_PLATFORM=offscreen`. This binds Qt to a software renderer that has no display dependency, so tests run in CI containers without X11 / Wayland / Xvfb setup. The cost is that `widget.show()` doesn't paint to a screen — but `widget.grab()` still produces a real `QPixmap` from the offscreen surface, so screenshot-driven tests work identically headless and on a developer's laptop.

This unlocks the visual test class. The screenshots saved by the visual tests are real renderings; a human reads them to verify the UX visually. The tests' assertions on rendered widget heights are also real — Qt has computed the actual layout, not just the sizeHints.

---

## Testing

### Pipeline correctness over unit purity

We learned the lesson the hard way in v0.1.0: per-layer unit tests can be 100% green while the layers compose incorrectly. The fix was `tests/test_integration_corpus_pipeline.py` — the slice-2 corpus generator output runs through the real orchestrator (mock TMDB only) and asserts on canonical Plex output paths.

Every change touching the parser / resolver / orchestrator / planner / GUI logic must keep this test green. It's the only test that catches "field name drift" bugs (e.g. orchestrator reads `parent_dirs[-1]` when it should read `derive_show_name(...)`).

Unit tests still exist and still matter — they isolate failures, they're fast, they document intent at the layer level. But they're necessary, not sufficient.

See `docs/testing-retrospective-v0.1.0.md` for the original retrospective.

### Visual e2e screenshots with rendered-size assertions

The v0.1.0 retrospective fix caught pipeline bugs but didn't catch layout bugs. A GUI test that asserts `tmdb_panel is not None` passes even when the panel renders at 5px tall and is unusable.

The visual e2e test (`tests/test_visual_e2e_lazarus_recovery.py`) does two things:
1. Saves PNG screenshots at every step of the user's flow. A human (or LLM) reads them to confirm the UX is correct.
2. Asserts on RENDERED widget heights, not just construction. `assert widget.height() >= widget.sizeHint().height()` catches Qt silently compressing widgets below their natural size — the bug class that causes `QFormLayout` rows to overlap.

The test runs at 1400×850 (a realistic mid-size window) rather than 1800×1000 (developer's full screen). The smaller window puts the layout under enough pressure that broken size policies actually fail.

The screenshots are saved OUTSIDE pytest's `tmp_path` so they survive the test process. Path printed to stdout; reader opens each PNG.

### Cover unresolved AND resolved states

Asserting only on the post-pick happy path lets bugs that only manifest before resolution slip through. The visual test asserts on the source panel's group label AT the unresolved step (step 2, right after drop) AND at the resolved step (step 7, after pick). Same for the target panel.

The v0.1.3 target-panel label bug shipped because the test only checked the source panel label and the target panel had a parallel-but-different code path.

### Library-root change → re-Preview path

Configuration the user can edit gets a test for the edit → re-operation path. The visual test changes `settings.tv_root`, emits `library_roots_changed`, calls `_on_preview_clicked()` again, and asserts the new targets sit under the new root. Without this step, the "I changed it but it didn't take effect" snapshot bug ships.

---

## Process

### `make-it-so` skill drives sliced PRs

The project was built using the `make-it-so` agent-coding workflow: a brief in `~/.agent-coding/projects/plex-renamer/`, sliced into 6 PRs (scaffold, parser+corpus, TMDB, planner+executor+CLI, GUI, packaging), with plan-reviewer → AC-validator → cold reviewers → scribe at each slice. The discipline produces ~334 tests passing at v0.1.0 with every slice gated by cold reviewers.

This is the right shape for greenfield work of this size: more discipline than the change deserves at any individual step, but the aggregate effect is that nothing escapes review and the journal is auditable. Hotfixes are smaller and don't carry the same overhead.

### `INVARIANTS.md` is the product spec

The brief describes implementation; `INVARIANTS.md` describes the product. They're separate because the brief moves (slices get added, dropped, renumbered) while the product invariants are stable across implementation changes. Any change to behavior visible to the user updates `INVARIANTS.md` BEFORE the corresponding code lands. This is how we keep the spec from drifting.

The file lives at the repo root because it's the project's product surface, not a build artifact.

### One commit per logical change; small commits

Each fix in v0.1.3 is its own commit even when they ship together: target-panel label fix, root propagation + scroll area + picker URLs. We could have squashed them; we didn't, because `git blame` is more useful when each commit names one thing.

Conventional commits format (`fix(v0.1.3): ...`, `feat: ...`, `refactor: ...`) keeps the changelog auto-generatable and the subject line scannable.
