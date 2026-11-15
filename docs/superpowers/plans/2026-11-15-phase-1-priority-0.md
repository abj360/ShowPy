# Phase 1: Priority 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the thirteen Priority 0 defects of the audit (47 ledger tests; ADRs 0003 and 0017
put 7 more out of scope and delete them) plus 63 companion ledger tests that share their root
causes, so that no Priority 0 test is open, no parser hangs or
leaks a non-`ShowPyError`, and a 300-spot x 60-set show passes the scenario checks. Ends with
release v0.3.0.

**Architecture:** One serial lane, L0, first lands the boundary primitives that cause most leaks
(pydantic errors mapped to `SchemaError`, `allow_inf_nan=False`, a revalidating
`FrozenModel.replace`, size caps, picklable errors, the ID checker, the canonical JSON encoder and
the hash built on it, a v0.2.0 repository fixture and the fuzz harness). Seven lanes then run in
parallel worktrees, each
owning disjoint files: A show and storage; B engine; C geometry; D core and cast;
E export and publishing; F sheets; G forms and matching. Every fix flips its ledger test
in the same commit. A final serial lane checks the exit criteria and prepares the release.

**Tech stack:** Python 3.12 to 3.14, pydantic v2, numpy, shapely, uv 0.12.9, nox, ruff, mypy
`--strict`, pyright, pytest + Hypothesis + pytest-timeout + pytest-xdist, git-cliff, Prettier
3.6.2, GitHub Actions.

**Spec:** `docs/upgrade-plan.md` sections 3, 4.1 to 4.3, 5.2.3, 9.1 to 9.7 and 10 ("Phase 1").
Decisions: ADRs 0003, 0004, 0005, 0006, 0014 and 0017, accepted by the maintainer on 2026-11-15.
Master plan: `/home/uapb-ai/.claude/plans/look-at-the-upgrade-nifty-kay.md` ("Phase 1").

## Global constraints

- **ADR gate.** No task that names an ADR in its **Gate** line starts before that ADR is
  `Accepted`. If the maintainer overrules an ADR, its tasks are re-planned first.
- **Ledger flip procedure** (every task that fixes a ledger ID):
  1. Run the ledger test with `--runxfail` and confirm it fails for the audited reason.
  2. Implement the fix.
  3. Remove the test's `@pytest.mark.defect(...)` and `@pytest.mark.xfail(...)` markers, or its
     `@defect(...)` helper decorator, in the same commit as the fix. Never edit the assertion to
     make it pass; only the tightening that an accepted ADR names (ADR 0005 item 6) or an API
     rename (a guessed keyword, a moved call) is allowed, and the commit body says which.
  4. Run `set -o pipefail; uv run pytest -m defect -q 2>&1 | tail -3` and confirm
     no `XPASS(strict)` anywhere. A test that flips in another lane's file is unmarked by the lane
     whose code flipped it (marker lines only).
- **Gates before every commit:** `set -o pipefail; uv run nox -s lint types tests hashseeds`.
  Lanes A and D also run `uv run nox -s slow`. Coverage floor stays 96% (branch).
- Every new behavior starts with a failing `test_regression_<what>` test; never weaken a check,
  threshold or default to turn a test green (project rule 4). A newly exposed invariant
  violation is fixed or reported to the controller, never xfailed without a new ledger ID.
- `uv` only. Before any `npm`/`npx` command:
  `PATH=/home/uapb-ai/.nvm/versions/node/v22.23.5/bin:$PATH`.
- Library modules under `showpy/` have no shebang; scripts and tests keep it. Module docstrings
  open `<filename> --- <role>` with a `Contains:` index; public functions get a verb-first
  docstring with a `>>>` example importing `showpy.examples`.
- New public names go in `showpy/_api_table.py`; regenerate with
  `uv run python scripts/gen_public_api.py` and refresh the snapshot with
  `SHOWPY_UPDATE_API=1 uv run pytest tests/unit/test_api_snapshot.py`. A changed public name is
  backticked in the commit subject or a `BREAKING CHANGE:` footer, so git-cliff lists it.
- Changed stored models regenerate `showpy/schemas/` with
  `SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`. Phase 1 adds optional
  fields only and keeps the schema name `showpy.show.v1`; format v2 is Phase 2 (ADR 0007).
- Shared generated files (`showpy/__init__.py`, `tests/fixtures/api_snapshot.json`,
  `showpy/schemas/*.json`, `CHANGELOG.md`) are never hand-merged: after a rebase, rerun their
  generators.
- Commits: Conventional Commits, one logical change each, no `Co-Authored-By` or "Generated
  with" trailers, under the identity `Oluwatoyosi Peter Abolaji`. Never push without the
  maintainer's approval.
- Execution: subagent-driven. One worktree per lane (`Agent` with `isolation: "worktree"`), a
  reviewer subagent gates every task, lanes A and E add `differential-review`, the controller
  merges each lane with `git merge --no-ff` and reruns every gate on `main`.

## Review focus

1. **Repositories written by v0.1.x and v0.2.0.** Expected: they load with no data loss, stored
   revision IDs are never rewritten, the first save writes exactly one `normalize` revision and a
   second save none, and consumer-held hashes (`Set.source_hash`, unsaved-show publication
   revisions) mismatch exactly once. Pinned by L0.9's fixture and A6's tests.
2. **Consumers pinned to v0.2.0.** Agent writes are no longer refused, canvas events need an
   explicit `force`, `replace_sheet` and `SheetReplaceReport` are gone, and `place()`'s return
   type, `frozenset` tags and selections, `content_hash` values and `publish(join_token=)` all
   change. Expected: removed or re-typed names appear in the API snapshot diff and the
   changelog. Pinned by A4's tests, F1 and X3's release notes.
3. **Agents addressing sets by display ID after a renumber.** Expected: `/sets/2/set.py` in a
   workspace means display ID 2 before and after storage moves to set keys. Pinned by A5.
4. **Windows and case-insensitive file systems.** Expected: hard-link and name-length failures
   raise `StorageError` with code `NO_HARDLINKS` or `NAME_TOO_LONG`, never `OSError`. Pinned by
   A7 (Windows-marked tests run in the CI matrix).
5. **Cost of the new checks at 300 spots x 60 sets.** `replace()` revalidation and the commit
   validation decode on every show-document commit. Expected: `nox -s bench` M tier stays within
   20% of the baseline, and a one-spot edit stays under 300 ms (198 ms today plus about 70 ms
   decode, ADR 0003). Pinned by L0.3 and A3 bench steps.

---

## Sequencing

```
L0.1 → L0.2 → … → L0.11          (serial, one worktree)
        │
        ├── A  show/storage        gate: ADR 0003, 0004, 0006, 0014
        ├── B  engine              no ADR gate (adds an ADR 0002 addendum)
        ├── C  geometry            no ADR gate
        ├── D  core/cast           gate: ADR 0005 (D3), ADR 0006 (D2)
        ├── E  export/publish      no ADR gate
        ├── F  sheets              gate: ADR 0017
        └── G  forms/match         no ADR gate
                    │
                    ▼
        X1 → X2 → X3               (serial: scenario, exit checks, release v0.3.0)
```

- L0.7 needs ADR 0006, and L0.8 and L0.10 need ADR 0014; every other L0 task can start at once.
- Lanes start when L0 is merged. Merge order is free, with two preferences: C before G (C
  unmarks UP-9.6-42 to UP-9.6-44 in G's test file) and D before E (D unmarks UP-4.3-01 in E's
  test file). Either way a rebase conflict is marker lines only.
- **Text on field, stage 1** (`docs/superpowers/plans/2026-11-14-text-on-field-stage-1.md`)
  starts after lanes C and G merge, because it builds on `geo` spacing and the form functions
  both lanes change. Its fonts ADR is renumbered 0016, after this program's reserved range
  0003 to 0015.

## File map (lane ownership)

