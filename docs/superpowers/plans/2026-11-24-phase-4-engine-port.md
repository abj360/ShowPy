# Phase 4: Engine Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the computation of `core`, `geo`, `time`, `sheet`, `cast` and `choreo` into one
Rust core (ADR 0025) behind unchanged public names, with the same answers as v0.4.0 and the
Phase 4 budgets met, then release v0.6.0.

**Architecture:** A serial lane L0 switches the build to maturin, adds the Cargo workspace
(`e8s-core`, `e8s-py` built into `e8s._core`, `e8s-wasm`), the Rust gates, CI, licence notices,
fuzzing, mutation testing, the error and panic boundary, the per-part cache and the parity
harness that compares the core against a v0.4.0 oracle venv. Port lanes then follow the layer
order, at most three at a time: C (`core`), then G (`geo`) beside T (`time`), then S (`sheet`)
beside K (`cast`), then E (engine) beside F (forms and matching). Each lane ports its modules
one task at a time against the unchanged v0.4.0 tests, and ends with a parity run on `main` and
the deletion of its Python computation. A serial lane X proves the exit criteria, drops scipy
and shapely, and prepares v0.6.0.

**Tech Stack:** Rust 1.100.0 (edition 2024), PyO3 0.30.1 (`abi3-py313`), the `numpy` crate
0.30.0, `libm` =0.2.16, wasm-bindgen, maturin 1.16.0, cargo-deny, cargo-about, cargo-fuzz,
cargo-mutants; Python 3.13 and 3.14, pydantic v2 (models stay pydantic), numpy, uv, nox, ruff,
mypy `--strict` with `stubtest`, pyright basic, pytest, Hypothesis, mutmut; Node for the wasm
parity check; Prettier; GitHub Actions with `PyO3/maturin-action`.

**Spec:** ADR 0025 (`docs/adr/0025-core-architecture.md`, Accepted 2026-11-24), with ADRs 0014,
0015 (Phase 3 owns `e8s/show/`), 0021, 0023 and 0026. ADR 0027 (custom callables) gates lane E
and the callable tasks of lane F. Transition plan section 4.3 and decisions D12, D13, D17 and
D36; the trial report and `phase-4-trial/` (crates, scripts, `wheels.yml`) in the maintainer's
agent notes; `algorithms-and-evaluation.md` (crate keep and avoid lists, section 1.8 on the
assignment solver).

Where the transition plan and the code disagree, this plan follows the code (`main` at
`14cfd9a`, 2026-11-24):

- The crates are `e8s-core`, `e8s-py`, `e8s-wasm`, and `e8s/` stays at the repository root
  (maintainer, 2026-11-24); the plan's `e8show-*` names and `python/e8show/` are not used.
- `export` and `notes` are already deleted (ADR 0021), so nothing leaving is ported or kept.
- The data models stay pydantic until Phase 6 (ADR 0025 decision 6), so "pydantic only in
  `show`" is not a Phase 4 exit criterion.

## Decisions needed

Answered by the maintainer on 2026-11-24: P1 to P6 as recommended below; for P3, the
shapely-typed public names are removed.

The maintainer accepted ADR 0025 on 2026-11-24. Six decisions belong to this plan; each task
that depends on one names it. A decision answered against its recommendation re-plans those
tasks first.

- **P1. What "delete the package" means while models stay pydantic.** Recommendation: a lane
  deletes every module whose work is computation (the "moves" column of the module table). It
  keeps the data models, the registries of Python callables, and the modules whose functions
  only build or edit models (`timeline.py`, `spots.py`, `rewrite.py`, `paths.py`, `routes.py`,
  `facing.py`, `form_ops.py`, `form_place.py`, `match_ops.py`, `text_edit.py`, `forms.py`,
  `set_snapping.py`, `set_transforms.py`); their numeric work calls the core. They move with
  the models in Phase 6, so nothing is ported twice. Cost if wrong: Phase 4 also ports the edit
  modules, with Rust building pydantic objects through the binding, and Phase 6 rewrites them.
- **P2. Modules that `e8s/show/` imports.** Phase 4 must not touch `e8s/show/`, which imports
  `e8s.core.{canonical,caps,csv_cells,numbers,strict_json,units,util}`, `e8s.time.{clock,
measures,validate}`, `e8s.cast.select` and `e8s.choreo.engine`. Recommendation: those
  modules keep their path as re-export modules (a docstring and imports from `e8s._core`,
  nothing else) until the phase that next owns `e8s/show/` rewires its imports; lane X lists
  them in `docs/HANDOFF.md`. Cost if wrong: 33 import lines in `e8s/show/` move later.
- **P3. Public names tied to shapely.** `Region.to_shapely`, `Region.from_shapely`,
  `CurveBase.to_shapely` and the `Polygon` alias return or take shapely objects.
  Recommendation: remove them in task X2 with no shim (no users), named in the commit and the
  release notes. Cost if wrong: an optional `shapely` extra that keeps the three methods.
- **P4. Phase 3 shares three things with L0.** Phase 3 task M2 edits `e8s/geo/fonts.py`; task
  K4 edits `pyproject.toml`, `uv.lock` and `.github/workflows/nightly.yml`; task X3 tags
  v0.5.0. Recommendation: task G2 starts only after M2 merges; Phase 4's nightly jobs go in a
  new `nightly-core.yml`; whichever of K4 and L0.1 merges second rebases and reruns `uv lock`;
  and if L0.1 merges before v0.5.0, Phase 3's release commit also sets `Cargo.toml` to
  `0.5.0` (a step the Phase 3 plan does not have yet). After L0.5, `scripts/gen_error_codes.py`
  also writes `crates/e8s-core/src/base/error_codes.rs`; a Phase 3 task that adds a code stages
  it beside `docs/error-codes.md`. Cost if wrong: a rebase conflict or a stale generated file.
- **P5. Version on `main` between releases.** The version is bumped only at release (ADR 0025
  decision 4), so `main` builds report the last release. Recommendation: accept it. Cost if
  wrong: one commit after each release sets `X.Y.Z-alpha.0`.
- **P6. Phase 4's release is v0.6.0.** Phase 3 releases v0.5.0 and Phase 5 v0.7.0.

## Global Constraints

- **ADR gates.** No task starts before ADR 0025 is `Accepted` and P1 to P6 are answered. Lane E,
  and tasks F2 and F3, start only after ADR 0027 is `Accepted`.
- **Phase 3 boundary.** No task edits anything under `e8s/show/` or `tests/unit/show/`, nor
  `.github/workflows/nightly.yml`. Files shared with Phase 3: the generated ones
  (`e8s/__init__.py`, `tests/fixtures/api_snapshot.json`, `e8s/schemas/*.json`,
  `docs/error-codes.md`, `uv.lock`, `crates/e8s-core/src/base/error_codes.rs`) and two
  hand-edited row tables where each lane edits only its own rows (`e8s/_api_table.py`,
  `e8s/core/error_codes.py`). After a rebase, rerun the generators; never hand-merge them.
- **The oracle is the v0.4.0 test suite.** A module is ported when the tests that cover it pass
  against the core with no edited assertion. Allowed test changes, each listed in the commit
  body: an import line that points at the new path; a test of a private Python helper the port
  removes, rewritten as a Rust `#[test]` of the same name with the same cases; packaging and
  tooling tests whose subject is the build (L0 only). A test that pins a third-party artifact
  (a GEOS vertex order, a scipy tie choice, a LAPACK sign) stops the task and goes to the
  controller.
- **Same algorithms first.** Today's methods, tables, sample counts, caps and tie rules are
  kept wherever a test or a golden pins them. Better algorithms are Phase 5.
- **Never slower.** Every case the parity harness times must run no slower in the core than in
  the v0.4.0 oracle (`nox -s parity -- <area> --speed`, minimum of 7 runs each). Compiled
  values (clocks, plans, curve tables) are cached beside their frozen part.
- **Float rules (ADR 0025 decision 8):** `libm =0.2.16` for every transcendental; no `mul_add`;
  `libm::fma` only as a correctly rounded error-free transform; fixed order, no parallel
  reductions; `u ** 2.0` is `u * u`; `np.interp`'s formula and edge rules, not its arm64 FMA;
  NaN rejected at the boundary with `SchemaError`.
- **Errors and panics:** core functions return `CoreResult<T>`; every binding entry goes
  through `guard`; no `unwrap`, `expect`, `panic!`, `todo!` or `unimplemented!` (clippy
  denies them); no `unsafe` in our crates.
- **Ledger flip procedure** (a strict xfail that the core makes pass):
  1. Confirm with `--runxfail` on the parent commit that it failed for the audited reason.
  2. Remove its `defect` and `xfail` markers in the same commit as the port; never edit an
     assertion. Add its case to `tests/parity/flipped.toml` with the ledger ID.
  3. Run `set -o pipefail; uv run pytest -m defect -q 2>&1 | tail -3`: no `XPASS(strict)`.
- **Gates before every commit:** `set -o pipefail; uv run nox -s lint types tests hashseeds rust`
  and `uv run nox -s parity -- <area>` for the lane's area. Lanes G, E, F and X also run
  `uv run nox -s slow`. Tasks that touch the build also run `uv run nox -s wheel core wheels`.
  Before a task is reported done: `uv run nox -s api-changes`. Coverage floor stays 96%.
- **Library rules:** library modules under `e8s/` have no shebang; module docstrings open
  `<filename> --- <role>` with a `Contains:` index; Rust items get `///` docs; every public core
  function's Rust doc carries the docstring it had in Python, verb-first, with its `>>>`
  example importing `e8s.examples`. Docstrings and comments never cite ADRs, docs or ledger
  IDs. Rust files stay under 500 lines.
