# Issue retrospective

Every issue we hit during plex-renamer development through v0.1.3, with:

1. **The issue** — what broke and how it surfaced.
2. **Resolution** — what we changed to fix it.
3. **How it could have been avoided** — the structural change that would have prevented the issue from existing in the first place, not just the patch.
4. **Prompt-modification proposals** — concrete additions to one or more of:
   - **Planner** (the agent that drafts the implementation plan / brief)
   - **Plan reviewer** (cold review of the brief before code is written)
   - **Implementer** (the agent that writes the code)
   - **Code reviewer** (cold review of the diff before merge)

The proposals are written so they can be copy-pasted into the corresponding agent's system prompt or task description.

Issues are listed roughly chronologically.

---

## Issue 1 — v0.1.0 group label showed first episode's filename for unknown shows

**Issue.** User dropped `MAX/Lazarus/s1/[S01.E01] Goodbye Cruel World.mp4` (13 episodes). Every row showed `<unresolved>`. The source panel's group label was `[S01.E01] Goodbye Cruel World.mp4`. The show-anchor picker came back empty when clicked.

Root cause: filenames shaped `[S01.E01] Title.mp4` legitimately store the EPISODE title in the filename; the SHOW name sits on a parent directory. The orchestrator was reading `parent_dirs[-1]` (which gave the season folder `s1`) or `raw_filename` (which gave the .mp4 path); neither is the show.

**Resolution.** Added `derive_show_name(input_root, parent_dirs)` that walks parent dirs skipping season-shaped folders. Added `ItemRow.show_name_hint` field that the orchestrator populates at parse time. Per-group TV resolution now uses the hint.

**How to avoid.** End-to-end pipeline test against realistic input from the start. The slice-2 corpus generator emitted exactly this pattern; no test composed corpus → orchestrator → planner. Per-layer unit tests using hand-rolled `ParseResult` instances sidestepped the bug because the author chose inputs that didn't exercise it.

**Prompt proposals.**

- **Planner.** When the project's core value is a pipeline (input → multiple layers → output), the brief MUST claim, as an explicit acceptance criterion: "the full pipeline runs end-to-end against the most-realistic available input fixture, with assertions on the OUTPUT surface (paths emitted, files written), not on intermediate dataclass fields." Hand-rolled per-layer test inputs are necessary for fast unit cycles but never sufficient as an AC.
- **Plan reviewer.** Add a scope check: "Are all ACs unit/widget-level? If the project ships a pipeline, where is the end-to-end coverage? Refuse the brief if every AC is per-layer and no AC asserts on the final user-facing artifact." This is the question that catches the layered-tests-pass-but-product-fails class.
- **Implementer.** When implementing a slice that adds a new layer to a pipeline, the slice MUST add or extend the integration test that exercises the layer's interaction with the layers above and below. The integration test feeds realistic input (from the corpus generator if one exists) through the production wrappers (`build_window`, `parse_fn`, `apply_fn`) and asserts on canonical output. Skipping this with "covered by my unit tests" is not allowed.
- **Code reviewer.** Ask the literal question: "Could this PR cause a basic real-world drop to produce wrong output, and would the test suite catch it?" That question on the slice-5 review would have surfaced the integration gap before v0.1.0 shipped.

---

## Issue 2 — `uv.lock` repeatedly poisoned with Azure private mirror URLs

**Issue.** Several times during development, sub-agents ran `uv lock` without stripping `UV_EXTRA_INDEX_URL`. The lockfile picked up `pkgs.dev.azure.com` URLs that only resolve inside the developer's private network. CI fetches failed.

**Resolution.** Every `uv lock` invocation in scripts, makefiles, agent prompts, and CI explicitly strips the env: `env -u UV_EXTRA_INDEX_URL uv lock --index https://pypi.org/simple`. CI added `grep -c 'pkgs.dev.azure.com' uv.lock` as a guard — a poisoned lockfile fails the build before anyone tries to install from it.