| Lane | Owns (source)                                                                                                                                                                                                                                                          | Owns (tests)                                                                                                                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L0   | `showpy/core/{types,errors,validation,caps,ids,canonical}.py`, `content_hash` in `showpy/core/util.py` and its revision call, every `model_copy(update=)` call site, `noxfile.py`, `pyproject.toml`, `.github/workflows/nightly.yml`, `scripts/make_legacy_fixture.py` | `tests/unit/core/test_{types,errors,validation,caps,ids,canonical}.py`, `tests/unit/test_library_rules.py`, `tests/fixtures/repos/`, `tests/fuzz/conftest.py`, `tests/helpers.py`, `tests/unit/test_helpers.py` |
| A    | `showpy/show/*`, `showpy/choreo/models.py` (`Set.key` only), new `showpy/show/integrity.py`                                                                                                                                                                            | `tests/unit/show/*`, `tests/fuzz/test_fuzz_show.py`                                                                                                                                                             |
| B    | `showpy/choreo/{engine,engine_compile,engine_facing,engine_models,routes,paths}.py`, `docs/adr/0002-path-engine.md` (addendum)                                                                                                                                         | `tests/unit/choreo/test_{engine,engine_compile,routes,paths}.py`                                                                                                                                                |
| C    | `showpy/geo/*`, `showpy/choreo/form_fns.py`                                                                                                                                                                                                                            | `tests/unit/geo/*`, `tests/fuzz/test_fuzz_svg.py`                                                                                                                                                               |
| D    | `showpy/core/util.py`, `showpy/cast/*`                                                                                                                                                                                                                                 | `tests/unit/core/test_util.py`, `tests/unit/cast/*`, `tests/fuzz/test_fuzz_select.py`                                                                                                                           |
| E    | `showpy/export/*`                                                                                                                                                                                                                                                      | `tests/unit/export/*`, `tests/fuzz/test_fuzz_import.py`                                                                                                                                                         |
| F    | `showpy/sheet/*`                                                                                                                                                                                                                                                       | `tests/unit/sheet/*`, `tests/fuzz/test_fuzz_coords.py`                                                                                                                                                          |
| G    | `showpy/choreo/{form_place,form_ops,form_fit,form_layout,forms,match,match_cost,match_solve,match_ops}.py`                                                                                                                                                             | `tests/unit/choreo/test_{form_place,form_ops,form_fit,forms,match,match_ops,match_models}.py`                                                                                                                   |
| X    | `tests/scenario/`, `tests/unit/test_ledger.py`, `CHANGELOG.md`, `docs/HANDOFF.md`, `docs/showpy-api-reference.md`                                                                                                                                                      | —                                                                                                                                                                                                               |

## Ledger coverage

| Lane | Priority 0 IDs (47)                                                                        | Companion IDs (63)                                                                             |
| ---- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| L0   | —                                                                                          | UP-4.1-01; UP-9.3-04 to -12, -29, -30, -37, -62, -100 to -104, -110, -115; UP-9.6-22, -23 (23) |
| A    | UP-9.7-01 to UP-9.7-07 (7)                                                                 | UP-9.7-17, -18, -19, -20, -22, -23, -24, -25, -30, -31 (10)                                    |
| B    | UP-9.2-01, -02, -21 (3)                                                                    | —                                                                                              |
| C    | UP-9.1-01, -02, -03, -04, -06, -07, -08, -09; UP-9.6-44 (9)                                | UP-9.1-05, -43, -58, -59; UP-9.6-42, -43 (6)                                                   |
| D    | UP-4.3-01; UP-9.3-01, -22, -23, -53, -54, -55, -77, -78, -88, -89, -90, -91, -92, -94 (15) | UP-9.3-02, -03, -17, -18, -73, -74, -75, -79, -80, -93 (10)                                    |
| E    | UP-9.5-01, -02, -07, -27, -53 (5)                                                          | UP-9.5-03, -04, -05, -08, -28, -35 (6)                                                         |
| F    | UP-9.4-02 (1)                                                                              | UP-9.4-03, -04, -05, -06, -15, -16 (6)                                                         |
| G    | UP-9.6-01, -02, -04, -05, -06, -07, -10 (7)                                                | UP-9.6-08, -09 (2)                                                                             |

L0.2's ten NaN and infinity IDs (UP-4.1-01, UP-9.3-37, -62, -100 to -104, -110, -115) were measured
on 2026-11-15 by setting `allow_inf_nan=False` on `main` at `e48e4b6`; L0.2 re-measures after
L0.1 and unmarks exactly what flips. Fuzz tasks (A8, C4, D2, E5, F3) list the leak IDs known to
fall inside their targets; any extra leak the fuzzers find gets a new ledger ID and a fix in the
same lane.

Deleted as out of scope (not fixed): UP-9.7-08 to UP-9.7-13 by ADR 0003 (permissions belong to
the host), and UP-9.4-01, UP-9.4-07, UP-9.4-43 by ADR 0017 (no sheet replacement). Each deletion
lands in the task that removes the code, with the ADR named in the commit body.

---

## Lane L0: boundary primitives (serial)

Branch `fix/p1-l0-boundary-primitives`.

### Task L0.1: pydantic errors become `SchemaError` at every model boundary

**Files:** Modify `showpy/core/errors.py`, `showpy/core/types.py`, `showpy/core/validation.py`.
Test `tests/unit/core/test_errors.py`, `tests/unit/core/test_validation.py`.

**Interfaces:**

- Produces `showpy.core.errors.schema_error_from(exc, *, model: str) -> SchemaError`, where
  `exc` is a `pydantic.ValidationError`: message `"<model> rejects <n> field(s): <loc>: <msg>; …"`
  with at most 5 errors listed,
  `context={"model": model, "errors": [{"loc": "a.b", "msg": …, "input": …}]}`,
  each echoed input truncated to 80 characters, hint `"fix the fields named in context"`.
- Produces `FrozenModel.__init__`, `FrozenModel.model_validate` and
  `FrozenModel.model_validate_json` raising `SchemaError` (chained `from` the pydantic error)
  instead of `pydantic.ValidationError`.
- Produces
  `showpy.core.validation.validate_or_schema_error[M: BaseModel](model: type[M], data: object) -> M`
  for non-`FrozenModel` models and `TypeAdapter`-free call sites.

- [ ] **Step 1:** Write `test_regression_model_construction_raises_schema_error` (a `Placement`
      with `x="abc"` raises `SchemaError` whose `context["errors"][0]["loc"] == "x"`), and
      `test_regression_schema_error_truncates_echoed_input` (a 10,000-character input appears
      as at most 80 characters in the message and context).
- [ ] **Step 2:** Run them; expect FAIL with `pydantic_core.ValidationError`.
- [ ] **Step 3:** Implement the three interfaces. Frozen-instance assignment errors stay
      pydantic's (Phase 2).
- [ ] **Step 4:** Run the full suite. Tests that asserted `pytest.raises(PydanticValidationError)`
      on construction (15 sites today, mostly `tests/unit/time/`) now expect `SchemaError`;
      update those, and tuples like `REJECTED = (ShowPyError, PydanticValidationError)` keep
      working. Apply the ledger flip procedure to anything that flips (candidates: UP-9.1-52,
      UP-9.6-59) and list the IDs in the commit body.
- [ ] **Step 5:** Commit `fix(core): raise SchemaError from every model constructor`.

### Task L0.2: `allow_inf_nan=False` on `FrozenModel`

**Files:** Modify `showpy/core/types.py:34-37`. Test `tests/unit/core/test_types.py`.

- [ ] **Step 1:** Run `uv run pytest tests/unit/core/test_types.py --runxfail -k non_finite`;
      expect UP-4.1-01 to fail.
- [ ] **Step 2:** Set `model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)`.
- [ ] **Step 3:** Audit fields that need infinity:
      `grep -rnE 'inf\b|math\.inf|float\("inf"\)' showpy`. Measured 2026-11-15: every hit is a
      function argument or a numpy cost array
      (`show/sections.py` clip bounds, `choreo/match*.py`, `time/clock.py`), none a model field,
      and the full suite has no failure beyond the expected flips. Record the audit result in
      the commit body. If a field does need infinity, it gets `Field(allow_inf_nan=True)` plus an
      after-validator that still rejects NaN, and its own test.
- [ ] **Step 4:** Ledger flip procedure. Expected flips: UP-4.1-01, UP-9.3-37, UP-9.3-62,
      UP-9.3-100 to UP-9.3-104, UP-9.3-110, UP-9.3-115.
- [ ] **Step 5:** Commit `fix(core): reject NaN and infinity in every model`.

### Task L0.3: revalidating `FrozenModel.replace()` and a ban on `model_copy(update=)`

**Files:** Modify `showpy/core/types.py`; every one of the 160 `model_copy(update=` call sites in
37 modules under `showpy/` (`grep -rln 'model_copy(update' showpy`). Create
`tests/unit/test_library_rules.py`. Modify `tests/property/` purity test and the UP-4.4-02 test
in `tests/unit/time/test_timeline.py`.

