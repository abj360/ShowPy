# Phase 3: Storage v2 and Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build repository layout 2 (ADR 0015) and the collaboration layer on it: O(1) `head()`,
verified and durable commits on any file system, per-document encoding, digest-based
`events_since`, item-level changes with three-way merge over documents split by design role,
persisted proposals, incremental validation, the public backend protocol with its conformance
kit, fonts stored with the show, and the read window and upgrade for layout 1. Closes 41 of the
42 open show-layer ledger IDs and ends with release v0.5.0.

**Architecture:** A serial lane L0 commits the v0.4.0 fixture, splits the oversized show modules,
publishes the backend protocol and the layout version file, lands every new model shape, the item
table with `changes()`, and the role document set. Eight lanes then run in parallel worktrees,
each owning disjoint files: A the layout-2 file backend, B encoding, caches and events, C diff,
merge, blame and ID changes, D proposals, E validation, F sets, sections and position import, M
assets and fonts, K the conformance kit and storage property suites. A serial lane X proves the
exit criteria and prepares v0.5.0.

**Tech Stack:** Python 3.13 and 3.14, pydantic v2 (behind the ADR 0026 interface), numpy, uv,
nox, ruff, mypy `--strict`, pyright basic, pytest, Hypothesis (stateful machines), pytest-xdist,
pytest-benchmark, HypoFuzz (nightly only), Prettier 3.6.2, GitHub Actions.

**Spec:** ADR 0015 (`docs/adr/0015-repository-layout-v2.md`, Accepted 2026-11-24), with ADRs 0003,
0004, 0006, 0007, 0014, 0016, 0021, 0023 and 0026. Audit `docs/upgrade-plan.md` section 9.7 and
section 10 "Phase 3" (read only). Phase 2 plan `docs/superpowers/plans/2026-11-18-phase-2-foundations.md`,
Appendix A (deferred review items) and Appendix B (ledger IDs given to Phase 3).

Where the audit and the code disagree, this plan follows the code (`main` at `a6ef3ca`,
2026-11-23):

- The audit's show-layer list predates Phases 1 and 2. Of its 47 Phase 3 IDs, 42 are still open:
  UP-9.7-47, -51, -52, -69, -70, -73 and -74 were retired or flipped by ADR 0021 and task N1, and
  UP-9.7-54, -56 to -58 and -64 flipped in Phase 2. UP-9.5-41 and -42 (position import) moved into
  `e8s/show/` with the interchange and are counted here.
- The UP-9.5 publication IDs Appendix B gave to Phase 3 retired with `e8s.export` (ADR 0021,
  addendum 2026-11-23); nothing of publishing is built here.
- Notes left the show in v0.4.0. The notes document `/notes/notes.json` is a plain document of
  the tree: storage carries it and lane C's item table ignores it.
- `add_set` has `after=` only, so no call can insert a new first set. Lane F's `add_set(before=)`
  still applies (task F3).

## Decisions needed

ADR 0015 lists D1 to D10 with recommendations and costs; this plan assumes each recommendation. The maintainer accepted D1 to D10 and P1 to P3 as recommended on 2026-11-24.
Three more belong to the plan:

- **P1. `add_set(before=)` counts.** Recommendation: `counts` always times the move the insertion
  adds. With `after=`, or `before=` a set that is not first, that is the move into the new set;
  with `before=` the first set, the new set opens the show at 0 counts and `counts` times the
  move into the old opener. Passing both `after` and `before` raises `SchemaError`. Cost if
  wrong: one keyword's meaning changes before anyone uses it.
- **P2. UP-9.7-80 waits for Phase 5.** Recommendation: leave "unpinned sheet without a store" open.
  The sheet store and sheet dependencies are replaced when shows hold their own sheet (ADR 0022,
  format v3), so a fix now tests code that leaves. Cost if wrong: one more open ledger line for a
  phase.
- **P3. The conformance kit ships in the wheel.** Recommendation: `e8s.show.conformance`
  (plain functions, no pytest or Hypothesis import), so a third-party backend can certify itself;
  the Hypothesis machines stay in `tests/`. Cost if wrong: a public module to keep stable; moving
  it to `tests/` later removes one public name.

## Global Constraints

- **ADR gate.** No task starts before ADR 0015 is `Accepted` and D1 to D10 and P1 to P3 are
  answered. A decision answered against its recommendation re-plans the tasks that name it first.
- **No users, so no deprecation.** Removed or renamed public names are deleted outright, with no
  alias, warning or deprecation row (ADR 0021, addendum 2026-11-23). Every change to a public name
  is backticked in the commit subject or a `BREAKING CHANGE:` footer.
- **Ledger flip procedure** (every task that fixes a ledger ID):
  1. Run the ledger test with `--runxfail` and confirm it fails for the audited reason.
  2. Implement the fix.
  3. Remove its `@pytest.mark.defect(...)` and `@pytest.mark.xfail(...)` markers in the same
     commit. Never edit an assertion to make it pass; only an API rename, a moved path helper
     that ADR 0015 names (layout-2 folder paths in `tests/unit/show/test_file_repository.py`), or
     a tightening an accepted ADR names is allowed, and the commit body says which.
  4. Run `set -o pipefail; uv run pytest -m defect -q 2>&1 | tail -3` and confirm no
     `XPASS(strict)`. A test that flips in another lane's file is unmarked by the lane whose code
     flipped it (marker lines only).
- **Gates before every commit:** `set -o pipefail; uv run nox -s lint types tests hashseeds`.
  Lanes A, B and K also run `uv run nox -s slow`. Coverage floor stays 96% (branch). Before a
  task is reported done: `uv run nox -s api-changes`.
- Every new behavior starts with a failing `test_regression_<what>` test or a named test the task
  lists; never weaken a check, threshold or default to make a test pass. A newly exposed defect is
  fixed or reported to the controller, never xfailed without a new ledger ID.
- **No new escape hatches:** no new `type: ignore`, `pyright: ignore`, `noqa` or `Any` in `e8s/`;
  no new `dict[str, Any]` in any model.
- **The model interface (ADR 0026).** Storage, merge and validation code reads models only through
  attributes, `replace`, `to_json` and `from_json`, plus `FrozenModel`'s private helpers where a
  validation context is needed. The library rule test of ADR 0026 already scans `e8s/`.
- **No clock reads.** Time comes from explicit `at` arguments; temp-file age compares file times
  against `at` (ADR 0015 decision 6).
- Every boolean parameter is keyword-only; every destructive call (`gc`, `fsck(repair=True)`)
  takes `confirm: bool = False` and only reports without it.
- **Error codes.** A task that raises a new code adds it to `ErrorCode` with a docstring and
  regenerates `docs/error-codes.md` (`uv run python scripts/gen_error_codes.py`) in the same
  commit. Codes this plan adds include `OLD_LAYOUT`, `TORN_SLOT`, `MERGE_CONFLICT`, `PROPOSAL_STALE`.
- **Library docstrings and comments never cite ADRs, docs files or ledger IDs**; they state the
  rule (`test_library_cites_no_adr_docs_or_ledger_ids`). Plans, ADRs, commit bodies and pull
  request text may cite them.
- `uv` only (`/opt/homebrew/bin/uv`). Before any `npm` or `npx` command:
  `PATH=/Users/toyosiabolaji/.nvm/versions/node/v22.23.5/bin:$PATH`; `npm ci` once per worktree.
  Run `npx --no-install prettier --check` on every Markdown, YAML or JSON file a task writes.
- Library modules under `e8s/` have no shebang; scripts and tests keep it. Module docstrings open
  `<filename> --- <role>` with a `Contains:` index; public functions get a verb-first docstring
  with a `>>>` example importing `e8s.examples`. Modules stay under 500 lines.
- New public names go in `e8s/_api_table.py`; regenerate with
  `uv run python scripts/gen_public_api.py` and refresh the snapshot with
  `E8S_UPDATE_API=1 uv run pytest tests/unit/test_api_snapshot.py`, in the commit that changes it.
- **Stored models.** A task that changes a stored model regenerates `e8s/schemas/e8.show.v2.json`
  with `E8S_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`. Format v2 does not
  change shape in this phase (ADR 0023 decision 4): a model change that alters the v2 schema
  beyond the models L0.4 adds stops the task and goes to the controller.
  `e8s/schemas/showpy.show.v1.json` stays frozen.
- Shared generated files are never hand-merged; after a rebase rerun their generators:
  `e8s/__init__.py`, `tests/fixtures/api_snapshot.json`, `e8s/schemas/*.json`,
  `docs/error-codes.md`, `tests/fixtures/repos/*/expected*.json`. Never edit `CHANGELOG.md` in a
  change commit; `nox -s changelog` writes it at release time.
