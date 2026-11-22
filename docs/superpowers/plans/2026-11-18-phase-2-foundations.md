# Phase 2: Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply section 4 of the audit everywhere (typed IDs, immutable models and registries,
typed payloads, keyword-only flags, an error-code catalogue, ordered selections, optional extras,
cached indexes), introduce stored format v2 with a migration from v1, and close every ledger test
of sections 4 and 9.3 plus the boundary defects of the other packages: 186 ledger IDs (199
tests). Ends with release v0.4.0, the main breaking release before 1.0.

**Architecture:** A serial lane L0 first lands what every other lane builds on: the Python floor,
the error-code catalogue, deprecation machinery, keyword-only flags across the whole library, ID
`NewType`s, core helpers and types, optional extras, the split of `choreo/models.py`, the format
v2 scaffolding with committed v1 fixtures, and the cached public-name lookup. Nine lanes then run
in parallel worktrees, each owning disjoint files: A show, B paths and engine, C geometry, D cast
and notes, S selection grammar, T time, F sheets, G forms and matching, E export. Each lane that
changes a stored shape writes its own migration sub-step. A final serial lane X proves the exit
criteria and prepares v0.4.0.

**Tech stack:** Python 3.13 and 3.14 (ADR 0011), pydantic v2, numpy, scipy, shapely, uv, nox,
ruff (FBT and C90 at 10 added), mypy `--strict`, pyright basic, pytest, Hypothesis,
pytest-timeout, pytest-xdist, git-cliff, Prettier 3.6.2, GitHub Actions.

**Spec:** `docs/upgrade-plan.md` sections 2, 4.1 to 4.6, 6, 7, 9.1 to 9.7 and 10 ("Phase 2").
Decisions: ADRs 0007, 0008, 0009, 0010 and 0011 (proposed 2026-11-18), with ADRs 0004, 0005,
0006 and 0014 from Phase 1. Master plan: `/home/uapb-ai/.claude/plans/look-at-the-upgrade-nifty-kay.md`
("Phase 2"). What Phase 1 already did: `docs/releases/v0.3.0.md`.

Where the audit and the code disagree, this plan follows the code (measured on `main` at
`3824263`, 2026-11-18). Disagreements found while planning:

- The audit names the package `stage`; the code is `showpy`.
- The audit counts 114 positional booleans and 20 functions above complexity 10 (v0.1.1). Today
  there are 107 positional boolean parameters and no function above complexity 10, so the
  "C90 burn-down" is lowering the gate from 12 to 10 (task L0.10), not refactoring.
- The audit says `Cast.selections` and `Spot.tags` are frozensets; Phase 1 made them tuples.
- The audit's `Fix.params` is not stored; it is engine output. It is still typed (task B2).
- ADR 0006 bans `-` in landmark and zone IDs, but 335 of the 339 item IDs in the six bundled
  sheet templates contain `-` (decision 1 below).

## Amendment: scope and format decisions (accepted 2026-11-22)

The maintainer accepted these decisions on 2026-11-22. They are recorded in ADR 0019 (licence),
ADR 0020 (rename), ADR 0021 (scope, deprecation timeline, retired ledger IDs), ADR 0023 (format
versions) and the addenda to ADRs 0003 and 0006. Every affected task carries a bold note under
its heading, and each new task has a full section. The plan text below is kept; where it
disagrees with this block or a task's note, the amendment wins.

**Dropped (3).** Their code leaves the library (ADR 0021). Their ledger IDs retire per ADR
0021's appendix and stay strict xfails until the code is deleted at v0.6.0.

| Task | Reason                                                                                         |
| ---- | ---------------------------------------------------------------------------------------------- |
| E2   | Video export leaves the library; UP-9.5-55 and UP-9.5-56 retire.                               |
| E3   | Publishing, print and tables leave the library; the interchange part of `data.py` moves in E4. |
| F2   | The sheet functions it hardens leave the library; its 7 IDs retire.                            |

**Deferred (5).** A5, B3, C5, D4 and G6 are pure module splits, deferred to a later phase that
partitions these modules again. No moved-name deprecation rows are added for them. With the
split part of S4 also deferred, 11 library modules stay over 500 lines, a written exception in
ADR 0021.

**Modified (6).** Details are in each task's note.

| Task | What changes                                                                                                        |
| ---- | ------------------------------------------------------------------------------------------------------------------- |
| A4   | The gate adds the ADR 0003 and 0006 addenda; the ID step also renames a stored ID `props` to `props_`.              |
| D3   | Keeps typed group and spot patches, `define_selection(replace=)` and aware note times; drops `NotePatch`.           |
| S4   | Keeps the `mirror_pairs` maximum-cardinality matching; the split of `select.py` is deferred.                        |
| X1   | Drops publishing and claiming; asserts notes in `notes.json`, the `e8.show` format value and revision independence. |
| X2   | New ledger line expectations, `test_every_leaving_name_is_deprecated`, `MAX_SPOTS` and `MAX_SETS`.                  |
| X3   | The release notes add the licence, the rename, the "leaving the library" table and the guide update.                |

T1 also gains an appended note: its fix round refuses a tempo or `ramp_to` of 0 or less. Its
scope is otherwise unchanged.

**New (11).** Sections: L1, Z1 and L2 under "Transition tasks"; B4, C2b, C7, C8, F3 and E4 in
their lanes; N1 and R1 under "Serial tail before lane X".

| Task | Lane        | What                                                                   | Gate                                                                | State                                                   |
| ---- | ----------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------- |
| L1   | transition  | Relicense under PolyForm Noncommercial 1.0.0                           | ADR 0019 accepted                                                   | implemented on `chore/licence`, awaiting merge          |
| Z1   | transition  | Six strict-xfail ledger tests, `UP-E-01` to `UP-E-06`                  | none                                                                | implemented on `test/evaluation-ledger`, awaiting merge |
| L2   | transition  | `MAX_COORD_M`, `MAX_COUNTS` and `MIN_TEMPO` bounds in the core aliases | none                                                                | implemented on `feat/core-bounds`, awaiting merge       |
| B4   | B           | Cached engine lookups                                                  | A1 and B2 merged                                                    | to do                                                   |
| C2b  | C           | Composite curves must be continuous (UP-9.1-21)                        | G1 merged                                                           | to do                                                   |
| C7   | C           | `project()` over the whole curve (UP-E-01)                             | C2 merged                                                           | to do                                                   |
| C8   | C           | Geometry results are finite or an error (UP-E-03)                      | C7 merged                                                           | to do                                                   |
| F3   | F           | Deprecation rows for the sheet names that leave                        | F1 merged, ADR 0021 accepted                                        | to do                                                   |
| E4   | E           | Interchange moves to `showpy/show/interchange.py`; export deprecations | E1 merged, ADR 0021 accepted                                        | to do                                                   |
| N1   | serial tail | Notes leave `Show` for a `notes.json` document                         | lanes A and D merged, ADR 0021 accepted                             | to do                                                   |
| R1   | serial tail | The rename to e8Show                                                   | every lane merged, no lane branch open, ADRs 0020 and 0023 accepted | to do                                                   |

**Order.** L1 first, before any lane branch is pushed. Z1 and L2 before the lanes rebase. Then
as in "Sequencing", plus: C2b after G1; B4 after A1 and B2; C7 after C2; C8 after C7; F3 after
F1; E4 after E1; N1 after lanes A and D merge; R1 after every lane merges and before lane X; X
last.

**Ledger coverage changes.** The E2 and F2 rows of "Ledger coverage" no longer flip, and F1 does
not flip UP-9.4-45; these 10 IDs retire with the other retired IDs. C2b flips UP-9.1-21 (listed
in C2's row), C7 flips UP-E-01 and C8 flips UP-E-03.

**Exit-criteria changes.** These add to "Phase 2 exit criteria", whose text stays:

- Ledger: ADR 0021 retires 63 IDs, which stay strict xfails until their code is deleted at
  v0.6.0; 13 IDs in the folders that lose code still flip with E1 and F1; Z1 adds `UP-E-01` to
  `UP-E-06`, of which C7 flips UP-E-01 and C8 flips UP-E-03. The default suite prints
  `Ledger: 188 open` (the plan's 174, plus the 10 IDs that no longer flip, plus the 4 open
  evaluation IDs); `nox -s slow` prints `Ledger: 1 open`.
- `validate_show` at 300 spots by 60 sets runs under 1 s (A1, G3).
- A seeded 3,000-spot show builds in under 3 s (A1, G3).
- With 1,500 holds, a clock call takes under 1 ms (T1).
- `sample` cost no longer grows with the number of sets (B4).
- The nightly fuzz run returns no NaN or infinity (C8).
- `project()` is within tolerance of brute force on spirals and zigzags (C7).
- Every leaving name has a deprecation row, checked by `test_every_leaving_name_is_deprecated`
  (X2).
- `import e8show` works and `import showpy` raises `ModuleNotFoundError` (R1).
- The wheel's `License-Expression` is
  `PolyForm-Noncommercial-1.0.0 AND MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts` (L1).
- `git diff v0.3.0 -- showpy/fonts` is empty; after R1 moves the folder, every font file shows
  only as a 100% rename.

## Decisions needed

**Decided 2026-11-19:** the maintainer accepted ADRs 0007 to 0011 (Python floor 3.13) and all
twelve decisions below as recommended. Decision 1 is recorded as a dated ADR 0006 addendum.

Each line: the question, the recommendation, and what it costs if the recommendation is wrong.

1. **Sheet template IDs with hyphens.** ADR 0006 says landmark and zone IDs cannot contain `-`,
   but 335 of 339 IDs in the bundled templates do (`front-sideline`, `end-zone-left`), and so do
   all six template refs (`sheet:hs-football@1`). Recommendation: a dated ADR 0006 addendum that
   lets sheet items (landmarks, zones, guides) and sheet refs keep an inner `-`, because they
   never appear in spot ranges or folder names. Cost if wrong: if a later grammar names zones
   (`zone:end-zone-left`), it must treat the whole word as one name; renaming then is one more
   migration step. (Gate for lane F and task A4.)
2. **How wide Phase 2 reaches.** Section 4's rules (no NaN, no leaked exceptions, typos rejected,
   model invariants, immutability) apply to every package, so I assigned 110 ledger IDs outside
   sections 4 and 9.3 to this phase, for 186 in total. Recommendation: yes, close them here,
   because later phases rewrite these modules for algorithms and should start from clean
   boundaries. Cost if wrong: the phase closes 186 IDs instead of 76, about 2.4 times the work
   of a sections-4-and-9.3-only phase.
3. **Old timestamps without a time zone.** v1 notes may hold `created_at` without a time zone,
   which v2 refuses. Recommendation: the migration reads them as UTC and lists them in the report.
   Cost if wrong: such a note shows a time off by the designer's UTC offset.
4. **Where `Set`-aware geometry goes.** 28 public functions in five `showpy.geo` modules take a
   `Set` or `Sheet`, so the lowest layer imports `choreo` and `sheet`. Recommendation: move the
   `Set` and `Sheet` handling into four `showpy/choreo/set_*.py` modules; `showpy.shift` and the
   other top-level names keep accepting points, curves and sets; `showpy.geo.transforms.shift`
   given a `Set` warns and forwards until v0.6.0. Cost if wrong: consumers importing from
   `showpy.geo` see warnings for two releases.
5. **`Form.geometry` becomes `Form.recipe`.** The typed recipe is a union keyed by `kind`.
   Recommendation: rename the field (the old name stays a warning read-only property until
   v0.6.0) and derive `Form.kind` from the recipe. Cost if wrong: 54 internal references change
   for a clearer name; keeping `geometry` with typed contents would work too.
6. **Parameters of user-registered motions and form generators.** Built-in motions (`weave`,
   `zigzag`, `figure_eight`, `spiral`) and generators get typed parameter models. Recommendation:
   parameters of user-registered ones stay an open, read-only JSON object, checked by the
   registered function itself. Cost if wrong: a typo in a custom parameter reaches the user's
   function instead of being caught by ShowPy.
7. **Text-on-field follow-ups.** Recommendation: threading `font_registry` through `match`,
   `reorder`, `gate`, `split_form`, `merge_forms`, `place`, `rebuild`, `apply_match`, `swap` and
   `morph_targets` lands here (task G5), because lane G rewrites those functions anyway; storing
   fonts in repositories waits for Phase 3's repository layout (ADR 0015); text stages 2 and 3
   stay a separate feature track after v0.4.0. Cost if wrong: lane G grows by one task.
8. **Which large modules split now.** 12 library modules pass 500 lines. Recommendation: split
   10 of them here; leave `show/repositories.py` (568) and `show/storage.py` (509) to Phase 3,
   which rewrites both. Cost if wrong: two oversized modules for one more phase.
9. **`validate_show` gaps.** Recommendation: Phase 2 adds only the 8 checks that follow from its
   own model rules (non-finite values, bad IDs, key and ID mismatch, unknown spots in named
   selections); the other 24 cross-object checks move to Phase 3 with incremental validation.
   Cost if wrong: dangling references stay unreported one phase longer.
10. **Smallest text height.** Recommendation: `MIN_LAYOUT_HEIGHT_M = 0.001` (1 mm), refused at
    layout and in `TextRecipe`; heights near 1e-14 m pass layout today and then fail deep in
    `tangent_at`. Cost if wrong: text under 1 mm, which no stage or field uses, is refused.
11. **New timeline edits.** Recommendation: `edit_tempo`, `edit_hold`, `move_marker`,
    `edit_cue` and new tempo curve shapes wait for Phase 5; Phase 2 only makes removals find
    items by label instead of float equality. Cost if wrong: designers keep using remove then
    add to change a tempo for one more phase.
12. **Name of the renamed `ValidationError`.** ADR 0009 proposes `ShowValidationError`, code
    `VALIDATION` unchanged. Cost if wrong: a second rename, again behind an alias.

## Global constraints

- **ADR gate.** No task that names an ADR or a decision in its **Gate** line starts before that
  ADR is `Accepted` or that decision is answered. An overruled ADR re-plans its tasks first.
- **Ledger flip procedure** (every task that fixes a ledger ID), unchanged from Phase 1:
  1. Run the ledger test with `--runxfail` and confirm it fails for the audited reason.
  2. Implement the fix.
  3. Remove the test's `@pytest.mark.defect(...)` and `@pytest.mark.xfail(...)` markers (or its
     `@defect(...)` decorator) in the same commit. Never edit an assertion to make it pass; only
     an API rename (a moved import, a keyword) or a tightening an accepted ADR names is allowed,
     and the commit body says which.
  4. Run `set -o pipefail; uv run pytest -m defect -q 2>&1 | tail -3` and confirm no
     `XPASS(strict)`. A test that flips in another lane's file is unmarked by the lane whose code
     flipped it (marker lines only).
- **Gates before every commit:** `set -o pipefail; uv run nox -s lint types tests hashseeds`.
  Lanes A, D, G and S also run `uv run nox -s slow`. Coverage floor stays 96% (branch).
- Every new behavior starts with a failing `test_regression_<what>` test; never weaken a check,
  threshold or default to make a test pass (project rule 4). A newly exposed defect is fixed or
  reported to the controller, never xfailed without a new ledger ID.
- **No new escape hatches.** No new `type: ignore`, `pyright: ignore`, `noqa` or `Any` in
  `showpy/`; no new `dict[str, Any]` in any model. Exit task X2 makes both a test.
- **Keyword-only flags.** From task L0.4 on, every boolean parameter is keyword-only (ruff FBT).
- **Error codes.** From task L0.2 on, a task that raises or reports a new code adds it to
  `ErrorCode` with a docstring and regenerates `docs/error-codes.md` in the same commit.
- **Library docstrings and comments never cite ADRs, docs files or ledger IDs**; they state the
  rule. `test_library_cites_no_adr_docs_or_ledger_ids` enforces it. Plans, ADRs, commit bodies and
  pull request text may cite them.
- `uv` only. Before any `npm` or `npx` command:
  `PATH=/home/uapb-ai/.nvm/versions/node/v22.23.5/bin:$PATH`. Run `npx prettier --check` on every
  Markdown, YAML or JSON file a task writes.
- Library modules under `showpy/` have no shebang; scripts and tests keep it. Module docstrings
  open `<filename> --- <role>` with a `Contains:` index; public functions get a verb-first
  docstring with a `>>>` example importing `showpy.examples`.
- New or changed public names go in `showpy/_api_table.py`; regenerate with
  `uv run python scripts/gen_public_api.py` and refresh the snapshot with
  `SHOWPY_UPDATE_API=1 uv run pytest tests/unit/test_api_snapshot.py`. A changed public name is
  backticked in the commit subject or a `BREAKING CHANGE:` footer. A renamed or moved name gets a
  deprecation row (ADR 0009) with `since="0.4.0"` and `remove="0.6.0"`.
- **Stored models.** A task that changes a stored shape (1) regenerates
  `showpy/schemas/showpy.show.v2.json` with
  `SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`, (2) fills its own
  migration sub-step module under `showpy/show/migrations/` and nothing else there, and (3)
  regenerates the fixtures' expected v2 output with
  `SHOWPY_UPDATE_MIGRATION=1 uv run pytest tests/unit/show/test_migrate.py` and reviews the diff.
  `showpy/schemas/showpy.show.v1.json` is frozen from L0.9 on.
- Shared generated files are never hand-merged; after a rebase, rerun their generators:
  `showpy/__init__.py`, `tests/fixtures/api_snapshot.json`, `showpy/schemas/*.json`,
  `CHANGELOG.md`, `tests/fixtures/repos/*/expected_v2.json`, `docs/error-codes.md`.
- Commits: Conventional Commits, one logical change each, no `Co-Authored-By` or "Generated with"
  trailers, under the identity `Oluwatoyosi Peter Abolaji`. Breaking commits use `!` and a
  `BREAKING CHANGE:` footer. Never push without the maintainer's approval.