**Interfaces:**

- Produces `FrozenModel.replace(self, **changes: object) -> Self`: validates
  `{field: getattr(self, field) for field in type(self).model_fields} | changes` through
  `model_validate`, so model validators run and errors are `SchemaError`; an unknown field name
  raises `SchemaError`. Nested model instances are not revalidated (pydantic's default), so the
  cost is one top-level validation.
- `CurveBase.model_copy` (cache-safe, revalidating) stays; it is not an `update=` call site.

- [ ] **Step 1:** Write `test_regression_replace_revalidates` (`Set(...).replace(counts=-4)`
      raises `SchemaError`) and `test_library_never_calls_model_copy_with_update`: an `ast` walk
      over `showpy/**/*.py` that fails, naming file and line, on any `Call` whose function is an
      attribute `model_copy` with an `update` keyword.
- [ ] **Step 2:** Run them; expect FAIL (no `replace`; 160 offending sites).
- [ ] **Step 3:** Add `replace`, then rewrite every call site to `.replace(**…)`, package by
      package, running `uv run pytest tests/unit/<package>` after each. A test that newly fails
      exposes an edit path that produced an invalid model: fix it in this task when the fix is
      under about 20 lines, otherwise stop and report it to the controller.
- [ ] **Step 4:** Deferred minor from Phase 0: give the purity property test a negative control
      (a deliberately mutating function must make it fail), and rewrite UP-4.4-02 so it asserts
      the real defect (an edit function returning a model its own validators reject) instead of
      the `cast()` proxy.
- [ ] **Step 5:** Ledger flip procedure. Expected flips: UP-9.6-22, UP-9.6-23 (plus UP-9.1-50 if
      it flips).
- [ ] **Step 6:** Run `uv run nox -s bench`; expect no M-tier regression over 20%. If one appears,
      profile with `caveman:investigate-first` before changing anything.
- [ ] **Step 7:** Commit `refactor(core): edit models through revalidating replace()`.

### Task L0.4: constrained number types and `require_finite`

**Files:** Modify `showpy/core/types.py`, `showpy/core/validation.py`. Test
`tests/unit/core/test_types.py`, `tests/unit/core/test_validation.py`.

**Interfaces:**

- Produces in `showpy.core.types`: `Finite`, `NonNegFinite` (`ge=0`), `PositiveFinite`
  (`gt=0`), `NonNegCount`, `NonNegSeconds`, `PositiveMeters`, `CountsPerMinute` (positive), each
  `type X = Annotated[float, Field(...)]`. The existing aliases `Meters`, `Seconds`, `Count`,
  `Degrees` become finite-only; no range is added to them in Phase 1 (that changes accepted
  inputs and is Phase 2).
- Produces `showpy.core.validation.require_finite`:

  ```python
  def require_finite(
      value: float,
      *,
      name: str,
      low: float | None = None,
      low_open: bool = False,
      high: float | None = None,
  ) -> float: ...
  ```

  It returns `float(value)` or raises `SchemaError` with
  `context={"name": name, "value": value, "low": low, "high": high}`.
  Bools are rejected. Lanes C, D, E and F use it for function parameters.

- [ ] **Step 1:** Tests: a model field typed `PositiveMeters` rejects `0.0`, `-1.0`, `inf` and
      `nan` with `SchemaError`; `require_finite(0.0, name="tol", low=0.0, low_open=True)` raises
      and names `tol`; `require_finite(True, name="n")` raises.
- [ ] **Step 2:** Run; expect FAIL (names undefined).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full suite; expect PASS with no other change.
- [ ] **Step 5:** Commit `feat(core): add constrained number types and require_finite`.

### Task L0.5: output-size caps

**Files:** Create `showpy/core/caps.py`, `tests/unit/core/test_caps.py`.

**Interfaces:** Produces these constants (resource-safety bounds, not designer limits; every
value is at least 100x what the XL tier of 600 spots x 120 sets needs) and one check:

| Constant           | Value      | Bounds                                                   |
| ------------------ | ---------- | -------------------------------------------------------- |
| `MAX_POINTS`       | 1,000,000  | points one call returns (`fill`, spacing, `to_polyline`) |
| `MAX_TABLE`        | 1,000,000  | samples in a curve lookup table (spiral turns)           |
| `MAX_GRID_CELLS`   | 4,000,000  | cells of a raster (`density_map`)                        |
| `MAX_RANGE`        | 10,000     | IDs one range expands to; parts of `split(k)`            |
| `MAX_DEPTH`        | 64         | nesting of a selection expression                        |
| `MAX_TEXT_CHARS`   | 1,000,000  | characters of one parser input (selection, SVG `d`)      |
| `MAX_IMAGE_PIXELS` | 40,000,000 | pixels of one raster image (QR code, render)             |
| `MAX_VIDEO_PIXELS` | 8,294,400  | pixels of one video frame (3840 x 2160)                  |

```python
def require_size(
    requested: int, cap: int, *, what: str, error: type[ShowPyError] = SchemaError
) -> None: ...
```

It raises `error` with the message `"<what> would need <requested>, over the cap of <cap>"`,
`context={"what": what, "requested": requested, "cap": cap}` and a hint naming a coarser setting.

- [ ] **Step 1:** Test that `require_size(1_000_001, MAX_POINTS, what="points")` raises
      `SchemaError` with those context values and that `error=SpacingError` is honored.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(core): add output-size caps`.

### Task L0.6: picklable and deep-copyable `ShowPyError`

**Files:** Modify `showpy/core/errors.py`. Test `tests/unit/core/test_errors.py`.

- [ ] **Step 1:** Run UP-9.3-29 and UP-9.3-30 with `--runxfail`; expect `TypeError` for the
      missing keyword-only `hint`.
- [ ] **Step 2:** Add
      `ShowPyError.__reduce__(self) -> tuple[Callable[..., ShowPyError], tuple[object, ...]]`
      returning a module-level `_rebuild(cls, message, hint, context, code)`;
      `copy.deepcopy` uses it too.
- [ ] **Step 3:** Ledger flip procedure: UP-9.3-29, UP-9.3-30.
- [ ] **Step 4:** Commit `fix(core): make every ShowPyError picklable`.

### Task L0.7: one ID checker (ADR 0006)

**Gate:** ADR 0006 accepted.

**Files:** Modify `showpy/core/ids.py`. Test `tests/unit/core/test_ids.py`.

**Interfaces:**

- Produces `ID_PATTERN = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$")`,
  `ID_MAX_LENGTH = 64`,
  and `RESERVED_IDS`, a frozenset of `all`, `con`, `prn`, `aux`, `nul`, `com1` to `com9` and
  `lpt1` to `lpt9`.
- Produces `check_id(text: str, *, kind: str) -> str`: NFC-normalizes, then raises
  `SchemaError` (context `kind`, `text` truncated to 80, `position` of the first bad character)
  for a pattern miss, `..`, or a reserved word (case-insensitive); returns the normalized ID.
- Produces `find_case_clashes(ids: Iterable[str]) -> list[tuple[str, str]]`: pairs of IDs equal
  under `casefold()`, sorted.

- [ ] **Step 1:** Parametrized tests: accepts `1`, `12A`, `2.5`, `T1`, `x_y`; rejects the empty
      string, `-1`, `a-b`, `.a`, `a.`, `a..b`, `ALL`, `Con`, `T*`, `group:1`, `T٣` and a
      65-character ID; `find_case_clashes(["T1", "t1", "B2"]) == [("T1", "t1")]`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(core): add the one ID pattern of ADR 0006`.

### Task L0.8: canonical values and the RFC 8785 encoder (ADR 0014)

**Gate:** ADR 0014 accepted.

**Files:** Create `showpy/core/canonical.py`, `tests/unit/core/test_canonical.py`. Modify
`pyproject.toml` (dev group: `rfc8785`, pinned `==` to the version uv resolves), `uv.lock`.

**Interfaces:**

- Produces
  `type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]`.
- Produces `canonical_value(obj: object) -> JsonValue` with exactly the rules of ADR 0014
  decision 1, including the `{"$showpy:map": [[key, value], …]}` tag for non-string keys and
  `SchemaError` for NaN, infinity, lone surrogates, `$showpy:` user keys and unsupported types.
- Produces `jcs(obj: object) -> bytes`: RFC 8785 serialization of `canonical_value(obj)`.

- [ ] **Step 1:** Tests from RFC 8785 Appendix B (IEEE-754 bits to expected text):
      `0x0000000000000001` → `5e-324`, `0x8000000000000000` → `0`,
      `0x4340000000000000` → `9007199254740992`, `0x4430000000000000` →
      `295147905179352830000`, `0x444b1ae4d6e2ef50` → `1e+21`, `0x44b52d02c7e14af6` → `1e+23`,
      `0x3eb0c6f7a0b5ed8d` → `0.000001`, `0x41b3de4355555555` → `333333333.3333333`; plus
      `jcs({"b": 1.0, "a": [True, None]}) == b'{"a":[true,null],"b":1}'`,
      `jcs({1: "a"}) != jcs({"1": "a"})`, `jcs({1: "a", "b": 2}) == jcs({"b": 2, 1: "a"})`,
      `jcs(-0.0) == b"0"`, and `SchemaError` for `nan`, `"\ud800"` and `{"$showpy:map": 1}`.
- [ ] **Step 2:** A Hypothesis differential test: for JSON-model values drawn from finite floats,
      ints, unicode text without surrogates, lists and string-keyed dicts,
      `jcs(v) == rfc8785.dumps(v)`.
- [ ] **Step 3:** Run; expect FAIL. **Step 4:** Implement (ECMAScript `Number::toString` for
      floats, UTF-16 code-unit key order). **Step 5:** Run; expect PASS.
- [ ] **Step 6:** Commit `feat(core): add canonical values and an RFC 8785 encoder`.

### Task L0.9: a committed v0.2.0 repository fixture

**Files:** Create `scripts/make_legacy_fixture.py`, `tests/fixtures/repos/v0.2.0/` (a
`FileRepository` root plus `expected.json`), `tests/unit/show/test_legacy_repository.py`.

**Interfaces:** Produces the fixture A6 and L0.10 test against. `expected.json` holds
`{"show_id", "head", "revisions": [ids…], "legacy_content_hash", "show": <Show JSON>}`.

- [ ] **Step 1:** Write the script against the v0.2.0 API: `seeded_show(seed=7, spots=24, sets=6)`,
      plus a placement at `x=-0.0`, a spot with tags `{"lead", "horn"}`, a named
      selection of four spots, a `/sets/2/set.py` script, then three revisions (designer save,
      agent path edit, designer pin), all stamped `at=2026-11-13T12:00:00+00:00`.
- [ ] **Step 2:** Generate from the tag, outside the repository:

  ```bash
  git worktree add /tmp/showpy-v0.2.0 v0.2.0
  cd /tmp/showpy-v0.2.0
  uv run python <repo>/scripts/make_legacy_fixture.py \
      --out <repo>/tests/fixtures/repos/v0.2.0
  cd <repo> && git worktree remove /tmp/showpy-v0.2.0
  ```

  Check the fixture stays under 1,024 KB
  (the `check-added-large-files` hook).

- [ ] **Step 3:** `test_v020_repository_loads_without_loss`: opening the fixture with today's
      code lists the same revision IDs as `expected.json` and decodes a head show equal to
      `Show.model_validate(expected["show"])`. Run; expect PASS today (it guards later lanes).
- [ ] **Step 4:** Commit `test(show): commit a repository written by v0.2.0`.

### Task L0.10: `content_hash` over domain-tagged JCS (ADR 0014)

**Gate:** ADR 0014 accepted. **Fixes:** UP-9.3-04 to UP-9.3-12. **Files:** Modify
`showpy/core/util.py` (`content_hash`), `showpy/show/repositories.py` (revision IDs pass
`domain="showpy.revision"`). L0 owns both files until the lanes start, so no lane edits them for
this.

**Interfaces:** `content_hash(obj: object, *, domain: str = "showpy.content") -> str`, exactly as
ADR 0014 decision 3; 64 lowercase hex characters.

- [ ] **Step 1:** Run the nine with `--runxfail`.
- [ ] **Step 2:** Implement on `canonical.jcs`. Test that a revision committed on L0.9's fixture
      after the change gets a new-scheme ID while every stored ID is unchanged, and that
      `content_hash(two_set_show())` is identical under hash seeds 0, 1 and 2
      (`digests_under_hash_seeds`).
- [ ] **Step 3:** Golden fixtures that store hashes: regenerate with
      `SHOWPY_UPDATE_GOLDEN=1 uv run pytest -m golden`, review the diff (only hash values may
      change), and name the files in the commit body.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(core)!: hash canonical JSON with domain tags` with a `BREAKING CHANGE:` footer
      ("every `content_hash` value changes once; stored revision IDs are kept").