- **Commits:** Conventional Commits, one logical change each, committed the moment it is complete
  and passing on its own; no `Co-Authored-By` or "Generated with" trailers; identity
  `Oluwatoyosi Peter Abolaji` with the maintainer's personal email. Breaking commits use `!` and a
  `BREAKING CHANGE:` footer.
- **One PR per task; rebase only on conflict.** A reviewed, green task goes out as its own
  stacked PR; the next task may start on its head. Never rebase only because `main` moved. Never
  push without the maintainer's approval.
- **Workspace safety.** Stage explicit paths only (never `git add -A`, `.` or `commit -a`); run
  `git diff --cached --summary` before every commit and stop on `mode 120000` or any path under
  `.superpowers/` or `.claude/`; never `--no-verify`; tests that need git get their own `GIT_DIR`.
- **No deadlines.** No dates, durations or forecasts in briefs, ledgers or reports; start each
  task as soon as its inputs exist and run independent tasks in parallel.
- **Execution:** subagent-driven. One worktree per lane (`Agent` with `isolation: "worktree"`), a
  reviewer subagent gates every task, lanes A, B and K add `differential-review`, and the
  controller merges PRs one at a time and reruns every gate on `main`.

## Review Focus

1. **Repositories written by v0.1.1 to v0.4.0.** Expected: each opens read-only with every
   revision loadable, refuses a write with `OLD_LAYOUT` naming `upgrade_repository`, upgrades by
   copy with every revision ID, parent, time and tree kept, and takes a write afterwards whose
   first revision only re-encodes documents as roles. Pinned by L0.1, A7 and X1:
   `test_regression_every_released_fixture_upgrades_with_ids_kept`.
2. **A crash at any write step.** Expected: reopening shows the old head or the new head, never a
   third state; `fsck` reports only unreachable files; `gc(confirm=True)` leaves `fsck` clean.
   Pinned by K3: `test_crash_at_every_write_step_leaves_old_or_new_head`.
3. **Two writers on one parent, across processes and file systems without hard links.**
   Expected: exactly one lands, the other gets `ConflictError` naming the winner; on the fallback
   path a torn slot is `TORN_SLOT`, never a silent head. Pinned by A1, A5 and K2:
   `test_one_winner_per_slot_across_eight_processes`,
   `test_regression_torn_slot_is_reported_with_the_repair_command`.
4. **Merging role edits that touch one spot.** Expected: a drill move, a facing turn and a note on
   the same spot in the same set merge with no conflict; two different `x` values for that spot
   conflict and name the item; a merge the models refuse (a placement of a spot the other side
   deleted) is a conflict, never a repair. Pinned by C2:
   `test_three_role_agents_on_one_set_merge_with_no_conflict`,
   `test_regression_merge_refused_by_the_models_is_a_conflict`.
5. **Incremental results drifting from whole results.** Expected: `revalidate` equals
   `validate_show(after)`; `events_since` replayed with `apply_event` reproduces head; the cached
   encoding of an unchanged set equals a fresh `encode_show`. Pinned by E4, B4 and B1 with
   Hypothesis properties over random edit sequences:
   `test_revalidate_equals_validate_show_after_random_edits`,
   `test_events_since_replay_reproduces_head_after_random_edits`,
   `test_cached_documents_equal_a_fresh_encode_after_random_edits`.

---

## Sequencing

```
ADR 0015 Accepted; D1-D10 and P1-P3 answered
        |
L0.1 -> L0.2 -> L0.3 -> L0.4 -> L0.5 -> L0.6     (serial, one worktree)
        |
        +-- A  file backend, layout 2      A1 first; A2-A8 in order
        +-- B  encoding, caches, events    B4 needs L0.5
        +-- C  diff, merge, blame          C2 before D2
        +-- D  proposals                   D2 after C2 merges
        +-- E  validation                  E4 after E1-E3
        +-- F  sets, sections, positions
        +-- M  assets and fonts
        +-- K  conformance and properties  K1 after A1 and L0.3; K3 after A3 and A5
                    |
                    v
        X1 -> X2 -> X3                     (serial: scenario, exit checks, release v0.5.0)
```

Lanes run in parallel from the moment L0.6 merges; inside a lane, tasks are serial in the order
listed unless a task names another order.

## File map (lane ownership)

| Lane | Owns (source)                                                                                                                                                                                                                                                                                                                | Owns (tests)                                                                                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L0   | the split of `e8s/show/{storage,repositories,collab,file_repository}.py`; new `e8s/show/{backend,document_repository,memory_backend,workspace,show_io,events,layout1,items}.py`; `e8s/show/models.py` (new models); `e8s/show/{documents,document_reading,files,integrity}.py` (L0.6 only); `scripts/make_legacy_fixture.py` | `tests/fixtures/repos/v0.4.0/`, `tests/unit/show/test_{backend,items,role_documents,layout1}.py`, `tests/unit/show/test_legacy_fixtures.py`                                     |
| A    | `e8s/show/{file_repository,file_backend,show_folders,durable_io,layout1,fsck,gc,upgrade,archive}.py`                                                                                                                                                                                                                         | `tests/unit/show/test_{file_repository,file_backend,show_folders,durable_io,fsck,gc,upgrade,archive}.py`, `tests/unit/show/test_repositories.py`                                |
| B    | `e8s/show/{documents,document_reading,decode_cache,encode_cache,files,integrity,structured,workspace,events}.py`                                                                                                                                                                                                             | `tests/unit/show/test_{documents,encode_cache,decode_cache,files,integrity,structured,workspace,events}.py`, `tests/property/test_storage_encoding.py`                          |
| C    | `e8s/show/{items,history,changes,merge,blame,id_changes}.py`                                                                                                                                                                                                                                                                 | `tests/unit/show/test_{items,history,changes,merge,blame,id_changes}.py`, `tests/property/test_merge_properties.py`                                                             |
| D    | `e8s/show/{collab,proposals}.py`                                                                                                                                                                                                                                                                                             | `tests/unit/show/test_{collab,proposals}.py`                                                                                                                                    |
| E    | `e8s/show/{validate,validate_refs,revalidate}.py`; the `Show` validators in `e8s/show/models.py` (E3 only)                                                                                                                                                                                                                   | `tests/unit/show/test_{validate,validate_refs,revalidate}.py`, `tests/property/test_revalidate_properties.py`; marker lines of UP-9.7-94 in `tests/unit/show/test_integrity.py` |
| F    | `e8s/show/{manage,sections,counts,interchange,interchange_positions}.py`                                                                                                                                                                                                                                                     | `tests/unit/show/test_{manage,sections,counts,interchange,interchange_positions}.py`                                                                                            |
| M    | new `e8s/show/{assets,font_documents}.py`, `e8s/show/show_io.py`, `e8s/geo/fonts.py`                                                                                                                                                                                                                                         | `tests/unit/show/test_{assets,font_documents,show_io}.py`, `tests/unit/geo/test_fonts.py`                                                                                       |
| K    | new `e8s/show/conformance.py`; `.github/workflows/nightly.yml`                                                                                                                                                                                                                                                               | `tests/conformance/`, `tests/property/test_storage_{stateful,crash}.py`                                                                                                         |
| X    | `tests/scenario/test_phase3_scenario.py`, `tests/bench/test_storage_budgets.py`, `docs/releases/v0.5.0.md`, `docs/HANDOFF.md`, `docs/e8s-api-reference.md`, `CHANGELOG.md` (release commit only)                                                                                                                             | —                                                                                                                                                                               |

Shared, regenerated, never hand-merged: `e8s/_api_table.py` (each lane adds its own rows),
`e8s/__init__.py`, `tests/fixtures/api_snapshot.json`, `e8s/core/error_codes.py` (each lane adds
its own members), `docs/error-codes.md`, `e8s/schemas/*.json`.

## Ledger coverage

Measured with `uv run pytest -m defect --co -q` on `main` at `a6ef3ca`: the default suite prints
`Ledger: 129 open`; `nox -s slow` holds one more (UP-9.1-72, geometry, Phase 4). Every open ID
whose test lives under `tests/unit/show/` is in this table: 42 IDs, one test each.

