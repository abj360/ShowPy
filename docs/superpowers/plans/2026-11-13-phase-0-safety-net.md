# Phase 0: Safety Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare everything the upgrade needs, with no change to library behavior. That means
one task runner, a CI matrix, real packaging, test generators and benchmarks, every audited
defect encoded as a strict-xfail test, an API snapshot, and stronger lint and type gates. Ends
with release v0.2.0.

**Architecture:** `noxfile.py` becomes the single definition of every gate, and CI, pre-commit
and Docker all call it. A pytest marker `defect("UP-…")` combined with `xfail(strict=True)` turns
the audit into a burn-down ledger: each later fix flips exactly one test. A generated
`stage/__init__.py` and a signature snapshot make every public API change visible in review.

**Tech stack:** uv 0.12.9, nox, hatchling + hatch-vcs, ruff, mypy `--strict`, pyright,
pytest + pytest-cov, pytest-timeout, pytest-benchmark, pytest-xdist, Hypothesis, deptry,
git-cliff, Prettier 3.6.2 via npm, GitHub Actions.

**Spec:** `docs/upgrade-plan.md` (sections 3, 5, 6, 7, 9 and 10 "Phase 0"). Master plan:
`/home/uapb-ai/.claude/plans/look-at-the-upgrade-nifty-kay.md`.

## Global constraints

- The library's behavior does not change in this phase. A real bug found along the way becomes a
  ledger xfail test, not a fix. The one exception is the 13 pyright errors (Task 7), each fixed
  with a `test_regression_` test.
- `uv` only. Before any `npm` command, put Node on the path:
  `PATH=/home/uapb-ai/.nvm/versions/node/v22.23.5/bin:$PATH`.
- Pin every new dependency exactly in `[dependency-groups].dev` in the same commit that adds it.
- Runtime dependencies move from `==` pins to `>=` lower bounds, set to today's locked versions.
  `uv.lock` keeps the exact versions.
- Shebangs: remove them from `stage/**/*.py` (94 files). Keep the 82 in `tests/**` and give
  `tests/**` a per-file ignore for `EXE001` (decision of 2026-11-13: library modules only).
- Commits follow Conventional Commits with no AI trailers, under the identity
  `Oluwatoyosi Peter Abolaji`. Never push without the user's approval.
- Run every gate with `set -o pipefail`.

## Review focus

1. **Docker build without `.git`.** hatch-vcs cannot read a tag inside the image. Expected
   behavior: the Dockerfile passes `SETUPTOOLS_SCM_PRETEND_VERSION` as a build argument, and the
   build never fails on a missing version. Test in Task 3.
2. **Hang tests on Windows.** `pytest-timeout` uses its thread method there and aborts the whole
   run. Expected behavior: ledger hang tests use `timeout(5, method="signal")` plus
   `skipif(sys.platform == "win32")`. Test in Task 5.
3. **Generated `stage/__init__.py` drifting from its table.** Expected behavior: a test
   regenerates the file in memory and fails on any difference, naming the command that rewrites
   it. Test in Task 6.
4. **Benchmarks flaking on hosted runners.** Expected behavior: PRs compare the `min` timing
   against a baseline from the same runner class, the threshold is 20%, and only the M tier
   blocks. Test in Task 4.
5. **xfail tests that pass by accident.** If a ledger test passes because its assertion is weak,
   strict xfail turns it into an XPASS failure in the matrix. Expected behavior: every ledger test
   asserts the exact error class and code, or the exact value. A passing assertion like "does not
   raise" is never allowed. Enforced in Task 5's reviewer gate.

---

## File map