### Task L0.11: fuzz harness and nightly fuzz job

**Files:** Modify `tests/helpers.py`, `pyproject.toml` (marker `fuzz`, `addopts` adds
`and not fuzz`), `noxfile.py`, `.github/workflows/nightly.yml`. Create `tests/fuzz/conftest.py`.

**Interfaces:**

- Produces `tests.helpers.only_showpy_errors(call: Callable[[], object]) -> None`: runs `call`
  under `fails_after(5)`; any exception that is not a `ShowPyError` fails the test, naming its
  type and the input. Lanes A, C, D, E and F add `tests/fuzz/test_fuzz_<area>.py` targets with
  `@pytest.mark.fuzz` and Hypothesis strategies over text and bytes.
- Produces nox session `fuzz`: `pytest -m fuzz` with
  `HYPOTHESIS_PROFILE=nightly` unless set; not a default session.

- [ ] **Step 1:** `tests/unit/test_helpers.py::test_only_showpy_errors_rejects_a_builtin_error`
      (a call raising `KeyError` fails) and `…_accepts_a_showpy_error`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement the helper, marker, session and a
      nightly step `uv run nox -s fuzz`. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Deferred minor from Phase 0: in the `tests` session pass `--cov-fail-under`
      after `*session.posargs`, so local arguments cannot lower the floor. Check:
      `uv run nox -s tests -- --cov-fail-under=0 -k test_helpers` must fail on the 96% floor.
- [ ] **Step 6:** Commit `test: add the fuzz harness and a nightly fuzz session`.

**L0 exit:** merge `fix/p1-l0-boundary-primitives` with `--no-ff`; all gates green on `main`.

---

## Lane A: show layer and storage

Branch `fix/p1-a-show-storage`. **Gate:** ADRs 0003, 0004, 0006 and 0014 accepted. Skills
on top of the loop: `superpowers:systematic-debugging` for races, stateful property tests,
`differential-review` and `security-review` before the lane PR.

### Task A1: the memory backend commits atomically

**Fixes:** UP-9.7-02. **Files:** Modify `showpy/show/repositories.py` (`_MemoryBackend`).

- [ ] **Step 1:** Run UP-9.7-02 with `--runxfail` in a 20-iteration shell loop; expect at least
      one run with two winners.
- [ ] **Step 2:** Give `_MemoryBackend` a `threading.Lock` held across the head check and the
      append in `commit`, and around `log`, `tree` and `blob` reads.
- [ ] **Step 3:** Run it 200 times in a loop; expect 0 failures. Ledger flip procedure.
- [ ] **Step 4:** Commit `fix(show): commit to the memory repository under a lock`.

### Task A2: the file repository refuses tampered links and trees

**Fixes:** UP-9.7-03, UP-9.7-04, UP-9.7-05, UP-9.7-22, UP-9.7-23.
**Files:** Modify `showpy/show/file_repository.py`.

**Interfaces:** Produces `REVISION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")` and
`StorageError` codes `CORRUPT` (bad ID in `next/`, cycle, bad digest in a tree, missing or
truncated object or tree) with `context={"show", "path"}`.

- [ ] **Step 1:** Run the five tests with `--runxfail`; expect traversal reads, a hang (cycle,
      caught by `fails_after`), `FileNotFoundError` and `JSONDecodeError`.