**How to avoid.** The repeating failure was a tooling-discipline gap. The right answer is to encode the discipline in a place the developer / agent can't forget. A make target (`make lock`) that runs the env-stripped command, plus a CI grep guard, makes the safe path the default path.

**Prompt proposals.**

- **Planner.** When a project depends on private/mirrored package registries, the brief MUST include a "lockfile hygiene" AC: every lock-regeneration must produce a lockfile that resolves against the public registry. The CI grep guard is mandatory, not optional.
- **Plan reviewer.** Flag any brief that references private package mirrors without an accompanying lockfile-hygiene safeguard.
- **Implementer.** Before running `uv lock`, `pip-compile`, `poetry lock`, or any equivalent, check for environment variables that point at private registries (`UV_EXTRA_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `POETRY_REPOSITORIES_*`). If present, run the lock command with `env -u <VAR>` to strip them. Never assume the developer's shell environment is reproducible in CI. After locking, grep the lockfile for known-internal URLs and fail loudly if any survive.
- **Code reviewer.** When a PR touches `uv.lock` / `poetry.lock` / `Pipfile.lock`, run `grep -E 'pkgs\.dev\.azure\.com|artifactory|nexus|internal' <lockfile>` before approving. If any hit, request a fix before merging.

---

## Issue 3 — Cross-platform CI failures from POSIX-only paths

**Issue.** Several tests referenced `/Volumes/Cage/Media/CleverGet` (a POSIX-only absolute path) and failed on Windows runners. The Windows path semantics don't accept the POSIX shape, so `Path("/Volumes/...")` silently became a different path object.

**Resolution.** Tests that depend on POSIX-specific path semantics now guard with `@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path semantics don't translate to Windows")`. Tests that need to work on both platforms use `tmp_path` for path-shape testing instead of hard-coded prefixes.

**How to avoid.** Any test that hard-codes an absolute path has implicit platform assumptions. Either parametrize the path via `tmp_path` (works on all platforms) or explicitly mark the test as platform-specific.

**Prompt proposals.**

- **Planner.** Cross-platform projects' briefs include a "cross-platform test discipline" line: tests that depend on platform-specific behavior must be explicitly marked; tests that don't depend on platform must use `tmp_path` or equivalent. Hard-coded absolute paths in test bodies are a smell.
- **Implementer.** Before committing a test that contains an absolute path starting with `/` (POSIX) or `C:\` (Windows), check whether the path is essential to the test's intent. If yes, mark the test with `@pytest.mark.skipif` for the other platform. If no, refactor to use `tmp_path`. Never assume CI is your dev machine's OS.
- **Code reviewer.** Grep new tests for `Path("/`, `Path("C:`, or hard-coded prefixes like `/Volumes`, `/Users`, `C:\Users`. Any hit gets challenged: is this test platform-specific? If yes, is it marked? If no, refactor.

---

## Issue 4 — NSIS path-resolution failure in CI

**Issue.** The Windows installer build used relative paths in the NSIS script (`File "dist/plex-renamer/..."`). NSIS resolves `File` and `OutFile` relative to the SCRIPT, not the invocation directory. CI runs NSIS from a different working directory, so the paths broke.

**Resolution.** All paths in the `.nsi` script use script-relative form with explicit `..` segments: `File "..\\..\\dist\\plex-renamer\\..."`. Verified with a temporary CI debug step that listed the resolved dist tree.

**How to avoid.** Cross-tool path conventions need explicit verification on the target CI, not the developer's machine. NSIS, Make, Bazel, Docker, and every other tool that processes scripts has its own working-directory convention; never assume.

**Prompt proposals.**

- **Implementer.** When writing a script for a tool with non-obvious working-directory semantics (NSIS, Docker `COPY`, GitHub Actions composite actions, anything that runs in a separate process from where you invoked it), explicitly document the working directory the script will run from and use that as the reference for relative paths. If unsure, run the script with a verbose / debug flag and check the actual paths it resolved.
- **Code reviewer.** Build scripts (`.nsi`, `Dockerfile`, `.github/workflows/*`, `Makefile`) get extra scrutiny on path handling. Ask: "Is this path relative to the script, the invocation, or some third thing? Is the script always invoked from the same directory?" When in doubt, request a debug step that prints the resolved paths.

---

## Issue 5 — PowerShell argument splitting on dots

**Issue.** CI invoked NSIS with `-DAPP_VERSION=0.1.0`. PowerShell parses unquoted arguments aggressively and split `0.1.0` on the dots, passing `-DAPP_VERSION=0`, `1`, `0` as three separate tokens. The build used `0` as the version.

**Resolution.** Quoted the argument explicitly: `"-DAPP_VERSION=0.1.0"`. PowerShell respects quoted strings.

**How to avoid.** Whenever an argument contains shell-meaningful characters (dots, spaces, parens, etc.), quote it. PowerShell, bash, cmd, and zsh all have slightly different rules; quoting is the lowest-common-denominator safety.

**Prompt proposals.**

- **Implementer.** When passing arguments that contain `.`, ` `, `(`, `)`, `&`, or any shell-meaningful character, ALWAYS quote them. Don't try to remember which shell needs which quotes; just quote everything.
- **Code reviewer.** Grep CI workflows for unquoted arguments containing `.` or `$`. Any unquoted version string (`-DVERSION=0.1.0`), unquoted path (`-DPATH=C:\Program Files`), unquoted variable interpolation gets challenged.

---

## Issue 6 — Chocolatey `PATH` not refreshed in same CI step

**Issue.** A CI step ran `choco install nsis` followed immediately by an NSIS invocation. The `PATH` update from `choco install` only takes effect in subsequent steps; the same-step NSIS call couldn't find `makensis.exe`.

**Resolution.** Explicitly added the NSIS install dir to `$env:GITHUB_PATH` after `choco install`:

```yaml
- run: |
    choco install nsis
    echo "C:\Program Files (x86)\NSIS" >> $env:GITHUB_PATH
```

**How to avoid.** GitHub Actions' `$GITHUB_PATH` is the cross-step PATH mechanism; relying on the installer's PATH update is fragile because each step starts a fresh shell.

**Prompt proposals.**

- **Implementer.** In CI, never assume a package manager's PATH update is visible in the same step. Always `echo "<install-dir>" >> $env:GITHUB_PATH` (Windows) or `echo "<install-dir>" >> $GITHUB_PATH` (POSIX) after installing a tool, then use it from the next command.
- **Code reviewer.** CI scripts that install tools AND use them in the same step are suspicious. Look for `choco install ... && some-tool`, `apt-get install ... && some-tool`, etc. Request a PATH-update step in between unless the tool is a known same-step-safe install.

---

## Issue 7 — macOS Gatekeeper bypass instructions wrong (pyenv `xattr`)

**Issue.** The README said to run `xattr -dr com.apple.quarantine /Applications/plex-renamer.app`. The user's `xattr` was a pyenv shim that doesn't support `-r`. The command errored: `option -r not recognized`.

**Resolution.** Documented the full path: `/usr/bin/xattr -dr ...`. The system `xattr` always supports `-r`; the pyenv shim doesn't.

**How to avoid.** User-facing shell instructions should use absolute paths for system binaries when the binary's behavior matters. Don't trust `$PATH` to resolve to the system binary; pyenv, asdf, conda, and homebrew all shim common utilities.

**Prompt proposals.**

- **Implementer.** When writing user-facing install / setup instructions that invoke system binaries (`xattr`, `python`, `pip`, `git`, etc.), prefer absolute paths for the system binary when its specific behavior is required (`/usr/bin/xattr`, `/usr/bin/python3`). Document the absolute path in commands the user copies.
- **Code reviewer.** README / install docs / migration scripts that invoke common utilities by bare name (`xattr`, `python`, `make`, `node`) deserve a question: "Does the user's environment guarantee this resolves to the system binary?" If not, suggest the absolute path.

---

## Issue 8 — v0.1.2 source panel group label backfill never refreshed the UI

**Issue.** v0.1.1 added `show_name_hint` and `derive_show_name(...)`. The drop handler in `MainWindow._on_paths_dropped` built `ItemRow(parsed=parsed)` with `show_name_hint=None`, called `set_rows(rows)` (which emitted `rows_reset` and triggered source-panel render with the WRONG label), THEN emitted `parsed_inputs` which fired `Orchestrator._on_parsed_inputs`, which backfilled `show_name_hint` by mutating each row directly. The backfill never emitted a signal. When TMDB also returned nothing (e.g. unknown show "Lazarus_2"), no `set_candidate` call fired either, so `row_changed` never triggered a rebuild. The group label stayed stuck on the first row's filename.

**Resolution.** Added `ItemModel.notify_rows_reset()` that re-emits `rows_reset` without mutating data. The orchestrator's `_on_parsed_inputs` calls it after the backfill loop. The source panel rebuilds with the freshly-set hint.

**How to avoid.** Any code path that mutates model state directly (skipping the model's setters) MUST follow up with an explicit signal emit. The model owns its consistency contract; direct mutation breaks it.

**Prompt proposals.**

- **Planner.** When the design involves multi-step state population (e.g., GUI populates model with placeholders THEN orchestrator backfills), the brief MUST list the signal-emit step at every mutation point. "Update field X" is incomplete; "Update field X AND emit row_changed for the affected row" is complete.
- **Implementer.** When code mutates a model field directly (`row.field = value` rather than `model.set_field(row, value)`), pause and ask: "What signal does the model emit when this field changes through the proper setter? Am I bypassing it?" If yes, either use the setter or follow up with the equivalent signal emit. Never leave the model and its observers in disagreement.
- **Code reviewer.** Grep diffs for direct attribute assignment on model-tracked rows (`<row>.<field> = ...`). Any hit gets challenged: "Is the model supposed to emit a signal when this field changes? Where's the emit?"

---

## Issue 9 — v0.1.3 target panel group label NOT mirroring source panel fix

**Issue.** v0.1.3's source-panel fix correctly used `show_name_hint` for unresolved TV groups. The TARGET panel (right side, "Proposed Plex path") still used the first row's `parsed.title_candidate or parsed.raw_filename` for the group header. For a folder like `MAX/Lazarus_2/s1/[S01.E01] Goodbye Cruel World.mp4`, the source panel correctly said "Lazarus_2 — 13 item(s)" but the target panel said "[S01.E01] Goodbye Cruel World.mp4" as the group header.

**Resolution.** Mirrored the source-panel logic in `target_panel._make_group_item`. Both panels now apply the same precedence: candidate.title (if set) → show_name_hint (TV-only) → title_candidate → raw_filename.

**How to avoid.** When the UI has two parallel widgets (source panel + target panel, two views of the same model), tests must assert on BOTH. The visual e2e test asserted only on source-panel group label; if it had also asserted on target-panel group label at the unresolved step, the bug would have failed the test instead of shipping.

**Prompt proposals.**

- **Planner.** When the design has parallel widgets that share a logical concept (e.g., "group label" rendered in two panels), the brief MUST list both widgets in the AC for that concept and require parallel tests. "Group label uses show_name_hint" is incomplete; "Group label uses show_name_hint in BOTH source panel and target panel, with parallel test assertions on both" is complete.
- **Implementer.** When fixing a rendering bug in one of two parallel widgets, check the other widget for the same code path. Don't fix one side and assume the other is fine. If both widgets render the same logical concept, they share the same bug class.
- **Code reviewer.** When reviewing a fix to a panel/view, grep the codebase for other widgets that render the same data. Any second widget that uses the same logical concept needs the same fix (or an explicit reason why it's different). Ask the implementer: "Where else does this concept render? Is that location covered by this fix?"

---

## Issue 10 — v0.1.3 edit pane Manual override section visually squished

**Issue.** User reported "Manual override fields are squished" with the Title / Year / Season / Episode / Edition rows overlapping each other and the Apply button half-hidden. Root cause: the IMDb and Manual override `QGroupBox` widgets had `QSizePolicy.Fixed` on the vertical axis. Qt's layout engine VIOLATED Fixed when the right column was short, compressing the boxes BELOW their `sizeHint` and causing the inner `QFormLayout` rows to overlap.

**Resolution.** Wrapped the edit pane content in a `QScrollArea`. Restored default `Preferred` policy on the override boxes — their `sizeHint` stands. When the right column is short, the user scrolls instead of seeing overlapping widgets.

**How to avoid.** Two layers of prevention. First, never trust Qt's `Fixed` size policy to actually be fixed; Qt violates it when layout constraints force it. Second, test visual layouts at realistic small window sizes, with assertions on `widget.height() >= widget.sizeHint().height()`. The original v0.1.3 test ran at 1800×1000 and the bug didn't reproduce.

**Prompt proposals.**

- **Planner.** GUI briefs that include forms / panels that might not fit on small screens MUST claim, as an AC: "Each pane that hosts variable-size content wraps in a `QScrollArea` (or platform equivalent). Tests assert rendered widget heights are at least sizeHint." Hard-coding size policies (`Fixed`, `Maximum`) is a code smell — Qt violates them when constrained.
- **Plan reviewer.** Flag any GUI brief that proposes `QSizePolicy.Fixed` / `Maximum` as a layout solution. Ask: "Will Qt actually honor this when the parent is small? Is the test at a small enough window size to verify? Should this be a QScrollArea instead?"
- **Implementer.** When tempted to use `QSizePolicy.Fixed` to "lock" a widget at its sizeHint, stop. Qt's Fixed policy is advisory, not a guarantee — when the layout engine MUST compress, it violates Fixed. Use a `QScrollArea` wrapper instead for any pane with variable-size content. Test the layout at a realistic small window (1400×800 or smaller), not your full screen.
- **Code reviewer.** Grep diffs for `QSizePolicy.Fixed` and `QSizePolicy.Maximum`. Any hit gets challenged: "Will this hold when the parent is short? Have you tested at a small window?"

---

## Issue 11 — v0.1.3 library-root change didn't propagate to planner

**Issue.** User changed the TV root via the bottom-bar Change... button, clicked Preview again, and the proposed paths still went to the OLD root. Root cause: `OrchestratorDeps.movies_root` / `tv_root` were snapshotted at construction time. The Change button updated `Settings` and saved to disk, but never refreshed the orchestrator's view.

**Resolution.** Added `MainWindow.library_roots_changed = Signal(str, str)` that fires when the user picks a new root. Added `Orchestrator.update_library_roots(movies_root, tv_root)` that refreshes `_deps`. Wired the signal in `Orchestrator.connect()`.

**How to avoid.** Any configuration the user can change from the UI must be designed as live-mutable from day one, not snapshotted. The integration test that exercises change → re-operation is the regression gate.

**Prompt proposals.**

- **Planner.** When the design involves user-editable configuration (library roots, API keys, toggles), the brief MUST state explicitly whether each value is LIVE (re-read on every operation) or SNAPSHOT (read once at startup). Default to live. Snapshot is only correct when the value is genuinely immutable for the session (e.g., a one-time key bootstrap). Every live value gets a regression test for the change → re-operation path.
- **Plan reviewer.** When a brief includes "user can change setting X from the UI" AND "X is consumed by engine Y at operation Z", challenge the planner: "Does X propagate to Y between operation Zs? Where is the test that proves it?"
- **Implementer.** When wiring a user-editable setting to an engine consumer, ask: "If the user changes this mid-session, does my code see the new value or the old one?" If the consumer reads from a snapshot (a dataclass field set at startup, a captured local in a closure), build a signal/refresh path so changes propagate. Default to fetching the current value at consumption time, not caching it.
- **Code reviewer.** When a diff adds a setting that the user can change AND the setting is consumed by engine code, look for the propagation path. Ask: "If the user changes this mid-session, what re-reads the new value?" If the answer is "nothing," request a signal + handler before approving.

---

## Issue 12 — Picker had no way to validate suggestions against TMDB

**Issue.** The user asked: "when you click and are given suggestions, there should be a way to link to the tmdb page for each so i can validate." The picker showed `<title> (<year>) — tmdb:<id>` text but no clickable URL. Validating required copying the ID, opening a browser, pasting into a search.

**Resolution.** Added a clickable hyperlink label (`https://www.themoviedb.org/tv/<id>`) below the result list that appears when a row is selected. Added a "View on TMDB" / "View on IMDb" button that opens the URL via `QDesktopServices.openUrl`. The button label adapts to the anchor kind.

**How to avoid.** When the user's task is "verify that the engine's suggestion matches the source," the UI must surface the suggestion's canonical reference. The picker design originally focused on the SELECT action (pick this show); it missed the VALIDATE action (verify this is the right show first).

**Prompt proposals.**

- **Planner.** When designing UI that surfaces external-source suggestions (TMDB results, search hits, autocomplete entries), include a "validate against source" affordance in the design, not just a "select" action. Users want to verify before committing. The minimum bar is a clickable URL to the canonical source page; the higher bar is an inline preview (thumbnail, summary).
- **Implementer.** Before considering a "pick from suggestions" UI done, ask: "If the user is uncertain about a suggestion, what's the friction to verify it's correct?" If the answer is "they have to leave the app and search manually," add a URL link or preview affordance.
- **Code reviewer.** When reviewing a picker / selector / autocomplete component, ask: "How does the user validate a suggestion is correct before committing?" If the only validation is the suggestion's text, that's a friction issue worth raising.

---

## Issue 13 — TMDB result ranking was popularity-order, not relevance-order

**Issue.** Query "Lazarus" returned `[The Lazarus Project (2008), Lazarus (2025), Lazarus (2021)]` because TMDB ordered by popularity / vote count. The user expected an exact-match title to outrank a partial-match title.

**Resolution.** Added `rank_candidates(query, candidates)` using `rapidfuzz.fuzz.WRatio` plus a prefix-match bonus. The orchestrator runs every TMDB result list through this step before populating the picker. Exact matches outrank distant matches; "Lazarus" outranks "The Lazarus Project" for the query "Lazarus."

**How to avoid.** External APIs almost never order results the way YOUR users expect. When the user types a query and reads the results in order, they read the first row as "the answer." If the first row isn't a relevance match, the UX is broken. Always plan for local re-ranking.

**Prompt proposals.**

- **Planner.** When the design consumes ordered results from an external API and surfaces them to the user, the brief MUST address ranking explicitly: either "use API's order verbatim because X" or "re-rank locally on Y signal because the API's order doesn't match user intent." Don't leave it implicit.
- **Implementer.** When integrating an external search API, never assume the API's default ordering matches user intent. Test with realistic queries that have ambiguous results; if the top result for a literal-match query isn't the literal match, add a local ranking step.
- **Code reviewer.** When a diff calls a search/list endpoint and renders results in order, ask: "What's the ordering? Is the first row the user's most likely intent? What if the API ranks by popularity / recency / random?"

---

## Issue 14 — Picker had no fallback for queries with zero results

**Issue.** User dropped `Lazarus_2/` (folder renamed to disambiguate from an earlier copy). The auto-seeded TMDB search for "Lazarus_2" returned 0 results. The picker opened with an empty results list and a hint "type a different name." The user had no recourse except manually deducing that they should retry with "Lazarus."

**Resolution.** Added `cleaned_query_variants(query)` that yields fallback queries: original first, then trailing `_<digits>` / `-<digits>` stripped, then parenthesized suffix stripped, then leading "The " stripped. When the original query returns 0, the orchestrator walks the variants until one returns results. The successful variant becomes the search-box text and the picker shows a notice: "No matches for 'Lazarus_2' — showing results for 'Lazarus'."

**How to avoid.** Empty-results states are first-class UX surfaces. The default behavior (show "no results" and leave the user to figure it out) is usually wrong. Design the empty-results path with the same care as the populated path.

**Prompt proposals.**

- **Planner.** When the design includes search / lookup that can return zero results, the brief MUST describe the empty-results UX explicitly. "Show 'no matches'" is the minimum; "auto-retry with a cleaned variant and surface what was searched" is the bar for shipped UX.
- **Plan reviewer.** When a brief mentions a search / lookup but doesn't describe the empty-results path, ask: "What does the user see when this returns nothing? Is there a fallback strategy?"
- **Implementer.** When wiring a search to a UI, plan the empty-results branch first. What does the user see? What's their next action? If the answer is "guess a different query," add an auto-retry with a cleaned-variant strategy.
- **Code reviewer.** When a diff adds a search UI, ask: "What happens when the search returns zero?" If the only answer is "show the no-results state," that's worth a discussion about fallback strategies.

---

## Issue 15 — Visual tests passed at large window size, layout bug only manifested at small size

**Issue.** The original v0.1.3 visual test ran at 1800×1000. The Manual override squish didn't reproduce — the right column was tall enough to fit everything at sizeHint. The user reported the bug on their actual window (smaller). The test had to be revised to 1400×850 to catch the layout regression.

**Resolution.** Visual e2e tests now run at 1400×850 (a realistic mid-size window). Added assertions on `widget.height() >= widget.sizeHint().height()` so layout compression fails the test.

**How to avoid.** Tests should reproduce the user's environment, not the developer's full-screen workstation. "Works on my machine" is the failure mode that visual tests at large window sizes encode.

**Prompt proposals.**

- **Planner.** GUI briefs with visual tests MUST specify the test window size and justify the choice. The default should be a mid-size desktop window (1400×850 or smaller), NOT the developer's full screen. Tests at large sizes hide layout bugs by giving every widget room.
- **Implementer.** When writing a visual test, choose the window size that puts the layout under realistic pressure. If the layout works at 1800×1000 but breaks at 1400×800, the user will see the break. Pick the small window. Add assertions on rendered heights (`widget.height() >= widget.sizeHint().height()`) — Qt silently compressing a widget below sizeHint is the visual-squish bug class.
- **Code reviewer.** When a diff adds a visual test, check the window size. If it's larger than the typical user's window (anything >= 1600×1000 is suspect for a desktop app), ask why. Layout bugs hide at large sizes.

---

## Issue 16 — Visual tests asserted widget construction, not rendered layout

**Issue.** The v0.1.3 first-cut visual test asserted `tmdb_panel is not None` and `tmdb_panel.height() >= 150`. The panel was constructed and visible, but the IMDb / Manual override boxes BELOW it were squished. The 150px assertion passed; the squish shipped.

**Resolution.** Tests now assert on EVERY relevant widget's rendered height >= sizeHint height. The visual test checks `tmdb_panel.height() >= 150`, `imdb_box.height() >= imdb_box.sizeHint().height()`, AND `manual_box.height() >= manual_box.sizeHint().height()`. A widget rendered below its sizeHint means Qt was forced to compress it, which (for QFormLayout containers) means the rows overlap visually.

**How to avoid.** A "widget exists and isn't too small" assertion is necessary but not sufficient. The real question is "is the widget rendered at AT LEAST the size its content requires?"

**Prompt proposals.**

- **Planner.** GUI briefs MUST specify, per widget: "rendered height assertion is `widget.height() >= widget.sizeHint().height()`." The "is the widget at least X pixels" form is a weaker assertion that misses compression bugs.
- **Implementer.** When asserting on a widget's rendered size in a test, default to `widget.height() >= widget.sizeHint().height()`. A hard-coded threshold (`>= 150px`) only catches catastrophic squish; sizeHint-based assertions catch any compression below the widget's natural size.
- **Code reviewer.** When a test asserts `widget.height() >= <constant>`, ask: "Should this be `>= widget.sizeHint().height()` instead? What's the rationale for the hard-coded number?"

---

## Issue 17 — Visual tests only covered the resolved (happy-path) state

**Issue.** The v0.1.3 first-cut visual test only asserted on the source-panel group label AFTER picking Lazarus (resolved state). The unresolved state — where the v0.1.3 target-panel label bug actually lived — was never asserted on for the target panel. The bug shipped because the test never rendered the unresolved-target-panel state.

**Resolution.** Added an assertion at step 2 (right after drop, before picking): the target panel group label must show "Lazarus_2" (the show_name_hint), not the first episode's filename. This is the regression gate that would have caught the target-panel bug.

**How to avoid.** Visual tests must walk the user's full state machine — drop → unresolved → review → pick → resolved → preview — and assert at EACH state, not just the terminal state. Bugs that only manifest in intermediate states slip through tests that jump straight to the end.

**Prompt proposals.**

- **Planner.** Visual / e2e GUI briefs MUST list the user's state machine and require assertions at each state, not just the terminal state. "Drop → unresolved → resolved → preview → apply" is five assertion points, not one.
- **Implementer.** When writing a visual / e2e test, walk every state the user passes through and assert on the relevant UI at each. Don't skip intermediate states because "the test for the next state would catch it" — the next state's test might shortcut around the bug.
- **Code reviewer.** When a visual / e2e test is long, count the assertion points and compare to the user's state machine. If the test has 1 assertion per 3 state transitions, ask: "Are intermediate states asserted on? What if a bug only manifests at state N but state N+1 papers over it?"

---

## Issue 18 — Sub-agent reports describe intent, not actual changes

**Issue.** Several times during the project, a sub-agent's summary claimed "the X bug is fixed" but the actual diff showed the fix was applied to a different widget OR the test that gates the fix didn't actually assert on the user-visible behavior. The primary agent had to re-verify by reading diffs and screenshots independently.

**Resolution.** The primary agent always reads the actual diff and the actual screenshot before declaring a fix shipped. "Sub-agent says it's fixed" is never sufficient evidence.

**How to avoid.** This is a process discipline, not a project change. The principle: trust the artifact, not the summary. Every claim ("the bug is fixed", "the test now asserts on X", "the layout is no longer squished") must be backed by an artifact (the diff, the screenshot, the test output) that the primary independently reads.

**Prompt proposals.**

- **Planner.** When the workflow involves a primary agent delegating to sub-agents, the brief MUST specify the verification artifact for each deliverable. "Sub-agent reports the fix is shipped" is not a verification; "Sub-agent saves a screenshot showing X, primary reads the screenshot and confirms" is.
- **Code reviewer (in the agent context).** When reviewing a sub-agent's report, look for the verification artifact. Did they save a screenshot? Run the test? Show the diff line that proves the fix? If the report is "I fixed it" without an artifact, request the artifact before declaring the work done.
- **Primary agent process.** Before declaring any delegated work complete, independently verify the artifact the sub-agent claims to have produced. Read the diff. Read the screenshot. Check the test output. Trust but verify.

---

## Summary — process changes this retrospective motivates

Synthesizing across all 18 issues, the structural changes for future briefs are:

1. **End-to-end pipeline tests are a load-bearing AC**, not a nice-to-have. Issues 1, 9, 11, 17 all hide behind unit tests that don't compose.
2. **Configuration the user can edit is LIVE by default**, not snapshotted. Issue 11 is the canonical case.
3. **Parallel widgets / mirrored logic get parallel tests.** Issue 9 is the canonical case.
4. **Visual tests run at realistic small windows** (1400×850 or smaller) with `>= sizeHint().height()` assertions. Issues 10, 15, 16 are all visual-layout bugs hidden by lazy test setup.
5. **Visual tests walk every state in the user's flow**, asserting at each transition, not just the terminal state. Issue 17 is the canonical case.
6. **Empty-results / fallback paths are designed up front**, not bolted on. Issues 12, 13, 14 are UX-validation gaps.
7. **Cross-platform discipline is encoded in the tests**, not in developer memory. Issue 3 is the recurring case.
8. **Tooling hygiene (lockfiles, PATH, quoting) is encoded in the safe-default path**, not in agent memory. Issues 2, 4, 5, 6 are tooling-hygiene gaps.
9. **Trust artifacts, not summaries.** Issue 18 is the meta-issue.

Every prompt section above can be lifted directly into the corresponding agent's instructions. The proposals are written as additive — they extend the existing prompts without contradicting them.