| File                                                                  | Responsibility                                                   |
| --------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `noxfile.py`                                                          | every gate as a nox session                                      |
| `package.json`, `package-lock.json`                                   | pin Prettier for `npm ci`                                        |
| `.pre-commit-config.yaml`                                             | hooks call nox; pytest on `pre-push`                             |
| `.github/workflows/{ci,nightly,codeql,scorecard,zizmor}.yml`          | CI matrix and hardening                                          |
| `.github/dependabot.yml`, `CODEOWNERS`, `SECURITY.md`, templates      | repository hygiene                                               |
| `pyproject.toml`                                                      | ShowPy metadata, version ranges, hatch-vcs, tool config          |
| `stage/schemas/stage.show.v1.json`                                    | schema moved inside the package                                  |
| `docker/Dockerfile`, `.dockerignore`                                  | multi-stage build, non-root, tests the wheel                     |
| `tests/helpers.py`                                                    | shared builders (`make_set`, `make_session`, `pin`, `with_rows`) |
| `tests/conftest.py`                                                   | Hypothesis profiles, the `defect` marker hook                    |
| `tests/strategies/*.py`                                               | Hypothesis strategies built through public builders              |
| `tests/fixtures/generators.py`                                        | `seeded_show(seed, spots, sets)`                                 |
| `tests/bench/test_budgets.py`, `benchmarks/`                          | performance budgets and baseline                                 |
| `tests/unit/**/test_*.py`                                             | ledger `test_regression_*` tests, in the mirrored files          |
| `scripts/gen_public_api.py`, `stage/_api_table.py`                    | one table → generated `stage/__init__.py`                        |
| `tests/unit/test_api_snapshot.py`, `tests/fixtures/api_snapshot.json` | API signature snapshot                                           |
| `CHANGELOG.md`, `cliff.toml`                                          | changelog                                                        |

---

### Task 1: one task runner

**Files:**

- Create: `noxfile.py`, `package.json`, `package-lock.json`
- Modify: `pyproject.toml` (dev group), `.pre-commit-config.yaml`, `docker/docker-compose.yml`,
  `.github/workflows/ci.yml` (steps call nox)

**Interfaces:**

- Produces these nox sessions, which every later task extends:
  - `lint`
  - `types`
  - `tests`, parametrized `hashseed` ∈ {0, 1, 2}
  - `audit`
  - `wheel` (a stub until Task 3)
  - `bench` (a stub until Task 4)
  - `docs`

  `uv run nox` with no arguments runs `lint`, `types`, `tests` and `audit`.

- [ ] **Step 1:** Add dev dependencies, each pinned with `==`: `nox`, `pyright`, `pytest-cov`,
      `pytest-timeout`, `pytest-benchmark`, `pytest-xdist`, `deptry`. Run `uv lock`.
- [ ] **Step 2:** Write `noxfile.py`. Every session runs under `uv run` with
      `venv_backend="none"`. `lint` runs `ruff format --check .`, `ruff check .` and
      `npx prettier --check "**/*.{md,yml,yaml,json}"` from `node_modules`. `tests` sets
      `PYTHONHASHSEED` from its parameter. Give the module the `Contains:` docstring; it is a runnable
      script, so it keeps a shebang.
- [ ] **Step 3:** Run `npm install --save-exact --save-dev prettier@3.6.2`, then commit
      `package.json` and `package-lock.json`. Add `node_modules/` to `.gitignore` and
      `.prettierignore`.
- [ ] **Step 4:** Point every hook in `.pre-commit-config.yaml` at `uv run nox -s <session>`.
      Move `tests` to `stages: [pre-push]`. Add the `pre-commit-hooks` repo, pinned by rev, with
      check-yaml, check-toml, end-of-file-fixer, trailing-whitespace,
      check-added-large-files (`--maxkb=1024`, excluding the guide PDF) and check-merge-conflict.
      Add a local `uv lock --check` hook and `typos`.
- [ ] **Step 5:** Change the compose command to `["uv", "run", "nox"]`.
- [ ] **Step 6:** Run `set -o pipefail; uv run nox`. Expected: every session passes, with the
      same 1,468 tests run under each of the 3 seeds. A hash-seed failure is not fixed here: record
      it as ledger UP-9.3-01 and keep it xfail in Task 5.