- Execution: subagent-driven. One worktree per lane (`Agent` with `isolation: "worktree"`), a
  reviewer subagent gates every task, lane A and lane E add `differential-review`, and the
  controller merges each lane with `git merge --no-ff` and reruns every gate on `main`.

## Review focus

1. **Repositories written by v0.1.1, v0.2.0 and v0.3.0.** Expected: each loads, migrates in
   memory with a `MigrationReport`, writes v2 in exactly one revision on the first save and
   nothing on a second save, keeps every stored revision ID, and can be renumbered afterwards
   (legacy set IDs and case pairs included). Pinned by L0.9 (fixtures), A4 (ID step) and X1.
2. **Consumers pinned to v0.3.0.** 61 functions with keyword-only flags, four extras,
   `ShowValidationError`, moved module paths, `Form.recipe`, typed payloads and Python 3.13.
   Expected: every changed name is in the API snapshot diff and the changelog; moved names warn
   with `DeprecatedWarning`; an extra-gated call without the extra raises `MissingExtraError`
   with the install command. Pinned by L0.3, L0.4, L0.7, L0.8 and X3.
3. **Stored selectors that change meaning.** A v1 zone with `applies_to = "T1 + T2 & T2"` selects
   `T2` today. Expected: after migration it is stored as `(T1 + T2) & T2` and still selects
   `T2`; a new unparenthesized mix raises `MIXED_OPERATORS`. Pinned by S2.
4. **Stale cached indexes.** `Set.get`, `Cast.spot`, `ShowIndex` and the compiled clock are
   cached on frozen models. Expected: neither `replace()` nor `model_copy()` (still used by
   tests) ever returns an object whose cache describes its source. Pinned by L0.6 (the
   `FrozenModel.model_copy` rule) and tests in A1, D1, G3 and T1.
5. **Cost of the new checks at 300 spots x 60 sets.** Annotated ID types run `check_id` on every
   ID field, and migration adds a pass on load. Expected: `nox -s bench` M tier within 20% of
   baseline; decoding a 300 x 60 v1 show plus migration under 1 s on the maintainer's machine.
   Pinned by L0.5 step 5, L0.9 step 7 and X2.

---

## Sequencing

```
ADR gate: 0007 0008 0009 0010 0011, decisions 1-12
        │
L0.1 → L0.2 → … → L0.10            (serial, one worktree)
        │
        ├── A  show             gate: ADR 0007; A4 after D2 merges
        ├── B  paths/engine     gate: ADR 0007, decision 6
        ├── C  geometry         gate: decisions 4, 10
        ├── D  cast/notes       gate: ADR 0008 (D3); D2 after B1 and G1 merge
        ├── S  selection        gate: ADR 0008
        ├── T  time             gate: ADR 0007, decision 11
        ├── F  sheets           gate: decision 1
        ├── G  forms/matching   gate: ADR 0007, decisions 5, 7
        └── E  export           gate: ADR 0010
                    │
                    ▼
        X1 → X2 → X3               (serial: migration scenario, exit checks, release v0.4.0)
```

- L0.1 needs ADR 0011, L0.2 and L0.3 need ADR 0009, L0.4 needs ADR 0009, L0.7 needs ADR 0010,
  L0.9 needs ADR 0007. L0.5, L0.6, L0.8 and L0.10 need none.
- L0 lands everything that touches many packages (keyword-only flags in 31 modules, extras in 9,
  the `choreo/models.py` split), so the lanes never edit the same file.