| ID        | Test (in `tests/unit/show/`)                                                                         | Task    |
| --------- | ---------------------------------------------------------------------------------------------------- | ------- |
| UP-9.5-41 | `test_interchange.py::test_regression_import_positions_reads_a_utf8_bom`                             | F4      |
| UP-9.5-42 | `test_interchange.py::test_regression_import_positions_reads_semicolon_delimited_csv`                | F4      |
| UP-9.7-14 | `test_repositories.py::test_regression_show_ids_differing_by_case_are_distinct_or_rejected`          | A1      |
| UP-9.7-16 | `test_repositories.py::test_regression_trailing_dot_show_id_is_rejected_or_stored_safely`            | A1      |
| UP-9.7-27 | `test_file_repository.py::test_regression_commit_cleans_stale_temp_files`                            | A4      |
| UP-9.7-28 | `test_file_repository.py::test_regression_losing_commit_leaves_no_orphan_tree`                       | A4      |
| UP-9.7-32 | `test_collab.py::test_regression_counts_only_change_is_not_reported_as_a_paths_edit`                 | B4      |
| UP-9.7-33 | `test_collab.py::test_regression_events_since_replay_reproduces_a_form_deletion`                     | B4      |
| UP-9.7-34 | `test_collab.py::test_regression_accepting_twice_says_the_proposal_was_accepted`                     | D1      |
| UP-9.7-35 | `test_collab.py::test_regression_proposal_against_unsaved_show_can_be_accepted`                      | D1      |
| UP-9.7-38 | `test_history.py::test_regression_diff_show_reports_a_new_group`                                     | C1      |
| UP-9.7-39 | `test_history.py::test_regression_diff_show_reports_a_pack_change`                                   | C1      |
| UP-9.7-40 | `test_history.py::test_regression_diff_show_reports_a_set_cue_change`                                | C1      |
| UP-9.7-41 | `test_history.py::test_regression_diff_show_reports_a_set_label_change`                              | C1      |
| UP-9.7-42 | `test_history.py::test_regression_diff_show_reports_a_placement_note_change`                         | C1      |
| UP-9.7-43 | `test_history.py::test_regression_diff_show_reports_a_selection_change`                              | C1      |
| UP-9.7-46 | `test_collab.py::test_regression_rescale_proposal_conflicts_with_a_newer_row_of_the_same_transition` | D2      |
| UP-9.7-48 | `test_sections.py::test_regression_join_after_split_keeps_markers_that_repeat_a_label`               | F1      |
| UP-9.7-49 | `test_sections.py::test_regression_join_after_split_keeps_one_facing_override_across_the_split`      | F1      |
| UP-9.7-50 | `test_sections.py::test_regression_split_does_not_leave_a_ramp_longer_than_its_section`              | F1      |
| UP-9.7-53 | `test_manage.py::test_regression_remove_last_set_with_merge_counts_keeps_total_time`                 | F2      |
| UP-9.7-55 | `test_manage.py::test_regression_insert_subset_after_a_names_a_subset_of_a`                          | F2      |
| UP-9.7-60 | `test_manage.py::test_regression_set_counts_rescale_shifts_facing_overrides`                         | F2      |
| UP-9.7-61 | `test_validate.py::test_regression_validate_show_reports_form_members_naming_unknown_spots`          | E1      |
| UP-9.7-62 | `test_validate.py::test_regression_validate_show_reports_forms_of_a_missing_set`                     | E1      |
| UP-9.7-63 | `test_validate.py::test_regression_validate_show_reports_sets_declaring_missing_forms`               | E1      |
| UP-9.7-65 | `test_validate.py::test_regression_validate_show_reports_members_whose_placements_do_not_point_back` | E1      |
| UP-9.7-66 | `test_validate.py::test_regression_validate_show_reports_pins_naming_missing_items`                  | E2      |
| UP-9.7-67 | `test_validate.py::test_regression_validate_show_reports_pins_on_unparsable_transitions`             | E2      |
| UP-9.7-68 | `test_validate.py::test_regression_validate_show_reports_pins_that_disagree_with_placement_flags`    | E2      |
| UP-9.7-71 | `test_validate.py::test_regression_validate_show_reports_facing_overrides_naming_unknown_spots`      | E2      |
| UP-9.7-72 | `test_validate.py::test_regression_validate_show_reports_facing_overrides_past_the_show`             | E2      |
| UP-9.7-79 | `test_validate.py::test_regression_validate_show_warns_about_placements_far_off_the_sheet`           | E3      |
| UP-9.7-80 | `test_validate.py::test_regression_validate_show_reports_unpinned_sheet_without_a_store`             | Phase 5 |
| UP-9.7-83 | `test_validate.py::test_regression_validate_show_reports_placements_of_inactive_spots`               | E3      |
| UP-9.7-84 | `test_validate.py::test_regression_validate_show_reports_a_nonzero_first_set_count`                  | E3      |
| UP-9.7-85 | `test_manage.py::test_regression_remove_set_leaves_no_transition_pin_on_a_vanished_pair`             | F2      |
| UP-9.7-86 | `test_sections.py::test_regression_split_drops_audio_whose_section_starts_after_the_half_ends`       | F1      |
| UP-9.7-87 | `test_sections.py::test_regression_split_puts_a_cue_in_one_half_only`                                | F1      |
| UP-9.7-90 | `test_validate.py::test_regression_validate_show_reports_comments_anchored_to_missing_items`         | E3      |
| UP-9.7-91 | `test_collab.py::test_regression_accepting_a_form_edit_does_not_resurrect_a_deleted_form`            | D2      |
| UP-9.7-94 | `test_integrity.py::test_regression_commit_refuses_a_cast_spot_still_placed`                         | E3      |

**Phase 5, UP-9.7-80:** the show's own sheet copy in format v3 (ADR 0022) replaces the sheet store
and sheet dependencies this check reads (P2).

**Total flipped: 41 IDs, 41 tests** (A 4, B 2, C 6, D 4, E 14, F 11). After Phase 3 the default
suite prints `Ledger: 88 open`, and `nox -s slow` still holds UP-9.1-72. Lanes that flip a test
in another lane's file: E3 (UP-9.7-94 in `test_integrity.py`, lane B's), marker lines only.

## Phase 2 review items picked up

Appendix A rows of the Phase 2 plan and lane notes that belong to the show layer. Each is a
step in the named task, with a test where it changes behavior.

| Item                                                                                        | Task |
| ------------------------------------------------------------------------------------------- | ---- |
| A34 Windows junctions under the root; temp unlink race                                      | A2   |
| A52 a failed first commit leaves a phantom show in memory                                   | L0.3 |
| A53, A101 log, tree and blob reads stay consistent; `LazyBlobs` errors on every backend     | K1   |
| A54 deleting a history file rolls the head back silently                                    | A1   |
| A55 to A59 log length cap, read caps, symlinked show folders, tree path check, TOCTOU       | A2   |
| A61 `repo.backend` is public; A74 `_guarded` keeps an `Any`, error context `show=""`        | A2   |
| A62 Windows names under `/exports`; Cf characters in paths; A71 odd transition folders      | B2   |
| A72, A73 `write(expected_revision=X)` normalizing mismatch; uncapped canonical cache        | B3   |
| A77 double decode; A93 `Workspace.tree_at` rescans and has no total-bytes cap               | B3   |
| A79 `put_forms(set, ())` leaves a dangling `Set.forms`                                      | L0.3 |
| A82 FIFO tests: index, unjoined thread, skip condition                                      | A2   |
| A94 storage `fsync` and log cost                                                            | A3   |
| A96 to A99 `revert` hint past a derived table, report wording, conflict list, render reads  | C4   |
| Lane A, A3: event kinds round trip; legacy-root `events_since`; derived event IDs unchecked | B4   |
| Lane A, A4: `get_script` re-migrates per call on a v1 head                                  | B3   |
| Lane A, A6: tree-record corrupt wording; duplicate BOM check; context key `error`           | A2   |
| Lane A, A7: an `include=` typo near `extensions` gets no close-match hint                   | F4   |
| L0.9: plain `json.loads` on stored data in `documents.py`                                   | B2   |
| ADR 0007 addendum: a v1 workspace cannot read a file at a renamed set folder                | B3   |

Out of scope here: the time-lane items (`_clock_text` on negative seconds, `model_construct`
tempos) and cast items (`delete_spots` discarding its report) are not show-layer code.

---

## Lane L0: foundations (serial)

Branch `feat/p3-l0`. Skills: `differential-review` on L0.3 and L0.6.

### Task L0.1: commit the v0.4.0 repository fixture

**Files:**

- Modify: `scripts/make_legacy_fixture.py` (add `--show v040`)
- Create: `tests/fixtures/repos/v0.4.0/{repo/,expected.json,README.md}`
- Test: `tests/unit/show/test_legacy_fixtures.py`

**Interfaces:**

- Produces: `tests/fixtures/repos/v0.4.0/repo/`, a layout-1 root written by the `v0.4.0` tag:
  the v0.3.0 fixture's history, then the v0.4.0 first save (normalizing and v2 documents,
  `/notes/notes.json`), a path edit and a pin. `expected.json` holds, per revision ID, the show
  as `oracle_json` dumps it.

- [ ] **Step 1:** Add `v040_history(source: Path, out: Path) -> None` to the script: copy the
      v0.3.0 fixture root to `out`, then load, save, edit a path row and pin one spot with the
      library of the running checkout.