- [ ] **Step 7:** Commit `chore(tooling): run every gate through one nox runner`.

### Task 2: CI matrix and workflow hardening

**Files:**

- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/{nightly,codeql,scorecard,zizmor}.yml`, `.github/dependabot.yml`,
  `.github/CODEOWNERS`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/bug.yml`,
  `SECURITY.md`

- [ ] **Step 1:** Rewrite `ci.yml`:
  - A `check` job on a matrix of `os: [ubuntu-24.04, macos-14, windows-2022]` ×
    `python: ["3.12", "3.13", "3.14"]`, running `uv run --python ${{ matrix.python }} nox -s
tests-0`.
  - A `lint-types` job on Linux 3.14 that runs `nox -s lint types audit`, plus a `hashseed` job
    for `nox -s tests`.
  - Every action pinned by full SHA with a version comment.
  - `permissions: contents: read`, `concurrency: {group: ci-${{ github.ref }},
cancel-in-progress: true}`, `timeout-minutes: 30`, and `enable-cache: true` for setup-uv.
- [ ] **Step 2:** Make the Docker job **run** the image:
      `docker compose -f docker/docker-compose.yml up --build --exit-code-from stage`.
- [ ] **Step 3:** Create `nightly.yml` (cron `0 3 * * *`) running `nox -s bench -- --tier L`.
      Its `tests` step uses `HYPOTHESIS_PROFILE=nightly`. Leave a mutation-testing job out until
      Phase 4.
- [ ] **Step 4:** Add the CodeQL (python), Scorecard and zizmor workflows; Dependabot for `uv`,
      `github-actions`, `docker` and `npm`, weekly; `CODEOWNERS` (`* @abj360`); and `SECURITY.md`
      telling reporters to use GitHub private vulnerability reporting, never public issues.
- [ ] **Step 5:** Validate locally:
  - `uvx zizmor .github/workflows` reports no findings at medium or above.
  - `npx prettier --check .github` is clean.
- [ ] **Step 6:** Commit
      `ci: test on three operating systems and three Pythons, harden workflows`.
- [ ] **Step 7:** The matrix only runs on a push. Ask the user before pushing. Triage each
      macOS or Windows failure as either an environment problem (fix CI) or a library defect (add a
      ledger entry in Task 5).

### Task 3: packaging as ShowPy

**Files:**

- Modify: `pyproject.toml`, `docker/Dockerfile`, `tests/unit/show/test_schema.py`,
  `stage/__init__.py` (add `__version__`), `.prettierignore`
- Create: `.dockerignore`, `tests/unit/test_packaging.py`
- Move: `schemas/stage.show.v1.json` → `stage/schemas/stage.show.v1.json`

**Interfaces:**

- Produces:
  - `stage.__version__: str`.
  - `stage.core.schema_path(name: str) -> importlib.resources.abc.Traversable`. Add this to the
    lazy export table and the snapshot.
  - A working `nox -s wheel` session.

- [ ] **Step 1:** Write the failing tests in `tests/unit/test_packaging.py`:
  - `test_version_is_exported`: `stage.__version__` matches `r"^\d+\.\d+\.\d+"`.
  - `test_schema_is_a_package_resource`: `schema_path("stage.show.v1.json").is_file()`.
  - `test_distribution_name_is_showpy`:
    `importlib.metadata.metadata("ShowPy")["Name"] == "ShowPy"`.
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_packaging.py -v`. Expected: 3 FAIL.
- [ ] **Step 3:** Edit `pyproject.toml`:
  - `name = "ShowPy"` and `dynamic = ["version"]`.
  - Build requires `hatchling` and `hatch-vcs`, pinned. Add `[tool.hatch.version]
source = "vcs"`.
  - Dependencies become `>=` today's lock versions. Drop `imageio` (check with `deptry`). Declare
    `pydantic-core`.
  - Classifiers: Python 3.12, 3.13 and 3.14, `Typing :: Typed`, `License :: OSI Approved :: MIT