- **No new escape hatches:** no new `type: ignore`, `pyright: ignore`, `noqa`, `Any` or
  `#[allow(...)]` without a one-line reason.
- **Public names** keep their signatures; `e8s/_api_table.py` rows are re-pointed only in a
  lane's deletion task. The API snapshot changes only in X2 (P3). Error codes added: `CORE_PANIC`
  (L0.5) and nothing else without the controller.
- `uv` only (`/opt/homebrew/bin/uv`). Rust through the pinned toolchain
  (`PATH=/opt/homebrew/opt/rustup/bin:$PATH` on the maintainer's Mac). Node is in
  `/opt/homebrew/bin`; `npm ci` once per worktree; Prettier on every Markdown, YAML or JSON file
  a task writes. Cargo tools (`cargo-deny`, `cargo-about`, `cargo-fuzz`, `cargo-mutants`,
  `wasm-bindgen-cli`) are installed with `cargo install --locked --root .cargo-tools` at the
  exact versions in `noxfile.py`; nothing goes into `~/.cargo/bin`.
- **Commits:** Conventional Commits, one logical change each, committed the moment it is
  complete and passing on its own; no `Co-Authored-By` or "Generated with" trailers; identity
  `Oluwatoyosi Peter Abolaji` with the maintainer's personal email. Breaking commits use `!` and
  a `BREAKING CHANGE:` footer naming each public name.
- **One PR per task; rebase only on conflict.** A reviewed, green task goes out as its own
  stacked PR. Never rebase only because `main` moved. Never push without the maintainer's
  approval.
- **Workspace safety.** Stage explicit paths only; run `git diff --cached --summary` before every
  commit and stop on `mode 120000` or any path under `.superpowers/` or `.claude/`; never
  `--no-verify`.
- **No deadlines.** No dates, durations or forecasts in briefs, ledgers or reports.
- **Execution:** subagent-driven. One worktree per lane, a reviewer subagent per task;
  `differential-review` on L0.1, L0.5, L0.6, C2, E2 and F1. The controller merges PRs one at a
  time and reruns every gate on `main`.

## Review Focus

1. **A Phase 3 lane works on `main` after L0.1.** Expected: `uv sync` compiles the extension
   with the pinned toolchain, rebuilds it when a crate changes, and every Phase 3 gate passes
   with no Phase 3 file edited. Pinned by L0.1:
   `test_uv_rebuilds_the_extension_when_rust_sources_change`,
   `test_every_job_that_syncs_installs_the_pinned_rust_toolchain`.
2. **A v0.4.0 test pins a third-party choice.** Expected: the core reproduces scipy's choice
   among equal-cost optima and GEOS's ring orientation and start where a test reads them, and a
   task that cannot stops instead of editing the test. Pinned by K2, F1 and G4:
   `test_sparse_solver_matches_scipy_on_mirror_pair_ties`,
   `test_dense_solver_matches_scipy_on_tie_heavy_instances`,
   `test_region_rings_keep_v040_orientation_and_start`.
3. **A frozen part is mutated behind the cache.** Expected: an equal but distinct object is
   never served another object's core value, a collected part is evicted, and the library never
   writes to a frozen model. Pinned by L0.5:
   `test_part_cache_misses_for_an_equal_but_distinct_object`,
   `test_part_cache_evicts_a_collected_part`, `test_library_never_bypasses_frozen_models`.
4. **Hostile input reaches a core parser.** Expected: NaN, infinities, 1e308 coordinates, deep
   selection nesting and oversized SVG or font data raise an `E8sError` subclass; never a panic,
   an abort or `PanicException`. Pinned by L0.5 and each parser's task:
   `panic_in_a_bound_call_raises_e8s_error` (Rust), `test_regression_core_rejects_nan_with_schema_error`,
   and the fuzz targets `refs`, `strict_json`, `svg_path`, `font`, `selection`, `timeline`.
5. **Same input, different bytes.** Expected: sampled arrays, documents and hashes are
   byte-identical across the 6 CI cells, 3 hash seeds, and native against wasm; no FMA outside
   `libm::fma`. Pinned by L0.2, L0.6, T1, E2 and X1: `test_flags_an_fma_outside_libm_fma`,
   `test_wasm_clock_matches_native_on_10000_timelines`,
   `test_wasm_engine_matches_native_on_the_goldens`,
   `test_core_outputs_are_byte_identical_across_cells_and_seeds`.

---

## Sequencing

```
ADR 0025 Accepted; P1-P6 answered
        |
L0.1 -> L0.2 -> L0.3 -> L0.4 -> L0.5 -> L0.6 -> L0.7        (serial, one worktree)
        |
C1 -> C2 -> C3 -> C4                                          core
        |
        +-- G1 -> G2* -> G3 -> G4 -> G5 -> G6 -> G7           geo   (*G2 after Phase 3 M2 merges)
        +-- T1 -> T2 -> T3                                    time
                 |
   after G7:     +-- S1 -> S2                                 sheet
                 +-- K1 -> K2 -> K3                           cast
                          |
   after T3, S2 and K3:   +-- F1 (any time) ; ADR 0027 Accepted:
                          +-- E1 -> E2 -> E3 -> E4            choreo engine
                          +-- F2 -> F3 -> F4 -> F5            choreo forms and matching
                                   |
                                   v
                          X1 -> X2 -> X3                      exit, v0.6.0
```

A lane starts only when every package below it has passed its deletion task on `main`. At most
three lanes run at once (T beside S and K is the widest point). Inside a lane, tasks are serial
in the order listed.

## Module disposition

"Moves" modules become core functions. "Re-export" modules are moves that `e8s/show/` imports
(P2): they keep their path and hold only imports from `e8s._core`. "Stays" modules hold models,
registries of Python callables, or model edits (P1); their numeric work calls the core.

| Package  | Moves (deleted at the lane's last task)                                                                                                                                                                                      | Re-export (P2)                                                              | Stays until Phase 6 (Phase 5 for `sheet`)                                                                                                                                                                                                                 |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core`   | —                                                                                                                                                                                                                            | `canonical`, `caps`, `csv_cells`, `numbers`, `strict_json`, `units`, `util` | `types`, `arrays`, `errors`, `error_codes`, `error_context`, `extras`, `resources`, `readonly`, `json_types`, `validation`, `selection`, `ids` (aliases and checked fields; parsers delegate)                                                             |
| `geo`    | `affine`, `checks`, `font_strokes`, `measure`, `regions`, `shapes`, `snapping`, `spacing`, `svg`, `text`, `text_layout`, `transform_base`, `transforms`                                                                      | —                                                                           | `models`, `curve_base` (`CurveBase`, `Curve`), `curves` (curve models; methods delegate), `fonts` (`Font`, `FontRegistry`; parsing delegates)                                                                                                             |
| `time`   | —                                                                                                                                                                                                                            | `clock`, `measures`, `validate`                                             | `models`, `timeline`                                                                                                                                                                                                                                      |
| `sheet`  | `bounds`, `replace`                                                                                                                                                                                                          | —                                                                           | `models`, `surfaces`, `templates`                                                                                                                                                                                                                         |
| `cast`   | —                                                                                                                                                                                                                            | `select`                                                                    | `models`, `spots`, `rewrite`                                                                                                                                                                                                                              |
| `choreo` | `avoid`, `engine_batch`, `engine_compile`, `engine_facing`, `form_fit`, `form_layout`, `form_text`, `match`, `match_cost`, `match_solve`, `motion_stats`, `read`, `set_analysis`, `set_measure`, `text_count`, `text_spread` | `engine`                                                                    | `models`, `path_models`, `form_models`, `recipe_models`, `match_models`, `read_models`, `engine_models`, `motion`, `form_fns`, `forms`, `paths`, `routes`, `facing`, `form_ops`, `form_place`, `match_ops`, `text_edit`, `set_snapping`, `set_transforms` |

Type aliases and constants that a deleted module defined (for example `Matrix`, `Area`,
`MAX_LAYOUT_CHARS`) move to the package's `models.py`, or to its re-export module.

## File map (lane ownership)

| Lane | Owns (source)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Owns (tests)                                                                                                                                                                                                                                                                                                                               |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| L0   | `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `deny.toml`, `about.toml`, `about.hbs`, `crates/*/Cargo.toml`, `crates/*/src/lib.rs`, `crates/e8s-core/src/{base,geo,time,sheet,cast,choreo,assign,rng}/mod.rs` and `crates/e8s-core/src/choreo/{engine,forms,matching}/mod.rs` (empty skeletons), `crates/e8s-core/src/base/error.rs`, `crates/e8s-py/src/{errors,cache}.rs`, `fuzz/`, `e8s/_core/__init__.pyi`, `e8s/licenses/`, `pyproject.toml`, `noxfile.py`, `docker/Dockerfile`, `.gitignore`, `.github/workflows/{ci,wheels,nightly-core}.yml`, `scripts/{check_no_fma,check_release_version,gen_third_party_notices,build_oracle}.py`, `scripts/gen_error_codes.py` | `tests/parity/{__init__,conftest,cases,compare,oracle_runner}.py`, `tests/unit/harness/test_parity_compare.py`, `tests/unit/rules/test_port_rules.py`, `tests/unit/api/{test_packaging,test_api_snapshot,test_core_boundary}.py`, `tests/unit/tooling/test_{noxfile,dockerfile,check_no_fma,check_release_version,third_party_notices}.py` |
| C    | `crates/e8s-core/src/base/` (except `error.rs`), `crates/e8s-core/src/rng/`, `crates/e8s-py/src/base.rs`, `e8s/_core/core.pyi`, the modules of `e8s/core/` in the table, `fuzz/fuzz_targets/{refs,strict_json}.rs`                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `tests/parity/{cases_core,test_parity_core}.py`, `tests/unit/core/test_core_rng.py`, `tests/unit/rules/ported/core.toml`; import lines of `tests/unit/core/`                                                                                                                                                                               |
| G    | `crates/e8s-core/src/geo/`, `crates/e8s-py/src/geo.rs`, `e8s/_core/geo.pyi`, `e8s/geo/`, `fuzz/fuzz_targets/{svg_path,font}.rs`; import lines in `e8s/{sheet,cast,choreo}/` (G7 only)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `tests/parity/{cases_geo,test_parity_geo}.py`, `tests/unit/rules/ported/geo.toml`; import lines of `tests/unit/geo/`, `tests/property/test_geo_transforms.py`                                                                                                                                                                              |
| T    | `crates/e8s-core/src/time/`, `crates/e8s-py/src/time.rs`, `crates/e8s-wasm/src/time.rs`, `e8s/_core/time.pyi`, `e8s/time/{clock,measures,validate}.py`, `fuzz/fuzz_targets/timeline.rs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `tests/parity/{cases_time,test_parity_time}.py`, `tests/unit/time/test_clock_cache.py`, `tests/unit/rules/ported/time.toml`                                                                                                                                                                                                                |
| S    | `crates/e8s-core/src/sheet/`, `crates/e8s-py/src/sheet.rs`, `e8s/_core/sheet.pyi`, `e8s/sheet/{bounds,replace}.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `tests/parity/{cases_sheet,test_parity_sheet}.py`, `tests/unit/rules/ported/sheet.toml`; import lines of `tests/unit/sheet/`                                                                                                                                                                                                               |
| K    | `crates/e8s-core/src/cast/`, `crates/e8s-core/src/assign/{mod,sparse}.rs`, `crates/e8s-py/src/cast.rs`, `e8s/_core/cast.pyi`, `e8s/cast/select.py`, `fuzz/fuzz_targets/selection.rs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `tests/parity/{cases_cast,test_parity_cast}.py`, `tests/unit/rules/ported/cast.toml`                                                                                                                                                                                                                                                       |
| E    | `crates/e8s-core/src/choreo/engine/`, `crates/e8s-py/src/engine.rs`, `crates/e8s-wasm/src/engine.rs`, `e8s/_core/engine.pyi`, the engine modules of `e8s/choreo/` (E tasks list them)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `tests/parity/{cases_engine,test_parity_engine}.py`, `tests/bench/test_core_budgets.py` (E2 creates; F2 adds one test), `tests/unit/rules/ported/engine.toml`; import lines of the E tasks' test files                                                                                                                                     |
| F    | `crates/e8s-core/src/choreo/{forms,matching}/`, `crates/e8s-core/src/assign/{dense,bottleneck,ordered}.rs`, `crates/e8s-py/src/forms.rs`, `e8s/_core/forms.pyi`, the forms and matching modules of `e8s/choreo/` (F tasks list them)                                                                                                                                                                                                                                                                                                                                                                                                                                            | `tests/parity/{cases_forms,test_parity_forms}.py`, `tests/unit/rules/ported/forms.toml`; import lines of the F tasks' test files                                                                                                                                                                                                           |
| X    | `pyproject.toml` and `uv.lock` (X2), the interop names (P3), `docs/releases/v0.6.0.md`, `docs/HANDOFF.md`, `docs/e8s-api-reference.md`, `Cargo.toml` version (X3), `CHANGELOG.md` (release commit only)                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `tests/parity/test_parity_cross_cell.py`                                                                                                                                                                                                                                                                                                   |

`crates/e8s-core/src/choreo/mod.rs` declares `engine`, `forms` and `matching` from L0, so E and
F never edit the same Rust file. K fills L0's empty `assign/mod.rs` with `pub mod sparse;` and the
three lines `pub mod dense; pub mod bottleneck; pub mod ordered;` behind empty files, which F fills.

## The port procedure

Every port task (C1 to C3, G1 to G6, T1 and T2, S1, K1 and K2, E1 to E3, F1 to F4) follows these
steps; the task names its modules, its tests and anything extra.

1. Write the task's new tests (named in the task) and its parity cases in
   `tests/parity/cases_<area>.py`. Run them: FAIL (missing core names).
2. Run the covered v0.4.0 tests on the parent commit: PASS. They are the oracle.
3. Implement in `crates/e8s-core/src/<area>/`, bind in `crates/e8s-py/src/<area>.rs` (every
   entry through `guard`, frozen inputs through a `PartCache`), and type in
   `e8s/_core/<area>.pyi`. Port the module's docstrings to `///` docs unchanged.
4. Replace the Python implementation: a "moves" or "re-export" module becomes a docstring plus
   imports from `e8s._core.<area>` of exactly the names it defined; a "stays" module keeps its
   models and calls the core for the numeric work. Delete the private helpers. Move each test
   of a removed private helper to a Rust `#[test]` of the same name and cases.
5. Run the covered v0.4.0 tests unchanged: PASS. Run `uv run nox -s parity -- <area> --speed`:
   positions within 1e-9 m, discrete outputs identical, no case slower than the oracle.
6. Apply the ledger flip procedure to any `XPASS(strict)`.
7. Gates; commit `perf(<area>): compute <what> in the core` (body: the moved tests, the parity
   table, any flipped ledger ID).

Every lane's last task is its **deletion task**: run `uv run nox -s parity -- <area> --speed`
on `main` with the lane merged and paste the table into the PR body; delete the "moves"
modules; re-point their `_api_table.py` rows to `e8s._core.<area>` and regenerate
`e8s/__init__.py` (the snapshot must not change); change import lines in tests and in the
packages above (never in `e8s/show/`); write `tests/unit/rules/ported/<area>.toml` (lists
`moves`, `reexport`, `stays` and `interop`); gates; commit `refactor(<area>)!: delete the Python
<area> computation`.

---

## Lane L0: build, gates and harness (serial)

Branch `feat/p4-l0`. Skills: `differential-review` on L0.1, L0.5 and L0.6.

### Task L0.1: Cargo workspace and the maturin build backend

**Files:**

- Create: `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`,
  `crates/e8s-core/{Cargo.toml,src/lib.rs}`, `crates/e8s-py/{Cargo.toml,src/lib.rs}`, `crates/e8s-wasm/{Cargo.toml,src/lib.rs}`, the empty
  module skeletons, `e8s/_core/__init__.pyi`
- Modify: `pyproject.toml`, `uv.lock`, `.gitignore`, `docker/Dockerfile`,
  `.github/workflows/ci.yml` (toolchain setup only), `noxfile.py` (`WHEEL_CHECK`)
- Test: `tests/unit/api/test_packaging.py`, `tests/unit/tooling/test_noxfile.py`,
  `tests/unit/tooling/test_dockerfile.py`

**Interfaces:**

- Produces: workspace `version = "0.4.0"` (P5), edition 2024, `rust-version = "1.100"`,
  `[workspace.dependencies] libm = "=0.2.16"`; `rust-toolchain.toml` with
  `channel = "1.100.0"`, `components = ["clippy", "rustfmt", "llvm-tools"]`,
  `targets = ["wasm32-unknown-unknown"]`; profiles `release` (thin LTO, for local and editable
  builds) and `dist` (`inherits = "release"`, `lto = "fat"`, `codegen-units = 1`,
  `strip = "symbols"`, `panic = "unwind"`); `[tool.maturin]` with
  `manifest-path = "crates/e8s-py/Cargo.toml"`, `module-name = "e8s._core"`,
  `python-source = "."`, `python-packages = ["e8s"]`; `[build-system]
requires = ["maturin==1.16.0"]`; `[tool.uv] cache-keys` listing `pyproject.toml`,
  `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `crates/**/Cargo.toml`, `crates/**/*.rs`.
- Produces: `e8s._core` registering submodules `core`, `geo`, `time`, `sheet`, `cast`, `engine`,
  `forms`, each importable by dotted path (inserted into `sys.modules`), each empty but for
  `__doc__`; `e8s._core.__version__` from `CARGO_PKG_VERSION`.
- `e8s-py` depends on `pyo3 =0.30.1` (`abi3-py313`, `extension-module`) and `numpy =0.30.0`
  only; no `pythonize`, no `serde_json`.

- [ ] **Step 1: Write the failing tests**
  - `test_packaging.py::test_version_matches_the_cargo_manifest`: `e8s.__version__` equals the
    PEP 440 form of `[workspace.package] version` read with `tomllib`.
  - `test_packaging.py::test_core_extension_imports_with_its_seven_submodules`:
    `import e8s._core.time` etc. succeed and `e8s._core.__version__ == e8s.__version__`.
  - `test_noxfile.py::test_uv_rebuilds_the_extension_when_rust_sources_change`: the
    `cache-keys` contain the six entries above.
  - `test_noxfile.py::test_every_job_that_syncs_installs_the_pinned_rust_toolchain`: every
    `ci.yml` job with a `uv sync` or `uv run` step has a `rustup toolchain install` step before
    it and a `Swatinem/rust-cache` step pinned by SHA.
  - `test_dockerfile.py`: the existing `test_image_copies_every_tracked_top_level_entry` fails
    until `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml` and `crates/` are copied.
- [ ] **Step 2:** Run `uv run pytest tests/unit/api/test_packaging.py tests/unit/tooling -q`:
      FAIL on the new tests.
- [ ] **Step 3:** Create the workspace from `phase-4-trial/` (drop `serde`, `serde_json`,
      `pythonize`, the counting allocator and the `platform-math` feature; workspace
      `license = "PolyForm-Noncommercial-1.0.0"`, `publish = false`). Switch the build
      backend; delete `[tool.hatch.*]`. `.gitignore`: `target/`, `e8s/*.so`, `e8s/*.pyd`,
      `.parity/`, `.cargo-tools/`, `fuzz/{target,artifacts,corpus}/`, `mutants.out*/`.
      Dockerfile builder stage: install rustup at a pinned version, build with the pinned
      toolchain, drop `SETUPTOOLS_SCM_PRETEND_VERSION`, copy the Rust files. In every `ci.yml`
      job that syncs: `rustup toolchain install` (reads `rust-toolchain.toml`) and the cache
      step, both before `uv sync`.
- [ ] **Step 4:** Verify that `maturin` honours `rust-toolchain.toml` and that the editable
      build uses the `release` profile; if `[tool.maturin] profile` is not honoured for
      editable installs, set it through `MATURIN_PEP517_ARGS` in `noxfile.py` and say so in
      the commit body.
- [ ] **Step 5:** Run every gate plus `uv run nox -s wheel core` and
      `docker compose -f docker/docker-compose.yml up --build`. Check the wheel holds the same
      files a hatchling wheel of the same tree would hold, plus the extension
      (`test_packaging.py::test_wheel_holds_every_tracked_package_file_plus_the_extension`:
      every tracked file under `e8s/`, the licence files, and `e8s/_core.abi3.so` or
      `.pyd`, nothing else).
- [ ] **Step 6:** Commit `build: build e8s with maturin and a Cargo workspace`.

### Task L0.2: the `rust` and `wheels` sessions, Rust lints and the FMA check

**Files:** Modify `noxfile.py`, `Cargo.toml` (`[workspace.lints]`). Create
`scripts/check_no_fma.py`. Test: `tests/unit/tooling/test_noxfile.py`,
`tests/unit/tooling/test_check_no_fma.py`.

**Interfaces:**

- `nox -s rust`: `cargo fmt --all --check`; `cargo clippy --workspace --all-targets --locked
-- -D warnings`; `cargo test --workspace --locked`; `cargo build -p e8s-wasm --target
wasm32-unknown-unknown --profile dist --locked`; on arm64 hosts, an unstripped
  `cargo build -p e8s-py --profile fma-check` then `python scripts/check_no_fma.py <lib>`.
  `cargo deny` and the notice check join it in L0.3.
- `nox -s wheels`: builds the sdist, builds a wheel from that sdist with `--profile dist`, and
  runs the ported packages' tests (`tests/parity` excluded) against it in fresh venvs on 3.13
  and 3.14.
- `[workspace.lints.rust] unsafe_code = "forbid"` (overridden to `deny` in `e8s-py` and
  `e8s-wasm` only if a macro needs it, with a reason); `[workspace.lints.clippy]` denies
  `unwrap_used`, `expect_used`, `panic`, `todo`, `unimplemented`.
- `check_no_fma(path: Path) -> list[str]`: disassembles with `llvm-objdump` from
  `llvm-tools`, returns each `fmadd`, `fmsub`, `fnmadd`, `fnmsub` (and x86 `vfmadd*` family)
  instruction whose enclosing symbol does not demangle to `libm::…::fma`; exit 1 when any.

- [ ] **Step 1:** Write `test_rust_session_runs_fmt_clippy_test_and_the_wasm_build`,
      `test_wheels_session_tests_the_wheel_on_313_and_314`,
      `test_flags_an_fma_outside_libm_fma` and `test_accepts_an_fma_inside_libm_fma` (both on
      recorded disassembly text, so the check can fail). Run: FAIL.
- [ ] **Step 2:** Implement; run `uv run nox -s rust wheels`: PASS.
- [ ] **Step 3:** Gates; commit `build: add the rust and wheels sessions and the FMA check`.

### Task L0.3: `cargo deny` and third-party notices in the wheel

**Files:** Create `deny.toml`, `about.toml`, `about.hbs`, `scripts/gen_third_party_notices.py`,
`e8s/licenses/THIRD_PARTY_RUST.txt` (generated). Modify `pyproject.toml` (`license`,
`license-files`), `noxfile.py` (`LICENSE_EXPRESSION`, `rust` session),
`tests/unit/api/test_packaging.py` (`LICENSE_EXPRESSION`, `LICENSE_FILES`). Test:
`tests/unit/tooling/test_third_party_notices.py`.

**Interfaces:**

- `deny.toml`: `[licenses] allow = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
"ISC", "Zlib", "Unicode-3.0", "Apache-2.0 WITH LLVM-exception"]`, `private = { ignore =
true }`; `[bans] deny` = `clipper2`, `geo-clipper`, `cp_sat`, `minicbor`, `poisson-diskus`,
  `serde_cbor`; `[advisories]` unmaintained and yanked are errors; `[sources]` crates.io only.
- `gen_third_party_notices(check: bool) -> int`: runs `cargo about generate` over the non-build
  dependency graph of `e8s-py`; writes or compares the notice file; returns the exit code.
- `License-Expression` becomes the v0.4.0 expression `AND` each licence the notice file names,
  in sorted order.

- [ ] **Step 1:** Write `test_third_party_notices_are_current` (regenerates and compares),
      `test_license_expression_names_every_linked_crate_licence` (parses the notice file) and
      `test_deny_config_bans_the_avoid_list`. Update the two packaging constants (build-subject
      tests; commit body says so). Run: FAIL.
- [ ] **Step 2:** Implement; add `cargo deny check` and `gen_third_party_notices --check` to
      `nox -s rust`. Run `uv run nox -s rust wheel`: PASS.
- [ ] **Step 3:** Gates; commit `build: check crate licences and ship Rust notices in the wheel`.

### Task L0.4: CI jobs and the wheel matrix

**Files:** Create `.github/workflows/wheels.yml` (from `phase-4-trial/wheels.yml`),
`scripts/check_release_version.py`. Modify `.github/workflows/ci.yml`, `scripts/ci_changes.py`.
Test: `tests/unit/tooling/test_noxfile.py`, `tests/unit/tooling/test_check_release_version.py`,
`tests/unit/tooling/test_ci_changes.py`.

**Interfaces:**

- `ci.yml` gains a `rust` job (ubuntu-24.04 and macos-15, `nox -s rust`) that runs when
  `ci_changes.py` reports `rust=true` (paths `crates/`, `Cargo.*`, `rust-toolchain.toml`,
  `deny.toml`, `fuzz/`).
- `wheels.yml`: the trial's eight wheel jobs and the sdist, every action pinned to a full SHA,
  `persist-credentials: false`, `maturin-action` args `--profile dist --locked`; each wheel
  job installs its wheel with its dependencies and the dev group's test tools, and runs
  `pytest tests/unit/{core,geo,time,sheet,cast,choreo} tests/golden` on 3.13 and 3.14.
  Triggers: pull requests touching `crates/**`, `Cargo.toml`, `Cargo.lock`,
  `rust-toolchain.toml`, `pyproject.toml`, `.github/workflows/wheels.yml`; tags `v*`; `workflow_dispatch`. On a tag, a first job runs
  `check_release_version.py`.
- `check_release_version(tag: str, cargo_toml: Path) -> None` raises `SystemExit(1)` with
  `E: tag vX.Y.Z does not match Cargo.toml version A.B.C` when they differ.

- [ ] **Step 1:** Write `test_wheels_workflow_runs_on_rust_changes_and_tags`,
      `test_wheels_workflow_builds_eight_wheels_and_the_sdist`,
      `test_every_wheels_action_is_pinned_to_a_sha`, `test_rust_job_runs_the_rust_session`,
      `test_ci_changes_reports_rust_for_a_crate_change`,
      `test_refuses_a_tag_that_differs_from_cargo` and `test_accepts_the_matching_tag`. Run: FAIL.
- [ ] **Step 2:** Implement; `zizmor` clean; Prettier clean on
      `.github/workflows/*.yml`.
- [ ] **Step 3:** Before merging, run `wheels.yml` once with `workflow_dispatch` on the PR
      branch (maintainer approval for the push). Record billed minutes per job in the PR body;
      confirm scipy and shapely install on `windows-11-arm`, and that `maturin-action` honours
      `rust-toolchain.toml` inside the manylinux container. A failing platform stops the task.
- [ ] **Step 4:** Gates; commit `ci: build and test the abi3 wheel matrix`.

### Task L0.5: the error boundary, panics, the part cache and the port rules

**Files:** Create `crates/e8s-core/src/base/error.rs`, `crates/e8s-py/src/{errors,cache}.rs`,
`crates/e8s-py/tests/panics.rs`, `tests/unit/api/test_core_boundary.py`,
`tests/unit/rules/test_port_rules.py`. Modify `scripts/gen_error_codes.py`,
`e8s/core/error_codes.py` (`CORE_PANIC`), `docs/error-codes.md` (generated),
`tests/unit/api/test_api_snapshot.py`, `noxfile.py` (`types`: `stubtest`).

**Interfaces:**

- `crates/e8s-core/src/base/error_codes.rs` (generated by `scripts/gen_error_codes.py` from
  `e8s/core/error_codes.py` and the classes of `e8s/core/errors.py`): `enum ErrorCode`,
  `enum ErrorKind` (one variant per `E8sError` subclass).
- `pub struct CoreError { kind: ErrorKind, code: ErrorCode, message: String, context:
Vec<(String, ContextValue)>, hint: String }`; `pub type CoreResult<T> = Result<T, CoreError>`.
- `e8s-py`: `fn guard<T>(name: &'static str, f: impl FnOnce() -> CoreResult<T> +
UnwindSafe) -> PyResult<T>`: maps `CoreError` to the Python class of its `ErrorKind` with the
  same fields; catches an unwinding panic and raises `E8sError` code `CORE_PANIC`, context
  `{"function": name}`, hint "This is a bug in e8s; report it with the function name."
- `pub struct PartCache<T>`: `fn get_or_insert_with(&self, part: &Bound<'_, PyAny>, convert:
impl FnOnce(&Bound<'_, PyAny>) -> PyResult<T>) -> PyResult<Arc<T>>`, keyed by `id(part)`,
  verified by a weak reference, evicted by the weak reference's callback.
- Private hooks in `e8s._core.core`: `_cache_probe(part: Box2) -> int` (caches a converted
  `Box2`, returns the cached value's address) and `_cache_stats() -> dict[str, int]` (`hits`,
  `misses`, `entries`), both absent from `_api_table.py`.
- The API snapshot renders a function of `e8s._core` from its stub in `e8s/_core/<area>.pyi`,
  so a port with an unchanged stub signature leaves the snapshot unchanged.
- `nox -s types` adds `python -m mypy.stubtest e8s._core`.

- [ ] **Step 1: Write the failing tests**
  - Rust, `crates/e8s-py/tests/panics.rs::panic_in_a_bound_call_raises_e8s_error` (pyo3
    `auto-initialize` in dev-dependencies; a closure that panics, run through `guard`; asserts
    the Python exception is `E8sError` with code `CORE_PANIC` and is not `PanicException`).
  - `test_core_boundary.py::test_core_error_raises_the_e8s_class_with_its_code`,
    `test_regression_core_rejects_nan_with_schema_error`,
    `test_part_cache_hits_for_the_same_object`,
    `test_part_cache_misses_for_an_equal_but_distinct_object`,
    `test_part_cache_evicts_a_collected_part`.
  - `test_port_rules.py::test_no_unsafe_in_our_crates`,
    `test_core_docstring_examples_run` (doctest over every public callable of the seven
    submodules), `test_every_public_core_function_has_an_example`,
    `test_library_never_bypasses_frozen_models` (no `object.__setattr__` under `e8s/`),
    `test_ported_package_matches_its_disposition` and
    `test_ported_packages_import_no_scipy_or_shapely` (both read
    `tests/unit/rules/ported/*.toml`; with no file they assert nothing and say so in the test
    id).
  - `test_api_snapshot.py::test_snapshot_renders_a_core_function_from_its_stub` (a stub on a
    temporary path).
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement; regenerate `error_codes.rs` and `docs/error-codes.md`.
- [ ] **Step 4:** Gates (`types` now runs `stubtest`); commit
      `feat(core): raise e8s errors from the core and cache frozen parts`.

### Task L0.6: the parity harness

**Files:** Create `scripts/build_oracle.py`, `tests/parity/{__init__,conftest,cases,compare,
oracle_runner}.py`, `tests/parity/flipped.toml`, `tests/unit/harness/test_parity_compare.py`.
Modify `noxfile.py` (`parity`), `pyproject.toml` (`parity` marker, excluded from `addopts`),
`.github/workflows/ci.yml` (`parity` and `parity-compare` jobs).

**Interfaces:**

- `build_oracle(ref: str = "v0.4.0", out: Path = Path(".parity/oracle-v0.4.0")) -> Path`:
  builds the wheel of `ref` in a temporary `git worktree`, creates a venv on the running Python
  version, installs the wheel with the dependencies locked at `ref` (`uv export --frozen`),
  writes `READY` holding the wheel's SHA-256, removes the worktree; returns the interpreter.
  Skips all work when `READY` matches.
- `Case(area: str, name: str, run: Callable[[ModuleType], Mapping[str, Output]], timed: bool =
False)`; `Output = npt.NDArray[np.float64] | npt.NDArray[np.int64] | JsonValue`;
  `load_cases(area: str) -> tuple[Case, ...]` imports `tests/parity/cases_<area>.py`. Cases use
  only public `e8s` names that exist in v0.4.0, and build their inputs from `e8s.examples`
  seeds, so both sides see the same input.
- Output keys carry their gate: `pos:` floats in metres, at most 1e-9 apart; `deg:` floats in
  degrees, at most 1e-9 apart; `val:` other floats, at most 4 ulp apart (or 1e-12 absolute);
  every other key must be identical (ints, IDs, orders, codes, JSON, hashes).
- `oracle_runner.py <area> <out>` runs under the oracle interpreter with `-I` and writes each
  output as raw little-endian float64 or int64 plus a shape header, or canonical JSON, and
  `timings.json` (minimum of 7 runs per timed case).
- `compare(oracle: Mapping[str, Output], core: Mapping[str, Output]) -> ParityReport` with
  `values`, `bit_identical`, `max_abs_m`, `max_abs_deg`, `max_ulp`, `ulp_histogram`,
  `discrete_mismatches`; `ParityReport.passed`.
- `nox -s parity -- <area> [--speed] [--digest PATH]`: builds the oracle, runs both sides,
  prints the table, fails on a gate; `--speed` also fails when a timed case's core minimum
  exceeds its oracle minimum; `--digest` writes the SHA-256 of every core output.
- `flipped.toml`: `[[case]] name = "...", ledger = "UP-..."`; such a case is compared against
  the fixed behaviour its ledger test asserts, not against the oracle.
- CI: `parity` runs on the 6 `check` cells for every pull request that touches `e8s/` or
  Rust, under hash seeds 0, 1 and 2, uploads the digests; `parity-compare` fails unless all 18
  digests are equal.

- [ ] **Step 1:** Write `test_parity_compare.py::test_compare_counts_bit_identical_values_and_ulps`,
      `test_compare_flags_a_position_past_the_gate` (1.1e-9 m fails),
      `test_compare_flags_a_discrete_mismatch`, `test_compare_flags_a_slower_case`,
      `test_flipped_case_is_compared_against_its_ledger_expectation`. Run: FAIL.
- [ ] **Step 2:** Implement. Add one self-check case,
      `cases_time.py::clock_on_200_seeded_timelines` (created here, owned by T afterwards), and
      run `uv run nox -s parity -- time`: the working tree is still Python, so every value is
      bit-identical.
- [ ] **Step 3:** Gates; commit `test: compare the working tree against a v0.4.0 oracle`.

### Task L0.7: fuzz targets and mutation testing

**Files:** Create `fuzz/{Cargo.toml,fuzz_targets/{refs,strict_json,svg_path,font,selection,
timeline}.rs}`, `.github/workflows/nightly-core.yml`. Modify `noxfile.py` (`rustfuzz`,
`mutants`), `pyproject.toml` and `uv.lock` (`mutmut` pinned with `uv add --dev`,
`[tool.mutmut]`), `Cargo.toml` (`exclude = ["fuzz"]`). Test:
`tests/unit/tooling/test_noxfile.py`.

**Interfaces:**

- `fuzz/` is a `cargo-fuzz` crate `e8s-fuzz` depending on `e8s-core`; each target is
  `fuzz_target!(|_data: &[u8]| {})` until its lane fills it.
- `nox -s rustfuzz -- [target] [--seconds N]` runs each target with the nightly toolchain named
  in `FUZZ_TOOLCHAIN` (a dated nightly, pinned in `noxfile.py`); default 60 s per target.
- `nox -s mutants` runs `cargo mutants -p e8s-core` and `mutmut run` over the Python modules
  listed under `stays` in `tests/unit/rules/ported/*.toml`; writes `mutants-summary.json`
  with both scores; fails only when a tool cannot run.
- `nightly-core.yml`: daily; `rustfuzz` for 10 minutes per target, then `mutants`; uploads
  crash artifacts and the summary; pinned SHAs.

- [ ] **Step 1:** Write `test_fuzz_crate_declares_one_target_per_parser` (names equal the six),
      `test_mutants_session_reports_without_gating`,
      `test_nightly_core_runs_fuzz_and_mutants`. Run: FAIL.
- [ ] **Step 2:** Implement; run `uv run nox -s rustfuzz -- refs --seconds 5` and
      `uv run nox -s mutants` once locally; record that both run.
- [ ] **Step 3:** Gates; commit `ci: fuzz the core parsers and run mutation tests nightly`.

---

## Lane C: `core`

Branch `feat/p4-c`. Gate: L0 merged. Skills: `differential-review` on C2.

### Task C1: numbers, units, caps and ID parsing

**Files:** `crates/e8s-core/src/base/{numbers,units,caps,ids}.rs`, `crates/e8s-py/src/base.rs`,
`e8s/_core/core.pyi`, `e8s/core/{numbers,units,caps,ids}.py`, `fuzz/fuzz_targets/refs.rs`,
`tests/parity/{cases_core,test_parity_core}.py`.

**Covered v0.4.0 tests:** `tests/unit/core/test_{numbers,units,caps,ids}.py`,
`tests/fuzz/test_fuzz_finite.py`.

- [ ] **Step 1:** New tests: `test_parity_core.py::test_plain_decimal_text_matches_v040_on_a_million_floats`
      (includes subnormals, -0.0, 1e±308), `test_unit_conversions_match_v040`,
      `test_reference_and_transition_parsing_match_v040_on_the_fuzz_corpus`. Then the port
      procedure. The `refs` fuzz target calls the reference and transition parsers.
- [ ] **Step 2:** Commit `perf(core): parse IDs and format numbers in the core`.

### Task C2: canonical hashing and strict JSON

**Files:** `crates/e8s-core/src/base/{canonical,strict_json}.rs` (crates `serde_json_canonicalizer`,
`sha2`), `crates/e8s-py/src/base.rs`, `e8s/core/{canonical,strict_json}.py`,
`fuzz/fuzz_targets/strict_json.rs`.

**Covered v0.4.0 tests:** `tests/unit/core/test_{canonical,strict_json}.py`,
`tests/property/test_document_dump.py`, `tests/unit/show/test_legacy_fixtures.py` (read only;
it runs, it is not edited).

**Interfaces:** the frozen wire constants stay byte-identical: hash domain `showpy-hash/1` and
map tag `$showpy:map`. The binding walks Python values (models through the ADR 0026 interface,
`ReadOnly` maps, tuples, numpy arrays) into the canonical form in one pass.

- [ ] **Step 1:** New tests: `test_parity_core.py::test_content_hash_matches_v040_on_every_stored_fixture`
      (every document under `tests/fixtures/repos/`), `test_canonical_bytes_match_v040_on_generated_json`
      (Hypothesis JSON values, ECMAScript number text), `test_strict_json_refuses_what_v040_refuses`
      (duplicate keys, NaN, `Infinity`, depth and size caps: identical error codes). Then the
      port procedure; timed cases for `content_hash` on a 300 x 60 show.
- [ ] **Step 2:** Commit `perf(core): hash and parse JSON in the core`.

### Task C3: utilities, the seeded generator and CSV cells

**Files:** `crates/e8s-core/src/rng/`, `crates/e8s-core/src/base/{util,csv_cells}.rs`,
`crates/e8s-py/src/base.rs`, `e8s/core/{util,csv_cells}.py`, `tests/unit/core/test_core_rng.py`.

**Interfaces:** `rng::Pcg64::from_seed(seed: u64) -> Pcg64` reproducing numpy's
`PCG64(SeedSequence(seed))`; `random(&mut self) -> f64`, `integers(&mut self, high: u64) ->
u64` (numpy's bounded Lemire method), `choice_without_replacement(&mut self, n: usize, size:
usize) -> Vec<usize>` (numpy `Generator.choice(n, size, replace=False)`). `seeded_rng(seed)`
stays public and still returns a `numpy.random.Generator`. The shapely branch of `util.py`
stays until X2 (interop).

- [ ] **Step 1:** New tests: `test_core_rng.py::test_core_pcg64_matches_numpy_on_10000_seeds`
      (each of the three draws, 1,000 values per seed, bit for bit),
      `test_parity_core.py::test_float_comparison_and_id_ranges_match_v040`. Then the port
      procedure.
- [ ] **Step 2:** Commit `perf(core): seeded randomness and utilities in the core`.

### Task C4: `core` parity run and deletion

The deletion task. `core` has no "moves" module: every computation module is a re-export (P2).
Write `ported/core.toml` with `interop = ["e8s/core/types.py", "e8s/core/util.py"]`. Commit
`refactor(core)!: serve core computation from the compiled core`.

---

## Lane G: `geo`

Branch `feat/p4-g`. Gate: C4 merged; G2 also after Phase 3 task M2 merges (P4).

### Task G1: curves and the arc-length machinery

**Files:** `crates/e8s-core/src/geo/{curve,arc_length,flatten,project}.rs`,
`crates/e8s-py/src/geo.rs`, `e8s/_core/geo.pyi`, `e8s/geo/{curve_base,curves}.py`.

**Covered v0.4.0 tests:** `tests/unit/geo/test_{curve_base,curve_base_evaluation,curves,curves_evaluation}.py`,
`tests/property/test_geo_transforms.py`.

**Interfaces:** the 11 curve kinds stay pydantic models; `point_at`, `tangent_at`, `length`,
`flatten`, `project` and `batch_exact` call the core through a `PartCache` per curve that holds
the 2,048-sample chord table. The `cached_property` table goes; `CurveBase.model_copy` still
revalidates. Bezier binomials are exact integers (replaces `scipy.special.comb`).

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_curve_points_match_v040_for_every_kind`
      (`pos:` at 1,001 parameters per curve), `test_curve_inverse_keeps_np_interp_edge_rules`,
      `test_projection_matches_v040_with_its_scan_and_golden_section`. Rust:
      `geo::curve::tests::exponent_two_is_an_exact_square`. Then the port procedure, with timed
      cases for `point_at` batches.
- [ ] **Step 2:** Commit `perf(geo): evaluate curves in the core`.

### Task G2: SVG paths, stroke fonts and text layout

**Files:** `crates/e8s-core/src/geo/{svg,fonts,font_strokes,text_layout,text}.rs`,
`e8s/geo/{svg,fonts,font_strokes,text_layout,text}.py`, `fuzz/fuzz_targets/{svg_path,font}.rs`.

**Covered v0.4.0 tests:** `tests/unit/geo/test_{svg_text,fonts,text_layout}.py`,
`tests/golden/test_golden_text.py`, `tests/fuzz/test_fuzz_{svg,fonts}.py`.

**Interfaces:** caps unchanged and checked before allocation: `MAX_LAYOUT_CHARS = 1_000`,
`MAX_LAYOUT_EXTENT_M = 1e6`, `MAX_FONT_BYTES` 8 MiB, `MAX_GLYPH_PIECES = 512`,
`MAX_STROKE_PIECES = 384`, `MAX_LAYOUT_PIECES = 512_000`, `MAX_LAYOUT_CURVE_PIECES = 60_000`.
`Font` and `FontRegistry` stay pydantic; font parsing and hash checks (`FONT_HASH_MISMATCH`)
delegate. Bundled font bytes are unchanged (their SHA-256 URIs are identities).

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_svg_pieces_match_v040_on_the_fuzz_corpus`,
      `test_text_outline_matches_v040_for_every_bundled_font`,
      `test_regression_layout_caps_raise_before_allocating` (a 1,001-character text raises
      with no large allocation, measured by `tracemalloc` under 1 MB). Then the port procedure;
      the two fuzz targets call the SVG and font parsers.
- [ ] **Step 2:** Commit `perf(geo): parse SVG and lay out text in the core`.

### Task G3: affine maps, transforms and snapping

**Files:** `crates/e8s-core/src/geo/{affine,transform,snapping,checks}.rs`,
`e8s/geo/{affine,transform_base,transforms,snapping,checks}.py`.

**Covered v0.4.0 tests:** `tests/unit/geo/test_{affine,transforms,checks,set_forwarding}.py`,
`tests/property/test_geo_transforms.py` (360° rotation is identity).

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_transforms_match_v040_on_points_curves_and_sets`,
      `test_snap_reports_match_v040`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(geo): transform and snap in the core`.

### Task G4: regions and shapes without shapely

**Files:** `crates/e8s-core/src/geo/{region,shapes}.rs` (crates `i_overlay`, `geo`),
`e8s/geo/{regions,shapes}.py`, `Region`'s validator in `e8s/core/types.py` (method bodies only).

**Covered v0.4.0 tests:** `tests/unit/geo/test_{regions,shapes,shapes_evaluation}.py`,
`tests/unit/core/test_types.py`.

**Interfaces:** booleans, validity (`shapely.is_valid` semantics), area, `covers` (boundary
inclusive, as GEOS) and buffers in the core. The output ring orientation and start vertex follow
v0.4.0's (GEOS-normalized) rule wherever a value is returned. `to_shapely`, `from_shapely` and
the `Polygon` alias stay until X2 (P3) and are the only shapely imports left in `geo`.

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_region_rings_keep_v040_orientation_and_start`,
      `test_region_booleans_match_v040_by_area_and_vertices`,
      `test_regression_invalid_region_is_refused_without_shapely` (a bow-tie ring raises
      `SchemaError` with v0.4.0's code). Then the port procedure. A test that reads a GEOS
      artifact the core does not reproduce stops the task (Global Constraints).
- [ ] **Step 2:** Commit `perf(geo): build and combine regions in the core`.

### Task G5: spacing and scatter

**Files:** `crates/e8s-core/src/geo/spacing.rs`, `e8s/geo/spacing.py`.

**Covered v0.4.0 tests:** `tests/unit/geo/test_{spacing,spacing_evaluation}.py`,
`tests/property/test_sheet_properties.py` (spacing is even).

**Interfaces:** `space_along`, `fill`, blocks, rings, Bridson scatter and farthest-point thinning
use `rng::Pcg64` from C3 with the same seeds and draw order; `apportion` stays exact integer
arithmetic; `fill`'s containment uses `covers` from G4.

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_scatter_matches_v040_point_for_point`
      (seeds 0 to 99), `test_fill_and_space_along_match_v040`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(geo): place spots in the core`.

### Task G6: measures

**Files:** `crates/e8s-core/src/geo/measure.rs` (crate `rstar`), `e8s/geo/measure.py`.

**Covered v0.4.0 tests:** `tests/unit/geo/test_measure.py`.

**Interfaces:** nearest neighbours replace `cKDTree` with ties broken by lower index, as
`cKDTree.query` returns them; `cdist` becomes a fixed-order loop; `minimize_scalar` (bounded) is
replaced by a port of scipy's `fminbound` with the same `xatol` and iteration cap, so the
minimizer and its evaluations match.

- [ ] **Step 1:** New tests: `test_parity_geo.py::test_neighbours_match_v040_including_ties`,
      `test_symmetry_minimizer_matches_v040`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(geo): measure in the core`.

### Task G7: `geo` parity run and deletion

The deletion task. Deletes the thirteen "moves" modules of `geo`; moves `Matrix`, `Area` and the
layout caps to `e8s/geo/models.py`; rewrites import lines in `e8s/{sheet,cast,choreo}/` and the
tests. `ported/geo.toml` has `interop = ["e8s/geo/curve_base.py"]`. Commit
`refactor(geo)!: delete the Python geometry computation`.

---

## Lane T: `time`

Branch `feat/p4-t`. Gate: C4 merged. Runs beside G.

### Task T1: the clock, compiled once per timeline

**Files:** `crates/e8s-core/src/time/{clock,pieces}.rs`, `crates/e8s-py/src/time.rs`,
`crates/e8s-wasm/src/time.rs`, `e8s/_core/time.pyi`, `e8s/time/clock.py`,
`fuzz/fuzz_targets/timeline.rs`, `tests/unit/time/test_clock_cache.py`.

**Covered v0.4.0 tests:** `tests/unit/time/test_clock.py`, `tests/property/test_time_properties.py`
(count to seconds round trip), `tests/fuzz/test_fuzz_clock.py`, `tests/bench/test_time_clock.py`.

**Interfaces:** `count_to_seconds`, `seconds_to_count`, `tempo_at` and the batch forms; the
compiled clock (pieces, cumulative seconds) is cached beside its `Timeline` part. The port is
line for line: same piece splitting and operation order, `log1p` and `log` from `libm`. The wasm
crate exports a batch `count_to_seconds` through wasm-bindgen for the parity check.

- [ ] **Step 1:** New tests: `test_clock_cache.py::test_regression_count_to_seconds_compiles_each_timeline_once`
      (`_cache_stats` shows one miss for 1,000 calls on one `Timeline`),
      `test_parity_time.py::test_clock_matches_v040_on_10000_timelines` (`val:`, seed
      `20261123`; bit-identical where no ramp), `test_wasm_clock_matches_native_on_10000_timelines`
      (built with `wasm-bindgen`, run under Node; every value bit-identical). Then the port
      procedure; the timed case `count_to_seconds` must beat v0.4.0's 0.78 µs per call on the
      same machine.
- [ ] **Step 2:** Commit `perf(time): run the clock in the core`.

### Task T2: measures and timeline checks

**Files:** `crates/e8s-core/src/time/{measures,validate}.rs`, `e8s/time/{measures,validate}.py`.

**Covered v0.4.0 tests:** `tests/unit/time/test_{measures,validate}.py`.

- [ ] **Step 1:** New tests: `test_parity_time.py::test_measure_labels_match_v040`,
      `test_timeline_findings_match_v040` (codes and order identical). Then the port procedure.
- [ ] **Step 2:** Commit `perf(time): measure labels and timeline checks in the core`.

### Task T3: `time` parity run and deletion

The deletion task. `clock`, `measures` and `validate` are re-exports (P2); `timeline.py` and
`models.py` stay. Commit `refactor(time)!: serve time computation from the compiled core`.

---

## Lane S: `sheet`

Branch `feat/p4-s`. Gate: G7 merged.

### Task S1: bounds and the similarity fit

**Files:** `crates/e8s-core/src/sheet/{bounds,similarity}.rs`, `crates/e8s-py/src/sheet.rs`,
`e8s/_core/sheet.pyi`, `e8s/sheet/{bounds,replace}.py`.

**Covered v0.4.0 tests:** `tests/unit/sheet/test_{bounds,replace}.py`,
`tests/property/test_sheet_properties.py`.

**Interfaces:** bounds use `covers` from G4 (no shapely). `similarity_fit` solves the 2D
least-squares similarity in closed form with v0.4.0's reflection guard; parity is `pos:`.

- [ ] **Step 1:** New tests: `test_parity_sheet.py::test_bounds_match_v040_on_and_off_every_template`,
      `test_similarity_fit_matches_v040_including_the_reflection_guard`. Then the port
      procedure.
- [ ] **Step 2:** Commit `perf(sheet): bounds and similarity fit in the core`.

### Task S2: `sheet` parity run and deletion

The deletion task. Deletes `bounds.py` and `replace.py`; `surfaces.py` and `templates.py` stay
until sheets become data (ADR 0022, Phase 5). Commit
`refactor(sheet)!: delete the Python sheet computation`.

---

## Lane K: `cast`

Branch `feat/p4-k`. Gate: G7 merged. Runs beside S.

### Task K1: the selection language and spatial picks

**Files:** `crates/e8s-core/src/cast/{grammar,picks}.rs`, `crates/e8s-py/src/cast.rs`,
`e8s/_core/cast.pyi`, `e8s/cast/select.py` (all but `mirror_pairs`),
`fuzz/fuzz_targets/selection.rs`.

**Covered v0.4.0 tests:** `tests/unit/cast/test_select.py`, `tests/property/test_select_properties.py`,
`tests/fuzz/test_fuzz_select.py`.

**Interfaces:** the recursive-descent parser keeps its depth cap, its error codes and its error
positions; operators need spaces as today. Spatial picks use `rstar` with ties by lower index.

- [ ] **Step 1:** New tests: `test_parity_cast.py::test_selection_results_and_errors_match_v040`
      (the fuzz corpus plus every expression in `tests/unit/cast/test_select.py`),
      `test_regression_deep_nesting_raises_schema_error_not_a_panic` (nesting 100,000 deep).
      Then the port procedure.
- [ ] **Step 2:** Commit `perf(cast): parse selections and pick spots in the core`.

### Task K2: mirror pairs without scipy

**Files:** `crates/e8s-core/src/assign/{mod,sparse}.rs` (plus empty `dense.rs`,
`bottleneck.rs`, `ordered.rs`), `crates/e8s-core/src/cast/mirror.rs`, `e8s/cast/select.py`
(`mirror_pairs`).

**Interfaces:** `assign::sparse::min_weight_full_matching(rows: usize, cols: usize, edges:
&[(usize, usize, f64)]) -> CoreResult<Vec<usize>>`, reproducing scipy's
`min_weight_full_bipartite_matching` (LAPJVsp) including its choice among equal-cost optima;
connected components by union-find in index order.

- [ ] **Step 1:** New tests: `test_parity_cast.py::test_sparse_solver_matches_scipy_on_mirror_pair_ties`
      (10,000 symmetric seeded layouts; the oracle side calls scipy),
      `test_mirror_pairs_match_v040`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(cast): pair mirrored spots in the core`.

### Task K3: `cast` parity run and deletion

The deletion task. `select.py` is a re-export (P2); `spots.py`, `rewrite.py` and `models.py`
stay. Commit `refactor(cast)!: serve cast computation from the compiled core`.

---

## Lane E: the choreo engine

Branch `feat/p4-e`. Gate: T3, S2 and K3 merged and ADR 0027 `Accepted`. Skills:
`differential-review` on E2.

### Task E1: motions and compiling path rows

**Files:** `crates/e8s-core/src/choreo/engine/{easing,compile,plan}.rs`,
`crates/e8s-py/src/engine.rs`, `e8s/_core/engine.pyi`, `e8s/choreo/{motion,engine_compile}.py`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_{motion,engine_compile}.py`.

**Interfaces:** every `PathStyle` compiles to a core `MotionPlan` (curve plus time map), cached
beside its `TransitionSpec`. Easings, including periodic `steps`, keep their `easing_breaks`.
Custom motions arrive as ADR 0027 prescribes; `MotionRegistry` stays a Python registry.

- [ ] **Step 1:** New tests: `test_parity_engine.py::test_plans_match_v040_for_every_path_style`,
      `test_compile_errors_match_v040` (codes and context). Then the port procedure.
- [ ] **Step 2:** Commit `perf(choreo): compile path rows in the core`.

### Task E2: positions, sampling, segments and facing

**Files:** `crates/e8s-core/src/choreo/engine/{engine,sample,segments,facing,batch}.rs`,
`crates/e8s-wasm/src/engine.rs`, `e8s/choreo/{engine,engine_facing,engine_batch}.py`,
`tests/bench/test_core_budgets.py`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_{engine,engine_facing,engine_batch}.py`,
`tests/property/test_engine_properties.py` (paths end on targets; 9 probes per segment),
`tests/golden/test_golden_{opener,large}.py`, `tests/unit/choreo/test_paths.py`,
`tests/unit/choreo/test_routes.py`.

**Interfaces:** `PathEngine(show)` assembles its core value from the part cache (`Arc` parts);
`positions`, `sample`, `frames` and velocities return numpy arrays that own the Rust buffer;
`locate` returns `None` outside `[0, total]`; `segments` returns local counts; facing runs the
three phases of v0.4.0.

- [ ] **Step 1:** New tests: `test_parity_engine.py::test_engine_positions_match_v040_on_the_goldens`
      (`pos:` every 1/16 count; segment counts and IDs identical),
      `test_wasm_engine_matches_native_on_the_goldens`,
      `test_core_budgets.py::test_sample_300_by_60_over_300_frames_per_second`,
      `test_compile_all_300_by_64_under_2_s` (`seeded_show(11, 300, 64)`),
      `test_engine_after_a_one_spot_edit_converts_one_part`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(choreo): run PathEngine in the core`.

### Task E3: gaps, motion statistics and the floor readers

**Files:** `crates/e8s-core/src/choreo/engine/{avoid,stats,read}.rs`,
`e8s/choreo/{avoid,motion_stats,read}.py`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_{avoid,avoid_evaluation,motion_stats,read,read_describe,facing}.py`,
`tests/property/test_read_properties.py`, `tests/golden/test_golden_large.py` (`min_gaps`).

**Interfaces:** `min_gaps` keeps the all-pairs scan with the whole-move box prune (the broad
phase is Phase 5); fixes build `BendFix` and path-row models through the binding.

- [ ] **Step 1:** New tests: `test_parity_engine.py::test_min_gaps_match_v040_on_the_large_golden`
      (pairs and order identical, `pos:` gaps), `test_floor_reader_arrays_match_v040`. Record
      `min_gaps` and `frames` (8 counts at 30 fps) times; not gated. Then the port procedure.
- [ ] **Step 2:** Commit `perf(choreo): gaps, statistics and floor readers in the core`.

### Task E4: engine parity run and deletion

The deletion task. Deletes `engine_compile`, `engine_facing`, `engine_batch`, `avoid`,
`motion_stats` and `read`; `engine.py` is a re-export (P2). Commit
`refactor(choreo)!: delete the Python engine computation`.

---

## Lane F: choreo forms and matching

Branch `feat/p4-f`. Gate: T3, S2 and K3 merged; F2 and F3 also need ADR 0027 `Accepted`.
Skills: `differential-review` on F1.

### Task F1: the assignment solvers

**Files:** `crates/e8s-core/src/assign/{dense,bottleneck,ordered}.rs`,
`crates/e8s-core/src/choreo/matching/{solve,swaps}.rs`, `crates/e8s-py/src/forms.rs`,
`e8s/_core/forms.pyi`, `e8s/choreo/match_solve.py`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_match_solve_evaluation.py`,
`tests/property/test_match_properties.py`.

**Interfaces:** `assign::dense::solve_sum(cost: ArrayView2<f64>) -> CoreResult<Vec<usize>>`, our
own Jonker-Volgenant (Crouse's rectangular shortest augmenting path) returning scipy's
`linear_sum_assignment` choice among equal-cost optima; infinite cost forbids a pair;
`min_max_threshold` keeps the binary search over sorted unique costs; `solve_ordered`,
`crossing_mask`, `count_crossings` and `improve_by_swaps` unchanged. If the solver is
translated from scipy's source, scipy's BSD-3-Clause notice is added to `about.toml`.

- [ ] **Step 1:** New tests: `test_parity_forms.py::test_dense_solver_matches_scipy_on_tie_heavy_instances`
      (10,000 seeded matrices with integer costs 0 to 3, sizes 1 to 64, rectangular included;
      the oracle side calls scipy), `test_bottleneck_threshold_matches_v040`,
      `test_solver_refuses_an_infeasible_matrix_like_v040`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(choreo): solve assignments in the core`.

### Task F2: matching and costs

**Files:** `crates/e8s-core/src/choreo/matching/{cost,match_}.rs`,
`e8s/choreo/{match_cost,match}.py`, `tests/bench/test_core_budgets.py` (one test added).

**Covered v0.4.0 tests:** `tests/unit/choreo/test_match.py`, `tests/property/test_match_properties.py`.

**Interfaces:** one mask per constraint as today; `cost_fn` arrives as ADR 0027 prescribes.

- [ ] **Step 1:** New tests: `test_parity_forms.py::test_matches_match_v040_for_every_objective`,
      `test_core_budgets.py::test_match_500_spots_default_objective_under_0_2_s`. Then the port
      procedure.
- [ ] **Step 2:** Commit `perf(choreo): match spots to slots in the core`.

### Task F3: form layout, text slots and form fitting

**Files:** `crates/e8s-core/src/choreo/forms/{layout,text,spread,count,fit}.rs`,
`e8s/choreo/{form_layout,form_text,text_spread,text_count,form_fit}.py`, the slot-generation
functions of `e8s/choreo/forms.py`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_{forms,form_fns,form_text,text_spread,text_count,form_fit,font_registry_threading}.py`,
`tests/property/test_form_properties.py`, `tests/golden/test_golden_text.py`.

**Interfaces:** `MAX_TEXT_SPOTS = 10_000` checked before allocation; `JUNCTION_GAP_SHARE = 0.25`;
`COINCIDENT_TOL_M` unchanged; the Kåsa plus Gauss-Newton circle fit and the line PCA keep their
iteration caps; generated forms call registered form functions as ADR 0027 prescribes.

- [ ] **Step 1:** New tests: `test_parity_forms.py::test_slots_match_v040_for_every_recipe_kind`,
      `test_text_slots_match_v040_on_the_text_golden`, `test_form_fit_matches_v040`. Then the
      port procedure.
- [ ] **Step 2:** Commit `perf(choreo): lay out forms and text slots in the core`.

### Task F4: set analysis and measures

**Files:** `crates/e8s-core/src/choreo/forms/{analysis,set_measure}.rs`,
`e8s/choreo/{set_analysis,set_measure}.py`; the numeric calls of `set_snapping.py` and
`set_transforms.py` (which stay, P1) move to G3's transforms and F1's `solve_sum`.

**Covered v0.4.0 tests:** `tests/unit/choreo/test_{set_analysis,set_measure,set_snapping,set_transforms}.py`.

- [ ] **Step 1:** New tests: `test_parity_forms.py::test_set_analysis_matches_v040`,
      `test_set_transforms_match_v040_without_scipy`. Then the port procedure.
- [ ] **Step 2:** Commit `perf(choreo): analyse and measure sets in the core`.

### Task F5: forms and matching parity run and deletion

The deletion task. Deletes `form_fit`, `form_layout`, `form_text`, `match`, `match_cost`,
`match_solve`, `set_analysis`, `set_measure`, `text_count` and `text_spread`. Commit
`refactor(choreo)!: delete the Python forms and matching computation`.

---

## Lane X: exit (serial, after every lane merges)

### Task X1: exit measurements

**Files:** Create `tests/parity/test_parity_cross_cell.py`.

- [ ] **Step 1:** Write `test_core_outputs_are_byte_identical_across_cells_and_seeds` (reads the
      18 digests the CI `parity` jobs uploaded; local runs compare the local digest against the
      last `main` artifact) and `test_no_fuzz_target_is_a_placeholder`.
- [ ] **Step 2:** Run every nox session, including `slow`, `bench`, `wheel`, `core`, `wheels`,
      `parity` for every area with `--speed`, `rustfuzz` (10 minutes per target) and
      `mutants`. Run the budgets on the maintainer's Mac and the CI runner class; record
      machine and load.
- [ ] **Step 3:** If a committed golden failed anywhere, refresh it once
      (`E8S_UPDATE_GOLDEN=1`), only within 1e-9 m and with identical counts, order and IDs, with
      the harness report attached for maintainer review (D36). Otherwise record "no refresh".
- [ ] **Step 4:** Commit `test: prove the Phase 4 exit criteria`.

### Task X2: drop scipy and shapely

**Files:** `pyproject.toml`, `uv.lock`, `e8s/core/{types,util}.py`, `e8s/geo/curve_base.py`,
`e8s/_api_table.py`, the API snapshot, `tests/unit/rules/ported/{core,geo}.toml`.

- [ ] **Step 1:** Write `test_shapely_and_scipy_are_not_dependencies` (reads `pyproject.toml`)
      and empty every `interop` list. Run: FAIL.
- [ ] **Step 2:** Remove `Region.to_shapely`, `Region.from_shapely`, `CurveBase.to_shapely` and
      the `Polygon` alias (P3) and the shapely branch of `util.py`; remove `scipy`, `shapely`,
      `scipy-stubs` and `types-shapely`; `uv lock`; `deptry` clean.
- [ ] **Step 3:** Gates; refresh the snapshot; commit
      `refactor!: drop scipy and shapely` with a `BREAKING CHANGE:` footer naming the four
      names.

### Task X3: release v0.6.0

**Files:** `docs/releases/v0.6.0.md`, `docs/HANDOFF.md`, `docs/e8s-api-reference.md`,
`Cargo.toml` (version `0.6.0`), `CHANGELOG.md` (generated).

- [ ] **Step 1:** Release notes: building from source needs Rust 1.100.0; the wheel matrix;
      the removed names; the exit table; numbers that moved in the last bits and the parity
      report. `HANDOFF.md` lists the P2 re-export modules for the next owner of `e8s/show/`.
- [ ] **Step 2:** Set the workspace version to `0.6.0`; `uv run nox -s changelog`; Prettier;
      commit `docs: release notes for v0.6.0`.
- [ ] **Step 3:** Ask the maintainer to approve the push, the merge and the `v0.6.0` tag; the
      tag runs `wheels.yml`, whose first job checks the tag against `Cargo.toml`.

---

## Exit criteria

"Today" is v0.4.0 on the maintainer's Mac; the trial's numbers are in ADR 0025.

| Criterion                                                                      | Today                                 | Exit                                   |
| ------------------------------------------------------------------------------ | ------------------------------------- | -------------------------------------- |
| `sample`, 300 x 60                                                             | 77.8 frames/s                         | > 300 frames/s                         |
| `compile_all`, 300 x 64                                                        | 1.49 s at 300 x 60 (trial)            | < 2 s                                  |
| `match`, default objective, 500 spots                                          | 0.082 s                               | < 0.2 s                                |
| Peak memory, `compile_all` plus 60 samples, 300 x 60                           | 395 MB RSS                            | < 500 MB                               |
| Core outputs across the 6 CI cells and 3 hash seeds                            | not comparable (numpy, platform libm) | byte-identical                         |
| Core against the v0.4.0 oracle                                                 | —                                     | positions ≤ 1e-9 m; discrete identical |
| Native against wasm on the goldens                                             | —                                     | bit-identical                          |
| Ported cases slower than v0.4.0                                                | —                                     | 0                                      |
| scipy and shapely imported by `core`, `geo`, `time`, `sheet`, `cast`, `choreo` | 15 modules                            | 0; neither is a dependency             |
| v0.4.0 tests of the six packages                                               | pass on Python                        | pass on the core, no edited assertion  |
| `unsafe` blocks in our crates                                                  | —                                     | 0                                      |
| Nightly fuzz run                                                               | —                                     | no panic, no non-`E8sError` exception  |
| Wheels                                                                         | one pure-Python wheel                 | 8 abi3 wheels and an sdist, all tested |
| Mutation score (Rust and Python)                                               | —                                     | recorded (the 85% gate is Phase 5)     |
| API snapshot against v0.4.0                                                    | —                                     | differs only by the four P3 names      |
| `min_gaps` (one transition, 4 counts) and `frames` (8 counts, 30 fps)          | 20.1 s and 3.06 s                     | measured and recorded, not gated       |

## Counts

- Lanes: 9 (L0, C, G, T, S, K, E, F, X). Tasks: 38 (L0 7, C 4, G 7, T 3, S 2, K 3, E 4, F 5,
  X 3); 21 port tasks, 7 deletion tasks.
- Fuzz targets: 6 (`refs`, `strict_json`, `svg_path`, `font`, `selection`, `timeline`).
- Ledger: no ID is planned to flip; any the core fixes as written flips with its port task.