- [ ] **Step 2:** In a `git worktree add ../e8s-v0.4.0 v0.4.0` checkout, run
      `PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py --out <repo>/tests/fixtures/repos/v0.4.0 --show v040`.
      Remove the extra worktree after.
- [ ] **Step 3:** Write `test_v040_fixture_loads_every_revision` (each revision's `load_show`
      equals `expected.json`) and `test_v040_fixture_holds_v1_and_v2_revisions` (the first three
      `show.json` have no header, the later ones `"format": "e8.show"`). Run:
      `uv run pytest tests/unit/show/test_legacy_fixtures.py -v`. Expected: PASS on `main`.
- [ ] **Step 4:** Commit `test(show): commit a repository fixture written by v0.4.0`.

### Task L0.2: split the oversized show modules

**Files:** split `e8s/show/storage.py` (567 lines) into `storage.py` (`Repository`,
`AssetStore`, `ROOT_REVISION`), `workspace.py` (`Workspace`, `open_workspace`) and `show_io.py`
(`load_show`, `save_show`); `repositories.py` (605) into `backend.py` (`RepositoryBackend`,
`conflict_error`, `digest`), `document_repository.py` (`DocumentRepository`) and
`memory_backend.py` (`InMemoryRepository`, its backend); `InMemoryAssetStore` moves to new
`assets.py`; `collab.py` loses `events_since`, `apply_event` and `decode_event` to `events.py`;
`file_repository.py` becomes `layout1.py` unchanged, with `FileRepository` re-pointed at it.
Update importers, `_api_table.py`, the snapshot.

- [ ] **Step 1:** Write `test_show_modules_stay_under_500_lines` in
      `tests/unit/test_library_rules.py` (every `e8s/show/*.py` under 500 lines). Run it: FAIL on
      `storage.py` and `repositories.py`.
- [ ] **Step 2:** Move code with no behavior change and no alias; old module paths are deleted.
- [ ] **Step 3:** Gates; the full suite passes unchanged apart from imports.
- [ ] **Step 4:** Commit `refactor(show)!: split storage, repositories and collab modules` with a
      `BREAKING CHANGE:` footer naming each moved public name's new path.

### Task L0.3: the public backend protocol, the layout file and the memory adapter

**Files:** Modify `e8s/show/{backend,document_repository,memory_backend,layout1}.py`,
`e8s/core/error_codes.py`. Test: `tests/unit/show/test_backend.py`.

**Interfaces:**

- Produces (ADR 0015 decision 13), in `e8s/show/backend.py`:

  ```python
  class RepositoryBackend(Protocol):
      def shows(self) -> list[str]: ...
      def head(self, show_id: str) -> str: ...
      def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]: ...
      def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]: ...
      def blob(self, show_id: str, digest: str) -> bytes: ...
      def commit(
          self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
      ) -> None: ...


  LAYOUT_FILE = "layout.json"
  LAYOUT_FORMAT = "e8.repository"
  CURRENT_LAYOUT = 2


  def read_layout(root: Path) -> int: ...  # 1 for a layout-1 root, 2 with layout.json, else raises
  ```

- `DocumentRepository` uses `backend.head` and `backend.log(since=)`; `revisions_since` is
  deleted. The twelve single-document helpers (`get_set`, `put_set`, `list_sets`, `get_script`,
  `put_script`, `get_paths`, `put_paths`, `get_forms`, `put_forms`, `get_timeline`,
  `get_limits`, `get_waivers`) are deleted (ADR 0015 D7), which also removes Appendix A row 79.
- `ErrorCode.OLD_LAYOUT` added.

- [ ] **Step 1:** Write tests: `test_memory_head_and_log_since_cost_does_not_grow_with_history`
      (1,000 revisions; `log(since=head_minus_two)` returns 2 revisions and reads no more),
      `test_regression_failed_first_commit_leaves_no_phantom_show` (a first commit that fails
      validation leaves `shows() == []`), `test_read_layout_reports_layout_1_for_a_v040_root`
      (the L0.1 fixture), `test_read_layout_refuses_a_newer_layout` (`{"layout": 3}` raises
      `VersionError` naming 3 and 2), `test_read_layout_refuses_other_content` (`CORRUPT`).