- [ ] **Step 2:** Validate every ID read from `next/` and every digest read from a tree against
      `REVISION_ID_PATTERN`; track visited IDs in `log` and raise `CORRUPT` on a repeat; map
      `FileNotFoundError`, `IsADirectoryError` and `json.JSONDecodeError` in `_record`, `tree`
      and `blob` to `CORRUPT`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(show): refuse tampered links, cycles and trees in the file repository`.

### Task A3: every commit is validated, whoever makes it

**Fixes:** UP-9.7-06, UP-9.7-07. **Files:** Create `showpy/show/integrity.py`; modify
`showpy/show/repositories.py` (`transaction`). Test `tests/unit/show/test_integrity.py`.

**Interfaces:**

- Produces `KNOWN_ROOTS`: `/show/`, `/time/`, `/sets/`, `/transitions/`, `/canvas/`,
  `/config/`, `/publish/`, `/renders/`, `/exports/`, `/lib/drafts/`.
- Produces
  `require_valid_tree(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> Show | None`:
  raises `StorageError` code `BAD_PATH` for a path outside `KNOWN_ROOTS`; when any
  show document changed, decodes `after` (raising `SchemaError` on failure) and returns it.
- `DocumentRepository.transaction` calls it before `backend.commit`, for every actor alike.

- [ ] **Step 1:** Run both tests with `--runxfail`; expect the garbage commit and the unknown
      root to land.
- [ ] **Step 2:** Implement. Decode only when a show document changes.
- [ ] **Step 3:** Ledger flip procedure.
- [ ] **Step 4:** Bench: time a one-spot edit on `seeded_show(seed=3, spots=300, sets=60)`
      through a workspace; expect under 300 ms (Review focus 5); record the number in the commit
      body.
- [ ] **Step 5:** Commit `fix(show): validate every commit before it lands`.

### Task A4: remove the half-built permission checks (ADR 0003)

**Deletes:** UP-9.7-08, UP-9.7-09, UP-9.7-10, UP-9.7-11, UP-9.7-12, UP-9.7-13 (out of scope by ADR
0003). **Files:** Modify `showpy/show/storage.py` (drop `DESIGNER_PRINCIPALS`,
`_require_allowed`, `_require_pins_kept`), `showpy/show/files.py` (drop `DESIGNER_PATHS`),
`showpy/show/collab.py`, `showpy/_api_table.py` if a removed name is public; the tests that
assert an agent refusal.

**Interfaces:**

- `actor=`, `open_workspace(repo, show_id, principal)`, `Workspace.principal` and
  `Revision.actor` stay as recorded labels; no code in `showpy/` branches on their value
  (checked by `grep -rn '"agent"' showpy/` returning only docstrings and examples).
- `collab.py`: `_require_designer` and the agent refusal in `accept_proposal` are removed;
  applying a canvas event takes keyword-only `force: bool = False` from the caller instead of
  `force=event.actor != "agent"`.

- [ ] **Step 1:** Write `test_regression_agent_write_is_recorded_not_judged`: an `actor="agent"`
      workspace commit that writes `/config/limits.yaml` lands, and its revision's `actor` is
      `agent`. Write `test_canvas_event_needs_explicit_force`: a designer event that moves a
      pinned spot raises `LockedError` without `force=True` and lands with it. Run; expect FAIL.
- [ ] **Step 2:** Implement. Delete the six ledger tests and every other test that asserts an
      agent refusal, listing each deleted test name in the commit body.
- [ ] **Step 3:** Regenerate the API table and snapshot if a public name changed.
- [ ] **Step 4:** Commit `feat(show)!: leave permissions to the host` with a `BREAKING CHANGE:`
      footer: agent writes are no longer refused; canvas events need an explicit `force`.

### Task A5: stable set keys (ADR 0004, ADR 0006)

**Fixes:** UP-9.7-01. **Files:** Modify `showpy/choreo/models.py` (`Set.key`),
`showpy/show/documents.py`, `showpy/show/files.py`, `showpy/show/storage.py` (path mapping),
`showpy/show/manage.py` (key assignment), `showpy/show/repositories.py` (script paths).

**Interfaces:**

- `Set.key: str | None = None`; an after-validator sets it to `id` when `None` and runs
  `check_id(key, kind="set key")`; serialized only when it differs from `id`.
- `add_set` and `insert_subset` assign `key = id`, or the first free `<id>.<n>` (n from 2) when
  that key is live; `renumber` keeps keys.
- `encode_show` writes `/sets/<key>/…`, `/transitions/<key a>-<key b>/paths.json` and the set
  order as keys; `decode_show` reads them back, defaulting a missing `key` to the folder name.
- `Workspace` maps `/sets/<id>/…` and `/transitions/<id a>-<id b>/…` to stored paths and back
  in `read`, `ls`, `write` and `commit`.

- [ ] **Step 1:** Run UP-9.7-01 with `--runxfail`; expect the script under the wrong set. Change
      its two final reads from `repo.read("s", …)` to the workspace (`ws.refresh()`, then
      `ws.read("/sets/2/set.py")`), the API rename ADR 0004 names; keep its assertions.
- [ ] **Step 2:** New tests: encoding a never-renumbered show gives byte-identical documents to
      v0.2.0 (compare against L0.9's fixture head); inserting `2` after a renumber that left key
      `2` live gives key `2.2`.
- [ ] **Step 3:** Implement. **Step 4:** Ledger flip procedure; regenerate schemas.
- [ ] **Step 5:** Commit `fix(show): key set folders by a stable set key`.

### Task A6: canonical documents and one `normalize` revision (ADR 0014)

**Fixes:** UP-9.7-30, UP-9.7-31. **Files:** Modify `showpy/show/documents.py` (`dump_json` uses
`canonical_value`), `showpy/show/models.py` (`RevisionKind` gains `"normalize"`),
`showpy/show/storage.py` (`save_show`), `showpy/show/collab.py` (`events_since` skips
`normalize`). Test `tests/unit/show/test_legacy_repository.py`.

**Interfaces:** `save_show` re-encodes the decoded base tree; when any show document differs from
its stored bytes, it first commits those documents as `kind="normalize"`, actor `system`,
message `normalize stored documents`, then the edit on top.

- [ ] **Step 1:** Tests on L0.9's fixture: after `save_show` of the unchanged head there is
      exactly one new revision, of kind `normalize`, and `diff_show(before, after) == {}`; a
      second save adds no revision; every original revision ID is still listed;
      `events_since(<pre-upgrade head>)` returns no events.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Ledger flip procedure
      (UP-9.7-30, UP-9.7-31). **Step 5:** Commit
      `fix(show): write canonical documents and normalize old ones once`.

### Task A7: storage failures map to `StorageError` codes

**Fixes:** UP-9.7-17, UP-9.7-24. **Files:** Modify `showpy/show/file_repository.py`. Test
`tests/unit/show/test_file_repository.py` (new tests marked
`skipif(sys.platform != "win32")` where they need Windows behavior).

**Interfaces:** `StorageError` codes `NO_HARDLINKS` (`os.link` raising `PermissionError`,
`OSError(EPERM, EXDEV, ENOTSUP)`), `NAME_TOO_LONG` (`ENAMETOOLONG`), `IO` (any other `OSError`),
each with `context={"show", "path", "errno"}`.

- [ ] **Step 1:** Run both tests with `--runxfail`; expect `PermissionError` and `OSError`.
- [ ] **Step 2:** Implement the mapping around every filesystem call in `_FileBackend`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(show): map file-system failures to StorageError codes`.

### Task A8: fuzz the show decoders and workspace paths

**Fixes (expected):** UP-9.7-18, UP-9.7-19, UP-9.7-20, UP-9.7-25.
**Files:** Create `tests/fuzz/test_fuzz_show.py`; modify `showpy/show/{documents,files,storage}.py`.

- [ ] **Step 1:** Targets, each through `only_showpy_errors`: `decode_show` over dicts of known
      paths to arbitrary bytes and to JSON of the wrong shape; `split_path` and
      `Workspace.read` over arbitrary text; `staged_changes` over arbitrary bytes for every known
      path.
- [ ] **Step 2:** Run `uv run nox -s fuzz -- tests/fuzz/test_fuzz_show.py`; record each leak.
- [ ] **Step 3:** Fix each leak at its source (wrong-shaped JSON raises `SchemaError` naming the
      path; `"sets": "12"` is rejected, not split into characters). Ledger flip procedure for
      the IDs above; any leak without an ID gets `UP-9.7-93` onward and its fix here.
- [ ] **Step 4:** Commit `fix(show): keep decoder and path failures inside ShowPyError`.

**Lane A exit:** `differential-review` and `security-review` on the lane diff; findings fixed;
PR body (What / How tested / Checklist); merge after approval.

---

## Lane B: motion engine

Branch `fix/p1-b-engine-segments`. No ADR gate. Skills: `mattpocock-skills:research` before B1,
property tests at the 0.02 m boundary.

### Task B1: segments keep their 0.02 m guarantee

**Fixes:** UP-9.2-01, UP-9.2-02. **Files:** Create `docs/research/segment-chord-bounds.md`;
modify `showpy/choreo/engine.py`, `showpy/choreo/engine_compile.py`,
`showpy/choreo/engine_models.py`, `docs/adr/0002-path-engine.md` (dated addendum). Test
`tests/unit/choreo/test_engine.py`, `tests/property/` engine segment property.

**Interfaces:** `PathEngine.segments(tid, tol)` and `entity_segments` keep their signatures. A
segment that cannot meet `tol` before the depth cap raises `PathError` code `SEGMENT_BOUND` with
`context={"entity", "tid", "tol", "error"}`.

- [ ] **Step 1:** Research note: chord error `h²/8 · max|p''|` per piece; `p''` from curvature,
      speed and easing acceleration, closed-form for the built-in curve kinds and easings.
- [ ] **Step 2:** Run both tests with `--runxfail`; expect 1.0 m error on the 4-cycle weave and
      4.0 m reported travel for a 16.75 m path.
- [ ] **Step 3:** Seed time edges with every curve break mapped through the inverse time map
      (polyline vertices, spline knots, arc ends, easing breaks); split until the bound holds;
      for opaque custom motions, probe at seeded jittered shares (`seeded_rng` keyed on entity
      and transition) at 9 points per segment; raise at the depth cap. STRAIGHT with linear
      easing yields exactly two stations.
- [ ] **Step 4:** Extend the segment property test to custom periodic motions (weave with 1 to
      8 cycles): every probe within `tol` of the true path.
- [ ] **Step 5:** Ledger flip procedure. Add the ADR 0002 addendum, dated, stating that
      decision 5 now holds for custom motions and how.
- [ ] **Step 6:** Commit `fix(choreo): guarantee the segment chord bound for every motion`.

### Task B2: `two_part` respects locked rows

**Fixes:** UP-9.2-21. **Files:** Modify `showpy/choreo/routes.py`.

- [ ] **Step 1:** Run UP-9.2-21 with `--runxfail`; expect a locked row overwritten.
- [ ] **Step 2:** Add keyword-only `force: bool = False` to `two_part`; refuse locked rows with
      `LockedError` through `locks.require_unlocked` unless forced.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(choreo): refuse locked rows in two_part unless forced`.

---

## Lane C: geometry

Branch `fix/p1-c-geo-bounds`. No ADR gate. Skills: `caveman:surgical-patch`, grammar fuzzing of
the SVG parser.

### Task C1: `to_polyline` validates its tolerance

**Fixes:** UP-9.1-01, UP-9.1-02. **Files:** Modify `showpy/geo/curve_base.py`,
`showpy/geo/curves.py` (overrides).

- [ ] **Step 1:** Run both with `--runxfail`; expect a hang caught by `fails_after`.
- [ ] **Step 2:** Every `to_polyline(tol)` calls
      `require_finite(tol, name="tol", low=0.0, low_open=True)` first, and the output is capped with
      `require_size(…, MAX_POINTS, what="polyline points", error=GeometryError)`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(geo): reject non-positive and NaN polyline tolerances`.

### Task C2: spacing validates gaps and caps its output

**Fixes:** UP-9.1-03, UP-9.1-07, UP-9.1-08, UP-9.1-58, UP-9.1-59, UP-9.6-42, UP-9.6-43,
UP-9.6-44. **Files:** Modify `showpy/geo/spacing.py`.

- [ ] **Step 1:** Run the eight with `--runxfail`; expect a hang, 4M-point allocations and
      `ZeroDivisionError`.
- [ ] **Step 2:** `scatter` requires `min_gap > 0` and `n >= 0`; `fill` and `space_by_interval`
      require `interval > 0`; each predicts its output size before allocating and calls
      `require_size(…, MAX_POINTS, error=SpacingError)`.
- [ ] **Step 3:** Ledger flip procedure; UP-9.6-42 to UP-9.6-44 live in lane G's
      `tests/unit/choreo/test_forms.py` (marker lines only).
- [ ] **Step 4:** Commit `fix(geo): validate spacing gaps and cap spacing output`.

### Task C3: spiral turns and density grids are bounded

**Fixes:** UP-9.1-04, UP-9.1-05, UP-9.1-06. **Files:** Modify `showpy/geo/curves.py`
(`SpiralCurve`), `showpy/geo/analysis.py` (`density_map`), `showpy/choreo/form_fns.py` (the
spiral form function passes turns through unchanged).

- [ ] **Step 1:** Run the three with `--runxfail` (UP-9.1-04 and -06 under
      `capped_address_space`).
- [ ] **Step 2:** `SpiralCurve.turns` is `PositiveFinite`, and its table size is checked against
      `MAX_TABLE` in a model validator; `density_map` checks `cell` with
      `require_finite(…, low=0.0, low_open=True)` and the cell count against `MAX_GRID_CELLS`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(geo): bound spiral tables and density grids`.

### Task C4: linear SVG arc flags and a fuzzed path parser

**Fixes:** UP-9.1-09, UP-9.1-43 (expected from fuzzing). **Files:** Modify
`showpy/geo/svg.py`; create `tests/fuzz/test_fuzz_svg.py`.

- [ ] **Step 1:** Run UP-9.1-09 with `--runxfail`; expect 80,000 arcs to exceed its budget.
- [ ] **Step 2:** Replace the arc-flag unpacking at `svg.py:375` with a single forward scan over
      the token stream (no re-slicing); check `len(d)` against `MAX_TEXT_CHARS`.
- [ ] **Step 3:** Fuzz target: `parse_path` over a Hypothesis grammar of commands, numbers,
      packed flags and junk, through `only_showpy_errors`. Fix each leak (`M0 0 L1 Z` raises
      `SchemaError`).
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(geo): parse SVG arc flags in linear time`.

---

## Lane D: core and cast

Branch `fix/p1-d-core-cast`. Skills: the hash-seed CI job (`nox -s hashseeds`) on every task.

### Task D1: no hash-seed order in stored data or selections

**Fixes:** UP-4.3-01, UP-9.3-01, UP-9.3-02, UP-9.3-03, UP-9.3-53, UP-9.3-54, UP-9.3-55,
UP-9.3-78. **Files:** Modify `showpy/cast/models.py` (`Spot.tags`, `Cast.selections` become
ordered, deduplicated tuples), `showpy/cast/spots.py`, `showpy/cast/select.py`,
`showpy/core/util.py` (`natural_sort` key total, `seeded_rng`).

**Interfaces:**

- `Spot.tags: tuple[str, ...]` and `Cast.selections: dict[str, tuple[SpotId, ...]]`; an
  after-validator deduplicates keeping first occurrence; the JSON form stays an array, so v0.2
  documents decode unchanged.
- `natural_sort` key is `(prefix, number, original text)`, so ties such as `T02` and `T2` sort
  the same under every seed.
- `select(source, expr)` with a `set` or `frozenset` input sorts it with `natural_sort` first.
- `seeded_rng(seed: int) -> np.random.Generator` returns `Generator(PCG64(seed))` and raises
  `SchemaError` for `None`, bools and negative seeds.

- [ ] **Step 1:** Run the eight with `--runxfail` (they use `outputs_under_hash_seeds`).
- [ ] **Step 2:** Implement. Update `test_define_selection_stores_resolved_ids`, which asserts
      the old `frozenset` representation, to the tuple `("T1", "T4")`.
- [ ] **Step 3:** Ledger flip procedure (UP-4.3-01 lives in lane E's
      `tests/unit/export/test_data.py`, marker lines only). Regenerate schemas.
- [ ] **Step 4:** Commit `fix(cast)!: store tags and selections as ordered tuples` with a
      `BREAKING CHANGE:` footer naming `Spot.tags` and `Cast.selections`.

### Task D2: linear ID splitting, bounded ranges, a fuzzed selection grammar (ADR 0006)

**Gate:** ADR 0006 accepted. **Fixes:** UP-9.3-22, UP-9.3-23, UP-9.3-75, UP-9.3-77; expected from
fuzzing UP-9.3-17, UP-9.3-18, UP-9.3-73, UP-9.3-74. **Files:** Modify `showpy/core/util.py`
(`ID_PARTS`, `parse_range`, `natural_sort`), `showpy/cast/select.py`; create
`tests/fuzz/test_fuzz_select.py`.

**Interfaces:** `split_id(text: str) -> tuple[str, int | None]` in `showpy/core/util.py` using
`re.search(r"\d+\Z", text, re.ASCII)`, shared by ranges, `T*` and `label_prefix`.
`parse_range` checks `hi - lo + 1 <= MAX_RANGE` before building any string; `split(sel, k)`
requires `1 <= k <= max(len(sel), 1)` and `k <= MAX_RANGE`; the parser tracks depth against
`MAX_DEPTH` and input length against `MAX_TEXT_CHARS`.

- [ ] **Step 1:** Run the four fixed IDs with `--runxfail`.
- [ ] **Step 2:** Implement. **Step 3:** Fuzz targets: `select` over a Hypothesis grammar of
      words, ranges, operators, parentheses (to depth 500) and junk; `parse_range`,
      `natural_sort`, `parse_ref` and `parse_tid` over arbitrary text; all through
      `only_showpy_errors`. Fix each leak.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(cast): split IDs linearly and bound ranges, splits and nesting`.

### Task D3: cast lock policy (ADR 0005)

**Gate:** ADR 0005 accepted. **Fixes:** UP-9.3-79, UP-9.3-80, UP-9.3-88 to UP-9.3-94.
**Files:** Modify `showpy/cast/spots.py`, `showpy/cast/rewrite.py`.

**Interfaces:**

- `add_spot`, `add_spots_along`, `fill_spots` and `duplicate_spots` gain keyword-only
  `force: bool = False`; `delete_spots` keeps `force`.
- A private check in `showpy/cast/spots.py`:

  ```python
  def _require_pictures_kept(
      before: Show, after: Show, rename: Mapping[SpotId, SpotId], force: bool
  ) -> None: ...
  ```

  It rewrites `before` through `rename` with `rewrite_spot_ids`, then calls
  `locks.require_unlocked`; it also refuses a new on-field placement in a pinned set (ADR 0005
  items 1 to 3).

- `rewrite_spot_ids` rewrites `Show.pins` and sheet zones; `delete_spots(force=True)` drops pins
  on deleted spots.

- [ ] **Step 1:** Run the nine with `--runxfail`.
- [ ] **Step 2:** Tighten UP-9.3-92 and UP-9.3-93 as ADR 0005 item 6 says (drop the
      `except LockedError: return` branch).
- [ ] **Step 3:** New test: adding a spot that first appears on-field in set 3 while set 1 is
      pinned succeeds without `force`.
- [ ] **Step 4:** Implement. **Step 5:** Ledger flip procedure. **Step 6:** Commit
      `fix(cast): protect pinned positions in cast edits; let renames through`.

---

## Lane E: export and publishing

Branch `fix/p1-e-export-publish`. No ADR gate. Skills: `insecure-defaults:audit` on
`showpy/export/`, `differential-review` before the lane PR.

### Task E1: join tokens and safe base URLs

**Fixes:** UP-9.5-01, UP-9.5-04, UP-9.5-05. **Files:** Modify `showpy/export/publish.py`,
`showpy/export/models.py`.

**Interfaces:**

```python
def publish(
    show: Show,
    publication_id: str,
    base_url: str,
    at: datetime,
    previous: Publication | None = None,
    *,
    join_token: str,
) -> Publication: ...
```

`join_token` is required: at least 22 characters of `[A-Za-z0-9_-]` (128 bits when random
base64url). It appears only in `link` (`<base_url>/<publication id>?t=<token>`) and is stored as
`Publication.join_token_sha256`. `base_url` must parse with `urllib.parse` as `https` with a host
and no query or fragment, else `SchemaError`.

- [ ] **Step 1:** Run the three with `--runxfail`; drop the `# type: ignore[call-arg]` in
      UP-9.5-01 once the keyword exists.
- [ ] **Step 2:** Implement; add `verify_join_token(pub: Publication, token: str) -> bool` using
      `hmac.compare_digest`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(export)!: require a join token and an https base URL to publish`.

### Task E2: contacts stay out of publications

**Fixes:** UP-9.5-02, UP-9.5-03. **Files:** Modify `showpy/export/publish.py`.

**Interfaces:** `claim_spot` stores the performer with `contact=None`; `claims()`,
`who_is_on()` and `notify_changes()` return contact-free `Performer`s. Hosts keep contacts in
their own roster keyed by `Performer.id` (release note).

- [ ] **Step 1:** Run both with `--runxfail`. **Step 2:** Implement. **Step 3:** Ledger flip
      procedure. **Step 4:** Commit `fix(export): keep performer contacts out of publications`.

### Task E3: CSV cells cannot run as formulas

**Fixes:** UP-9.5-27, UP-9.5-28. **Files:** Modify `showpy/export/data.py`.

**Interfaces:** `to_csv(obj: Tabular, *, neutralize: bool = True) -> str` prefixes `'` to every
text cell starting with `=`, `+`, `-`, `@`, tab or carriage return (OWASP); numeric cells are
untouched; `neutralize=False` is the machine-pipeline opt-out.

- [ ] **Step 1:** Run both with `--runxfail`. **Step 2:** Implement. **Step 3:** Ledger flip
      procedure. **Step 4:** Commit `fix(export): neutralize formula cells in CSV output`.

### Task E4: QR codes and video canvases are capped

**Fixes:** UP-9.5-07, UP-9.5-08, UP-9.5-53. **Files:** Modify `showpy/export/publish.py`
(`qr_code`), `showpy/export/video.py` (`video_canvas`).

- [ ] **Step 1:** Run the three with `--runxfail`.
- [ ] **Step 2:** `qr_code` checks `size * size` against `MAX_IMAGE_PIXELS` before rendering and
      wraps `segno.DataOverflowError` in `SchemaError`; `video_canvas` checks width x height
      against `MAX_VIDEO_PIXELS`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(export): cap QR and video image sizes`.

### Task E5: fuzz the importers

**Fixes (expected):** UP-9.5-35. **Files:** Create `tests/fuzz/test_fuzz_import.py`; modify
`showpy/export/data.py`, `showpy/export/positions.py`.

- [ ] **Step 1:** Targets: `import_json` over arbitrary JSON values and bytes;
      `import_positions` over arbitrary CSV text; through `only_showpy_errors`.
- [ ] **Step 2:** Run `uv run nox -s fuzz -- tests/fuzz/test_fuzz_import.py`; fix each leak.
- [ ] **Step 3:** Ledger flip procedure (new leaks get `UP-9.5-58` onward). **Step 4:** Commit
      `fix(export): keep importer failures inside ShowPyError`.

---

## Lane F: sheets

Branch `fix/p1-f-sheets`. **Gate:** ADR 0017 accepted.

### Task F1: remove sheet replacement (ADR 0017)

**Deletes:** UP-9.4-01, UP-9.4-07, UP-9.4-43 (out of scope by ADR 0017). **Files:** Modify
`showpy/sheet/replace.py` (keep `similarity_fit` only), `showpy/sheet/models.py` (drop
`SheetReplaceReport`), `showpy/_api_table.py`, `CLAUDE.md` (layout line), `docs/HANDOFF.md`
(module map and deviations); delete the `replace_sheet` tests in
`tests/unit/sheet/test_replace.py` and `tests/property/test_purity_properties.py`.

- [ ] **Step 1:** Remove `replace_sheet`, `SheetMapping`, `RESTRICTED_ZONE_KINDS`,
      `SheetReplaceReport` and the private helpers only they use; update the module docstring's
      `Contains:` block.
- [ ] **Step 2:** Delete the three ledger tests and every other `replace_sheet` test, listing
      each deleted test name in the commit body. Keep every `similarity_fit` test.
- [ ] **Step 3:** Regenerate `showpy/__init__.py` and the API snapshot; run every gate.
- [ ] **Step 4:** Commit `feat(sheet)!: remove replace_sheet; a show keeps its sheet` with a
      `BREAKING CHANGE:` footer naming `replace_sheet` and `SheetReplaceReport`.

### Task F2: similarity fits refuse reflections and degenerate input

**Fixes:** UP-9.4-02 to UP-9.4-06. **Files:** Modify `showpy/sheet/replace.py`
(`similarity_fit`).

- [ ] **Step 1:** Run the five with `--runxfail`.
- [ ] **Step 2:** `similarity_fit` raises `GeometryError` when the fitted determinant is
      negative (code `REFLECTION`), when either point set's spread is under 1e-9 m, on fewer than
      2 matched rows (before any numpy mean, so no `RuntimeWarning`), and `SchemaError` on NaN or
      a shape other than `(N, 2)`.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(sheet): refuse mirrored and degenerate landmark fits`.

### Task F3: fuzz the coordinate parser

**Fixes (expected):** UP-9.4-15, UP-9.4-16. **Files:** Create `tests/fuzz/test_fuzz_coords.py`;
modify `showpy/sheet/coords.py`.

- [ ] **Step 1:** Targets: `from_coords` over arbitrary text; `to_coords` over arbitrary floats
      and every `CoordinateSystem` built from Hypothesis (including `precision=0`).
- [ ] **Step 2:** Fix each leak (`decimal` errors become `SchemaError`).
- [ ] **Step 3:** Ledger flip procedure (new leaks get `UP-9.4-47` onward). **Step 4:** Commit
      `fix(sheet): keep coordinate parsing failures inside ShowPyError`.

---

## Lane G: forms and matching

Branch `fix/p1-g-forms-match`. No ADR gate. Skills: property tests for form and placement
consistency. G must not change the stored `Form` schema (open slots stay derived; typed open
slots are Phase 2).

### Task G1: `apply_match` keeps forms and placements consistent

**Fixes:** UP-9.6-01, UP-9.6-02. **Files:** Modify `showpy/choreo/match_ops.py`,
`showpy/choreo/match.py` (`MatchResult` gains `slots: dict[SpotId, tuple[FormId, int]]`).

- [ ] **Step 1:** Run both with `--runxfail`.
- [ ] **Step 2:** `apply_match` writes x, y, `order` and facing from the target slot's pose and
      updates `Form.members` exactly as `swap` does; formed spots that are pinned need `force`.
- [ ] **Step 3:** Property test: for random matches onto line, arc and block forms,
      `rebuild(apply_match(…))` moves nobody.
- [ ] **Step 4:** Ledger flip procedure. **Step 5:** Commit
      `fix(choreo): apply matches to form members, order and facing`.

### Task G2: `place` returns the set and its filled forms

**Fixes:** UP-9.6-04, UP-9.6-05, UP-9.6-06, UP-9.6-07. **Files:** Modify
`showpy/choreo/form_place.py` and every caller of `place` (`showpy/choreo/`,
`tests/fixtures/generators.py`, `tests/strategies/`).

**Interfaces:** `place(show, set_id, forms, …) -> tuple[Set, list[Form]]`. Placing a set clears
form references to forms no longer placed; `filled_forms` derives open slots from placement
`order`s, accepts a pinned member's slot left open, and accepts matched spots in the nearest
open slots.

- [ ] **Step 1:** Run the four with `--runxfail`. **Step 2:** Implement; update every caller.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(choreo)!: return filled forms from place()` with a `BREAKING CHANGE:` footer naming
      `place`.

### Task G3: `rebuild` honors `force` and keeps unmoved rows

**Fixes:** UP-9.6-10, UP-9.6-08, UP-9.6-09. **Files:** Modify `showpy/choreo/form_place.py`.

- [ ] **Step 1:** Run the three with `--runxfail`.
- [ ] **Step 2:** `rebuild(force=True)` moves pinned members; path rows of members that did not
      move are kept; a locked row raises only when its member moves.
- [ ] **Step 3:** Ledger flip procedure. **Step 4:** Commit
      `fix(choreo): let rebuild(force=True) move pinned members, keep unmoved rows`.

---

## Lane X: exit (serial, after every lane merges)

Branch `chore/p1-exit`.

### Task X1: the 300-spot x 60-set scenario

**Files:** Create `tests/scenario/test_phase1_scenario.py` (marked `slow`).

- [ ] **Step 1:** Build `seeded_show(seed=11, spots=300, sets=60)`, then: pin 20 spots and set
      12; insert a subset and renumber; regroup and relabel through the cast edits; save through
      a workspace and run 10 more edits through a second workspace; publish with a join token
      and claim 30 spots.
- [ ] **Step 2:** Assert the section 5.2.3 checks: `validate_show(show).ok`; the stored documents
      validate against `showpy/schemas/showpy.show.v1.json`; positions continuous within 1e-6 m
      at every set boundary; every `segments()` piece within 0.02 m of `position()` at 9 probes;
      JSON and document round trips equal; `events_since` replay equals head; undo then redo is
      identity; two runs produce byte-identical `encode_show` and `content_hash`; no input
      mutated (compare `model_dump_json` before and after every call).
- [ ] **Step 3:** Run `uv run nox -s slow`; expect PASS. **Step 4:** Commit
      `test: add the phase 1 300-spot scenario`.

### Task X2: exit checks

**Files:** Modify `tests/unit/test_ledger.py`.

- [ ] **Step 1:** Add `test_no_priority_zero_defect_remains`: collects every test whose docstring
      cites `P0 #` and asserts none carries a `defect` marker. Run; expect PASS.
- [ ] **Step 2:** Run `uv run nox -s fuzz` with `HYPOTHESIS_PROFILE=nightly`; expect no hang and
      no non-`ShowPyError`. Trigger the nightly workflow once on the branch and check it is
      green.
- [ ] **Step 3:** Run `security-review` and `differential-review` over `v0.2.0..HEAD` for
      `showpy/show/` and `showpy/export/`; fix or ledger every finding as Priority 0.
- [ ] **Step 4:** Run every gate: `uv run nox`, `uv run nox -s bench`, `uv run nox -s wheel`,
      `docker compose -f docker/docker-compose.yml up --build`; expect all green.
- [ ] **Step 5:** Commit `test: guard that no priority 0 defect stays open`.

### Task X3: release v0.3.0

**Files:** Modify `CHANGELOG.md` (generated), `docs/HANDOFF.md`, `docs/showpy-api-reference.md`
(the changed names only).

- [ ] **Step 1:** `uv run nox -s changelog`; check every breaking name is listed: agent writes no
      longer refused, canvas-event `force=`, `replace_sheet` and `SheetReplaceReport` (removed),
      `Set.key`, `RevisionKind "normalize"`, `Spot.tags`,
      `Cast.selections`, `content_hash` (values and `domain=`), `FrozenModel.replace`, `force=`
      on four cast edits and `two_part`, `publish(join_token=)`, `place()`'s return type,
      `similarity_fit` reflections, `to_csv(neutralize=)`, the caps.
- [ ] **Step 2:** Write the release notes used for the tag message and the GitHub release body,
      with a "Migrating from v0.2" section: permissions in the host, a new show instead of
      `replace_sheet`, contacts in the host's roster, hashes stale once, set keys after a renumber, `place()` returning a tuple.
- [ ] **Step 3:** Update `docs/HANDOFF.md`: ledger count, Phase 1 done, Phase 2 next.
- [ ] **Step 4:** Commit `chore(release): prepare v0.3.0`. The controller asks the maintainer to
      approve the push, the merge and the `v0.3.0` tag.

## Phase 1 exit criteria

- Every Priority 0 ledger test passes; `test_no_priority_zero_defect_remains` guards it, and the
  63 companion IDs above are unmarked.
- `nox -s fuzz` under the nightly profile finds no hang and no non-`ShowPyError` in any parser
  (selection grammar, SVG paths, references, transition IDs, ranges, coordinate wording,
  positions CSV, JSON import, show documents, workspace paths).
- The 300-spot x 60-set scenario passes the section 5.2.3 checks.
- `security-review` and `differential-review` are clean on `showpy/show/` and `showpy/export/`.
- CI matrix (3 OS x 3 Python), hash seeds, slow, wheel and Docker jobs are green; `v0.3.0` is
  tagged after the maintainer approves the push.

## Deferred minors from Phase 0, placed

| Minor                                                                                             | Where                                   |
| ------------------------------------------------------------------------------------------------- | --------------------------------------- |
| Purity property test has no negative control                                                      | L0.3 step 4                             |
| UP-4.4-02 is a weak `cast()` proxy for the `model_copy` defect                                    | L0.3 step 4                             |
| `nox -s tests -- <args>` can lower `--cov-fail-under`                                             | L0.11 step 5                            |
| Guessed APIs in ledger tests (`join_token`)                                                       | E1 step 1                               |
| Text-on-field plan: build after lanes C and G; fonts ADR is 0016                                  | Sequencing                              |
| Ledger plugin rejects non-numeric sections (`UP-CI-nn`)                                           | stays deferred: Phase 1 IDs are numeric |
| Bench baseline is machine-specific; `min_gaps` 7x over budget                                     | stays deferred: Phase 4                 |
| `lowest-direct` job weak while bounds equal lock versions                                         | stays deferred: Phase 6                 |
| Guessed APIs `reassign(show=)`, `publish(renames=)`, `Publication.version`, `motions=`; UP-9.5-57 | stay deferred: their tests are Phase 2  |
