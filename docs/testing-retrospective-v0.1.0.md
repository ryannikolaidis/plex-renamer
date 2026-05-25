# Testing retrospective: how a basic show-anchor bug shipped in v0.1.0

A real-world user drop (`MAX/Lazarus/s1/[S01.E01] Goodbye Cruel World.mp4`) hit a fundamental bug in v0.1.0: every row landed as `<unresolved>`, the group label showed the first episode's filename instead of the show, and the show-anchor picker came back empty. v0.1.1 fixes the symptoms. This document explains why the bug shipped through 334 green tests, what we should have tested instead, and the discipline this codifies going forward.

## The bug class in one sentence

The orchestrator and source panel derived the TV show name from the wrong field — the parser correctly stored the episode title in `episode_title` and left `title_candidate=None` for filenames shaped `[S01.E01] Title.mp4`, but downstream code fell back to `parent_dirs[-1]` (the season folder) or `raw_filename` (the episode filename). Neither is the show. The show name "Lazarus" sat at `input_root.name` (or `parent_dirs[0]` for deeper drops); no code path derived it.

## What we tested per slice

| Slice | What the tests verified | What they did NOT verify |
|-------|--------------------------|---------------------------|
| 1 Scaffold | `INVARIANTS.md` H2 sections present; pyproject parses; CLI importable | n/a |
| 2 Parser + corpus | Parser unit tests on every observed and plausible pattern; corpus generator produces every catalog entry; conftest write-guard fires | The parser output's downstream consumability through the resolver/planner |
| 3 TMDB client | HTTP-mocked client methods; cache TTL semantics; IMDb fallback path; config persistence | That the resolver receives the right query strings from the orchestrator |
| 4 Planner + executor + CLI | Per-shape path emission; show-anchor matching given a candidate; cleanup safety guards; write-ahead journal; **CLI plan→apply→undo on a three-file stub tree** | That realistic ParseResults flowing from the parser produce correct plans |
| 5 GUI | Ten per-AC widget tests (drop zone, badge, edit pane, persistence, modal, collision review, run report, etc.); one integration test on a two-file synthetic tree | That the orchestrator's signal wiring produces correct outputs against the real corpus |
| 6 Packaging | Built CLI responds to `--version` exit 0 | n/a |

Total at v0.1.0: 334 tests passing. Every per-slice AC satisfied. Plan-reviewer, AC-validator, staff, manager, and prove-claims all green on every slice.

## Where the gap was

The bug touched four layers — parser output shape, orchestrator field selection, source-panel label rendering, resolver query construction. Each layer was unit-tested in isolation. No test composed them.

- **Slice 4's CLI e2e test** drove `plan → apply → undo` end-to-end. It used three hand-rolled `ParseResult` instances, not the slice-2 corpus output. The hand-rolled instances were shaped however the test author chose; they happened to have `title_candidate` set, sidestepping the bug.
- **Slice 5's GUI integration test** (added in the round-2 amend after manager surfaced a production-wiring concern) drove the production `_parse_fn` + `_apply_fn` against a two-file synthetic tree. Same problem: synthetic data the author chose. The author chose a shape that did not exercise the bug.
- **The corpus generator** emits exactly the bug-triggering pattern (`Game Of Thrones/s1/[S01.E01] Winter Is Coming.mp4`), but no test walked the corpus through the orchestrator → resolver → planner pipeline. It was used only to feed parser-unit-test assertions.

The corpus existed. The pipeline existed. The test connecting them did not.

## Why the review machinery did not catch it

Every gate in the make-it-so workflow asked a question that — on each slice — answered "yes":

- **Plan-reviewer** audited the brief's acceptance criteria against the slice descriptions. Every AC was claimed by at least one slice and tested. The plan-reviewer does not synthesize an AC the brief never claimed, and the brief never claimed "end-to-end pipeline on realistic data."
- **AC-validator** verified that each slice satisfied its claimed ACs. It correctly answered "yes" because the claimed ACs were widget/unit-level.
- **Staff and manager cold reviewers** flagged real concerns at each slice (most notably the round-1 slice-5 manager who caught the missing orchestrator wiring). When the round-2 amend added an integration test, they accepted it as load-bearing because it drove the production wrappers — but neither raised the deeper "the synthetic input doesn't exercise the real input shapes" concern.
- **Prove-claims** demonstrated that the literal commands run and pass. The literal commands ran the per-slice unit tests against hand-rolled inputs.