- [ ] **Step 2:** Run: `uv run pytest tests/unit/show/test_backend.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the protocol, `read_layout`, the memory adapter's index by revision
      ID, and the deletions. The layout-1 adapter implements `head` and `log(since=)` by its walk.
- [ ] **Step 4:** Gates, then commit
      `feat(show)!: publish RepositoryBackend with head and log(since=)` (footer names
      `revisions_since` and the twelve helpers).

### Task L0.4: the new models

**Files:** Modify `e8s/show/models.py`; regenerate the v2 schema only if a stored model changes
(only `Proposal` is stored). Test: `tests/unit/show/test_models.py`.

**Interfaces (Produces):** frozen models, each with `to_json`/`from_json` round trips:

- `ItemKey(kind: ItemKind, path: tuple[str, ...], role: str | None)`; `ItemKind` a `Literal`
  of `placement`, `set`, `set_order`, `form`, `path_row`, `spot`, `group`, `selection`, `pin`,
  `facing_override`, `sheet`, `timeline`, `show`.
- `ItemChange(key: ItemKey, before: JsonValue | None, after: JsonValue | None)`;
  `ChangeSet(changes: tuple[ItemChange, ...])` sorted by key.
- `MergeConflict(key: ItemKey, base, ours, theirs: JsonValue | None, reason: str)`;
  `MergeResult(show: Show | None, conflicts: tuple[MergeConflict, ...])`.
- `IdChanges(sets: tuple[tuple[SetId, SetId], ...], spots: tuple[tuple[SpotId, SpotId], ...])`.
- `Proposal` gains `accepted_revision: str = ""`; `ProposalState` becomes `open`, `accepted`,
  `rejected`, `withdrawn`.
- `FsckFinding(code: str, path: str, message: str)`, `FsckReport(findings, ok)`,
  `GcReport(removed: tuple[str, ...], bytes_freed: int, dry_run: bool)`,
  `UpgradeReport(shows, revisions, blobs_linked, blobs_copied)`,
  `ConformanceReport(passed: tuple[str, ...], failed: tuple[tuple[str, str], ...])`.

- [ ] **Step 1:** Write `test_every_new_model_round_trips_through_json` (one instance each) and
      `test_proposal_states_are_open_accepted_rejected_withdrawn`. Run: FAIL (names missing).
- [ ] **Step 2:** Add the models with docstrings and `Attributes:`; add `_api_table.py` rows.
- [ ] **Step 3:** Gates; refresh the snapshot and the v2 schema; commit
      `feat(show): add change, merge, proposal, fsck and upgrade models`.

### Task L0.5: the item table and `changes()`

**Files:** Create `e8s/show/items.py`. Test: `tests/unit/show/test_items.py`,
`tests/property/test_show_properties.py` (one property).

**Interfaces:**

- Produces: `ITEM_TABLE: Mapping[tuple[str, str], tuple[ItemKind, str | None]]` mapping (model
  name, stored field) to (item kind, role), with the roles of ADR 0015 decision 11: placement
  `drill` (`x`, `y`, `on_field`, `form`, `order`), `facing`, `labels` (`note`), `pin`
  (`pinned`); set `timing` (`counts`), `labels` (`label`, `cue`), `build` (`source_hash`,
  `state`), and `forms` (`forms`).
- Produces: `items(show: Show) -> dict[ItemKey, JsonValue]` and
  `changes(before: Show, after: Show) -> ChangeSet`, skipping sets, forms and transitions the two
  shows share by identity. `/notes/` and `/proposals/` hold no items.

- [ ] **Step 1:** Write `test_every_stored_field_has_an_item_row` (walks the v2 schema's
      properties of every stored model; fails naming a field with no row),
      `test_changes_of_a_show_with_itself_is_empty`,
      `test_one_spot_move_is_one_drill_change` (move `T1` in set 2: exactly
      `ItemKey("placement", ("2", "T1"), "drill")`),
      `test_turn_and_note_on_one_spot_are_two_roles`, and the property
      `test_changes_lists_exactly_the_items_that_differ`.
- [ ] **Step 2:** Run: FAIL. **Step 3:** Implement on `to_json()` values (ADR 0026), keys from
      set keys, not display IDs. **Step 4:** Run: PASS; gates.
- [ ] **Step 5:** Commit `feat(show): track changes per item and design role`.

### Task L0.6: the role document set

**Files:** Modify `e8s/show/{documents,document_reading,document_paths,files,integrity}.py`.
Test: `tests/unit/show/test_role_documents.py`.

**Interfaces:**

- Produces: `encode_show` writes `/sets/<key>/set.json` (ID, key, timing, build, form list,
  labels of the set), `/sets/<key>/drill.json`, `/sets/<key>/facing.json`,
  `/sets/<key>/labels.json` (placement notes) and `/cast/cast.json`; `show.json` loses `cast`.
  Document bodies keep their v2 shapes; each placement row is keyed by spot ID.
- `KNOWN_ROOTS` gains `/cast/`, `/proposals/`, `/assets/`; `/props/` and `/tracks/` are reserved
  (refused until a format defines them).
- The decoder reads both document sets; a `set.json` with `placements` is the layout-1 set; both
  forms for one set raise `SchemaError`. `show_changes` over a layout-1 tree re-encodes roles.
- Until A7 merges, the layout-1 writer stores role documents too; no release ships in between.

- [ ] **Step 1:** Write `test_role_split_round_trips_the_seeded_show` (300 x 60 decodes equal),
      `test_regression_layout1_set_documents_still_decode` (L0.1 fixture, every revision),
      `test_first_save_over_layout1_documents_changes_no_content` (`changes()` across it is
      empty), `test_regression_tree_with_both_set_forms_is_refused`,
      `test_reserved_roots_are_refused_until_a_format_defines_them`,
      `test_export_json_bytes_are_unchanged_by_the_role_split` (the interchange golden).
- [ ] **Step 2:** Run: FAIL. **Step 3:** Implement; workspace paths for the new files read and
      write through `files.py`. **Step 4:** Gates; regenerate fixture expectations that list
      stored paths and review the diff.
- [ ] **Step 5:** Commit `feat(show)!: store sets and the cast as role documents`.

---

## Lane A: the layout-2 file backend

Branch `feat/p3-a-files`. Skills: `differential-review` on every task. Gate: L0 merged.

### Task A1: slots, encoded show folders and O(1) head

**Fixes:** UP-9.7-14, UP-9.7-16; Appendix A row 54. **Files:** Create
`e8s/show/{file_backend,show_folders}.py`; modify `e8s/show/file_repository.py` (dispatch by
`read_layout`). Test: `tests/unit/show/test_{file_backend,show_folders}.py`,
`tests/unit/show/test_repositories.py`.

**Interfaces:**

- Produces: `show_folder(show_id: str) -> str` (slug plus 16 hex, ADR 0015 decision 8);
  `FileBackend(root: Path)` implementing `RepositoryBackend` on the decision 2 layout;
  `FileRepository(root)` writes layout 2 to an empty root and opens layout 1 read-only.
  `SLOT_DIGITS = 12`. New shows run `check_id(show_id, kind="show")`.

- [ ] **Step 1:** Run UP-9.7-14 and -16 with `--runxfail`: they fail on shared folders.
- [ ] **Step 2:** Write `test_show_folder_is_lowercase_slug_and_sixteen_hex`,
      `test_show_folder_is_never_a_device_name_or_over_49_characters`,
      `test_warm_head_probes_one_slot` and `test_cold_head_needs_at_most_22_lookups_at_1000_revisions`
      (count `os.lstat` through a recording wrapper), `test_one_winner_per_slot_across_eight_processes`
      (200 trials), `test_regression_deleted_slot_is_reported_corrupt`,
      `test_regression_commit_refuses_to_refill_a_slot_gap`,
      `test_conflict_lists_the_landed_revisions_from_the_slots` (Appendix A row 98).
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure (test helpers that build folder
      paths now call `show_folder`; the commit body says so). Gates and `nox -s slow`.
- [ ] **Step 5:** Commit `feat(show)!: store file repositories in layout 2 with numbered slots`.

### Task A2: verified records, read caps and error mapping

**Files:** Modify `e8s/show/file_backend.py`, `e8s/show/durable_io.py` (create: open-no-follow,
capped reads, `_mapped_io`). Test: `tests/unit/show/test_file_backend.py`.

- [ ] **Step 1:** Write `test_regression_record_with_a_wrong_id_is_corrupt` (edit one digest in a
      record), `test_legacy_ids_accepted_only_before_legacy_before`,
      `test_regression_symlinked_show_folder_is_refused`,
      `test_regression_junction_under_the_root_is_refused` (Windows only),
      `test_log_longer_than_max_revisions_is_refused`, `test_every_read_error_names_the_show`
      (no `show=""` context), and the FIFO cases of Appendix A row 82 rebuilt with a joined
      thread and an index check.
- [ ] **Step 2:** Run: FAIL. **Step 3:** Implement; `DocumentRepository.backend` becomes a
      read-only property; `_guarded` loses its `Any` (Appendix A rows 34, 55 to 59, 61, 74; lane
      A6 wording items). **Step 4:** Gates. **Step 5:** Commit
      `fix(show): verify revision records and map every read failure`.

### Task A3: durable commits

**Files:** Modify `e8s/show/{durable_io,file_backend}.py`. Test:
`tests/unit/show/test_durable_io.py`.

**Interfaces:** `flush_file(fd: int, *, is_commit_point: bool) -> None` (`fsync`; on macOS
`F_BARRIERFSYNC`, or `F_FULLFSYNC` for the commit point); `flush_directory(path: Path) -> None`
(no-op on Windows).

- [ ] **Step 1:** Write `test_commit_flushes_each_file_and_directory_before_its_slot` (a recording
      shim lists the calls in order: blobs, record, their directories, then the slot with a full
      flush) and `test_directory_flush_is_skipped_on_windows`. Run: FAIL.
- [ ] **Step 2:** Implement (ADR 0015 decision 4, D3). **Step 3:** Gates. **Step 4:** Commit
      `feat(show): flush files and directories before a commit lands`.

### Task A4: temp files and losing commits

**Fixes:** UP-9.7-27, UP-9.7-28. **Files:** Modify `e8s/show/file_backend.py`. Test:
`tests/unit/show/test_file_repository.py`.

- [ ] **Step 1:** Run both with `--runxfail`: FAIL for the audited reasons.
- [ ] **Step 2:** Write `test_fresh_temp_file_of_another_writer_is_kept` (mtime 1 hour before
      `at`) and `test_losing_identical_commit_keeps_the_shared_record`.
- [ ] **Step 3:** Implement `STALE_TEMP_AGE = timedelta(hours=24)` cleanup under `<root>/tmp/`
      against `at`, and loser record removal. **Step 4:** Ledger flip procedure (paths now under
      `tmp/` and `records/`; ADR 0015 decision 6 names them). Gates.
- [ ] **Step 5:** Commit `fix(show): remove stale temp files and losing commits' records`.

### Task A5: the hard-link-free fallback

**Files:** Modify `e8s/show/{file_backend,durable_io}.py`, `e8s/core/error_codes.py`
(`TORN_SLOT`). Test: `tests/unit/show/test_file_backend.py`.

- [ ] **Step 1:** Write, with a shim making `os.link` raise `EPERM`:
      `test_commit_lands_without_hard_links`, `test_one_winner_without_hard_links` (8 threads,
      200 trials), `test_regression_torn_slot_is_reported_with_the_repair_command` (an empty slot
      file raises `TORN_SLOT` whose hint names `fsck(root, repair=True, confirm=True)`). Run: FAIL.
- [ ] **Step 2:** Implement (ADR 0015 decision 7): probe once per instance, `O_CREAT | O_EXCL`,
      one write, three read retries. **Step 3:** Gates. **Step 4:** Commit
      `feat(show): commit on file systems without hard links`.

### Task A6: `fsck` and `gc`

**Files:** Create `e8s/show/{fsck,gc}.py`. Test: `tests/unit/show/test_{fsck,gc}.py`.

**Interfaces:** `fsck(root: Path, show_id: str | None = None, *, repair: bool = False,
confirm: bool = False) -> FsckReport`; `gc(root: Path, *, at: datetime, confirm: bool = False) ->
GcReport`. Finding codes: `MISSING_BLOB`, `BAD_BLOB`, `BAD_RECORD`, `SLOT_GAP`, `TORN_SLOT`,
`UNREACHABLE`, `STALE_TEMP`, `UNKNOWN_FILE`.

- [ ] **Step 1:** Write `test_fsck_of_a_clean_repository_is_ok`, one test per finding code built
      by damaging a fresh repository (`test_fsck_reports_<code>`),
      `test_gc_without_confirm_removes_nothing`, `test_gc_removes_only_unreachable_files`
      (fsck clean after; every revision still loads), `test_fsck_repair_removes_only_a_torn_last_slot`.
      Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates. **Step 4:** Commit
      `feat(show): add fsck and gc for file repositories`.

### Task A7: layout 1 read-only and `upgrade_repository`

**Files:** Modify `e8s/show/{layout1,file_repository}.py`; create `e8s/show/upgrade.py`. Test:
`tests/unit/show/test_upgrade.py`.