- Cross-lane order: D2 (rename machinery over typed payloads) starts after B1 and G1 merge; A4
  (the migration's ID step) starts after D2 merges. Every other lane merges in any order.
- Until A4 merges, `decode_show` runs its final validation with `legacy_ids` still on, so a lane
  that adopts an ID field type never breaks loading of a v1 fixture with hyphenated IDs.
- Module splits (A5, B3, C5, D4, E3, G6 and S4's split) need decision 8; A2's scope needs
  decision 9; D3 needs decision 3; L0.3 needs decision 12.

## File map (lane ownership)

| Lane | Owns (source)                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Owns (tests)                                                                                                                                                                                                          |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L0   | `showpy/core/*` (except `selection.py` after L0), `showpy/_api_table.py`, `scripts/gen_public_api.py`, new `scripts/gen_error_codes.py`, `docs/error-codes.md`, `pyproject.toml`, `uv.lock`, `noxfile.py`, `.github/workflows/*.yml`, `docker/`, the split of `showpy/choreo/models.py`, `showpy/show/documents.py` (header and strict JSON), new `showpy/show/migrate.py` and `showpy/show/migrations/__init__.py`, every boolean-flag signature (L0.4) and optional import (L0.7) | `tests/unit/core/*`, `tests/unit/test_library_rules.py`, `tests/unit/test_api_snapshot.py`, `tests/unit/show/test_migrate.py`, `tests/fixtures/repos/`, `scripts/make_legacy_fixture.py`, `tests/unit/test_extras.py` |
| A    | `showpy/show/*` (`migrate.py` once L0 merges); `migrations/v1_to_v2_ids.py`, `v1_to_v2_events.py`; the `Set.key` validator in `showpy/choreo/models.py` (A4 only)                                                                                                                                                                                                                                                                                                                   | `tests/unit/show/*` except `test_migrate.py`                                                                                                                                                                          |
| B    | `showpy/choreo/{path_models,engine,engine_compile,engine_facing,engine_models,routes,paths,motion,avoid,motion_stats,facing}.py`, `showpy/show/migrations/v1_to_v2_paths.py`                                                                                                                                                                                                                                                                                                        | `tests/unit/choreo/test_{path_models,engine,engine_compile,engine_facing,routes,paths,motion,avoid,motion_stats,facing}.py`                                                                                           |
| C    | `showpy/geo/*`, new `showpy/choreo/set_{transforms,snapping,measure,analysis}.py`                                                                                                                                                                                                                                                                                                                                                                                                   | `tests/unit/geo/*`, `tests/unit/choreo/test_set_*.py`, `tests/fuzz/test_fuzz_svg.py`, `tests/property/test_geo_transforms.py`                                                                                         |
| D    | `showpy/cast/{models,spots,rewrite}.py` and the modules split from `spots.py`, `showpy/notes/*`, `showpy/show/migrations/v1_to_v2_cast.py`                                                                                                                                                                                                                                                                                                                                          | `tests/unit/cast/test_{models,spots,rewrite}.py`, `tests/unit/notes/*`                                                                                                                                                |
| S    | `showpy/cast/select.py` and the modules split from it, `showpy/core/selection.py`, range functions in `showpy/core/util.py` (`parse_range`, `format_ranges`, `split_id`, `natural_sort`), `showpy/show/migrations/v1_to_v2_selectors.py`                                                                                                                                                                                                                                            | `tests/unit/cast/test_select.py`, `tests/unit/core/test_selection.py`, `tests/fuzz/test_fuzz_select.py`, `tests/property/test_select_properties.py`                                                                   |
| T    | `showpy/time/*`, `showpy/show/migrations/v1_to_v2_timeline.py`                                                                                                                                                                                                                                                                                                                                                                                                                      | `tests/unit/time/*`, `tests/property/test_time_properties.py`                                                                                                                                                         |
| F    | `showpy/sheet/*`, `showpy/show/migrations/v1_to_v2_sheet.py`                                                                                                                                                                                                                                                                                                                                                                                                                        | `tests/unit/sheet/*`, `tests/fuzz/test_fuzz_coords.py`                                                                                                                                                                |
| G    | `showpy/choreo/{models,form_models,match_models,forms,form_fns,form_layout,form_ops,form_text,form_fit,form_place,text_count,text_edit,text_spread,match,match_cost,match_solve,match_ops}.py` and their splits, `showpy/show/migrations/v1_to_v2_forms.py`                                                                                                                                                                                                                         | `tests/unit/choreo/test_{models,form_models,match_models,forms,form_fns,form_ops,form_text,form_fit,form_place,text_count,text_edit,text_spread,match,match_ops}.py`                                                  |
| E    | `showpy/export/*`                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `tests/unit/export/*`, `tests/fuzz/test_fuzz_import.py`                                                                                                                                                               |
| X    | `tests/scenario/test_phase2_scenario.py`, `tests/unit/test_ledger.py`, `CHANGELOG.md`, `docs/releases/v0.4.0.md`, `docs/HANDOFF.md`, `docs/showpy-api-reference.md`                                                                                                                                                                                                                                                                                                                 | —                                                                                                                                                                                                                     |

## Ledger coverage

Counts are ledger IDs, then tests (some IDs have several parametrized tests). Measured with
`uv run pytest -m defect --co` on `main` at `3824263`: 345 open IDs in 374 tests (373 in the
default suite, 1 slow).

| Task | IDs it flips                                                                           | IDs / tests |
| ---- | -------------------------------------------------------------------------------------- | ----------- |
| L0.2 | UP-4.2-01; UP-9.7-59                                                                   | 2 / 2       |
| L0.5 | UP-9.3-31, -32                                                                         | 2 / 4       |
| L0.6 | UP-9.3-13, -14, -15, -16, -19, -20, -21, -36, -38, -39, -40, -41, -43, -44, -45 to -52 | 22 / 22     |
| L0.9 | UP-9.7-29, -92                                                                         | 2 / 2       |
| A1   | UP-9.7-56, -57, -58, -64                                                               | 4 / 4       |
| A2   | UP-9.7-75, -76, -77, -78, -81, -82, -88, -89                                           | 8 / 8       |
| B1   | UP-9.2-09, -10, -11, -14, -15                                                          | 5 / 5       |
| B2   | UP-9.2-24, -28, -35, -36; UP-9.6-26, -34, -38                                          | 7 / 7       |
| C1   | UP-9.1-14, -53, -54, -55, -56, -60, -62, -63, -68, -69, -70, -71, -73                  | 13 / 20     |
| C2   | UP-9.1-20 to -25, -64, -65, -66                                                        | 9 / 9       |
| C3   | UP-9.1-44, -45, -46, -48, -49                                                          | 5 / 5       |
| D1   | UP-9.3-56 to -61, -63 to -71                                                           | 15 / 19     |
| D2   | UP-9.3-82, -84, -85, -86, -87                                                          | 5 / 5       |
| D3   | UP-9.3-122 to -125                                                                     | 4 / 4       |
| S1   | UP-9.3-34, -35                                                                         | 2 / 2       |
| S2   | UP-9.3-83                                                                              | 1 / 1       |
| S3   | UP-9.3-24, -25, -27, -28                                                               | 4 / 4       |
| S4   | UP-9.3-76                                                                              | 1 / 1       |
| T1   | UP-9.3-95, -96, -97, -105 to -108, -114, -116 to -121                                  | 14 / 14     |
| T2   | UP-9.3-109, -111, -112, -113                                                           | 4 / 4       |
| T3   | UP-9.3-98                                                                              | 1 / 1       |
| F1   | UP-9.4-08 to -14, -44, -45                                                             | 9 / 9       |
| F2   | UP-9.4-18, -27, -28, -29, -32, -37, -38                                                | 7 / 7       |
| G1   | UP-9.6-45 to -49, -51 to -54, -58, -60                                                 | 11 / 11     |
| G2   | UP-9.6-11 to -14, -62                                                                  | 5 / 5       |
| G3   | UP-9.6-19, -20, -21, -39, -40, -41; UP-9.7-54                                          | 7 / 7       |
| G4   | UP-9.6-25, -31, -37, -50, -55, -56, -57, -61                                           | 8 / 8       |
| E1   | UP-9.5-12, -14, -29, -30, -34, -43, -44                                                | 7 / 7       |
| E2   | UP-9.5-55, -56                                                                         | 2 / 2       |

**Total: 186 IDs, 199 tests.** Every open UP-4 and UP-9.3 ID is in this table (1 and 75 IDs).
After Phase 2 the default suite should print `Ledger: 174 open` (373 − 199), and `nox -s slow`
`Ledger: 1 open` (UP-9.1-72, Phase 4). Lanes that flip a test in another lane's file: L0.2
(UP-9.7-59 in `tests/unit/show/test_manage.py`), G3 (UP-9.7-54, same file) and S2 (UP-9.3-83 in
`tests/unit/cast/test_rewrite.py`), marker lines only. L0.8 moves UP-9.6-26, -34 and -38 into
`tests/unit/choreo/test_path_models.py` and UP-9.6-39 to -41 into
`tests/unit/choreo/test_form_models.py` and `test_models.py` as part of the split.

The 159 IDs assigned to Phases 3, 4 and 5, with reasons, are in Appendix B.

---

## Transition tasks (serial, before the lanes rebase)

Added by the amendment of 2026-11-22. L1 merges first, before any lane branch is pushed, so no
lane commit is published under the old licence. Z1 and L2 merge next, before the lane branches
rebase onto `main`. All three are implemented on their branches and await merge; each section
records what its branch does, so the reviewer checks the branch against it.

### Task L1: relicense the library (ADR 0019)

**State:** implemented on `chore/licence`, awaiting merge. **Gate:** ADR 0019 accepted.
**Fixes:** none. **Files:** Modify `LICENSE`, `pyproject.toml` (`license`, `license-files`, the
MIT classifier removed), `README.md`, `CONTRIBUTING.md`, `noxfile.py` (`wheel` session),
`scripts/build_fonts.py`, `showpy/schemas/showpy.show.v2.json` (a `$comment` naming its
licence), `CHANGELOG.md` (generated). Create `showpy/schemas/LICENSE-MIT.txt` and
`docs/adr/0019-noncommercial-licence.md`. Test `tests/unit/test_packaging.py`,
`tests/unit/show/test_schema.py`, `tests/unit/test_build_fonts.py`.

**Interfaces:** the wheel's `License-Expression` is
`PolyForm-Noncommercial-1.0.0 AND MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts`: the library
under PolyForm Noncommercial 1.0.0, the JSON schemas under MIT so any tool may read and validate
show files, and the bundled fonts under their own OFL-1.1, Hershey and MIT terms, unchanged.
Releases up to and including v0.3.0 stay under MIT.

- [ ] **Step 1:** Tests: `test_distribution_states_its_licences` (the expression, the five
      licence files, no `License ::` classifier), `test_schemas_ship_their_mit_notice` and
      `test_v2_schema_states_its_mit_licence`. Run; expect FAIL.
- [ ] **Step 2:** Ship `showpy/schemas/LICENSE-MIT.txt` and the schema's `$comment`; the font
      build script copies the MIT text from there. Commit
      `chore(schemas): ship an MIT notice with the JSON schemas`.
- [ ] **Step 3:** Replace `LICENSE` with the PolyForm Noncommercial 1.0.0 text and its required
      notice; set the expression and licence files in `pyproject.toml` and drop the MIT
      classifier; `README.md` says v0.3.0 and earlier stay MIT and the non-commercial terms do
      not cover the font files; `nox -s wheel` asserts the expression and the schema notice in
      the built wheel. Commit `chore(licence)!: relicense under PolyForm Noncommercial 1.0.0`.
- [ ] **Step 4:** `CONTRIBUTING.md` accepts outside contributions only with a written grant that
      lets the maintainer relicense them. Commit
      `docs(contributing): accept outside contributions only with a relicensing grant`.
- [ ] **Step 5:** ADR 0019, then `uv run nox -s changelog`. Commits
      `docs(adr): 0019 license ShowPy under PolyForm Noncommercial 1.0.0` and
      `docs: regenerate the changelog`.

### Task Z1: six evaluation findings become strict-xfail ledger tests

**State:** implemented on `test/evaluation-ledger`, awaiting merge. **Fixes:** none; adds ledger
IDs `UP-E-01` to `UP-E-06`. **Files:** Modify `tests/ledger_plugin.py` (accepts section `E`),
`tests/unit/test_ledger.py`. Create `tests/unit/geo/test_curve_base_evaluation.py`,
`tests/unit/choreo/test_avoid_evaluation.py`, `tests/unit/geo/test_curves_evaluation.py`,
`tests/unit/geo/test_spacing_evaluation.py`, `tests/unit/choreo/test_match_solve_evaluation.py`,
`tests/unit/geo/test_shapes_evaluation.py`.

| ID      | Test                                                                                        | Defect                                                                                | Flipped by    |
| ------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |
| UP-E-01 | `test_curve_base_evaluation.py::test_project_finds_global_nearest_point_on_a_tight_spiral`  | `project()` searches locally from one sample and can land on the wrong arm of a curve | C7            |
| UP-E-02 | `test_avoid_evaluation.py::test_min_gaps_measures_rectangle_footprints_as_rectangles`       | gaps between rectangle footprints are not measured as rectangles                      | a later phase |
| UP-E-03 | `test_curves_evaluation.py::test_bezier_of_degree_1100_never_returns_a_non_finite_length`   | a finite but extreme curve returns a NaN length with no error                         | C8            |
| UP-E-04 | `test_spacing_evaluation.py::test_scatter_samples_every_component_of_a_split_region`        | scatter misses components of a region made of several parts                           | a later phase |
| UP-E-05 | `test_match_solve_evaluation.py::test_bottleneck_assignment_does_not_depend_on_slot_order`  | the bottleneck assignment changes with the order of the slots                         | a later phase |
| UP-E-06 | `test_shapes_evaluation.py::test_arc_through_near_collinear_points_is_a_near_straight_line` | `arc_through` misbehaves on nearly collinear points                                   | a later phase |

- [ ] **Step 1:** The ledger plugin accepts `UP-E-<nn>`, and `test_ledger.py` still refuses a
      malformed section. Commit `test(ledger): accept evaluation section E in defect IDs`.
- [ ] **Step 2:** The six tests above, each marked `@pytest.mark.defect("UP-E-0n")` and
      `@pytest.mark.xfail(strict=True, reason="UP-E-0n")`; each fails today for the reason in the
      table (checked with `--runxfail`). Commit
      `test: record six evaluation findings as ledger defects`.
- [ ] **Step 3:** After the merge the default ledger line rises by 6.

### Task L2: bounds on stored coordinates, counts and tempo

**State:** implemented on `feat/core-bounds`, awaiting merge. **Fixes:** none. **Files:** Modify
`showpy/core/caps.py`, `showpy/core/types.py`, `showpy/core/errors.py`,
`showpy/choreo/engine_models.py`, `showpy/_api_table.py`; regenerate `showpy/__init__.py`,
`tests/fixtures/api_snapshot.json`, `showpy/schemas/showpy.show.v2.json` and `CHANGELOG.md`.
Test `tests/unit/core/test_caps.py`, `tests/unit/core/test_types.py`,
`tests/unit/choreo/test_engine.py`.

**Interfaces:**

- `showpy/core/caps.py` adds `MAX_COORD_M = 1e6` (meters), `MAX_COUNTS = 1_000_000` and
  `MIN_TEMPO = 1e-3` (counts per minute), public names.
- The core aliases apply them: `Meters` and the coordinate tuples (`Point`, `Point3`, `Ring`)
  stay within `MAX_COORD_M`, counts within `MAX_COUNTS`, and `CountsPerMinute` is at least
  `MIN_TEMPO`. A value past its bound raises `SchemaError` whose context names the bound and
  what is allowed.
- Derived engine values stay finite but unbounded, so a full-width move still compiles:
  `MotionPiece.length`, `GapInfo.gap` and `Fix.new_gap` are `Finite`, and
  `EntityState.velocity` is the new `FinitePoint`.

- [ ] **Step 1:** Add the caps with `test_caps_have_approved_values`. Commit
      ``feat(core): add the `MAX_COORD_M`, `MAX_COUNTS` and `MIN_TEMPO` caps``.
- [ ] **Step 2:** `test_regression_full_width_move_compiles_and_samples` (a 2e6 m move from
      `-MAX_COORD_M` to `MAX_COORD_M` in one count compiles and samples); type the derived
      values as unbounded. Commit:
      ``fix(choreo): type `MotionPiece`, `GapInfo`, `Fix` and `EntityState` derived values as unbounded``.
- [ ] **Step 3:** Tests: `test_regression_placement_rejects_a_huge_coordinate` (2e300 m),
      `test_regression_box2_rejects_a_huge_corner`,
      `test_regression_tempo_section_rejects_a_vanishing_tempo` (1e-320 counts per minute),
      `test_regression_spline_rejects_a_huge_control_point`,
      `test_bounded_aliases_accept_their_bound` and
      `test_bounded_aliases_refuse_the_next_float_past_their_bound`. Run; expect FAIL. Apply the
      bounds in the aliases; regenerate the schema and the API snapshot; commit.

---

## Lane L0: foundations (serial)

Branch `feat/p2-l0-foundations`.

### Task L0.1: Python 3.13 floor (ADR 0011)

**Gate:** ADR 0011 accepted. **Files:** Modify `pyproject.toml` (`requires-python`,
classifiers, `[tool.ruff] target-version`, `[tool.pyright] pythonVersion`), `uv.lock`,
`.github/workflows/ci.yml` (matrix), `docker/` (base image tag if it pins 3.12), `noxfile.py`
(`PYTHON_FOR_WHEEL` if 3.12). Test `tests/unit/test_noxfile.py`.

- [ ] **Step 1:** Write `test_ci_matrix_matches_requires_python`: parses `ci.yml` and
      `pyproject.toml` and asserts the matrix Pythons equal the classifier versions and every one
      satisfies `requires-python`. Run; expect FAIL after editing only `pyproject.toml`.
- [ ] **Step 2:** Set `requires-python = ">=3.13"`, drop the 3.12 classifier and matrix entry,
      set ruff `py313` and pyright `3.13`, run `uv lock`. If `uv python list` shows
      `cpython-3.15.0` final (not an `rc`), add 3.15 to the matrix and classifiers; otherwise
      leave it and note it in the commit body.
- [ ] **Step 3:** Run `uv run ruff check --select UP .`; apply the upgrades it offers for 3.13
      only where ruff fixes them automatically, then all gates.
- [ ] **Step 4:** Commit `build!: require Python 3.13` with a `BREAKING CHANGE:` footer.

### Task L0.2: error-code catalogue and a richer `ShowPyError`

**Fixes:** UP-4.2-01, UP-9.7-59. **Files:** Create `showpy/core/error_codes.py`,
`scripts/gen_error_codes.py`, `docs/error-codes.md`. Modify `showpy/core/errors.py`,
`showpy/core/validation.py`, every raise site whose `code=` is a string literal (24 today).
Test `tests/unit/core/test_errors.py`, `tests/unit/core/test_validation.py`,
`tests/unit/test_library_rules.py`.

**Interfaces:**

- Produces `class ErrorCode(StrEnum)` in `showpy/core/error_codes.py`: one member per code the
  library raises or reports (the 19 class defaults such as `SCHEMA`, `LOCKED`, the 24 call-site
  codes such as `BAD_PATH`, `CORRUPT`, `TOO_FEW_SPOTS`, and every `ValidationIssue` code such as
  `TEMPO_NONPOSITIVE`), each with a one-line docstring saying when it is raised. Values equal
  names, so stored and logged codes do not change.
- `ShowPyError.__init__(message, *, hint, context=None, code: ErrorCode | None = None)`;
  `ShowPyError.to_dict() -> dict[str, JsonValue]` with keys `type`, `code`, `message`, `hint`,
  `context`; `__repr__` shows class, code and context. Context values are bounded on
  construction: strings and reprs to 80 characters (`bounded_repr`), sequences to their first 20
  items plus `"(+n more)"`.
- `ValidationReport` gains `errors` and `warnings` (tuples of issues), `codes` (ordered unique
  codes), `raise_for_errors() -> None` (raises `ShowValidationError` with the issues in
  context) and `to_json() -> str`; `ValidationIssue` gains `context: ReadOnly[dict[str, JsonValue]]`
  defaulting to empty.
- `docs/error-codes.md` is generated by `uv run python scripts/gen_error_codes.py` from the enum
  docstrings: a table of code, raised by (class), meaning.

- [ ] **Step 1:** Run UP-4.2-01 and UP-9.7-59 with `--runxfail`; expect the long echo and the
      unbounded `ConflictError` text.
- [ ] **Step 2:** Write `test_every_raised_code_is_catalogued`: an `ast` walk of `showpy/` that
      collects every `code=` literal and every `ValidationIssue` code and asserts each is an
      `ErrorCode` value; `test_error_code_docs_are_current` (regenerating `docs/error-codes.md`
      gives the committed bytes); `test_to_dict_round_trips_through_json`; and
      `test_regression_context_lists_are_capped` (a 1,000-item context list keeps 20 items plus
      `"(+980 more)"`). Run; expect FAIL.
- [ ] **Step 3:** Implement, replacing every string literal code with the enum member.
- [ ] **Step 4:** Deferred minors (Appendix A rows 10, 18, 29, 31): `schema_error_from` adds
      "(+n more)" past 5 errors; `__reduce__` gets a docstring stating that `__cause__` and
      `__traceback__` are dropped by design and a test pins `PicklingError` for an unpicklable
      context; the `bounded_repr` index line says what it catches; `bounded_repr` marks cut
      digits with `…`.
- [ ] **Step 5:** Ledger flip procedure. Commit
      `feat(core): add the ErrorCode catalogue and bounded error context`.

### Task L0.3: deprecation machinery and `ShowValidationError` (ADR 0009)

**Gate:** ADR 0009 accepted, decision 12 answered. **Files:** Create
`showpy/core/deprecation.py`. Modify
`showpy/core/errors.py`, `showpy/_api_table.py`, `scripts/gen_public_api.py`,
`tests/unit/test_api_snapshot.py`, the 6 raise sites in `showpy/export/publish.py` and
`showpy/export/positions.py`. Test `tests/unit/core/test_deprecation.py`.

**Interfaces:**

- `DeprecatedWarning(DeprecationWarning)`.
- `showpy.core.deprecation.moved(module_name: str, names: Mapping[str, str], *, since: str, remove: str) -> Callable[[str], object]`:
  returns a module `__getattr__` that warns `DeprecatedWarning` ("`old` moved to `new`; the old
  path is removed in `remove`") and returns the new object; unknown names raise
  `AttributeError`.
- `showpy.core.deprecation.renamed_field(new: str, *, since: str, remove: str) -> property`:
  a read-only property that warns and returns `getattr(self, new)`.
- Function and class aliases use `warnings.deprecated(msg, category=DeprecatedWarning)`.
- `_api_table.py` gains a `DEPRECATED` table of `(name, target, since, remove)`; the generator
  emits warning aliases for it, and the snapshot records them under `"deprecated"`.
- `class ShowValidationError(ShowPyError)`, `default_code = ErrorCode.VALIDATION`;
  `ValidationError` is a deprecated alias in `showpy` and `showpy.core.errors`.

- [ ] **Step 1:** Tests: `test_moved_name_warns_and_resolves`,
      `test_renamed_field_warns_and_reads_the_new_field`,
      `test_validation_error_alias_warns_and_is_the_same_class`
      (`issubclass` holds and `except ValidationError` catches a `ShowValidationError`), and
      `test_deprecations_are_removed_on_time`: fails for any `DEPRECATED` row whose `remove` is
      at or below `showpy.__version__`'s minor. Run; expect FAIL.
- [ ] **Step 2:** Implement; rename the class and its 6 raise sites; regenerate `__init__.py`
      and the snapshot.
- [ ] **Step 3:** Commit
      `feat(core)!: add deprecation aliases and rename ValidationError to ShowValidationError`
      with a `BREAKING CHANGE:` footer naming `ValidationError` and
      `DeprecatedWarning`.

### Task L0.4: keyword-only boolean flags (ADR 0009)

**Gate:** ADR 0009 accepted. **Files:** Modify `pyproject.toml` (`[tool.ruff.lint] select` adds
`FBT`; `tests/**` ignores `FBT001`, `FBT002`), and the 97 functions in 31 modules that ruff
lists (`uv run ruff check showpy --select FBT001,FBT002 --output-format concise`), plus every
caller in `showpy/`, `tests/`, `scripts/` and doctests.

- [ ] **Step 1:** Enable FBT; run `uv run ruff check . --select FBT`; expect 184 findings in
      `showpy/` (107 FBT001, 73 FBT002, 4 FBT003).
- [ ] **Step 2:** Insert `*` before the first boolean parameter of each flagged function; any
      parameter after it becomes keyword-only too, and the commit body lists those. Fix every
      caller the type checkers and tests report.
- [ ] **Step 3:** Deferred minor (Appendix A row 114): `_require_rows_unlocked(force)` becomes
      keyword-only with a full docstring.
- [ ] **Step 4:** Run all gates and `uv run nox -s slow`; regenerate the API snapshot (61 public
      signatures change). Write the list of changed public functions with their flags into the
      commit body; X3 copies it into the release notes.
- [ ] **Step 5:** Commit `refactor!: make every boolean flag keyword-only` with a
      `BREAKING CHANGE:` footer naming the 61 functions.

### Task L0.5: typed IDs and `TransitionId` (ADR 0006)

**Gate:** decision 1 answered. **Fixes:** UP-9.3-31, UP-9.3-32. **Files:** Modify
`showpy/core/ids.py`. Test `tests/unit/core/test_ids.py`, `tests/bench/` (ID-check cost).

**Interfaces:**

- `SpotId`, `SetId`, `SetKey`, `FormId`, `RouteId`, `ZoneId`, `LandmarkId`, `ShowId`, `NoteId`,
  `SelectionName` become `NewType(..., str)`.
- `class TransitionId(NamedTuple): from_set: SetId; to_set: SetId` with `__str__` giving the
  canonical `1→2` spelling; `parse_tid(text) -> TransitionId` refuses a third part.
- Annotated field types for models: `SpotIdField = Annotated[SpotId, AfterValidator(_checked("spot"))]`
  and one per kind. The validator calls `check_id` unless the validation context holds
  `{"legacy_ids": True}`, in which case it accepts any non-empty string (ADR 0007 decision 5).
  Decision 1's answer sets the pattern for `LandmarkIdField` and `ZoneIdField`.
- `parse_ref` refuses `..`, `/` and any ID part that fails `check_id`.

- [ ] **Step 1:** Run UP-9.3-31 (3 tests) and UP-9.3-32 with `--runxfail`.
- [ ] **Step 2:** Tests: `test_id_fields_reject_bad_ids` (a toy model with `SpotIdField` refuses
      `"a b"`, `""`, `"all"`), `test_id_fields_accept_legacy_ids_in_context`
      (`model_validate({"id": "a-b"}, context={"legacy_ids": True})` passes),
      `test_transition_id_is_a_named_tuple`, and the Appendix A row 19 cases: `'..a'` reports
      position 0; `ID_PATTERN` and `ID_BODY_CHARS`/`ID_MAX_LENGTH` agree on 10,000 Hypothesis
      strings; `'a\n'` is refused (`fullmatch`, not `$`).
- [ ] **Step 3:** Implement. Models adopt the field types in their own lanes; this task changes
      only `core/ids.py` and its callers' imports.
- [ ] **Step 4:** Ledger flip procedure.
- [ ] **Step 5:** Benchmark: `check_id` on 300 x 60 placements (18,000 IDs) must cost under 20 ms;
      record the number in the commit body.
- [ ] **Step 6:** Commit `feat(core)!: type IDs as NewTypes and TransitionId as a NamedTuple`.

### Task L0.6: core helpers, number types and immutable containers

**Fixes:** UP-9.3-13, -14, -15, -16, -19, -20, -21, -36, -38, -39, -40, -41, -43, -44, -45 to
-52. **Files:** Modify `showpy/core/util.py` (`as_points`, `approx_equal`, `chunk_counts`),
`showpy/core/units.py`, `showpy/core/types.py`, `showpy/core/readonly.py`, `showpy/core/caps.py`.
Create `showpy/core/numbers.py`. Test `tests/unit/core/test_{util,units,types,readonly,numbers}.py`.

**Interfaces:**

- `as_points(geom) -> Points` always returns a new C-contiguous `float64` array of shape (n, 2),
  takes x and y of 3D coordinates, and raises `GeometryError` for multi-part geometry.
- `approx_equal(a, b, *, abs_tol, rel_tol)` treats equal infinities as equal, never warns.
- `chunk_counts(total, parts)` derives parts from boundary differences so they sum exactly; NaN
  raises `SchemaError`.
- `UnitRegistry.meters_per_unit: ReadOnly[dict[str, Fraction]]`; `DEFAULT_UNITS` cannot be
  edited in place; `register_unit` refuses `m`, empty names, zero, negative and non-finite
  factors with `SchemaError`.
- `round_to(value, step)` accepts numpy scalars, refuses bools, infinities and a zero step with
  `SchemaError`, and returns `0.0` for every result that rounds to zero.
- `normalize_angle(deg)` returns `[0, 360)`, never `-0.0`, and refuses NaN and infinity.
- In `core/types.py`: `Heading = Annotated[float, AfterValidator(normalize_angle)]` for stored
  facings (rotations keep `Degrees`); `CountsPerMinute` becomes positive (`gt=0`, ruling R7);
  `Region` requires at least three distinct points and a simple ring (`shapely.is_valid`).
- `FrozenModel.model_copy` drops every `cached_property` value on the copy (the rule
  `CurveBase.model_copy` already follows), so later tasks can cache indexes safely.
- `showpy/core/numbers.py`: `format_number(value: float, *, max_decimals: int = 6) -> str` writes
  plain decimal notation, never exponent form (`1234567.5` gives `"1234567.5"`).
- `showpy/core/caps.py` adds `MIN_LAYOUT_HEIGHT_M = 0.001` (decision 10).
- `JsonValue` and `JsonObject = ReadOnly[dict[str, JsonValue]]` exported from `core/types.py` for
  open JSON payloads.

- [ ] **Step 1:** Run the 22 IDs with `--runxfail`.
- [ ] **Step 2:** Tests for each new interface above, including
      `test_regression_model_copy_drops_cached_values` (a toy `FrozenModel` with a
      `cached_property` index: `model_copy(update=...)` gives a fresh index) and
      `test_format_number_never_writes_an_exponent` (Hypothesis over finite floats in
      ±1e15: no `e` in the output and `float(output)` within 1e-6 relative).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure.
- [ ] **Step 5:** Deferred minors (Appendix A rows 15, 16): `require_finite` takes
      `value: SupportsFloat` so tests need no `type: ignore`; NaN `low`/`high` bounds raise;
      `Contains:` indexes of `tests/unit/core/test_types.py` and `test_validation.py` complete.
- [ ] **Step 6:** Commit `fix(core)!: make core helpers total, exact and immutable`.

### Task L0.7: optional extras and lazy imports (ADR 0010)

**Gate:** ADR 0010 accepted. **Files:** Create `showpy/core/extras.py`,
`tests/unit/test_extras.py`. Modify `pyproject.toml` (`[project.optional-dependencies]`),
`uv.lock`, `noxfile.py` (new `core` session), `.github/workflows/ci.yml` (job `core`),
`docker/` (install `.[all]`), and the import sites in
`showpy/choreo/{models,paths,motion_stats}.py`, `showpy/export/{data,pdf,print_sheets,publish,video}.py`, `showpy/sheet/render.py`.

**Interfaces:**

- `class MissingExtraError(ShowPyError)`, code `ErrorCode.MISSING_EXTRA`.
- `require_extra(module: str, *, extra: str, needed_by: str) -> ModuleType`: imports and caches;
  on `ImportError` raises `MissingExtraError` with hint
  `install the extra: pip install "showpy[<extra>]" or uv add "showpy[<extra>]"`.
- Extras: `tables = ["pandas>=3.1.1"]`, `render = ["pillow>=13.0.0"]`,
  `qr = ["segno>=1.6.6", "pillow>=13.0.0"]`, `video = ["imageio-ffmpeg>=0.8.1", "pillow>=13.0.0"]`,
  `all` = the union. Core keeps numpy, scipy, shapely, pydantic, pydantic-core.
- `is_dataframe(obj: object) -> bool` checks `sys.modules.get("pandas")` before `isinstance`.

- [ ] **Step 1:** Tests in `tests/unit/test_extras.py`:
      `test_core_modules_import_no_optional_dependency` (a subprocess imports every module under
      `showpy/` and asserts none of `pandas`, `PIL`, `segno`, `imageio_ffmpeg` is in
      `sys.modules`), `test_missing_extra_names_the_install_command` (monkeypatch
      `importlib.import_module` to raise `ImportError` for pandas; `speeds(...)` raises
      `MissingExtraError` whose hint contains `showpy[tables]`). Run; expect FAIL.
- [ ] **Step 2:** Move every optional import into the functions that need it through
      `require_extra`; annotations import under `TYPE_CHECKING`.
- [ ] **Step 3:** Add the `core` nox session: build the wheel, install it without extras in a
      clean venv, run the README usage example, import every module, and call each of the 10
      gated functions expecting `MissingExtraError`. Add it to CI on Linux.
- [ ] **Step 4:** Measure `import showpy.choreo.models` (median of 9 fresh interpreters) before
      and after; record both in the commit body (ADR 0010 expects about 339 ms from 466 ms).
- [ ] **Step 5:** Commit `build!: make pandas, Pillow, segno and imageio-ffmpeg optional extras`
      with a `BREAKING CHANGE:` footer listing the extras and gated functions.

### Task L0.8: split `choreo/models.py` (738 lines)

**Files:** Create `showpy/choreo/form_models.py` (`SpacingSpec`, `FacingSpec`, `TextRecipe`,
`FormKind`, `CrowdedStroke`, `TextReport`, `Form`, `FormFnInfo`, `FONT_URI_PATTERN`,
`MAX_TEXT_FONTS`, `FontUri`), `showpy/choreo/path_models.py` (`PathStyle`, `FacingMode`,
`TurnDirection`, `FacingOverride`, `Waypoint`, `Easing`, `PathRow`, `Route`, `TransitionSpec`,
`DEFAULT_EASING_STRENGTH`), `showpy/choreo/match_models.py` (`MatchObjective`, `CostFn`,
`MatchOptions`, `MatchResult`, `MATCH_WEIGHT_KEYS`). `showpy/choreo/models.py` keeps
`ApprovalState`, `Placement`, `Set`, `SpotCount`, `TABLE_COLUMNS` and a `moved(...)` module
`__getattr__` for the 26 moved names. Split `tests/unit/choreo/test_models.py` the same way.

- [ ] **Step 1:** Write `test_moved_choreo_model_names_warn` (each of the 26 old paths warns
      `DeprecatedWarning` and returns the same object as the new path). Run; expect FAIL.
- [ ] **Step 2:** Move the code with no behavior change; update every library import to the new
      modules (`grep -rn "choreo.models import" showpy tests scripts`).
- [ ] **Step 3:** Run all gates; the schema file must not change
      (`git diff --exit-code showpy/schemas/`).
- [ ] **Step 4:** Commit `refactor(choreo): split choreo.models into form, path and match models`.

### Task L0.9: format v2 scaffolding, strict JSON and v1 fixtures (ADR 0007)

**Gate:** ADR 0007 accepted. **Fixes:** UP-9.7-29, UP-9.7-92. **Files:** Create
`showpy/show/migrate.py`, `showpy/show/migrations/__init__.py` and one stub per sub-step:
`v1_to_v2_{forms,paths,selectors,timeline,facings,sheet,cast,events,ids}.py` (each an identity
function with a docstring naming its owning lane), `tests/unit/show/test_migrate.py`,
`tests/fixtures/repos/v0.1.1/`, `tests/fixtures/repos/v0.3.0/`,
`showpy/schemas/showpy.show.v2.json`. Modify `showpy/show/documents.py` (header, strict parse),
`showpy/export/data.py` (`export_json` header, `import_json` accepts the v1 tag),
`scripts/make_legacy_fixture.py`.

**Interfaces:**

- `FORMAT = "showpy.show"`, `CURRENT_VERSION = 2`.
- `read_version(docs: Mapping[str, bytes]) -> int`: 1 when `show.json` has no header;
  `VersionError` (context `version`, `newest`) above `CURRENT_VERSION`.
- `class MigrationReport(FrozenModel)`: `from_version: int`, `renamed:
ReadOnly[dict[str, tuple[tuple[str, str], ...]]]` keyed by ID kind, `repairs: tuple[str, ...]`.
- `migrate_documents(docs: Mapping[str, bytes]) -> tuple[dict[str, bytes], MigrationReport]`
  (public); `decode_show` calls it, then validates with `context={"legacy_ids": True}`, runs the
  `ids` sub-step on the model, and validates again. Until A4 fills the `ids` step, the second
  validation also passes `legacy_ids`, so fixtures with hyphenated IDs keep loading.
- `ReportBuilder` (in `migrate.py`, not public): `rename(kind: str, old: str, new: str) -> None`,
  `repair(text: str) -> None`, `build() -> MigrationReport`; one per migration run.
- Sub-step signature (JSON steps): `def step(docs: dict[str, JsonValue], report: ReportBuilder) -> dict[str, JsonValue]`;
  the `ids` step: `def step(show: Show, report: ReportBuilder) -> Show`. Order fixed in
  `migrations/__init__.py`: forms, paths, selectors, timeline, facings, sheet, cast, events,
  then ids.
- Strict parse: `json.loads` with `parse_constant` raising, `object_pairs_hook` refusing duplicate
  keys, a leading BOM refused, lone surrogates refused when re-encoding; each a `SchemaError`
  naming the document.
- `encode_show` writes `"format"` and `"version"` first in `show.json`; `export_json` writes
  `{"format": "showpy.show", "version": 2, "show": ..., "fonts": ...}`.

- [ ] **Step 1:** Generate the fixtures. `v0.1.1/`: in a checkout of tag `v0.1.1`, run the
      fixture script adapted to the `stage` import name (a `--package stage` flag). `v0.3.0/`: in
      a checkout of `v0.3.0`, write a show with a renumbered set (explicit keys), a text form, a
      zone `applies_to = "T1 + T2 & T2"`, a legacy set ID `con`, and the v0.2 case pair `1a`/`1A`
      written by hand as v0.2 did. Each fixture keeps a `README.md` saying how it was made and
      that it is read-only. Commit them first: `test: add v0.1.1 and v0.3.0 repository fixtures`.
- [ ] **Step 2:** Run UP-9.7-29 and UP-9.7-92 with `--runxfail`.
- [ ] **Step 3:** Tests in `tests/unit/show/test_migrate.py`:
      `test_every_fixture_migrates_to_its_expected_v2` (documents and report equal
      `expected_v2.json`, regenerated only under `SHOWPY_UPDATE_MIGRATION=1`),
      `test_first_save_after_migration_writes_one_revision_and_the_second_none`,
      `test_stored_revision_ids_survive_migration`, `test_strict_json_refuses_nan_tokens`,
      `test_strict_json_refuses_duplicate_keys`, `test_v030_reader_refuses_a_v2_tree`
      (decoding a v2 `show.json` with the v0.3.0 `Show` schema from
      `showpy/schemas/showpy.show.v1.json` fails). Run; expect FAIL.
- [ ] **Step 4:** Implement. Freeze `showpy.show.v1.json`; generate `showpy.show.v2.json`.
- [ ] **Step 5:** Ledger flip procedure.
- [ ] **Step 6:** Deferred minors (Appendix A rows 22, 68): `scripts/make_legacy_fixture.py`
      type-checks again; the v0.2.0 legacy test asserts tags, selection, script and pins survive.
- [ ] **Step 7:** Benchmark: decode plus migrate of a 300 x 60 show written by v0.3.0 (generate
      it with `seeded_show` in the v0.3.0 checkout, keep it under `tests/bench/fixtures/`);
      budget 1 s. Commit `feat(show)!: add stored format v2 with a v1 migration chain`.

### Task L0.10: cached public names, complexity gate 10, layering test

**Files:** Modify `scripts/gen_public_api.py`, `showpy/__init__.py` (generated),
`pyproject.toml` (`[tool.ruff.lint.mccabe] max-complexity = 10`),
`tests/unit/test_library_rules.py`.

- [ ] **Step 1:** Tests: `test_public_names_are_cached_after_first_use` (after `showpy.Show`,
      `"Show" in vars(showpy)`), and `test_geo_imports_only_core`: an `ast` scan of
      `showpy/geo/*.py` (runtime and `TYPE_CHECKING` imports) that allows only `showpy.core`
      and `showpy.geo`, marked `xfail(strict=True, reason="lane C task C4")` until C4 lands.
      Run; expect the first to FAIL.
- [ ] **Step 2:** The generated `__getattr__` stores the resolved object in `globals()`.
      Deferred minor (Appendix A row 17): remove the duplicated `isupper()` test in
      `_import_order`.
- [ ] **Step 3:** Lower McCabe to 10; `uv run ruff check . --select C901` must report 0.
- [ ] **Step 4:** Commit `perf: cache public names on first use; lower the complexity gate to 10`.

---

## Lane A: show layer

Branch `feat/p2-a-show`. Skills: `differential-review` on every task.

### Task A1: `Show` invariants, immutability and a cached index

**Fixes:** UP-9.7-56, -57, -58, -64. **Files:** Modify `showpy/show/models.py`,
`showpy/show/validate.py`.

**Interfaces:** `Show.forms: ReadOnly[dict[FormIdField, Form]]` with a validator that every key
equals its form's ID; `Show.dependencies: ReadOnly[dict[str, str]]`; `Show` is hashable (frozen
containers only); `Show.index` is a `cached_property` returning `ShowIndex` (set position by ID,
transition by `TransitionId`, form by ID), used by `Show.set`, `Show.set_index` and
`Show.transition`. Every ID field uses its annotated type from L0.5.

- [ ] **Step 1:** Run the four IDs with `--runxfail`. **Step 2:** Write
      `test_regression_show_index_is_fresh_after_replace` (replace `sets` and look up a new set).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure; regenerate the v2 schema.
- [ ] **Step 5:** Commit `fix(show)!: freeze Show containers and cache its index`.

### Task A2: `validate_show` reports what models now refuse

**Gate:** decision 9 answered. **Fixes:** UP-9.7-75, -76, -77, -78, -81, -82, -88, -89.
**Files:** Modify
`showpy/show/validate.py`. These tests build invalid shows with `model_copy(update=)`, which
skips validation; `validate_show` must report what the constructors refuse.

- [ ] **Step 1:** Run the eight with `--runxfail`.
- [ ] **Step 2:** Add one check per gap, each with an `ErrorCode` (`NOT_FINITE`, `BAD_ID`,
      `KEY_MISMATCH`, `UNKNOWN_SPOT`) and the offending path in `ValidationIssue.context`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(show): report non-finite values and bad IDs in validate_show`.

### Task A3: typed canvas events

**Files:** Modify `showpy/show/models.py` (`CanvasEvent`), `showpy/show/collab.py`, fill
`showpy/show/migrations/v1_to_v2_events.py`.

**Interfaces:** `CanvasEvent.payload` becomes a discriminated union on `kind`, one frozen model
per event kind the library emits (measure the kinds with `grep -n "kind=" showpy/show/collab.py`
and list them in the commit body). `decode_event(data: Mapping[str, JsonValue]) -> CanvasEvent`
accepts v0.2 event JSON that still holds `actor` (dropped, reported) and v0.3 JSON (Appendix A
row 67).

- [ ] **Step 1:** Tests: `test_event_payload_rejects_unknown_fields`,
      `test_regression_v02_event_with_actor_decodes`, `test_every_emitted_event_round_trips`
      (every event `events_since` produces on the scenario show dumps and decodes equal).
- [ ] **Step 2:** Implement. **Step 3:** Commit `feat(show)!: type canvas event payloads`.

### Task A4: the migration's ID step (ADR 0007 decision 5)

**Amended:** gate adds the ADR 0003 and 0006 addenda; the ID step also renames a stored ID `props`
to `props_` (`props` is reserved, ADR 0006 addendum: case-insensitive and whole ID only, so `Props`
becomes `Props_` and `props.1` stays valid). Step 1 adds
`test_regression_stored_id_props_gets_a_trailing_underscore` (`props` becomes `props_`, `Props`
becomes `Props_`, `props.1` is kept).

**Gate:** ADR 0007 accepted, decision 1 answered, D2 merged. **Files:** Fill
`showpy/show/migrations/v1_to_v2_ids.py`, `showpy/show/migrate.py` (the final validation stops
passing `legacy_ids`); modify `showpy/show/manage.py` (`renumber` loses its legacy refusal),
`showpy/show/models.py` and the `Set.key` validator in `showpy/choreo/models.py` (those lines
only, as Phase 1 lane A did): the "key equals ID means legacy" carve-out goes, because after this
step no stored key needs it.

**Interfaces:** `rename_ids(show: Show, report: ReportBuilder) -> Show` applies the ADR 0007 rule
per kind, in show order, through D2's `rename_spots` for spots and a private
`_rename_sets(show, mapping)` (rewrites set IDs, keys, transition IDs, form `set`, note anchors,
pins, cues) for sets.

- [ ] **Step 1:** Tests: `test_regression_case_pair_show_renumbers_after_migration` (fixture
      v0.3.0: `1a`, `1A` become `1a`, `1A.2`; `renumber` succeeds), `test_regression_legacy_set_id_con_is_renamed`
      (`con` becomes `con_`), `test_v020_form_ids_lose_hyphens` (16 renames, `brass-2` to
      `brass_2`, `front-four` to `front_four`), `test_rename_rule_is_deterministic_under_hash_seeds`
      (`outputs_under_hash_seeds`).
- [ ] **Step 2:** Implement. Update `test_regression_v020_case_pair_show_cannot_be_renumbered` to
      its v2 meaning (it is a Phase 1 test, not ledger); the commit body names the change.
- [ ] **Step 3:** Regenerate `expected_v2.json` for all fixtures; review the rename lists.
- [ ] **Step 4:** Commit `feat(show)!: rename invalid stored IDs once during migration`.

### Task A5: split `show/manage.py` (553 lines)

**Deferred (amendment):** a pure module split; ADR 0021 defers it to a later phase, which partitions
these modules again, and no moved-name deprecation rows are added for it. Do not start in Phase 2.

**Files:** Split `showpy/show/manage.py` into `manage.py` (show-level edits) and
`set_edits.py` (add, remove, insert, renumber, counts). Deprecation rows for moved names.

- [ ] **Step 1:** Moved-name test as in L0.8. **Step 2:** Move with no behavior change.
- [ ] **Step 3:** Deferred minor (Appendix A row 100): promote `manage._with_sets` to a public
      helper `with_sets` or keep it private and stop `export/positions.py` importing it (lane E
      changes the import after this task merges). **Step 4:** Commit
      `refactor(show): split set edits out of show.manage`.

---

## Lane B: paths and engine

Branch `feat/p2-b-paths`.

### Task B1: typed motion parameters and a read-only motion registry

**Gate:** ADR 0007 accepted, decision 6 answered. **Fixes:** UP-9.2-09, -10, -11, -14, -15.
**Files:** Modify `showpy/choreo/motion.py`, `showpy/choreo/path_models.py`,
`showpy/choreo/engine_compile.py`; fill `showpy/show/migrations/v1_to_v2_paths.py` (motion part).

**Interfaces:**

- `WeaveParams`, `ZigzagParams`, `FigureEightParams`, `SpiralParams` (frozen, constrained:
  counts and cycles positive and capped, `strength >= 1`, easing `steps <= MAX_EASING_STEPS`
  with a value set from the largest built-in use times 10).
- `PathRow.motion_params: BuiltInMotionParams | JsonObject`, where the union is discriminated by
  `motion_fn`; a registered custom motion gets `JsonObject` (decision 6).
- `MotionRegistry` holds `ReadOnly[dict[str, MotionFn]]`; `register_motion` returns a new one.
- Custom motion output is checked once at compile: shape `(n, 2)`, finite, and its end within
  the segment tolerance of the row's target, else `PathError` / `ContinuityError`.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Tests:
      `test_builtin_motion_params_reject_unknown_keys`, `test_motion_registry_is_read_only`.
- [ ] **Step 3:** Implement; v1 `motion_params` dicts migrate into the typed model for the four
      built-ins. **Step 4:** Ledger flip procedure; schema and migration expectations.
- [ ] **Step 5:** Commit `feat(choreo)!: type motion parameters and freeze the motion registry`.

### Task B2: typed routes, facings and fixes; path model invariants

**Fixes:** UP-9.2-24, -28, -35, -36; UP-9.6-26, -34, -38. **Files:** Modify
`showpy/choreo/path_models.py`, `engine.py`, `engine_facing.py`, `engine_models.py`, `avoid.py`,
`motion_stats.py`, `routes.py`; fill the route part of `v1_to_v2_paths.py`.

**Interfaces:**

- `Route.curve: AnyCurve`; `TransitionSpec.routes: ReadOnly[dict[RouteIdField, Route]]` with keys
  equal to IDs; `TransitionSpec.rows` read-only; `TransitionSpec.id: TransitionId`.
- `FacingValue = FixedFacing | PointFacing | EntityFacing` (discriminated by `kind`), used by
  `PathRow.facing_value` and `FacingOverride.value`; removes the three `type: ignore` in
  `engine_facing.py`.
- `Fix.params` becomes `BendFix | DelayFix | ...` (one model per fix kind `avoid.py` emits).
- `PathRow.start` finite, `TransitionSpec.counts >= 0`.
- Leaks: a spot with no sets, `top=0`, an empty track and a negative `per_count` raise
  `ShowPyError` subclasses.

- [ ] **Step 1:** Run the seven with `--runxfail`. **Step 2:** Implement.
- [ ] **Step 3:** Deferred minors (Appendix A rows 109, 110, 113, 115): test that two engines give
      the same segments on a spline row; validate `tol` once; route lock state read from the
      stored transition, not the caller's copy (new `test_regression_route_builder_reads_stored_locks`);
      tests for an absent spec ID and an unselected locked row.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `feat(choreo)!: type routes, facing values and fixes`.

### Task B3: split oversized modules in lane B, if any

**Deferred (amendment):** a pure module split; ADR 0021 defers it to a later phase, which partitions
these modules again, and no moved-name deprecation rows are added for it. Do not start in Phase 2.

Run `wc -l showpy/choreo/{engine,paths,routes,avoid}.py` after B1 and B2. Split any file over
500 lines by responsibility, with moved-name deprecations. Today none is over 500
(`engine.py` 478). Commit `refactor(choreo): ...` only if a split happens.

### Task B4: cached engine lookups

**Gate:** A1 and B2 merged. **Fixes:** none. **Files:** Modify `showpy/choreo/engine.py`. Test
`tests/unit/choreo/test_engine.py`, `tests/bench/test_budgets.py`.

`PathEngine.locate` and `PathEngine.compile_all` call `show.transition_ids()`, which builds a new
list on every call (`showpy/show/models.py:159`, `showpy/choreo/engine.py:137`), so sampling a
show many times costs the number of samples times the number of sets. A1 caches the `Show`
index and G3 caches `Set.get`; this task makes the engine stop rebuilding its own lookups.

**Interfaces:** `PathEngine` reads transitions through `Show.index` (A1) and computes the start
count of every transition once per engine; `locate(t)` bisects those start counts and builds no
list or dictionary per call. Public signatures do not change.

- [ ] **Step 1:** Tests: `test_regression_sampling_builds_the_transition_lookup_once` (a counting
      spy on the lookup builder: 1,000 `positions` calls on one engine build it at most once) and
      `test_regression_sample_cost_does_not_grow_with_sets` (the same spy counts equal builds per
      sample for a 10-set and a 100-set show). Count builds, never wall time. Run; expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run `uv run nox -s bench`; tier M stays within 20% of the baseline. Record the
      before and after numbers in the commit body.
- [ ] **Step 4:** Commit `perf(choreo): stop rebuilding transition lookups on every engine call`.

---

## Lane C: geometry

Branch `feat/p2-c-geo`.

### Task C1: every geometry failure is a `ShowPyError`; typos are refused

**Gate:** decision 10 answered (for the text-height item). **Fixes:** UP-9.1-14, -53, -54, -55,
-56, -60, -62, -63, -68, -69, -70, -71, -73. **Files:** Modify
`showpy/geo/{shapes,spacing,analysis,svg,text_layout}.py`.

- [ ] **Step 1:** Run the 13 IDs (20 tests) with `--runxfail`.
- [ ] **Step 2:** String options go through `pick()` (`side`, `align`, `anchor`); Shapely and
      numpy failures are wrapped where they occur (`GEOSException`, `ValueError`,
      `LinAlgError`) into `GeometryError` or `SpacingError` with the input named.
- [ ] **Step 3:** Deferred minors (Appendix A rows 2, 3, 5): `space_by_interval(start=)` and
      `fill(angle_deg=, margin=, anchor=)` refuse non-finite input; `fill` on an empty or
      1e308-sized area without `anchor` raises `SpacingError`; the SVG `_arc` hint states only
      what is enforced. `text_layout` refuses `height < MIN_LAYOUT_HEIGHT_M`
      (`test_regression_layout_refuses_a_height_below_one_millimeter`).
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(geo): raise ShowPyErrors for every bad geometry input`.

### Task C2: curve model invariants

**Fixes:** UP-9.1-20 to -25, -64, -65, -66. **Files:** Modify `showpy/geo/curves.py`,
`showpy/geo/curve_base.py`, `showpy/geo/shapes.py`.

- [ ] **Step 1:** Run the nine with `--runxfail`.
- [ ] **Step 2:** Validators: `TrimmedCurve` `0 <= s0 < s1 <= length` of an open base;
      `CompositeCurve` parts meet within `CONTINUITY_TOL_M`, and `closed=True` only when the last
      part ends on the first start; `SvgCurve` `closed` matches a trailing `Z`; transforms are
      3x3; `Bezier` degree capped (`MAX_BEZIER_DEGREE`, set from the largest bundled font curve
      times 10); radii and widths finite and positive.
- [ ] **Step 3:** Ledger flip procedure; regenerate the v2 schema (curves are stored in routes
      and recipes). **Step 4:** Commit `fix(geo)!: curves refuse inconsistent construction`.

### Task C2b: composite curves must be continuous

**Gate:** G1 merged (G1 stores a multi-subpath SVG outline as separate curves, so no stored
recipe needs a gapped composite). **Fixes:** UP-9.1-21. **Files:** Modify
`showpy/geo/curves.py`. Test `tests/unit/geo/test_curves.py`.

C2 held back its composite-continuity validator; this task lands it once G1 no longer builds
gapped composites.

**Interfaces:** `CompositeCurve` refuses parts that do not meet within `CONTINUITY_TOL_M`, with a
`SchemaError` whose context names the index of the first part that does not meet and the gap in
meters.

- [ ] **Step 1:** Run UP-9.1-21 with `--runxfail`; expect a gapped composite to construct.
- [ ] **Step 2:** Test `test_regression_composite_gap_names_the_part` (a 5 m gap between parts 0
      and 1 raises `SchemaError` with `context["part"] == 1`). Run; expect FAIL.
- [ ] **Step 3:** Implement. Run the whole suite: lane B's
      `tests/unit/choreo/test_engine_compile.py` builds a 5 m gapped composite. Update that
      fixture only if an accepted ADR names the change; otherwise stop and report it to the
      controller. If a v1 fixture holds a gapped composite, stop and report too: its repair
      belongs to the sub-step that owns the stored shape.
- [ ] **Step 4:** Ledger flip procedure. Commit
      `fix(geo)!: refuse composite curves whose parts do not meet` with a `BREAKING CHANGE:`
      footer naming `CompositeCurve`.

### Task C3: strict SVG path tokens

**Fixes:** UP-9.1-44, -45, -46, -48, -49. **Files:** Modify `showpy/geo/svg.py`; extend
`tests/fuzz/test_fuzz_svg.py`.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Flags are exactly `0` or `1` and may
      be packed (`01.5.5`); digits are ASCII only; data must start with `M` or `m`.
- [ ] **Step 3:** Fuzz property: every accepted path re-serializes and re-parses to the same
      commands. **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(geo): parse SVG path data strictly`.

### Task C4: move `Set`-aware geometry out of `geo`

**Gate:** decision 4 answered. **Files:** Create `showpy/choreo/set_transforms.py` (`shift`,
`move_to`, `rotate`, `scale`, `expand`, `contract`, `set_interval`, `mirror`, `symmetrize`,
`skew`, `affine` on sets), `set_snapping.py` (`snap`, `snap_to_guides`, `align`, `distribute`),
`set_measure.py` (`on_field_ids`, `neighbors`, `nearest`, `symmetry_error`), `set_analysis.py`
(`density_map`, `coverage`, `balance`, `symmetry_score`, `change_amount`). Modify
`showpy/geo/{transforms,transform_base,snapping,measure,analysis}.py`, `showpy/_api_table.py`.

**Interfaces:** The geo functions accept points and curves only. Given a `Set`, each warns
`DeprecatedWarning` and forwards to its `showpy.choreo.set_*` twin through a function-level
import (the only upward imports the layering test allows, listed by name, removed in v0.6.0).
Top-level `showpy.<name>` points to a dispatcher that accepts points, curves and sets.

- [ ] **Step 1:** Remove the `xfail` from `test_geo_imports_only_core` (L0.10); run; expect FAIL
      naming five modules. Write `test_geo_set_overloads_warn_and_forward` (one per function).
- [ ] **Step 2:** Move the code; keep every existing test passing through the top-level names.
- [ ] **Step 3:** Commit `refactor(geo)!: move Set and Sheet handling out of geo`.

### Task C5: split `geo/curves.py` (597) and `geo/spacing.py` (507)

**Deferred (amendment):** a pure module split; ADR 0021 defers it to a later phase, which partitions
these modules again, and no moved-name deprecation rows are added for it. Do not start in Phase 2.

- [ ] **Step 1:** Split `curves.py` into `curves.py` (lines, arcs, circles, polylines) and
      `curves_compound.py` (composite, trimmed, SVG, Bezier); `spacing.py` into `spacing.py`
      (along curves) and `scatter.py` (area fills and scatter). Moved-name deprecations.
- [ ] **Step 2:** Commit `refactor(geo): split curves and spacing by responsibility`.

### Task C7: `project()` finds the nearest point on the whole curve

**Gate:** C2 merged. **Fixes:** UP-E-01. **Files:** Modify `showpy/geo/curve_base.py`. Test
`tests/unit/geo/test_curve_base_evaluation.py` (marker lines only),
`tests/unit/geo/test_curve_base.py`, new `tests/property/test_curve_project.py`.

`CurveBase.project` today refines from the single nearest sample, so on a tight spiral or a
zigzag it can return a point on the wrong arm.

**Interfaces:** `CurveBase.project(p: Point) -> Meters` keeps its signature. It splits the curve
into candidate pieces from its arc-length table, drops every piece whose lower distance bound
exceeds the best distance found so far, and refines each surviving piece with a safeguarded
Newton step on the squared distance (bisection whenever Newton leaves the piece or fails to
decrease it). Equal distances resolve to the smaller arc length, so the result is deterministic.
`PROJECT_TOL_M` is set from the largest excess measured over 10,000 property cases times 10; the
commit body records the measurement.

- [ ] **Step 1:** Run UP-E-01 with `--runxfail`; expect the projection on the wrong arm.
- [ ] **Step 2:** Property test `test_project_matches_brute_force` (Hypothesis): spirals of 1 to
      40 turns and zigzags of 2 to 200 segments, query points in a box around the curve; the
      distance to `point_at(project(p))` exceeds the brute-force minimum (dense sampling) by at
      most `PROJECT_TOL_M`. Run; expect FAIL.
- [ ] **Step 3:** Implement. Check that every curve kind's existing `project` tests still pass.
- [ ] **Step 4:** Ledger flip procedure. Commit
      `fix(geo): project onto the nearest point of the whole curve`.

### Task C8: geometry results are finite or an error

**Gate:** C7 merged. **Fixes:** UP-E-03. **Files:** Modify `showpy/geo/*` (the evaluation
entry points: length, points, tangents and the arc-length table in `curve_base.py` and
`curves.py`, plus `svg.py` and `spacing.py`). Add a code to `showpy/core/error_codes.py` and
regenerate `docs/error-codes.md` only if no existing code fits. Test
`tests/unit/geo/test_curves_evaluation.py` (marker lines only), `tests/unit/geo/test_curves.py`,
new `tests/fuzz/test_fuzz_finite.py`.

A finite but extreme input can still overflow inside numpy and return NaN or infinity with only a
`RuntimeWarning`. L2 bounds stored coordinates; this task closes what the bounds cannot, such as
high-degree curves and long chains of large steps.

**Interfaces:** each public geometry evaluation runs under `np.errstate(all="raise")` and checks
its result with `np.isfinite`; a `FloatingPointError` or a non-finite result becomes a
`GeometryError` naming the curve kind and the operation. No result of a public geometry call is
NaN or infinite.

- [ ] **Step 1:** Run UP-E-03 with `--runxfail`; expect a NaN length and no error.
- [ ] **Step 2:** Tests: `test_regression_extreme_svg_path_raises_geometry_error` (an SVG path of
      steps near 1e308 raises a `ShowPyError`, never returns NaN), and in
      `tests/fuzz/test_fuzz_finite.py` (run nightly by `nox -s fuzz`):
      `test_fuzz_curve_results_are_finite` (every `curve_from_construction` input either raises
      a `ShowPyError` or gives a finite length, finite points and finite tangents) and
      `test_fuzz_clock_results_are_finite` (`count_to_seconds` and its inverse on timelines
      within L2's bounds). Run; expect FAIL.
- [ ] **Step 3:** Implement. A non-finite clock result is lane T's: report it, never patch
      `showpy/time/` here.
- [ ] **Step 4:** Ledger flip procedure. Commit
      `fix(geo): raise GeometryError instead of returning NaN or infinity`.

---

## Lane D: cast and notes

Branch `feat/p2-d-cast`. Skills: `nox -s slow` on every task.

### Task D1: cast models refuse invalid data and stop mutating

**Fixes:** UP-9.3-56 to -61, -63 to -71. **Files:** Modify `showpy/cast/models.py`,
`showpy/cast/spots.py`; fill `showpy/show/migrations/v1_to_v2_cast.py` (key and selection-name
shape only; renames are A4's).

**Interfaces:** `Group.limits: ReadOnly[dict[str, PositiveFinite]]`, `Cast.groups:
ReadOnly[dict[str, Group]]` (key equals `Group.key`), `Cast.selections:
ReadOnly[dict[SelectionNameField, tuple[SpotIdField, ...]]]` naming only cast spots,
`LabelScheme.prefixes: ReadOnly[dict[str, str]]` naming real groups; radii and sizes
non-negative on both shape kinds; colors `#rrggbb`; `LabelScheme.pad` in `0..MAX_LABEL_PAD`
(8); spot IDs through `SpotIdField`. `Cast.spot` uses a cached `{id: index}`.

- [ ] **Step 1:** Run the 15 IDs (19 tests) with `--runxfail`. **Step 2:**
      `test_regression_cast_spot_index_is_fresh_after_replace`, and a bench: `mirror_pairs` and
      `regroup` on 300 spots stay linear (4x spots costs under 6x time).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure; schema.
- [ ] **Step 5:** Commit `fix(cast)!: validate and freeze cast models`.

### Task D2: public `rename_spots` and a `RewriteReport`

**Gate:** B1 and G1 merged. **Fixes:** UP-9.3-82, -84, -85, -86, -87. **Files:** Modify
`showpy/cast/rewrite.py`.

**Interfaces:** `rename_spots(show: Show, mapping: Mapping[str, str], *, force: bool = False) -> tuple[Show, RewriteReport]`:
checks injectivity up front (`SchemaError` naming both sources), resolves stored selectors
leniently (unknown spots are reported, never fatal), rewrites pins, sheet zones, typed motion
parameters that name spots (`EntityFacing`, follow leaders), form members, comments and notes.
`RewriteReport` lists `renamed`, `dropped_routes`, `dropped_facings`, `orphaned_notes`,
`unresolved_selectors`. Deletion drops routes with no members and facing overrides with no
entities and reports notes anchored to no spot.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Implement; `relabel` and
      `delete_spots` return through it.
- [ ] **Step 3:** Deferred minors (Appendix A row 37): skip the rewrite for an empty rename;
      thicken the multi-pinned-set test; fix the broken docstring wrap in `rewrite.py`.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `feat(cast): add rename_spots with a RewriteReport`.

### Task D3: notes, typed patches and ordered named selections (ADR 0008)

**Amended:** keep typed group and spot patches, `define_selection(replace=)`, aware note times and
their migration repair; drop `NotePatch` (notes leave `Show`, task N1).

**Gate:** ADR 0008 accepted, decision 3 answered. **Fixes:** UP-9.3-122 to -125. **Files:**
Modify
`showpy/notes/models.py`, `showpy/notes/notes.py`, `showpy/cast/spots.py`.

**Interfaces:**

- `Note.created_at: AwareDatetime`; area anchors use `Region` (three or more points);
  `accept_draft` accepts a hidden draft already in the show and refuses blank text.
- `class GroupPatch(TypedDict, total=False)` with `ReadOnly` keys for every editable `Group`
  field, `edit_group(show, key, /, **changes: Unpack[GroupPatch]) -> Show`; same for
  `NotePatch` and `edit_note`; `SpotPatch` for the private `_edit_spots`.
- `define_selection(show, name, sel, *, replace: bool = False) -> Show` raising `NAME_TAKEN`.
- v1 naive `created_at` values are read as UTC by the `cast` migration sub-step's notes part
  (decision 3) and reported.

- [ ] **Step 1:** Run the four with `--runxfail`. **Step 2:** Tests:
      `test_edit_group_rejects_an_unknown_field` (`edit_group(show, "brass", colour="#ffffff")`
      raises `SchemaError` naming `colour`; the `TypedDict` also makes it a type error),
      `test_define_selection_refuses_a_taken_name`.
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure.
- [ ] **Step 5:** Commit `feat(cast,notes)!: typed patches, aware timestamps, guarded selection names`.

### Task D4: split `cast/spots.py` (586 lines)

**Deferred (amendment):** a pure module split; ADR 0021 defers it to a later phase, which partitions
these modules again, and no moved-name deprecation rows are added for it. Do not start in Phase 2.

- [ ] Split into `spots.py` (add, delete, duplicate, fill) and `groups.py` (groups, labels,
      named selections); moved-name deprecations. Commit
      `refactor(cast): split group edits out of cast.spots`.

---

## Lane S: selection grammar

Branch `feat/p2-s-select`. Gate for the lane: ADR 0008 accepted.

### Task S1: `Selection` behaves like a value

**Fixes:** UP-9.3-34, -35. **Files:** Modify `showpy/core/selection.py`.

- [ ] **Step 1:** Run both with `--runxfail`. **Step 2:** Tests:
      `test_selection_rejects_a_bare_string`, `test_selection_operators_accept_iterables`,
      `test_selection_has_no_iter_override` (`dict(sel)` gives its fields).
- [ ] **Step 3:** Implement: equality and hash on `ids`; `description` excluded from comparison;
      `__iter__` override removed (one `type: ignore` gone); deduplication in `replace` and
      `model_copy`. **Step 4:** Ledger flip procedure.
- [ ] **Step 5:** Commit `fix(core)!: compare selections by IDs and stop overriding iteration`.

### Task S2: precedence rule, iterative parser and the selector migration

**Fixes:** UP-9.3-83. **Files:** Modify `showpy/cast/select.py`; fill
`showpy/show/migrations/v1_to_v2_selectors.py`; extend `tests/fuzz/test_fuzz_select.py`.

**Interfaces:** `parse_selector(text: str) -> SelectorAst`; mixing `&` with `+`/`-` at one level
raises `SchemaError` code `MIXED_OPERATORS` with `context["position"]` at the second operator
kind; evaluation on integer index sets; `normalize_v1_selector(text: str) -> str` adds spaces to
glued operators and the left-to-right parentheses.

- [ ] **Step 1:** Run UP-9.3-83 with `--runxfail`. Update `test_operators_combine_left_to_right`
      for the ADR: `"T1 + T2 & T2"` now raises `MIXED_OPERATORS` (an ADR-named tightening; the
      commit body says so).
- [ ] **Step 2:** Tests: `test_regression_migrated_zone_keeps_its_spots` (fixture v0.3.0's zone
      selects `T2` before and after migration), and fuzz properties: redundant parentheses never
      change a result; the iterative evaluator equals a reference recursive evaluator on every
      accepted expression; depth 10,000 raises `SchemaError`, never `RecursionError`.
- [ ] **Step 3:** Implement. **Step 4:** Deferred minors (Appendix A rows 36, 39, 40): `select`
      of a set of non-strings raises `SchemaError`; direct tests for digit tails over 64
      characters; `natural_sort` permutation and idempotence properties.
- [ ] **Step 5:** Ledger flip procedure. **Step 6:** Commit
      `feat(cast)!: refuse unparenthesized & mixes and parse selectors iteratively`.

### Task S3: ranges for every ID shape

**Fixes:** UP-9.3-24, -25, -27, -28. **Files:** Modify `showpy/core/util.py` (range functions
only).

- [ ] **Step 1:** Run the four with `--runxfail`. **Step 2:** Ranges filter parsed
      `(prefix, number)` pairs from `split_id`, so `P1x1-P1x3` works; `format_ranges` writes
      ASCII `-` that `parse_range` reads back; mixed zero-padding is refused.
- [ ] **Step 3:** Property: `parse_range(format_ranges(ids)) == ids` for Hypothesis ID lists.
- [ ] **Step 4:** Ledger flip procedure. Commit `fix(core): make ranges round-trip for every ID`.

### Task S4: `mirror_pairs` by maximum-cardinality matching; split `select.py`

**Amended:** keep the `mirror_pairs` maximum-cardinality matching; the split of `select.py` is
deferred (ADR 0021), so skip the `select_grammar` file and its `refactor(cast)` commit.

**Fixes:** UP-9.3-76. **Files:** Modify `showpy/cast/select.py`; create
`showpy/cast/select_grammar.py` (parser) leaving `select.py` (spatial helpers and `select`).

- [ ] **Step 1:** Run UP-9.3-76 with `--runxfail`. **Step 2:** Pairs within `tol` by
      `scipy.sparse.csgraph.min_weight_full_bipartite_matching` over a KD-tree graph of
      candidate pairs; ties by spot order.
- [ ] **Step 3:** Ledger flip procedure. Commit
      `fix(cast): pair mirror spots by maximum matching within tolerance`, then the split as
      `refactor(cast): move the selector grammar into select_grammar`.

---

## Lane T: time

Branch `feat/p2-t-time`. Gate: ADR 0007 accepted, decision 11 answered.

### Task T1: timeline invariants and a cached clock

**Amended:** its fix round also refuses a tempo or `ramp_to` of 0 or less with a `SchemaError`
naming document, field and value; the floor `MIN_TEMPO` itself comes from L2 through
`CountsPerMinute`.

**Fixes:** UP-9.3-95, -96, -97, -105 to -108, -114, -116 to -121. **Files:** Modify
`showpy/time/{models,clock,timeline,validate}.py`; fill
`showpy/show/migrations/v1_to_v2_timeline.py`.

**Interfaces:** `Timeline` after-validator: tempo sections sorted and unique by count, the first
at count 0, ramp pairs (`ramp_to` and `ramp_counts` together, `ramp_to > 0`), cue orders unique,
holds sorted, non-overlapping and inside the show, `total_counts >= 0`; `AudioTrack.gain` finite,
`sync_offset` finite. One hold policy: overlapping holds are refused everywhere (model, clock,
`add_hold`). `remove_tempo` and `remove_hold` find by label or exact stored count, never float
`==` on computed values. `Timeline.clock` is a `cached_property` holding the compiled pieces.

- [ ] **Step 1:** Run the 14 with `--runxfail`. **Step 2:**
      `test_regression_timeline_clock_is_fresh_after_replace`; bench: `count_to_seconds` with
      1,500 holds under 1 ms per call after the first (26 ms today).
- [ ] **Step 3:** Implement; the migration sorts v1 tempo sections (stable) and refuses two at
      one count. **Step 4:** Ledger flip procedure; schema.
- [ ] **Step 5:** Commit `fix(time)!: validate timelines at construction and cache the clock`.

### Task T2: measures

**Fixes:** UP-9.3-109, -111, -112, -113. **Files:** Modify `showpy/time/measures.py`.

- [ ] Run, implement (`counts_per_measure > 0`, lengths positive, one change per count, integer
      measure numbers), flip, commit `fix(time): validate measure changes and numbers`.

### Task T3: counts print in plain notation

**Fixes:** UP-9.3-98. **Files:** Modify `showpy/time/clock.py` (`describe_count` uses
`format_number`).

- [ ] Run, implement, flip, commit `fix(time): describe large counts without exponents`.

---

## Lane F: sheets

Branch `feat/p2-f-sheet`. Gate: decision 1 answered.

### Task F1: sheet models refuse invalid data

**Fixes:** UP-9.4-08 to -14, -44, -45. **Files:** Modify `showpy/sheet/models.py`; fill
`showpy/show/migrations/v1_to_v2_sheet.py`.

**Interfaces:** sheet size positive, outline at least three points, refs match
`sheet:<name>@<version>`, landmark IDs unique and not colliding with aliases, region rings valid,
guide lines non-empty, show zone IDs distinct from sheet zone IDs;
`CoordinateSystem.precision > 0` (ruling L-F1); `CoordinateSystem.labels: ReadOnly[...]`; item
IDs use the pattern decision 1 sets.

- [ ] **Step 1:** Run the nine with `--runxfail`. **Step 2:** Implement; the migration refuses a
      stored precision of 0 or less with `SchemaError` naming the field (ADR 0007 decision 6).
- [ ] **Step 3:** Deferred minors (Appendix A rows 132 to 135): one shared `precision > 0` guard;
      injected coordinate systems with wrong types raise `SchemaError`; non-string labels refused.
- [ ] **Step 4:** Ledger flip procedure; schema. **Step 5:** Commit
      `fix(sheet)!: validate sheets, zones and coordinate systems at construction`.

### Task F2: sheet functions refuse typos and render PDFs deterministically

**Dropped (amendment):** the sheet functions it hardens (coordinate wording, landmarks, templates,
rendering) leave the library (ADR 0021); its 7 ledger IDs retire per ADR 0021's appendix and stay
strict xfails until their code is deleted at v0.6.0. Do not start.

**Fixes:** UP-9.4-18, -27, -28, -29, -32, -37, -38. **Files:** Modify
`showpy/sheet/{coords,bounds,templates,render}.py`.

- [ ] **Step 1:** Run the seven with `--runxfail`. **Step 2:** `pick()` for style, landmark kind
      and surface type; finite and integer checks on `k`, points and sizes; `render_sheet(fmt="pdf")`
      goes through `showpy/export/pdf.py`'s writer (no clock), `customize` takes
      `**changes: Unpack[SheetPatch]`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(sheet): refuse unknown options and write PDFs without a clock`.

### Task F3: deprecation rows for the sheet names that leave

**Gate:** F1 merged, ADR 0021 accepted. **Fixes:** none. **Files:** Modify `showpy/sheet/*`
(warning lines only), `showpy/_api_table.py`; regenerate `showpy/__init__.py` and
`tests/fixtures/api_snapshot.json`. Test new `tests/unit/sheet/test_deprecations.py`, and the
sheet test modules that exercise a leaving name.

**Interfaces:** one `DEPRECATED` row per name in ADR 0021's timeline for `showpy.sheet`, each
with `since="0.4.0"` and `remove="0.6.0"`, and a message that names its replacement or says the
name "leaves the library":

- coordinate wording (6): `to_coords`, `from_coords`, `coordinate_presets`, `set_coordinates`,
  `CoordinateSystem`, `CoordinateKind`;
- landmarks and zones (14): `landmark`, `landmark_anchor`, `landmark_keys`, `nearest_landmarks`,
  `zones_at`, `line_landmark`, `point_landmark`, `add_landmark`, `remove_landmark`, `add_zone`,
  `remove_zone`, `Landmark`, `Zone`, `ZoneKind`;
- proscenium stage and rendering (4): `proscenium_stage`, `SheetCanvas`, `draw_sheet`,
  `render_sheet`;
- the names that change shape (up to 16, such as `sheet_templates`, `get_sheet`, `customize`,
  `GuideLine` and `on_surface`), each with the replacement ADR 0021's table gives; the commit
  body lists the final set.

Library code that still needs a leaving name calls its private implementation, so only callers
outside the library warn. The sheet test modules that exercise a leaving name filter
`DeprecatedWarning` only (a module-level `pytest.mark.filterwarnings`), never all warnings.

- [ ] **Step 1:** Test `test_leaving_sheet_names_warn` (parametrized over the rows: each name
      warns `DeprecatedWarning` naming v0.6.0 and returns the same object as before). Run; expect
      FAIL.
- [ ] **Step 2:** Add the rows and the warning aliases; regenerate `showpy/__init__.py` and the
      API snapshot; run all gates (warnings are errors, so any internal call to a leaving name
      fails the suite).
- [ ] **Step 3:** Commit `feat(sheet): deprecate the sheet names that leave the library`, with
      every name listed in the body.

---

## Lane G: forms and matching

Branch `feat/p2-g-forms`. Skills: `nox -s slow` on every task.

### Task G1: typed recipes with `recipe_version`

**Gate:** ADR 0007 accepted, decision 5 answered. **Fixes:** UP-9.6-45 to -49, -51 to -54,
-58, -60. **Files:** Modify `showpy/choreo/form_models.py`, `forms.py`, `form_fns.py`,
`form_text.py`, `text_count.py`, `text_edit.py`; fill `showpy/show/migrations/v1_to_v2_forms.py`.

**Interfaces:**

- `Recipe = LineRecipe | ArcRecipe | CurveRecipe | CircleRecipe | BlockRecipe | WedgeRecipe |
PolylineRecipe | SvgRecipe | FillRecipe | ScatterRecipe | CustomRecipe | GeneratedRecipe |
TextRecipe`, discriminated by `kind`, each with `recipe_version: Literal[1] = 1`, the shared
  `n: int | None` and `transform: Affine | None`, and its own constrained fields (block
  `anchor: Literal[...]`, arc sweep in `(0, 360]`, fill pattern `Literal[...]`, `n` an `int`).
- `Form.recipe: Recipe`; `Form.kind` is a property returning `recipe.kind`; `Form.geometry` is a
  `renamed_field("recipe", since="0.4.0", remove="0.6.0")` returning the recipe's JSON dump.
- `GeneratedRecipe.params: JsonObject` for user generators; the bundled generators get typed
  parameter models (`turns`, `wavelength` positive and finite); registering a generator probes
  it and wraps a crash in `SchemaError`.
- `FormFnRegistry.fns: ReadOnly[dict[str, FormFn]]`.

- [ ] **Step 1:** Run the 11 with `--runxfail`. **Step 2:** Tests:
      `test_every_form_kind_round_trips_its_recipe`, `test_v1_geometry_migrates_to_a_typed_recipe`
      (one v1 dict per kind from the fixtures).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure; schema; migration expectations.
- [ ] **Step 5:** Commit `feat(choreo)!: store form recipes as a typed, versioned union`.

### Task G2: slot counts stay in step; typed open slots

**Fixes:** UP-9.6-11 to -14, -62. **Files:** Modify `showpy/choreo/form_ops.py`,
`form_place.py`, `form_models.py`.

**Interfaces:** `Form.members: tuple[SpotIdField | None, ...]` where `None` is an open slot
(ruling L-G2), so gapped orders survive `place` and `rebuild`; `len(members) <= n` is a model
invariant; `edit` keeps a block's or custom form's `n` in step with its rows or points and
clears a curve form's `n` on `edit(n=None)`.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Implement; the forms migration turns
      v1 gapped orders into open slots. **Step 3:** Ledger flip procedure.
- [ ] **Step 4:** Commit `fix(choreo)!: keep slot counts consistent and store open slots`.

### Task G3: frozen form and set containers; cached `Set.get`

**Fixes:** UP-9.6-19, -20, -21, -39, -40, -41; UP-9.7-54. **Files:** Modify
`showpy/choreo/models.py`, `form_models.py`, `form_fns.py`.

**Interfaces:** `Form.meta: ReadOnly[dict[str, str]]`; forms hash; `SpacingSpec.gaps` non-negative;
placement and fixed facings `Heading` (wrapped to `[0, 360)`); `Set` refuses two placements for
one spot; `Set.get` uses a cached index; `Set.with_placements` takes
`Placement | PlacementPatch`. The `Set.key` validator is A4's and stays untouched here.

- [ ] **Step 1:** Run the seven with `--runxfail`. **Step 2:**
      `test_regression_set_index_is_fresh_after_replace`; bench: `ordered_along` on 300 spots is
      linear (4x spots under 6x time; 15x today).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure (UP-9.7-54 is in lane A's
      `test_manage.py`, marker lines only). **Step 5:** Commit
      `fix(choreo)!: freeze form and set containers and cache set lookups`.

### Task G4: matching and fitting boundaries; `MatchWeights`

**Fixes:** UP-9.6-25, -31, -37, -50, -55, -56, -57, -61. **Files:** Modify
`showpy/choreo/match.py`, `match_models.py`, `match_ops.py`, `form_fit.py`, `form_ops.py`.

**Interfaces:** `MatchOptions.weights: MatchWeights` (frozen model with `backward`, `crossing`,
`group_spread`, `turn`, each `NonNegFinite`); `pinned` slot indexes `StrictInt`; a repeated spot
is refused; a `cost_fn` exception becomes `MatchError`; too few target slots, members over slots,
no points and NaN starts raise `ShowPyError` subclasses.

- [ ] **Step 1:** Run the eight with `--runxfail`. **Step 2:** Implement.
- [ ] **Step 3:** Deferred minors (Appendix A rows 6, 7, 8): an out-of-range slot index in a
      hand-built `MatchResult` raises `SchemaError`; the `# fmt: skip` goes; a locked destination
      reports `LockedError` before `MatchError`; a pinned non-member row colliding with a matched
      slot names the pinned row.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(choreo)!: type match weights and refuse bad match input`.

### Task G5: thread `font_registry` (decision 7)

**Gate:** decision 7 answered. **Files:** Modify `showpy/choreo/{match,form_ops,form_place,match_ops}.py`.

**Interfaces:** `match`, `reorder`, `gate`, `split_form`, `merge_forms`, `place`, `rebuild`,
`apply_match`, `swap` and `morph_targets` take keyword-only `font_registry: FontRegistry | None
= None` and pass it to every text-recipe evaluation; `split_form` and `merge_forms` keep the
text recipe when every input is a text form with one font chain.

- [ ] **Step 1:** Test per function: a text form in a user font registered only in the caller's
      registry works with `font_registry=` and still raises `MissingFontError` without it.
- [ ] **Step 2:** Implement. **Step 3:** Commit
      `feat(choreo): accept font_registry wherever text forms are rebuilt`.

### Task G6: split `forms.py` (569) and `form_text.py` (565)

**Deferred (amendment):** a pure module split; ADR 0021 defers it to a later phase, which partitions
these modules again, and no moved-name deprecation rows are added for it. Do not start in Phase 2.

- [ ] Split `forms.py` into `forms.py` (constructors) and `form_slots.py` (slots, poses,
      placements); `form_text.py` into `form_text.py` (constructor and recipe) and
      `text_slots.py` (stroke spreading and the report). Moved-name deprecations. Commit
      `refactor(choreo): split form construction from slot generation`.

---

## Lane E: export

Branch `feat/p2-e-export`. Gate: ADR 0010 accepted. Skills: `differential-review`.

### Task E1: export boundaries

**Fixes:** UP-9.5-12, -14, -29, -30, -34, -43, -44. **Files:** Modify
`showpy/export/{publish,data,positions}.py`.

- [ ] **Step 1:** Run the seven with `--runxfail`. **Step 2:** Claim and reassignment times are
      `AwareDatetime`; numbers through `format_number`; `include` refuses a bare string;
      imported facings are `Heading`; imported entity IDs pass `SpotIdField`.
- [ ] **Step 3:** Deferred minors (Appendix A rows 42, 43, 47, 49): `publish(base_url=None)` raises
      `SchemaError`; `SHA256_HEX` moves beside the other constants; `verify_join_token` gets
      Args and Returns; `qr_code` accepts numpy integers; exact-cap boundary tests for the QR
      size and video pixels; stale test docstrings in `test_data.py` fixed.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(export)!: validate timestamps, IDs and facings at the export boundary`.

### Task E2: ffmpeg failures and hangs

**Dropped (amendment):** video export leaves the library (ADR 0021); UP-9.5-55 and UP-9.5-56 retire
per ADR 0021's appendix and stay strict xfails until their code is deleted at v0.6.0. Do not start.

**Fixes:** UP-9.5-55, -56. **Files:** Modify `showpy/export/video.py`.

- [ ] Run both with `--runxfail`; wrap writer start failures in `StorageError` (code `IO`); pass
      `timeout=VIDEO_MUX_TIMEOUT_S` (set from the measured worst case of the 300 x 60 export
      times 10) to the mux; fix `video_canvas(show, 1e-300)` returning an odd size (Appendix A
      row 48: re-check evenness after padding); flip; commit
      `fix(export): bound ffmpeg and report its failures as ShowPyErrors`.

### Task E3: split `publish.py` (709) and `data.py` (511)

**Dropped (amendment):** publishing, print and table code leaves the library (ADR 0021), and the
interchange part of `data.py` moves to `showpy/show/interchange.py` in E4; no ledger IDs. Do not
start.

- [ ] Split `publish.py` into `publish.py` (publish, links, tokens), `claims.py` (claims,
      reassignments, performer views) and `qr.py`; `data.py` into `data.py` (JSON) and
      `tables.py` (CSV and text). Moved-name deprecations. Commit
      `refactor(export): split publishing, claims and tables`.

### Task E4: interchange moves to `showpy.show`; the other export names are deprecated

**Gate:** E1 merged, ADR 0021 accepted; the pandas step after lanes B and G merge. **Fixes:**
none (UP-9.5-41 and UP-9.5-42 move with `import_positions`, markers unchanged). **Files:** Modify
`showpy/export/*`, `showpy/_api_table.py`, and for the five pandas results only their warning
lines in `showpy/choreo/{motion_stats,paths,models}.py`. Create `showpy/show/interchange.py` and
`tests/unit/show/test_interchange.py`. Regenerate `showpy/__init__.py` and
`tests/fixtures/api_snapshot.json`.

**Interfaces:**

- `export_json`, `import_json`, `import_fonts` and `import_positions` are defined in
  `showpy/show/interchange.py`. Their top-level names do not change; the old
  `showpy.export.data` and `showpy.export.positions` paths warn through `moved(...)` until
  v0.6.0. The interchange tests move to `tests/unit/show/test_interchange.py` with their ledger
  markers unchanged.
- `import_json` refuses an unknown top-level key with `SchemaError` naming the key, and keeps
  every key that starts with `x-`, which `export_json` writes back unchanged.
- One `DEPRECATED` row (`since="0.4.0"`, `remove="0.6.0"`) for every other export name, as ADR
  0021's timeline groups them: publishing 19, print and PDF 10, tables and the format registry
  9, video 3. Each message says the name "leaves the library" or names its replacement.
- The five pandas results (`speeds`, `energy_curve`, `phrase_profile`, `paths_table`,
  `Set.table`) keep their names and warn that they return arrays from v0.6.0.
- Library code that still needs a leaving name calls its private implementation; export test
  modules filter `DeprecatedWarning` only.

- [ ] **Step 1:** Tests: `test_regression_import_json_refuses_an_unknown_top_level_key`,
      `test_import_json_keeps_x_keys` (an `x-extra` key survives an import and export round
      trip), `test_interchange_old_paths_warn_and_resolve`,
      `test_leaving_export_names_warn` (parametrized over the rows) and
      `test_pandas_results_warn_about_arrays`. Run; expect FAIL.
- [ ] **Step 2:** Move the interchange code with no behavior change; commit
      `refactor(show): move JSON and positions interchange into show.interchange`, naming the
      four functions in the body.
- [ ] **Step 3:** Refuse unknown top-level keys and keep `x-` keys; commit
      `fix(show)!: refuse unknown top-level keys in import_json and keep x- keys`.
- [ ] **Step 4:** Add the export rows and warning aliases; regenerate `showpy/__init__.py` and the
      API snapshot; commit `feat(export): deprecate the export names that leave the library`,
      with every name listed in the body.
- [ ] **Step 5:** After lanes B and G merge, the pandas warnings; commit
      `feat(choreo): warn that pandas results become arrays in v0.6.0`.

---

## Serial tail before lane X

Added by the amendment of 2026-11-22. N1 runs once lanes A and D merge; R1 runs after every lane
merges and before X1.

### Task N1: notes leave `Show`

**Gate:** lanes A and D merged, ADR 0021 accepted. **Fixes:** none. **Files:** Modify
`showpy/notes/*`, `showpy/show/{models,manage,sections,collab}.py`, the integrity roots
(`KNOWN_ROOTS` in `showpy/show/integrity.py`, and `showpy/show/document_paths.py`),
`showpy/show/migrate.py` and `showpy/show/migrations/__init__.py` (the order gains `notes`),
`showpy/cast/rewrite.py` (note anchors), `showpy/show/interchange.py` (after E4), and
`showpy/_api_table.py`. Create `showpy/show/migrations/v1_to_v2_notes.py`. Test
`tests/unit/notes/*`, `tests/unit/show/test_migrate.py`, new
`tests/unit/show/test_notes_document.py`.

Today a repository stores notes as one of the show's own documents, `/show/notes.json`, decoded
into `Show.notes`. Format v2 holds no notes (ADR 0021 decision 3, ADR 0023).

**Interfaces:**

- `Show` has no `notes` field; a v2 `show.json` has no `notes` key.
- The notes document is
  `{"format": "e8.notes", "version": 1, "show": "<id>", "notes": [...]}`. This task picks its v2
  path under a new known document root, adds the root to `KNOWN_ROOTS`, and says why in the
  commit body.
- Migration sub-step `notes` (the tenth, after `ids`) takes the notes out of the v1 documents,
  rewrites each anchor through the ID renames the same migration recorded, and writes the notes
  document in the same single revision as the v2 documents. A second save writes nothing. A
  single-file export returns the notes document beside the show.
- `Anchor` moves to `showpy/show/models.py`, because comments use it; the old path warns until
  v0.6.0.
- The seven leaving `notes` names (`Note`, `add_note`, `edit_note`, `remove_note`, `notes_for`,
  `notes_near`, `accept_draft`) read and write the notes document instead of `Show.notes` and
  warn `DeprecatedWarning` until 1.0.0 (`remove="1.0.0"`). Their new signatures go in the commit
  body.
- D2's note-anchor rewrite and `RewriteReport.orphaned_notes` apply to the notes document.

- [ ] **Step 1:** Tests: `test_regression_v1_notes_move_to_the_notes_document` (for every
      fixture, each v1 note is in the notes document with the same text, author and time, and
      the show document has no `notes` key), `test_note_anchors_follow_the_migration_renames`,
      `test_first_save_writes_notes_in_the_same_revision_and_the_second_none`,
      `test_notes_functions_warn_until_1_0_0` and `test_anchor_old_path_warns`. Run; expect FAIL.
- [ ] **Step 2:** Implement, filling only `v1_to_v2_notes.py` among the sub-step modules.
- [ ] **Step 3:** Regenerate the v2 schema
      (`SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`), the migration
      expectations (`SHOWPY_UPDATE_MIGRATION=1 uv run pytest tests/unit/show/test_migrate.py`,
      then review the diff), `showpy/__init__.py` and the API snapshot.
- [ ] **Step 4:** Run all gates and `uv run nox -s slow`. Commit
      `feat(show)!: move notes out of Show into a notes document` with a `BREAKING CHANGE:`
      footer naming `Show.notes`, `Anchor` and the seven deprecated `notes` names.

### Task R1: rename the library to e8Show (ADR 0020)

**Gate:** every lane merged, no lane branch or worktree open, ADRs 0020 and 0023 accepted.
**Fixes:** none. **Files:** the whole tree, by a script kept outside the repository.

**Interfaces:** the distribution and import name `showpy` becomes `e8show`; `ShowPy` becomes
`e8Show`; `ShowPyError` becomes `E8ShowError`; `SHOWPY_` becomes `E8SHOW_`. No `showpy` alias or
shim (ADR 0020). The format value becomes `e8.show` and the v2 schema `e8.show.v2.json` (ADR
0023).

- [ ] **Step 1:** Write the script outside the repository and rehearse it in a scratch clone:
      run it, then every gate. Fix the script, never its output, and rehearse again until clean.
- [ ] **Step 2:** In the worktree, run the script. It moves `showpy/` to `e8show/` and rewrites
      `ShowPyError` to `E8ShowError` before `ShowPy` to `e8Show`, then `showpy` to `e8show` and
      `SHOWPY_` to `E8SHOW_`. Derived names follow the same rule.
- [ ] **Step 3:** The script skips, by path or exact string: the three frozen wire names
      (`showpy-hash/1`, `$showpy:map` with its `$showpy:` prefix, `showpy.font.v1`); the v1
      schema `showpy.show.v1.json` and its tag `"schema": "showpy.show.v1"`;
      `tests/fixtures/repos/`; the font files; `CHANGELOG.md` entries and release notes up to
      and including v0.3.0; ADRs 0001 to 0019.
- [ ] **Step 4:** Rename the v2 schema to `e8.show.v2.json` and set the format value to
      `e8.show`.
- [ ] **Step 5:** Regenerate every generated file: `e8show/__init__.py`, the API snapshot, the
      schemas, `CHANGELOG.md`, the fixtures' `expected_v2.json` and `docs/error-codes.md`.
- [ ] **Step 6:** Scripted replacement also edits `>>>` doctest lines (wanted here), so run the
      doctests, then every gate, Docker included.
- [ ] **Step 7:** Prove nothing stored moved: `git diff --stat` is empty for
      `tests/fixtures/repos/`, and the font files show only as 100% renames
      (`git diff -M100% --stat v0.3.0 -- showpy/fonts e8show/fonts`). Add
      `test_bundled_font_uris_equal_their_v030_values` (the five URIs copied from v0.3.0).
- [ ] **Step 8:** Commit `refactor!: rename the library to e8show` with a `BREAKING CHANGE:`
      footer naming `showpy`, `ShowPyError` and the `SHOWPY_` variables.
- [ ] **Step 9:** After the maintainer approves: rename the GitHub repository to
      `abj360/e8Show`, then update `[project.urls]` in its own commit,
      `build: point the project URLs at the renamed repository`.

---

## Lane X: exit (serial, after every lane merges)

Branch `chore/p2-exit`.

### Task X1: the migration scenario

**Amended:** drop "publish and claim 30 spots" from Step 1 (those calls are deprecated). Step 2 also
asserts: existing notes land in `notes.json` with the same text, author and time, and the show
document has no `notes` key; the format value is `e8.show`; one revision's show never changes
another's.

**Files:** Create `tests/scenario/test_phase2_scenario.py` (marked `slow`).

- [ ] **Step 1:** Load the committed 300 x 60 v0.3.0 benchmark show (L0.9 step 7) through a
      `FileRepository`, migrate, save, renumber, edit through a second workspace, publish and
      claim 30 spots, replay `events_since` and revert.
- [ ] **Step 2:** Assert: the report lists every rename; a second save writes nothing; selections
      and zones select the same spots before and after; positions continuous at every set
      boundary within 1e-6 m; the 0.02 m segment bound holds; JSON and document round trips are
      equal; two runs give byte-identical `encode_show` and `content_hash`; no call mutates its
      input.
- [ ] **Step 3:** Run `uv run nox -s slow`; expect PASS. Commit
      `test: add the phase 2 migration scenario`.

### Task X2: exit checks

**Amended:** the ledger line expectations of the amendment replace `Ledger: 174 open`: the default
line is 174 plus 10 (E2's 2 and F2's 7 IDs and UP-9.4-45 no longer flip) plus the 4 evaluation IDs
no Phase 2 task flips (UP-E-02, UP-E-04, UP-E-05, UP-E-06), so `Ledger: 188 open`, and `nox -s slow`
prints `Ledger: 1 open`; a measured difference is explained ID by ID in the commit body, never
closed by editing a marker. The Step 1 constant drops the E2 and F2 IDs and UP-9.4-45 and adds
UP-E-01 and UP-E-03. Add `test_every_leaving_name_is_deprecated` (every name in ADR 0021's timeline
has a `DEPRECATED` row with its removal version). Set `MAX_SPOTS = 5_000` and `MAX_SETS = 1_000`
from a re-measurement after A1 and G3, in `showpy/core/caps.py`, with tests at the boundary (5,000
accepted, 5,001 refused; the same for sets). 0 `type: ignore` in the library and 0 `Any` in a stored
model field, guarded by the Step 2 tests.

**Files:** Modify `tests/unit/test_ledger.py`, `tests/unit/test_library_rules.py`.

- [ ] **Step 1:** `test_no_phase_two_defect_remains`: no test carries a `defect` marker whose ID
      starts `UP-4.` or `UP-9.3-`, or is in the Phase 2 table above (kept as a constant).
- [ ] **Step 2:** `test_no_any_in_model_fields`: walks every pydantic model under `showpy/` and
      fails on a field annotation containing `Any`; `test_library_has_no_type_ignore`: fails on
      any `type: ignore` or `pyright: ignore` comment in `showpy/`. Each with a negative control.
- [ ] **Step 3:** Run every gate: `uv run nox`, `uv run nox -s slow fuzz bench wheel core`,
      `docker compose -f docker/docker-compose.yml up --build`; the default suite prints
      `Ledger: 174 open`.
- [ ] **Step 4:** Run `security-review` and `differential-review` over `v0.3.0..HEAD` for
      `showpy/show/`, `showpy/export/` and the selector parser.
- [ ] **Step 5:** Commit `test: guard the phase 2 exit criteria`.

### Task X3: release v0.4.0

**Amended:** the release notes add the licence change (ADR 0019; first paragraph: v0.3.0 and earlier
stay MIT), the rename (ADR 0020), the "leaving the library" table (ADR 0021) and the guide update
(the public guide is updated at v0.4.0 with the new imports and a wheel labelled with its licence);
distribution stays one wheel plus an sdist on GitHub releases.

**Files:** `CHANGELOG.md` (generated), `docs/releases/v0.4.0.md`, `docs/HANDOFF.md`,
`docs/showpy-api-reference.md` (changed names only; Appendix A row 124).

- [ ] **Step 1:** `uv run nox -s changelog`; check every breaking name is listed.
- [ ] **Step 2:** Release notes in the v0.3.0 shape, with "Migrating from v0.3": Python 3.13;
      extras and the gated functions; keyword-only flags (the L0.4 table); `ShowValidationError`;
      moved modules and their removal in v0.6.0; `Form.recipe`; typed payloads; the format v2
      header, the read window and the ID rename rule with a host checklist (claims, links and
      host-held notes must apply `MigrationReport.renamed`); `MIXED_OPERATORS`;
      `define_selection(replace=)`; the error-code catalogue.
- [ ] **Step 3:** Update HANDOFF: ledger counts, Phase 2 done, Phase 3 next. Commit
      `chore(release): prepare v0.4.0`. The controller asks the maintainer to approve the push,
      the merge and the tag.

## Phase 2 exit criteria

- No `dict[str, Any]` (or any `Any`) in a stored model field; no `type: ignore` in `showpy/`
  (today 8 `type: ignore` and 4 stored `dict[str, Any]` fields). Guarded by X2.
- Every model rejects the invalid inputs the workstreams list: every ledger ID in this plan's
  table is unmarked, including all of UP-4 and UP-9.3 (`test_no_phase_two_defect_remains`).
- v1 shows load and migrate: the v0.1.1, v0.2.0 and v0.3.0 fixtures migrate to their reviewed
  v2 output, and the X1 scenario passes.
- `import showpy.choreo.models` imports no optional dependency, and the `core` job passes.
- CI matrix (3 OS x Python 3.13 and 3.14, plus 3.15 if final), hash seeds, slow, fuzz, bench,
  wheel, core and Docker are green; `v0.4.0` is tagged after the maintainer approves the push.

---

## Appendix A: Phase 1 deferred items, triaged

Source: `/home/uapb-ai/.cache/showpy-scratch/phase-1-archive/deferred-items.txt` (140 lines).
Rows are the file's line numbers. "Done" means checked on `main` at `3824263`. Lines that are
fragments of a brief rather than items (35, 41, 50, 51, 106, 119 to 121, 136 to 140) are
listed last as "not an item".

| Line(s)                                     | Item (short)                                                                                                               | Decision                                                                                               |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| 1                                           | `to_polyline(tol=1e-300)` runs 20-27 s before the cap                                                                      | Phase 4: feature-aware flattening sets a tol floor (UP-9.1-67)                                         |
| 2                                           | NaN leaks in `space_by_interval(start=)`, `fill(angle_deg=, margin=, anchor=)`                                             | Phase 2, C1                                                                                            |
| 3                                           | `fill` of empty or 1e308 area leaks `GEOSException` without `anchor`                                                       | Phase 2, C1                                                                                            |
| 4                                           | density-map test docstring says 1,000,000 cells, builds 500,000; spiral boundary                                           | Phase 2, C1 (test wording and boundary values, same files)                                             |
| 5                                           | SVG `_arc` hint claims an unenforced range                                                                                 | Phase 2, C1                                                                                            |
| 6                                           | malformed `MatchResult` slot index leaks `IndexError`; `# fmt: skip`                                                       | Phase 2, G4                                                                                            |
| 7                                           | locked destination reports `MatchError` before `LockedError`                                                               | Phase 2, G4                                                                                            |
| 8                                           | pinned non-member row collides with a matched slot without naming it                                                       | Phase 2, G4                                                                                            |
| 9                                           | no test that a note survives a forced move                                                                                 | Phase 2, G2 (one test in `test_form_place.py`)                                                         |
| 10                                          | `**options: Any`; no "(+n more)" past 5 errors; non-str `_truncate`                                                        | Phase 2, L0.2                                                                                          |
| 11                                          | pragma line over 100 characters                                                                                            | Done (no line over 100 in `core/types.py`)                                                             |
| 12                                          | handler tests monkeypatch `model_validate`; `documents.py` could use `field_errors()`                                      | Phase 2, L0.9 (touches `documents.py`)                                                                 |
| 13                                          | stale "was 528" baseline in a report                                                                                       | Drop: a Phase 1 report, not code                                                                       |
| 14                                          | doctest `model_copy(update=)`; ban misses `**kw` and aliases; stale `Contains:`                                            | Done for the doctest; the ban widening is Phase 2, L0.6                                                |
| 15                                          | incomplete `Contains:` indexes; NaN bounds unguarded                                                                       | Phase 2, L0.6                                                                                          |
| 16                                          | `require_finite` typed `float` while numpy reals pass                                                                      | Phase 2, L0.6                                                                                          |
| 17                                          | `gen_public_api.py` duplicates the `isupper()` test; fragile doctest; `require_size` negative                              | Phase 2, L0.10 (generator); `require_size` negative in L0.6                                            |
| 18                                          | `__reduce__` docstring; `PicklingError` untested                                                                           | Phase 2, L0.2                                                                                          |
| 19                                          | `'..a'` position; pattern constants duplicate; `$` vs fullmatch                                                            | Phase 2, L0.5                                                                                          |
| 20                                          | lane F status line                                                                                                         | Drop: status, no item                                                                                  |
| 21                                          | `model_dump` errors leak from `canonical.py`; ADR 0014 caps addendum                                                       | Phase 2, L0.2 (wrap `PydanticSerializationError`); the addendum is done                                |
| 22                                          | `expected.json` selection in frozenset order                                                                               | Phase 2, L0.9 (fixture assertions); the bytes are v0.2's by design                                     |
| 23                                          | lane C status and rulings                                                                                                  | Drop: status; rulings are in the v0.3.0 notes                                                          |
| 24                                          | hash-seed helper covers seeds 1 and 2, not 0; docstring pointer                                                            | Phase 2, L0.6 (helper docstring states it)                                                             |
| 25, 26                                      | `only_showpy_errors` failure message unbounded                                                                             | Phase 2, L0.2 (use `bounded_repr`)                                                                     |
| 27                                          | `CoordinateSystem.precision` constraint parked for v2                                                                      | Phase 2, F1                                                                                            |
| 28                                          | ADR 0014 overclaims int/float equality                                                                                     | Done (ADR 0014 addendum 2026-11-17)                                                                    |
| 29                                          | `bounded_repr` "never raises" index line                                                                                   | Phase 2, L0.2                                                                                          |
| 30                                          | API snapshot bootstrap skips the Unreleased check                                                                          | Done (v0.3.0: refused once a release exists)                                                           |
| 31                                          | `bounded_repr` cuts digits without a marker                                                                                | Phase 2, L0.2                                                                                          |
| 32                                          | ADR 0018 cites a rewritten SHA                                                                                             | Done (ADR 0018 notes PR #19 as the stable reference)                                                   |
| 33                                          | device-name show IDs decision                                                                                              | Done (ADR 0006 addendum 2026-11-17)                                                                    |
| 34                                          | Windows junctions; temp unlink race                                                                                        | Phase 3 (ADR 0015)                                                                                     |
| 36                                          | `select(set of non-str)` leaks `TypeError`; `seeded_rng` docstring                                                         | Phase 2, S2 (select); L0.6 (`seeded_rng` docstring)                                                    |
| 37                                          | thin multi-pinned-set test; empty rename rewrite; broken docstring wrap                                                    | Phase 2, D2                                                                                            |
| 38                                          | unforced `add_spot` after a pinned set writes a pinned off-field placement                                                 | Done (release notes ruling, Lane D task D3)                                                            |
| 39                                          | digit tails over 64 characters untested; hint wording                                                                      | Phase 2, S2                                                                                            |
| 40                                          | `natural_sort` fuzz invariants; `MAX_TEXT_CHARS` unreachable by fuzz                                                       | Phase 2, S2                                                                                            |
| 42                                          | `SHA256_HEX` placement; `verify_join_token` docstring                                                                      | Phase 2, E1                                                                                            |
| 43                                          | `publish(base_url=None)` leaks `TypeError`; odd URL hosts accepted                                                         | Phase 2, E1 (the leak); host checks are the host's (ADR 0003): drop                                    |
| 44                                          | `print_sheets` reads claims raw; viewer identified by id                                                                   | Drop: safe under ruling R12; authentication is the host's (ADR 0003)                                   |
| 45                                          | `Claim.model_copy`/`model_construct` bypass the contact validator                                                          | Drop: the library never calls them (ban test); tests use `model_copy` on purpose to build invalid data |
| 46                                          | no property over the default CSV neutralizing path                                                                         | Phase 2, E1 (one property test)                                                                        |
| 47                                          | QR and video cap boundaries; `qr_code` refuses numpy ints                                                                  | Phase 2, E1                                                                                            |
| 48                                          | `video_canvas(show, 1e-300)` odd canvas                                                                                    | Phase 2, E2                                                                                            |
| 49                                          | stale docstrings in `test_data.py`; `positions.py` hint and docstring                                                      | Phase 2, E1                                                                                            |
| 52                                          | `_logs.setdefault` before the conflict check leaves a phantom show                                                         | Phase 3 (repository rewrite)                                                                           |
| 53                                          | no test that log, tree and blob reads stay consistent                                                                      | Phase 3 (conformance kit)                                                                              |
| 54                                          | deleting `next/` rolls the head back silently                                                                              | Phase 3 (signed heads or `fsck`, ADR 0015)                                                             |
| 55 to 59                                    | log chain cap, read-size cap, symlinked show folders, tree path check, TOCTOU, untested symlink kinds                      | Phase 3 (repository layout v2)                                                                         |
| 60                                          | no cast-vs-placements check at commit (UP-9.7-94)                                                                          | Phase 3 (validation and integrity)                                                                     |
| 61, 62                                      | public `repo.backend`; Windows names under `/exports`; orphan check scope; Cf characters in paths                          | Phase 3                                                                                                |
| 63, 64                                      | test comparisons and a missing docstring in show tests                                                                     | Phase 2, A1 (same files)                                                                               |
| 65                                          | docs naming removed features                                                                                               | Done (v0.3.0 lane X)                                                                                   |
| 66                                          | UP-9.7-36/37 pass by construction                                                                                          | Done (flipped in Phase 1; no action)                                                                   |
| 67                                          | no decoder for v0.2 events with `actor`                                                                                    | Phase 2, A3                                                                                            |
| 68                                          | `make_legacy_fixture.py` does not type-check                                                                               | Phase 2, L0.9                                                                                          |
| 69                                          | encode bench margin; workspace `show.json` key order                                                                       | Phase 2, L0.9 (bench step 7)                                                                           |
| 70                                          | misleading generated CHANGELOG line                                                                                        | Done (v0.3.0 release notes are the complete list)                                                      |
| 71                                          | raw files under odd transition folders                                                                                     | Phase 3                                                                                                |
| 72, 73                                      | `write(expected_revision=X)` normalizing mismatch; uncapped canonical cache; cold re-encode                                | Phase 3 (per-document encoding)                                                                        |
| 74                                          | `_guarded` keeps a commented `Any`; error context `show=""`                                                                | Phase 3 (repository rewrite)                                                                           |
| 75                                          | fuzz depth past the top-level shape; decode cache unfuzzed                                                                 | Phase 2, L0.9 (fuzz the v1 and v2 decoders over the fixtures)                                          |
| 76                                          | `_set_order` context field; `_is_directory` naming                                                                         | Phase 2, A1                                                                                            |
| 77                                          | `Selection` no longer dedupes generators; double decode; docstrings                                                        | Phase 2, S1 (dedupe); the rest Phase 3                                                                 |
| 78                                          | no false-positive tests for route storage                                                                                  | Phase 2, B2                                                                                            |
| 79                                          | `put_forms(set, ())` leaves dangling `Set.forms`                                                                           | Phase 3                                                                                                |
| 80                                          | `put_*` storage writes skip locks (ruling L-A1)                                                                            | Drop: decided in Phase 1 (storage guards validity, edits guard pins)                                   |
| 81                                          | parity test can pass vacuously; rename on a pinned row untested                                                            | Phase 2, D2                                                                                            |
| 82                                          | FIFO tests: index, unjoined thread, skip condition                                                                         | Phase 3                                                                                                |
| 83, 85, 86                                  | citation and library-file guard gaps                                                                                       | Phase 2, X2 (negative controls for every guard)                                                        |
| 84                                          | `engine_compile.py` comment misattached                                                                                    | Phase 2, B2                                                                                            |
| 87                                          | ADR 0014 addendum wording                                                                                                  | Done (addendum 2026-11-17)                                                                             |
| 88                                          | changelog heading formats; silent bootstrap                                                                                | Phase 2, X3                                                                                            |
| 89                                          | fuzz AST test misses a `_uv_run` default                                                                                   | Phase 2, L0.10                                                                                         |
| 90                                          | text fuzz deadline under the `dev` profile                                                                                 | Drop: dev profile is local (ruling); `ci` and `nightly` gate                                           |
| 91                                          | schema check near-tautological                                                                                             | Phase 2, L0.9 (v2 schema validates stored documents, not the model dump)                               |
| 92                                          | 60 s faulthandler dump in `nox -s slow`                                                                                    | Phase 4 (faster curve pieces remove the slow case)                                                     |
| 93                                          | `Workspace.tree_at` rescans and total-bytes cap                                                                            | Phase 3                                                                                                |
| 94                                          | token stripping, weak tokens, ffmpeg probing, claim locks, fsync, log cost, font budget, unreachable branch, large modules | Phase 3 for storage and ffmpeg probing; large modules Phase 2 (A5, E3); the rest drop as host concerns |
| 95                                          | nine deleted regression tests                                                                                              | Done (`780ca73` keeps a regression test per removed feature)                                           |
| 96 to 98                                    | revert hint past a derived table; report wording; file-backend conflict list                                               | Phase 3                                                                                                |
| 99                                          | `revert` reads render blobs                                                                                                | Phase 3                                                                                                |
| 100                                         | `positions.py` imports `manage._with_sets`                                                                                 | Phase 2, A5 then E1                                                                                    |
| 101                                         | `LazyBlobs` error tested on one backend; `join_link` error echo                                                            | Phase 3 (backend conformance kit)                                                                      |
| 102                                         | commit body omits a flip                                                                                                   | Drop: history; release notes record it                                                                 |
| 103                                         | ledger guard edge cases                                                                                                    | Phase 2, X2                                                                                            |
| 104                                         | migration step 13 omits `merge_counts=False`                                                                               | Done (v0.3.0 notes step 13)                                                                            |
| 105                                         | `fix(test):` prefix; `revisions_since` type; helpers missing from the reference                                            | Phase 2, X3 (reference); the rest drop                                                                 |
| 107, 112                                    | large golden diverged after lane B                                                                                         | Done (`uv run pytest -m golden` passes on `3824263`)                                                   |
| 108                                         | `SEGMENT_BOUND` hint wording                                                                                               | Done (hint names the accepted tol)                                                                     |
| 109                                         | no test pins `_probed_error` determinism                                                                                   | Phase 2, B2                                                                                            |
| 110                                         | `tol` validated twice                                                                                                      | Phase 2, B2                                                                                            |
| 111                                         | property test probes fixed shares, not jittered                                                                            | Phase 4 (engine property suite)                                                                        |
| 113                                         | route builders read lock state from the caller's spec                                                                      | Phase 2, B2                                                                                            |
| 114                                         | `_require_rows_unlocked` docstring; positional `force`                                                                     | Phase 2, L0.4                                                                                          |
| 115                                         | no test for absent spec ID or unselected locked row                                                                        | Phase 2, B2                                                                                            |
| 116 to 118                                  | `test_routes.py` index, weak assertion, prefix on never-failing tests                                                      | Phase 2, B2                                                                                            |
| 122, 124                                    | API reference and ADR 0001 still mention `replace_sheet`                                                                   | Phase 2, X3 for the reference; ADR 0001 stays (append-only; ADR 0017 supersedes)                       |
| 123                                         | `replace.py` holds only `similarity_fit`                                                                                   | Drop: ADR 0017 names the module; a rename needs an addendum nobody asked for                           |
| 125, 128, 129                               | `similarity_fit` hint wording and duplication                                                                              | Done for 125; 128 and 129 Phase 2, F2                                                                  |
| 126                                         | `RANK_ONE_RATIO` boundary tests                                                                                            | Phase 2, F2                                                                                            |
| 127                                         | RED evidence ran against a copy                                                                                            | Drop: history                                                                                          |
| 130                                         | `_rounded` hint promises 1e15 steps                                                                                        | Done (hint changed)                                                                                    |
| 131                                         | UP-9.4-47 test docstring lacks its ID                                                                                      | Phase 2, F2 (one docstring line; tests may cite ledger IDs)                                            |
| 132 to 135                                  | coordinate-system guard duplication and injected-type leaks                                                                | Phase 2, F1                                                                                            |
| 35, 41, 50, 51, 106, 119 to 121, 136 to 140 | brief and report fragments                                                                                                 | Not an item                                                                                            |

## Appendix B: ledger IDs assigned to later phases

159 IDs (175 tests). Each goes with the phase that rewrites its code, so it is fixed once.

| Phase   | IDs                                                                                                                  | Why there                                                                                                           |
| ------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Phase 3 | UP-9.7-14, -16 (case and trailing-dot show folders)                                                                  | ADR 0006 moves show IDs to encoded folders in layout v2 (ADR 0015)                                                  |
| Phase 3 | UP-9.7-27, -28 (temp and orphan tree files)                                                                          | garbage collection and `fsck` in layout v2                                                                          |
| Phase 3 | UP-9.7-32 to -35, -46, -91 (proposals)                                                                               | persisted proposals with a full lifecycle                                                                           |
| Phase 3 | UP-9.7-38 to -43 (change reports)                                                                                    | item-level change tracking                                                                                          |
| Phase 3 | UP-9.7-47 to -53, -55, -60, -85, -86, -87 (sets and sections)                                                        | the show-layer rewrite around change tracking; they need item identity across split and join                        |
| Phase 3 | UP-9.7-61 to -63, -65 to -74, -79, -80, -83, -84, -90, -94 (cross-object validation)                                 | incremental validation (decision 9)                                                                                 |
| Phase 3 | UP-9.5-10, -11, -13, -15 to -26 (publication state machine, claims, notifications, versioned claims)                 | collaboration: publications get the same lifecycle and versioning as proposals                                      |
| Phase 4 | UP-9.1-10, -11, -12, -13, -15 to -19, -26, -27, -29 to -36, -67, -72                                                 | geometry algorithms: arc length, projection, offsets, affine tolerances, spacing, scatter, flattening               |
| Phase 4 | UP-9.2-03 to -08, -16 to -20, -22, -23, -25, -26, -29 to -34, -37, -38                                               | engine rewrite: per-knot easing, waypoints, facing that plans ahead, caches, conflict resolution, analytic velocity |
| Phase 4 | UP-9.4-25, -26 (area anchors)                                                                                        | geometry: robust centroids                                                                                          |
| Phase 4 | UP-9.6-15 to -18 (exact transforms), -24 (transition order), -27 to -30, -32 (fitting)                               | exact transforms after ADR 0012; robust fitting with RANSAC and model selection; matching                           |
| Phase 5 | UP-9.1-37 to -42 (SVG import: boxes, `data-d`, transforms, viewBox, `use`, `rect`)                                   | geometry additions: SVG import                                                                                      |
| Phase 5 | UP-9.4-19 to -24 (coordinate wording), -30, -34, -35, -36, -46 (templates), -39 to -42 (render)                      | data-driven versioned templates and render work                                                                     |
| Phase 5 | UP-9.5-31, -32, -33, -36 to -42 (text tables, Unicode PDF, print sheets, CSV dialects), -45 to -52, -54, -57 (video) | Unicode PDF, dot books, CSV import formats, video formats                                                           |

## Appendix C: how the numbers were measured

All on 2026-11-18, WSL2, Python 3.14.7, `main` at `3824263`.

| Number                                  | Command                                                                                                                       |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Open ledger (345 IDs, 374 tests)        | `uv run pytest -m defect --co` with a collection plugin dumping each test's ID and docstring                                  |
| 107 positional booleans, 61 public      | `uv run ruff check showpy --select FBT001,FBT002 --output-format json`, mapped to functions with `ast` and to `_api_table.py` |
| 0 functions above complexity 10         | `uv run ruff check showpy --select C901 --config 'lint.mccabe.max-complexity=10'` (1 above 8)                                 |
| 8 `type: ignore`, 4 stored `Any` fields | `grep -rn "type: ignore" showpy`; a script listing pydantic fields whose annotation holds `Any` (104 models)                  |
| 26 mutable container fields             | the same script, fields typed `dict`, `list` or `set`                                                                         |
| 12 modules over 500 lines               | `find showpy -name "*.py" \| xargs wc -l`, excluding `__init__.py` and `_api_table.py`                                        |
| 28 `Set`/`Sheet`-aware geo functions    | `ast` scan of `geo/{snapping,analysis,transform_base,transforms,measure}.py` signatures                                       |
| 335 of 339 template IDs with `-`        | `check_id` over every landmark, zone and guide of `sheet_templates()`                                                         |
| Import times, sizes                     | see ADR 0010                                                                                                                  |

Not measured: whether CPython 3.15.0 final is installable on CI runners (local `uv` lists
3.15.0rc2); how many positional boolean calls consumers hold; event kinds emitted by
`collab.py` (A3 step 1 lists them); the decode and migration time of a 300 x 60 v0.3.0 show
(L0.9 step 7 measures it).