Every gate is asking the right question for its scope. The gap is at the layer above: the brief itself.

## What the brief should have required

A single AC could have closed this:

> The full parse → resolve → plan pipeline runs against the slice-2 corpus generator's output through the real `Orchestrator` (no widget/layer stubs except the hermetic mock TMDB), and produces canonical Plex output paths for every show + movie pattern the corpus emits.

This AC would have:

- Forced the integration test to use `build_corpus()` instead of hand-rolled `ParseResult` instances.
- Forced assertions on canonical Plex paths (the actual product surface), not on intermediate dataclass fields.
- Surfaced the show-name derivation bug the moment slice 5's orchestrator landed, because the very first corpus entry for `Game Of Thrones/s1/[S01.E01] Winter Is Coming.mp4` would have produced `<unresolved>` or a Plex path containing "Goodbye Cruel World" instead of "Game of Thrones."

The corpus is the closest hermetic stand-in we have for the user's real CleverGet tree (which is READ-ONLY per `INVARIANTS.md`). The brief's slice-2 description called the corpus generator "the source of truth for input patterns"; the brief's slice-4 and slice-5 descriptions accepted hand-rolled inputs for downstream tests. That asymmetry is the bug.

## What changed in v0.1.1

- `tests/test_integration_corpus_pipeline.py` is now the load-bearing CI gate. It drives the corpus through the real Orchestrator with a hermetic mock TMDB and asserts canonical Plex output for every known show, plus four high-priority gap-fill cases: Doctor Who Classic flat-with-season, sidecar retargeting, specials routing, multi-season drops.
- `INVARIANTS.md` carries a new `## Testing discipline` section codifying the rule: any change touching the parse/resolve/plan/UI chain must keep this test green.
- The bug fix itself (`derive_show_name`, `ItemRow.show_name_hint`, per-group TV resolution) closes the symptom.

## What still needs to happen

Medium-priority coverage gaps the v0.1.1 test does not yet assert on but the corpus emits:

- Multi-episode files (`Sherlock - S01E01-E02 - A Study in Pink.mkv`) — distinct planner path.
- Multi-part movies (`Kill Bill - cd1.avi`, `- cd2.avi`) — distinct grouping logic.
- Conflict detection on `_1`-suffix duplicates (`Spaceballs.mp4` + `Spaceballs_1.mp4`) — distinct executor surface.
- Edition tokens auto-detected but not auto-applied (`Movie (2010) Director's Cut.mp4`) — slice 4 promised `detected_editions` survives end-to-end; only widget-tested today.

Each is a one-test addition. They land as follow-up commits, not as a new release.

Discipline shifts for future work:

- **Every brief whose first-cut behavior is a pipeline (parser → resolver → planner → UI) MUST claim, as an explicit AC, end-to-end pipeline correctness against the realistic input corpus.** Unit-level ACs per layer continue to exist; they do not replace the end-to-end AC.
- **When a slice introduces a new layer, the brief lists the integration tests added or extended to cover the layer's interaction with the layers above and below it.** Slices that add a new dataclass field, a new signal, or a new public method touch the integration surface and the brief must say how the integration test grows.
- **The plan-reviewer's "scope right-sizing" check should flag any brief whose ACs are all unit/widget-level when the project's core value is a pipeline.** This is a pattern, not a one-off; the plan-reviewer prompt should learn to ask "where's the end-to-end coverage?" explicitly.
- **Cold reviewers reading a brief or a diff should ask: "Could this PR cause a basic real-world drop to produce wrong output, and would the test suite catch it?"** That question on slice 5's round-1 review would have surfaced the integration gap before we shipped.

## The honest read

This bug was predictable. The corpus generator existed from slice 2 and shipped exactly the pattern that broke. The tests we shipped proved the pieces worked in isolation; they did not prove the product works for the user.

A 30-line integration test would have caught it. The cost of writing that test in slice 2 (cheapest) or slice 5 (latest reasonable point) was small. The cost of shipping without it was a user-facing crash on the first real drop.

The fix codifies the missing test and the AC pattern that would have required it. The next bug will be different; the discipline of "the integration test sits between layered unit tests" should catch the next bug class too.