**Interfaces:** `upgrade_repository(source: Path, target: Path) -> UpgradeReport`; layout-1
writes raise `VersionError` code `OLD_LAYOUT` whose hint names `upgrade_repository`.

- [ ] **Step 1:** Write `test_layout1_root_is_read_only` (v0.4.0 fixture: every revision loads; a
      write raises `OLD_LAYOUT`), `test_regression_every_released_fixture_upgrades_with_ids_kept`
      (v0.1.1 to v0.4.0: same revision IDs, parents, times and trees; `load_show` equal at every
      revision; a write lands), `test_upgrade_never_touches_the_source` (hash every source file
      before and after), `test_interrupted_upgrade_target_is_refused` (no `layout.json`),
      `test_upgrade_links_blobs_on_one_file_system`. Run: FAIL.
- [ ] **Step 2:** Implement (ADR 0015 decision 15); the layout-1 writer is deleted. **Step 3:**
      Gates and `nox -s slow`. **Step 4:** Commit
      `feat(show)!: read layout 1 only and upgrade it by copy`.

### Task A8: a portable archive with history

**Files:** Create `e8s/show/archive.py`. Test: `tests/unit/show/test_archive.py`.

**Interfaces:** `export_archive(repo: DocumentRepository, show_id: str) -> bytes`;
`import_archive(repo: DocumentRepository, data: bytes) -> str`.

- [ ] **Step 1:** Write `test_archive_round_trips_every_revision` (memory to files and back),
      `test_equal_histories_give_equal_archive_bytes`, `test_regression_archive_with_a_bad_blob_is_refused`,
      `test_import_refuses_an_existing_show`, `test_regression_archive_entry_outside_the_show_is_refused`
      (`../` name). Run: FAIL.
- [ ] **Step 2:** Implement a ZIP with stored entries, sorted names, zero times (D9). **Step 3:**
      Gates. **Step 4:** Commit `feat(show): export and import a show's history as an archive`.

---

## Lane B: encoding, caches and events

Branch `feat/p3-b-encoding`. Skills: `differential-review` on B1 and B2. Gate: L0 merged.

### Task B1: per-document encoding

**Files:** Create `e8s/show/encode_cache.py`; modify `e8s/show/{files,documents}.py`. Test:
`tests/unit/show/test_encode_cache.py`, `tests/property/test_storage_encoding.py`.

**Interfaces:** `EncodeCache(max_bytes: int = ENCODE_CACHE_BYTES)` with
`documents(show: Show) -> dict[str, bytes]` memoized by the identity of each document's sources,
holding them; `ENCODE_CACHE_BYTES = 64 * 2**20`. `DocumentRepository` owns one.