License`, OS Independent. Add `[project.urls]` and `license-files = ["LICENSE"]`.
  - `[tool.hatch.build.targets.sdist] exclude = ["docs/", "tests/", ".claude/", "node_modules/"]`.
- [ ] **Step 4:** Move the schema with `git mv`. Point `test_schema.py` at `schema_path`. Remove
      `schemas/` from `.prettierignore` and add `stage/schemas/`. Implement `schema_path` in
      `stage/core/resources.py` with a `>>>` example.
- [ ] **Step 5:** Rewrite `docker/Dockerfile`:
  - Base images pinned by digest.
  - A builder stage running `uv build --wheel` with `ARG
SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+docker`.
  - A runtime stage that installs the wheel plus the dev group, copies `tests/`, and runs as
    `USER 10001`.
  - The command is `uv run nox -s tests-0`.

  Then write `.dockerignore`: `.git`, `.env`, `docs/`, `dist/`, `.venv/`, `node_modules/`,
  `**/__pycache__`, `.claude/`. In compose, set `env_file` to `required: false`.

- [ ] **Step 6:** Write `nox -s wheel`. It runs `uv build`, creates a clean venv
      (`uv venv /tmp/...`), installs `dist/*.whl`, runs
      `python -c "import stage, importlib.resources; ..."` to check `py.typed` and the schema, and
      then runs the README quickstart block extracted from `README.md`.
- [ ] **Step 7:** Add a `lowest` CI job: `uv sync --resolution lowest-direct && uv run nox -s
tests-0`. If a lower bound fails, raise that bound. Never pin it.
- [ ] **Step 8:** Run `set -o pipefail; uv run nox -s wheel tests-0 && uvx deptry .`. Expected:
      pass, and `deptry` reports no issues.
- [ ] **Step 9:** Commit `build: package as ShowPy with version from the tag and ranged
dependencies`.

### Task 3b: rename the library to `showpy`

User decision 2026-11-13: the whole library becomes `showpy`, including the import name. It is a
clean break with no `stage` compatibility shim. This task does it before any new test code is
written against the old name.

**Files:**

- Move: `stage/` → `showpy/` (`git mv`, so history follows)
- Modify:
  - Every `.py` file under `showpy/`, `tests/`, `scripts/`, `docs/guide/` and `noxfile.py`
    (imports and doctest `>>>` lines)
  - `pyproject.toml`
  - `docker/Dockerfile` and `docker/docker-compose.yml`
  - `.github/workflows/*.yml`
  - `.pre-commit-config.yaml`
  - `README.md`, `CLAUDE.md`, `CONTRIBUTING.md` and `docs/HANDOFF.md`
  - The `showpy/schemas/` file name
  - `tests/unit/show/test_schema.py` and `tests/unit/test_public_api.py`
- ADRs: **do not rewrite earlier sections.** Append a dated addendum
  (`## Addendum 2026-11-13: package renamed to showpy`) to `docs/adr/0001-*.md`.

**Rename map (exact):**

| Old                                                                       | New                                                                 |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| package `stage`, `import stage`, `from stage.…`                           | `showpy`                                                            |
| `StageError`                                                              | `ShowPyError`                                                       |
| `StageValidationError` (a local alias in `cast/spots.py`)                 | `ShowPyValidationError`                                             |
| env vars `STAGE_UPDATE_GOLDEN`, `STAGE_UPDATE_SCHEMA`, `STAGE_UPDATE_API` | `SHOWPY_UPDATE_GOLDEN`, `SHOWPY_UPDATE_SCHEMA`, `SHOWPY_UPDATE_API` |
| `SCHEMA_TAG = "stage.show.v1"` (`export/data.py`)                         | `"showpy.show.v1"`                                                  |
| schema file `stage.show.v1.json`, `$id` `stage.show.v1`                   | `showpy.show.v1.json`, `showpy.show.v1`                             |
| repo URLs `abj360/stage`                                                  | `abj360/ShowPy` (`https://github.com/abj360/ShowPy`)                |
| prose "the `stage` library"                                               | "ShowPy" (the distribution) or `showpy` (the package)               |

**Never rename** the domain word stage. That covers the sheet template and kind (`"stage"`,
`SheetKind.STAGE`), the `STAGE_WIDTH_M`, `STAGE_DEPTH_M`, `STAGE_Y_M`, `STAGE_THIRD_X_M`,
`STAGE_AREAS`, `STAGE_REF`, `STAGE_WORDS` and `STAGE_DIR` constants, and text about a theater
stage. Review every hit by hand, and never use a blind `sed`.

**Interfaces:**

- Produces:
  - The package `showpy` and the error base class `showpy.core.errors.ShowPyError`.
  - `showpy.export.data.SCHEMA_TAG == "showpy.show.v1"`.
  - `showpy.export.data.LEGACY_SCHEMA_TAGS == ("stage.show.v1",)`.

- [ ] **Step 1:** Write the failing tests:
  - `tests/unit/test_packaging.py::test_import_name_is_showpy`: `import showpy`, and
    `importlib.util.find_spec("stage") is None`.
  - `tests/unit/export/test_data.py::test_regression_import_json_reads_legacy_stage_tag`: a
    document tagged `{"schema": "stage.show.v1", ...}` loads to the same `Show` as one tagged
    `showpy.show.v1`.
  - `test_export_json_writes_showpy_tag`.

  Run them and watch them fail.

- [ ] **Step 2:** `git mv stage showpy`. Rewrite the imports with an AST-aware or anchored-regex
      script (`^(\s*)(from|import) stage\b` and `>>> (from|import) stage\b`). Then grep for
      leftovers with `grep -rnw stage --include=*.py`. Review each hit as either a domain word or a
      missed rename (HANDOFF trap: scripted edits mangle doctests).
- [ ] **Step 3:** Apply the rest of the rename map. `import_json` accepts `SCHEMA_TAG` and
      `LEGACY_SCHEMA_TAGS`, and `export_json` writes only `SCHEMA_TAG`. Regenerate the schema with
      `SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`.
- [ ] **Step 4:** Update the configs and docs: `pyproject.toml` (`packages = ["showpy"]`, ruff,
      mypy, pytest `testpaths`, pyright, deptry, the hatch build targets), the Dockerfile,
      compose, workflows (CodeQL paths), pre-commit, README, CLAUDE.md (layout, commands, rules,
      glossary), CONTRIBUTING and HANDOFF. Add the ADR 0001 addendum.
- [ ] **Step 5:** Run `set -o pipefail; uv run nox -s lint types tests hashseeds wheel`, then the
      Docker run `docker compose -f docker/docker-compose.yml up --build --exit-code-from showpy`
      (also rename the compose service to `showpy`). Expected: every gate green with the same test
      count plus the new tests, and `grep -rn "import stage\|from stage\|StageError" .` (excluding
      `.venv`, `.nox` and `uv.lock`) returns nothing.
- [ ] **Step 6:** Commit in this order:
  - `refactor!: rename the package from stage to showpy`
  - `feat(export): read legacy stage.show.v1 documents`

  The first message carries a `BREAKING CHANGE:` footer.

### Task 4: test infrastructure, generators, goldens and benchmarks

**Files:**

- Create: `tests/helpers.py`, `tests/conftest.py`, `tests/strategies/{__init__,cast,sheet,time,
placements,paths,forms,show}.py`, `tests/fixtures/generators.py`,
  `tests/property/test_generated_shows.py`, `tests/bench/test_budgets.py`,
  `benchmarks/baseline-2026-11-13.json`, `tests/golden/test_golden_large.py`
- Modify: the test files that duplicate `_set`, `_session`, `_pin` and `_with_rows`
  (`tests/unit/geo/test_{transforms,measure,analysis}.py`, `tests/unit/choreo/test_{models,match,
match_ops,form_place}.py`, `tests/unit/show/test_{history,collab}.py`)

**Interfaces:**

- Produces:
  - `seeded_show(seed: int, spots: int, sets: int) -> Show`.
  - The strategies `shows(max_spots: int = 20, max_sets: int = 6) -> SearchStrategy[Show]` and
    `path_rows(show) -> SearchStrategy[PathRow]`.
  - `make_set(*rows, off=())`.
  - Benchmark tiers `S` = (20, 4), `M` = (100, 20), `L` = (300, 64), `XL` = (600, 120), chosen
    with `--tier`.

- [ ] **Step 1:** Move the duplicated helpers into `tests/helpers.py`, without changing
      behavior. Where signatures differ, keep one function per signature, each with a distinct
      name. Run `uv run pytest`: same count, all green. Commit
      `test: share duplicated test helpers`.
- [ ] **Step 2:** In `conftest.py`, register the Hypothesis profiles:
  - `ci`: `derandomize=True`, `print_blob=True`, `deadline=500`.
  - `nightly`: `max_examples=2000`, `deadline=None`.
  - `dev`.

  Select the profile from `HYPOTHESIS_PROFILE`, defaulting to `ci`.

- [ ] **Step 3:** Write the property test first in `tests/property/test_generated_shows.py`:
  - `test_generated_shows_validate`: `validate_show(show).ok`.
  - `test_generated_shows_match_schema`: `jsonschema` validation against `schema_path(...)`.
    Pin `jsonschema` in the dev group.
  - `test_generated_show_round_trips`: `decode_show(encode_show(s)) == s`.

  Run it. Expected: FAIL, because `stage.strategies` does not exist yet.

- [ ] **Step 4:** Implement the strategies only through public builders (`new_show`,
      `add_spots_along`, `add_set`, `add_tempo`, `add_hold`, the path builders, the form
      constructors). Cover all 7 path styles, easings, windows, waypoints, facing modes, entrances
      and exits, and all form kinds. A generated show that fails validation because of a library
      bug becomes a ledger entry and an `assume()` exclusion carrying that ledger ID. Never write
      an unlabelled filter.
- [ ] **Step 5:** Implement `seeded_show` with `seeded_rng(seed)`, building a deterministic
      show of the requested size with mixed styles, ramps and holds. Write
      `test_seeded_show_is_deterministic`: the encoded bytes are equal across two calls.
- [ ] **Step 6:** Write `tests/bench/test_budgets.py`, one benchmark per row of section 5.3:
      `min_gaps`, `sample`, `compile_all`, `match`, `validate_show`, a one-spot edit save,
      `events_since`, `head`, video, import time and peak memory. Mark them `@pytest.mark.bench`;
      they are excluded from default runs (`-m "not bench"` in `addopts`). Run them at tier M and
      save the result to `benchmarks/baseline-2026-11-13.json`. The `bench` session compares
      against it with `--benchmark-compare-fail=min:20%`.
- [ ] **Step 7:** Build the large golden: a 240-spot show using every path style,
      `seeded_show(7, 240, 24)`. Snapshot segments, `min_gaps`, frames CSV, PDF text and the PNG
      render hash in `tests/golden/fixtures/large/` with `STAGE_UPDATE_GOLDEN=1`. The user reviews
      the fixture once, and it is marked `golden`.
- [ ] **Step 8:** Run `set -o pipefail; uv run nox`. Expected: green. Commit each piece
      separately:
  - `test: add Hypothesis show strategies and profiles`
  - `test: add seeded show factory and benchmark budgets`
  - `test: add a 240-spot golden show`

### Task 5: the defect ledger

**Files:**

- Modify: `pyproject.toml` (register the `defect(id)` marker), `tests/conftest.py` (add the
  hook)
- Modify/create: the `tests/unit/<package>/test_<module>.py` files that mirror each defect

**Interfaces:**

- Produces:
  - The marker `@pytest.mark.defect("UP-<section>-<nn>")`. Its `conftest` hook requires every
    `defect` test to also be `xfail(strict=True)` with `reason` equal to the ID, and fails
    collection otherwise.
  - A `defect` summary line at the end of the run: `Ledger: N open`.

- [ ] **Step 1:** Write `tests/unit/test_ledger.py::test_defect_marker_requires_strict_xfail`,
      using `pytester` with a `defect` test that lacks `xfail`, and expecting a collection error.
      Watch it fail. Implement the hook. Watch it pass. Commit
      `test: add the defect ledger marker`.
- [ ] **Step 2:** Fan out **7 parallel subagents**, each in its own worktree branched from this
      branch, with one per audit section:
  - 9.1 geo
  - 9.2 engine
  - 9.3 core, cast, notes and time
  - 9.4 sheet
  - 9.5 export
  - 9.6 forms and matching
  - 9.7 show

  Each one gets this brief:
  - Encode every confirmed defect in your section as a `test_regression_<behavior>` test in the
    mirrored unit file.
  - IDs run `UP-<section>-01` upward in order of appearance. Section 3 rows reuse the workstream
    ID; record the mapping in the test's docstring.
  - Each test asserts the **correct** behavior: an exact `StageError` subclass and `code`, an
    exact value with `approx_equal` and an explicit tolerance, or a property. It must fail today
    for the reason the audit gives. Run the test without its `xfail` marker and confirm it fails
    on the assertion or the expected error, not on a typo.
  - Hangs use `@pytest.mark.timeout(5, method="signal")` and
    `@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")`.
  - Unbounded-allocation tests assert the error before any allocation. Never actually allocate
    gigabytes in CI: use sizes just past the new cap and the `context` size.
  - Replace the weak assertions the audit lists for your section (§5.2.9).
  - Touch only `tests/unit/<your package>/`. One commit per module:
    `test(<package>): encode audited <module> defects as strict xfails`.

- [ ] **Step 3:** Run the reviewer gate per lane. The reviewer checks that each test fails for
      the stated reason and that no test asserts the current (wrong) behavior. Merge the lanes with
      `git merge --no-ff`, then remove the worktrees.
- [ ] **Step 4:** Run a serial task for the cross-cutting sections 4–7 (packaging, determinism,
      purity). Two examples:
  - `test_regression_content_hash_normalizes_negative_zero` (UP-4.3-01)
  - `test_regression_public_functions_do_not_mutate_inputs` (UP-4.4-01)
- [ ] **Step 5:** Run `set -o pipefail; uv run nox`. Expected: green, with every ledger test
      `XFAIL` and none `XPASS`. Record `Ledger: N open` in `docs/HANDOFF.md`.

### Task 6: one public-name table, the API snapshot and the changelog

**Files:**

- Create: `stage/_api_table.py`, `scripts/gen_public_api.py`, `tests/unit/test_api_snapshot.py`,
  `tests/fixtures/api_snapshot.json`, `CHANGELOG.md`, `cliff.toml`
- Modify: `stage/__init__.py` (now generated), `tests/unit/test_public_api.py`

**Interfaces:**

- Produces `PUBLIC_API: tuple[tuple[str, str, str], ...]`, holding (public name, module,
  attribute) and sorted by public name. It is the only hand-edited list.
- `scripts/gen_public_api.py --check` exits 1 on drift.

- [ ] **Step 1:** Write `test_generated_init_matches_table`, which renders
      `stage/__init__.py` from `PUBLIC_API` and compares it to the file. It fails until the
      generator exists.
- [ ] **Step 2:** Extract today's `_EXPORTS` into `stage/_api_table.py`, preserving the same
      names. The generator then writes the three blocks: the `TYPE_CHECKING` imports, `_EXPORTS`
      and `__all__`. Run `ruff format` on the output, then run the existing
      `tests/unit/test_public_api.py`: it must stay green. This is `caveman:safe-refactor`: compare
      the sorted `__all__` before and after.
- [ ] **Step 3:** Write the snapshot test. It holds `{name: str(inspect.signature(obj))}` for
      callables and the field names for models. With `STAGE_UPDATE_API=1` it rewrites the JSON. On
      a difference, the failure message lists the changed names and says to add them to
      `CHANGELOG.md` under "Unreleased".
- [ ] **Step 4:** Configure git-cliff for Conventional Commits groups and generate
      `CHANGELOG.md` for v0.1.0, v0.1.1 and Unreleased.
- [ ] **Step 5:** Run `set -o pipefail; uv run nox`. Commit
      `refactor(api): generate the public exports from one table` and
      `test(api): snapshot public signatures`.

### Task 7: stronger lint and types, and the docs truth fixes

**Files:**

- Modify: `pyproject.toml` (ruff rules, `[tool.pyright]`), `stage/**/*.py` (remove shebangs),
  `CLAUDE.md`, `CONTRIBUTING.md`, `README.md`, `docs/HANDOFF.md`,
  `docs/adr/0001-library-layout-and-reference-fixes.md`, `docs/adr/0002-path-engine.md`
  (addenda only)

- [ ] **Step 1:** Remove the shebang and the blank line after it from the 94 `stage/` modules
      with a scripted edit. Then check that every module still begins with its `"""` docstring
      (HANDOFF trap: scripted edits can mangle doctests). Run `uv run pytest stage` to check the
      doctests. Update the project `CLAUDE.md` "Module headers" bullet to read: library modules
      under `stage/` have no shebang; scripts and tests keep it.
- [ ] **Step 2:** Add these ruff rules: `EXE, PIE, PGH, RSE, ISC, LOG, DTZ, PTH, FURB, ARG,
PERF, C90, TID`. Set `mccabe.max-complexity = 12`; ban `tests` with
      `flake8-tidy-imports.banned-module-level-imports`; give `tests/**` an `EXE001` ignore. Fix
      the mechanical findings. Each C901 offender gets `# noqa: C901` followed by its ledger ID
      `UP-7-nn` (added to the ledger in this step). Commit
      `style: drop library shebangs and widen ruff rules`.
- [ ] **Step 3:** Add `[tool.pyright]` with `typeCheckingMode = "basic"` and include `stage`,
      `tests`. Run it and list the errors (13 expected). For each real bug, write
      `test_regression_<behavior>` first, watch it fail, then fix it; one example is the
      `IndexError` at `stage/choreo/routes.py:98`. Fix false positives with narrowing, never
      `# type: ignore`. Commit each fix separately.
- [ ] **Step 4:** Make the docs truth fixes (spec section 8, "Fix the stale and contradictory
      statements"):
  - `CLAUDE.md` names the code as the source of truth, with the reference trailing it until
    Phase 6.
  - Unify the Node path.
  - `CONTRIBUTING` gets the title "Contributing to stage" and supports Python 3.12+.
  - Update the README and HANDOFF test counts from the actual run.
  - Remove the dead `docs/project-overview.md` link.
  - Replace the HANDOFF "In flight" section with the upgrade program status.
  - Add a dated addendum to ADR 0002 recording that `min_gaps` dominates the hotspot forecast.

  Run Prettier, then commit `docs: fix stale and contradictory statements`.

- [ ] **Step 5:** Run `set -o pipefail; uv run nox && uv run nox -s wheel bench`. Expected:
      green, with the ledger reported.

### Phase exit

- [ ] Run `superpowers:verification-before-completion`: the full nox gate, plus
      `docker compose -f docker/docker-compose.yml up --build --exit-code-from stage`.
- [ ] Run `code-review` at medium on the branch, then the
      `superpowers:finishing-a-development-branch` PR. The body has three parts: What (the safety
      net before the Phase 1 fixes), How tested, and Checklist.
- [ ] Ask the user before pushing. After CI is green on all 9 matrix cells, merge and tag
      `v0.2.0`, with release notes calling out the distribution rename to ShowPy and the new
      `stage.__version__`.