- [ ] **Step 1:** Write `test_one_spot_save_encodes_only_the_edited_set_documents` (the cache's
      miss counter is the edited set's role documents plus none else),
      `test_cache_never_returns_bytes_for_a_reused_identity`, `test_cache_stays_under_its_byte_cap`
      and the property `test_cached_documents_equal_a_fresh_encode_after_random_edits`. Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates and `nox -s slow`; run
      `uv run pytest tests/bench/test_budgets.py -k one_spot --tier L` and record the time.
- [ ] **Step 4:** Commit `perf(show): encode only the documents whose sources changed`.

### Task B2: the incremental commit check

**Files:** Modify `e8s/show/{integrity,document_reading,decode_cache,structured}.py`. Test:
`tests/unit/show/test_integrity.py`.

- [ ] **Step 1:** Write `test_one_spot_commit_decodes_only_changed_documents` (decode counter),
      `test_commit_check_still_refuses_every_invalid_tree` (the existing refusal tests, run on a
      warm and a cold cache), `test_regression_cf_characters_in_paths_are_refused`,
      `test_regression_odd_transition_folder_is_refused`, `test_regression_stored_json_is_parsed_strictly_everywhere`
      (the `documents.py` set-order read). Run: FAIL.
- [ ] **Step 2:** Implement: decode changed documents, check the model rules they touch, reuse
      cached parts (ADR 0015 decision 9). **Step 3:** Gates. **Step 4:** Commit
      `perf(show): check only the changed documents on commit`.

### Task B3: workspace caches and migrated views

**Files:** Modify `e8s/show/workspace.py`. Test: `tests/unit/show/test_workspace.py`.

- [ ] **Step 1:** Write `test_tree_at_reads_a_revision_once`, `test_workspace_total_bytes_cap`,
      `test_regression_write_with_an_older_expected_revision_normalizes_that_revision`,
      `test_get_script_on_a_v1_head_migrates_once`,
      `test_regression_v1_workspace_reads_a_file_at_a_renamed_set_folder` (`/sets/con_/set.py`).
      Run: FAIL.
- [ ] **Step 2:** Implement (Appendix A rows 72, 73, 77, 93). **Step 3:** Gates. **Step 4:**
      Commit `perf(show): cache trees and migrated views per workspace revision`.

### Task B4: `events_since` by digest

**Fixes:** UP-9.7-32, UP-9.7-33. **Files:** Modify `e8s/show/events.py`. Test:
`tests/unit/show/test_events.py`; marker lines in `tests/unit/show/test_collab.py`.

**Interfaces:** Consumes `changes()` (L0.5). `events_since(ws, revision) -> list[CanvasEvent]`
decodes only changed documents per revision; event kinds gain `delete_form`.

- [ ] **Step 1:** Run both IDs with `--runxfail`. Write `test_events_since_decodes_no_whole_show`,
      `test_every_event_kind_round_trips_through_json`, `test_events_since_the_root_of_a_legacy_repository`
      (L0.1 fixture), `test_event_ids_are_checked_on_decode`, and the property
      `test_events_since_replay_reproduces_head_after_random_edits`.
- [ ] **Step 2:** Implement (ADR 0015 decision 10). **Step 3:** Ledger flip procedure; gates and
      `nox -s slow`. **Step 4:** Commit `perf(show)!: derive events from changed documents only`.

---

## Lane C: diff, merge, blame and ID changes

Branch `feat/p3-c-merge`. Gate: L0 merged.

### Task C1: `diff_show` from a change set

**Fixes:** UP-9.7-38 to UP-9.7-43. **Files:** Modify `e8s/show/history.py`. Test:
`tests/unit/show/test_history.py`.

- [ ] **Step 1:** Run the six with `--runxfail`. Write `test_diff_show_reports_every_item_kind`
      (one edit per `ItemKind`). **Step 2:** Rebuild `diff_show(a, b) -> dict[str, JsonValue]` as
      a rendering of `changes(a, b)`, keeping today's keys and adding `groups`, `selections`,
      `labels` and `pack`. **Step 3:** Ledger flip procedure; gates.
- [ ] **Step 4:** Commit `fix(show)!: derive diff_show from item changes`.

### Task C2: three-way merge

**Files:** Create `e8s/show/merge.py`; `e8s/core/error_codes.py` (`MERGE_CONFLICT`). Test:
`tests/unit/show/test_merge.py`, `tests/property/test_merge_properties.py`.

**Interfaces:** `merge(base: Show, ours: Show, theirs: Show) -> MergeResult`.

- [ ] **Step 1:** Write `test_three_role_agents_on_one_set_merge_with_no_conflict` (drill moves
      `T1`, facing turns `T1`, labels annotates `T1`, all in set 2 of the 300 x 60 show),
      `test_two_x_values_for_one_spot_conflict_on_that_item`,
      `test_regression_merge_refused_by_the_models_is_a_conflict`,
      `test_set_order_changes_merge_when_they_touch_different_sets`, and the properties
      `test_merge_with_an_unchanged_side_returns_the_other`, `test_merge_is_symmetric_in_ours_and_theirs`.
      Run: FAIL.
- [ ] **Step 2:** Implement (ADR 0015 decision 11). **Step 3:** Gates. **Step 4:** Commit
      `feat(show): merge two edits of a show item by item`.

### Task C3: `blame` and `id_changes`

**Files:** Create `e8s/show/{blame,id_changes}.py`. Test: `tests/unit/show/test_{blame,id_changes}.py`.

**Interfaces:** `blame(ws: Workspace, key: ItemKey) -> Revision | None`;
`id_changes(before: Show, after: Show) -> IdChanges` (set renames by key; spot renames only on
equal placements in every set, ADR 0015 D6).

- [ ] **Step 1:** Write `test_blame_names_the_revision_that_last_moved_a_spot` (reads only
      revisions whose changed paths hold the item), `test_blame_of_an_untouched_item_is_the_first_save`,
      `test_renumber_reports_every_set_rename`, `test_rename_spots_is_reported_when_placements_match`,
      `test_rename_and_move_is_a_removal_and_an_addition`. Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates. **Step 4:** Commit
      `feat(show): add blame and id_changes`.

### Task C4: `revert` follow-ups

**Files:** Modify `e8s/show/history.py`. Test: `tests/unit/show/test_history.py`.

- [ ] **Step 1:** Write `test_revert_reads_no_render_blobs` (a counting backend),
      `test_regression_revert_hint_names_the_stored_path_of_a_derived_table`,
      `test_revert_report_lists_restored_items`. Run: FAIL.
- [ ] **Step 2:** Implement (Appendix A rows 96, 97, 99). **Step 3:** Gates. **Step 4:** Commit
      `fix(show): revert reads only show documents`.

---

## Lane D: proposals

Branch `feat/p3-d-proposals`. Gate: L0 merged; D2 after C2 merges.

### Task D1: persisted proposals

**Fixes:** UP-9.7-34, UP-9.7-35. **Files:** Create `e8s/show/proposals.py`; modify
`e8s/show/collab.py`. Test: `tests/unit/show/test_proposals.py`; marker lines in
`tests/unit/show/test_collab.py`.

**Interfaces:** `propose(ws, change, note, proposal_id, at) -> Proposal` (writes
`/proposals/<id>.json`); `proposals(ws) -> list[Proposal]`; `get_proposal(ws, proposal_id) ->
Proposal`; `reject(ws, proposal_id, reason, at) -> Proposal`; `withdraw(ws, proposal_id, at) ->
Proposal`; `is_stale(ws, proposal_id) -> bool`. `ErrorCode.PROPOSAL_STALE`.

- [ ] **Step 1:** Run both IDs with `--runxfail`. Write `test_proposal_survives_reopening_the_repository`,
      `test_rejected_proposal_keeps_its_reason`, `test_withdrawn_proposal_cannot_be_accepted`,
      `test_stale_is_derived_never_stored`, `test_proposal_documents_are_not_show_changes`
      (`changes`, `diff_show`, `events_since` ignore `/proposals/`).
- [ ] **Step 2:** Implement (ADR 0015 decision 12). **Step 3:** Ledger flip procedure; gates.
- [ ] **Step 4:** Commit `feat(show)!: store proposals with their lifecycle`.

### Task D2: accept and preview by merge

**Fixes:** UP-9.7-46, UP-9.7-91. **Files:** Modify `e8s/show/{proposals,collab}.py`. Test:
`tests/unit/show/test_proposals.py`.

**Interfaces:** Consumes `merge` (C2). `accept(ws, proposal_id, at) -> Revision` writes the merge
result and the `accepted` state in one revision; `preview_proposal(ws, proposal_id) ->
ProposalPreview` shows the merge on the head.

- [ ] **Step 1:** Run both IDs with `--runxfail`. Write
      `test_accept_writes_the_change_and_the_state_in_one_revision`,
      `test_non_overlapping_proposal_lands_after_the_base_moved`,
      `test_preview_shows_the_merge_on_the_head`.
- [ ] **Step 2:** Implement. **Step 3:** Ledger flip procedure; gates. **Step 4:** Commit
      `feat(show)!: accept proposals by three-way merge`.

---

## Lane E: validation

Branch `feat/p3-e-validate`. Gate: L0 merged.

### Task E1: form and set references

**Fixes:** UP-9.7-61, -62, -63, -65. **Files:** Create `e8s/show/validate_refs.py` (the
cross-object checks, keeping `validate.py` under 500 lines); modify `e8s/show/validate.py`.

- [ ] **Step 1:** Run the four with `--runxfail`. **Step 2:** Add one check each with a code
      (`UNKNOWN_SPOT`, `UNKNOWN_SET`, `UNKNOWN_FORM`, `MEMBER_MISMATCH`) and the item key in
      `ValidationIssue.context`. **Step 3:** Ledger flip procedure; gates.
- [ ] **Step 4:** Commit `fix(show): report form and set references that do not resolve`.

### Task E2: pins and facing overrides

**Fixes:** UP-9.7-66, -67, -68, -71, -72. **Files:** Modify `e8s/show/validate_refs.py`.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** One check each (`UNKNOWN_PIN`,
      `BAD_TRANSITION`, `PIN_MISMATCH`, `UNKNOWN_SPOT`, `PAST_THE_SHOW`). **Step 3:** Ledger flip
      procedure; gates. **Step 4:** Commit `fix(show): report pins and facing overrides that do not resolve`.

### Task E3: placements, the first set, comments and the cast invariant

**Fixes:** UP-9.7-79, -83, -84, -90, -94. **Files:** Modify `e8s/show/{validate,validate_refs}.py`,
the `Show` validators in `e8s/show/models.py`; marker lines in `tests/unit/show/test_integrity.py`.

**Interfaces:** `validate_show(show, store=None, *, comments: Sequence[Comment] = ())`; `Show`
refuses a placement naming a spot outside the cast; `FAR_OFF_SHEET_M` names the warning distance.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Implement; the `Show` invariant
      makes the commit check refuse UP-9.7-94's tree. **Step 3:** Ledger flip procedure;
      regenerate the v2 schema (validators only: no shape change expected) and confirm the diff is
      empty; gates. **Step 4:** Commit `fix(show)!: refuse placements outside the cast and report comment anchors`.

### Task E4: incremental validation

**Files:** Create `e8s/show/revalidate.py`. Test: `tests/unit/show/test_revalidate.py`,
`tests/property/test_revalidate_properties.py`.

**Interfaces:** Consumes `changes()` (L0.5). `revalidate(previous: ValidationReport, before:
Show, after: Show) -> ValidationReport`; path continuity only for changed spots' rows through the
engine's per-entity compile.

- [ ] **Step 1:** Write `test_one_spot_revalidation_runs_no_whole_transition_check`,
      `test_revalidate_keeps_issues_of_unchanged_items`, the property
      `test_revalidate_equals_validate_show_after_random_edits`, and the timing test
      `test_one_spot_revalidation_under_50_ms_at_300_by_60` (`@pytest.mark.timing`). Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates and `nox -s slow`. **Step 4:** Commit
      `feat(show): revalidate only what changed`.

---

## Lane F: sets, sections and position import

Branch `feat/p3-f-sets`. Gate: L0 merged.

### Task F1: split and join keep every timeline item once

**Fixes:** UP-9.7-48, -49, -50, -86, -87. **Files:** Modify `e8s/show/sections.py`.

- [ ] **Step 1:** Run the five with `--runxfail`. **Step 2:** Implement: markers keyed by
      (count, label), one facing override across a split, ramps clipped to their section, audio
      and cues in the half that holds their start. **Step 3:** Ledger flip procedure; gates.
- [ ] **Step 4:** Commit `fix(show): split and join sections without losing or doubling items`.

### Task F2: set edits keep time, pins and overrides consistent

**Fixes:** UP-9.7-53, -55, -60, -85. **Files:** Modify `e8s/show/{manage,counts}.py`.

- [ ] **Step 1:** Run the four with `--runxfail`. **Step 2:** Implement: the last set's counts
      merge into the previous set; `insert_subset(after="A")` names `A` plus the next free letter;
      `set_counts(rescale=True)` shifts facing overrides; `remove_set` drops transition pins of
      vanished pairs. **Step 3:** Ledger flip procedure; gates.
- [ ] **Step 4:** Commit `fix(show): keep time, pins and overrides consistent across set edits`.

### Task F3: `add_set(before=)`

**Files:** Modify `e8s/show/manage.py`. Test: `tests/unit/show/test_manage.py`.

**Interfaces:** `add_set(show, set_id, counts, after=None, placements=None, *, before: str | None
= None, force=False) -> Show` (P1).

- [ ] **Step 1:** Write `test_add_set_before_the_first_set_opens_the_show`
      (`add_set(two_set_show(), "0", 4.0, before="1")`: `set_ids() == ["0", "1", "2"]`, counts
      `[0.0, 4.0, 5.0]`), `test_add_set_before_a_later_set_equals_after_its_predecessor`,
      `test_add_set_refuses_both_after_and_before`. Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates; snapshot. **Step 4:** Commit
      `feat(show): insert a set before another with add_set(before=)`.

### Task F4: position import and interchange hints

**Fixes:** UP-9.5-41, UP-9.5-42. **Files:** Modify `e8s/show/{interchange_positions,interchange}.py`.

- [ ] **Step 1:** Run both with `--runxfail`. Write `test_regression_include_typo_gets_a_close_match_hint`.
- [ ] **Step 2:** Implement: strip one leading BOM; sniff `,` or `;` from the header only.
- [ ] **Step 3:** Ledger flip procedure; gates. **Step 4:** Commit
      `fix(show): read BOM and semicolon position files`.

---

## Lane M: assets and fonts

Branch `feat/p3-m-fonts`. Gate: L0 merged; M2 after A1 (layout-2 known roots in use).

### Task M1: `FileAssetStore`

**Files:** Modify `e8s/show/assets.py`. Test: `tests/unit/show/test_assets.py`.

**Interfaces:** `FileAssetStore(root: Path)` implementing `AssetStore` under `<root>/assets/`,
reusing A's exclusive create, flush and verified read helpers from `durable_io.py`.

- [ ] **Step 1:** Write `test_file_asset_store_round_trips_and_dedupes`,
      `test_regression_tampered_asset_is_corrupt`, `test_unknown_asset_names_put` (hint). Run: FAIL.
- [ ] **Step 2:** Implement. **Step 3:** Gates. **Step 4:** Commit
      `feat(show): add a file-backed asset store`.

### Task M2: repositories store the fonts of text forms

**Files:** Create `e8s/show/font_documents.py`; modify `e8s/show/show_io.py`, `e8s/geo/fonts.py`.
Test: `tests/unit/show/test_font_documents.py`.

**Interfaces:** `save_show(ws, show, at, *, font_registry: FontRegistry | None = None) ->
Revision` writes `/assets/fonts/<sha256>.json` for each text form's non-bundled font;
`load_fonts(ws) -> FontRegistry` returns bundled plus stored fonts.

- [ ] **Step 1:** Write `test_reopened_user_font_text_form_rebuilds_without_a_registry`,
      `test_font_lands_in_the_same_revision_as_its_form`, `test_save_without_the_font_raises_missing_font`,
      `test_tampered_font_document_is_refused` (`FONT_HASH_MISMATCH`). Run: FAIL.
- [ ] **Step 2:** Implement (ADR 0015 decision 14). **Step 3:** Gates; snapshot. **Step 4:**
      Commit `feat(show): store the fonts of text forms with the show`.

---

## Lane K: conformance and storage properties

Branch `feat/p3-k-conformance`. Skills: `differential-review` on K1. Gate: K1 after L0.3 and A1;
K3 after A3 and A5.

### Task K1: the conformance kit

**Files:** Create `e8s/show/conformance.py`, `tests/conformance/test_backends.py`.

**Interfaces:** `check_backend(make_backend: Callable[[], RepositoryBackend]) -> ConformanceReport`
running named checks: `one_winner_per_parent`, `read_your_writes`, `failed_commit_changes_nothing`,
`log_since_is_ordered`, `head_matches_log`, `blob_reads_are_verified`, `ids_are_never_trusted`,
`errors_are_storage_errors`, `unicode_and_case_paths`, `long_names`.

- [ ] **Step 1:** Write `test_memory_backend_conforms`, `test_file_backend_conforms`,
      `test_file_backend_without_hard_links_conforms`, and
      `test_a_backend_that_lets_two_writers_win_fails_the_kit` (a deliberately broken adapter;
      the mock must be able to fail). Run: FAIL.
- [ ] **Step 2:** Implement (P3); covers Appendix A rows 53 and 101. **Step 3:** Gates and
      `nox -s slow`. **Step 4:** Commit `feat(show): add a conformance kit for repository backends`.

### Task K2: stateful storage tests

**Files:** Create `tests/property/test_storage_stateful.py`.

- [ ] **Step 1:** Write a Hypothesis `RuleBasedStateMachine` over both backends (rules: commit,
      racing commit, revert, read old revision, reopen, gc) with invariants: the head equals the
      model's head; every revision loads; `fsck` is ok. **Step 2:** Run under the `ci` profile and
      fix or report every finding. **Step 3:** Commit `test(show): stateful storage tests on both backends`.

### Task K3: crash injection

**Files:** Create `tests/property/test_storage_crash.py`.

- [ ] **Step 1:** Write `test_crash_at_every_write_step_leaves_old_or_new_head`: a shim stops the
      commit after the n-th OS call for every n (with and without hard links); reopen; assert the
      head is the parent or the new revision, every revision loads, `fsck` lists only unreachable
      files and stale temps, and `gc(confirm=True)` leaves it ok.
- [ ] **Step 2:** Run; fix or report. **Step 3:** Commit `test(show): crash injection for file commits`.

### Task K4: HypoFuzz in the nightly run

**Files:** Modify `.github/workflows/nightly.yml`, `pyproject.toml` and `uv.lock` (`hypofuzz` as a pinned dev dependency).

- [ ] **Step 1:** Add `hypofuzz` with `uv add --dev` (pinned in `uv.lock`) and a job running `hypothesis fuzz` over `tests/property/test_storage_*.py`
      with a fixed budget of examples, pinned action SHAs and `zizmor` clean. **Step 2:**
      `npx --no-install prettier --check .github/workflows/nightly.yml`. **Step 3:** Commit
      `ci: fuzz the storage machines nightly`.

---

## Lane X: exit (serial, after every lane merges)

### Task X1: the Phase 3 scenario

**Files:** Create `tests/scenario/test_phase3_scenario.py`.

- [ ] **Step 1:** Write `test_phase3_scenario_at_1000_revisions`: upgrade the v0.4.0 fixture;
      run 1,000 revisions mixing designer saves, three role agents on one set merged by `merge`,
      proposals proposed, previewed, accepted, rejected and withdrawn, a user-font text form, an
      archive round trip and a `revert`; assert after every 100 revisions that `head()` is under
      1 ms, `fsck` is ok, `events_since` replays to head, and `revalidate` equals `validate_show`.
- [ ] **Step 2:** Run; fix or report. **Step 3:** Commit `test: the Phase 3 storage scenario`.

### Task X2: exit checks

**Files:** Create `tests/bench/test_storage_budgets.py`.

- [ ] **Step 1:** Bench each exit criterion below on both backends at 300 x 60, and the cap rows
      at 300 x 1,000 and 5,000 x 60; record the table in the PR body with machine and load.
- [ ] **Step 2:** Confirm `Ledger: 88 open` (default) and `Ledger: 1 open` (`nox -s slow`); run
      every nox session including `bench`, `wheel`, `core` and the Docker build.
- [ ] **Step 3:** Commit `test(bench): storage budgets for layout 2`.

### Task X3: release v0.5.0

**Files:** `docs/releases/v0.5.0.md`, `docs/HANDOFF.md`, `docs/e8s-api-reference.md`,
`CHANGELOG.md` (generated).

- [ ] **Step 1:** Write the release notes: copy repositories before upgrading, `upgrade_repository`,
      the removed names, the new item and merge API, measured numbers.
- [ ] **Step 2:** `uv run nox -s changelog`; Prettier; commit `docs: release notes for v0.5.0`.
- [ ] **Step 3:** Ask the maintainer to approve the push, the merge and the `v0.5.0` tag.

---

## Exit criteria

Measured on the maintainer's Mac with the X2 bench and ADR 0015's script; "today" is
`main` at `a6ef3ca`.

| Criterion                                                            | Today               | Exit                       |
| -------------------------------------------------------------------- | ------------------- | -------------------------- |
| `head()`, file backend, 1,000 revisions                              | 62.8 ms             | < 1 ms                     |
| `events_since` over 200 revisions, 300 x 60, memory / files          | 6.35 s / 11.70 s    | < 1 s each                 |
| One-spot save, 300 x 60, memory / files (also at 1,000 revisions)    | 191 ms / 195 ms     | < 50 ms each               |
| Incremental validation of a one-spot edit, 300 x 60                  | none (2.01 s whole) | < 50 ms                    |
| One-spot save at 300 x 1,000 and at 5,000 x 60 (X2 superlinear)      | 5.5 s / 3.9 s       | < 100 ms each              |
| `events_since` over 2 revisions at 300 x 1,000 (X2)                  | 4.0 s               | < 100 ms                   |
| Incremental validation of a one-spot edit at 300 x 1,000, 5,000 x 60 | none                | < 100 ms each              |
| `load_show` at 1,000 revisions against 1 revision, files             | grows with history  | within 10%                 |
| Conformance kit, stateful and crash-injection tests                  | none                | pass on both backends      |
| v0.4.0 repository fixture                                            | none                | committed, loads, upgrades |
| Three role agents editing one set                                    | one overlap key     | merge with no conflict     |
| Default ledger line                                                  | `Ledger: 129 open`  | `Ledger: 88 open`          |

Whole-show `validate_show` (2.01 s at 300 x 60; 29.6 s at 300 x 1,000 and 125.6 s at 5,000 x 60
in X2's loaded run) is outside this phase; X2 records it unchanged.

## Counts

- Lanes: 10 (L0, A, B, C, D, E, F, M, K, X). Tasks: 41 (L0 6, A 8, B 4, C 4, D 2, E 4, F 4, M 2,
  K 4, X 3).
- Ledger: 41 IDs flipped, 1 to Phase 5.
