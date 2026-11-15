# Handoff: building the `showpy` library

Audience: the next agent or engineer continuing this repository. Scope: state of work, what is
left, and the traps already hit.

Last updated: 2026-11-15

## Where things stand

| Item          | Value                                                                                                     |
| ------------- | --------------------------------------------------------------------------------------------------------- |
| Repository    | `abj360/ShowPy` (public since 2026-11-15); split into its own repository on 2026-11-12 with full history  |
| Gates         | `nox -s tests`: about 1,500 passed, `Ledger: 528 open` (2026-11-13); ruff, mypy `--strict`, pyright clean |
| CI            | lint, types, tests, dependency scan, docker build, on every pull request and push to main                 |
| Defect ledger | `Ledger: 528 open` in the default suite, `Ledger: 1 open` in `nox -s slow` (2026-11-13)                   |
| Out of scope  | `stagecheck`, `showpy.packs` and `showpy.index`, by the user's decision (2026-11-11)                      |
| Pushing       | needs explicit approval                                                                                   |

**Repository split (2026-11-12).** Agent-only code moved to a separate consumer repository with
its history: set-script builds and the bubblewrap sandbox (`show/build.py`, `build_models.py`,
`sandbox.py`, `sandbox_child.py`), agent note drafting (`draft_notes`, `NoteDrafter`,
`DraftedNote`) and `summarize_events`. `showpy` must never import from a consumer; consumers pin
a `showpy` release tag.

The user's instructions, in their words:

- "the reference is just a suggestion, if there's a much better algorithm or material to strengthen this library then build it"
- "fix every contradiction"

Use `uv` only, never pip.

## Read first

1. `CLAUDE.md` (project) and `~/.claude/CLAUDE.md` (user style: file headers with a `Contains:` section, verb-first docstrings, no AI trailers on commits).
2. `docs/adr/0001-library-layout-and-reference-fixes.md`: 24 fixes to the reference, the origin convention (front-center), the facing vector `(sin θ, −cos θ)`, and why subpackage `__init__` files are empty.
3. `docs/adr/0002-path-engine.md`, plus its three addenda: segment probe density, time-based measures living in `showpy.choreo`, and the `min_gaps` hotspot forecast.
4. `docs/upgrade-plan.md` and `docs/superpowers/plans/2026-11-13-phase-0-safety-net.md`: the upgrade program and its first phase.

## Module map (what exists)

| Package  | Modules                                                                                                                                                                                                                                              |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core`   | errors, ids, types (`FrozenModel`, `Region`, `Box2`), units (immutable `UnitRegistry`), util (`close_matches`, `content_hash`, `seeded_rng`), selection, validation                                                                                  |
| `geo`    | curve_base, curves (11 kinds, arc-length param), svg, text (stroke font), shapes, regions, spacing (Bridson scatter), affine, transforms + transform_base + snapping, measure, analysis, models                                                      |
| `time`   | models, timeline, clock (ramps and holds, analytic inverse), measures, validate                                                                                                                                                                      |
| `cast`   | models, spots, select (expression grammar), rewrite (renames IDs everywhere)                                                                                                                                                                         |
| `sheet`  | models, templates + surfaces (NFHS, NCAA and NFL fields, stage, gym), coords, bounds, replace, render (`SheetCanvas`, `draw_sheet`)                                                                                                                  |
| `notes`  | models (tagged `Anchor`), notes (add, edit, find, `accept_draft`)                                                                                                                                                                                    |
| `choreo` | models, motion (easing, `MotionRegistry`), engine, engine_compile, engine_facing, engine_models, paths, routes, facing, avoid, motion_stats, forms, form_fns, form_layout, form_ops, form_fit, form_place, match, match_cost, match_solve, match_ops |
| `show`   | models (`Show`, `Pin`, `ShowIndex`, diffs, `Revision`, `CanvasEvent`, `Proposal`), manage, sections, storage, repositories, file_repository, documents, files, history, collab, pinning, changes, locks, validate, paths_store, counts               |
| `export` | models, data (JSON, CSV, format registry), positions, print_sheets, pdf, publish (claims, QR codes), video                                                                                                                                           |

## Upgrade program

The library is being upgraded in phases; `docs/upgrade-plan.md` holds the program and
`docs/superpowers/plans/2026-11-13-phase-0-safety-net.md` the Phase 0 plan.

**Phase 0, safety net: merged and tagged `v0.2.0` on 2026-11-15** (PR #3, merge commit `89d676f`).

1. One task runner: `noxfile.py` drives every local, hook and CI gate.
2. CI matrix: Python 3.12 to 3.14 on Linux, macOS and Windows, plus hash seeds, slow tests, the
   wheel smoke test, a lowest-direct resolution job and the Docker run.
3. Defect ledger: every audited defect is a strict xfail keyed `UP-<section>-<nn>`;
   `tests/ledger_plugin.py` rejects malformed markers and prints the open count.
4. Benchmarks with a baseline (`nox -s bench`), a public API snapshot and a generated
   `showpy/__init__.py`, and a changelog generated from Conventional Commits.
5. Stronger gates: ruff adds `EXE PIE PGH RSE ISC LOG DTZ PTH FURB ARG PERF C90 TID` (McCabe
   limit 12, no function over it today) and bans importing `tests` from the library;
   `nox -s types` runs pyright basic after `mypy --strict`. Library modules carry no shebang.
6. Pyright's real findings are fixed with `test_regression_` tests: a one-point route
   (`make_route`, closes UP-9.2-27), a counter-march with no members, and trimming a curve
   class outside `AnyCurve`.

**Next: Phase 1, Priority 0.** Make the Priority 0 ledger tests pass: storage data loss and
traversal, principal enforcement, guaranteed segment bounds, hangs and unbounded allocations,
hash-seed determinism, publication tokens, sheet replacement, form consistency after matching,
and every lock and pin bypass. Removing a fixed test's `defect` and `xfail` markers is part of
the fix.

Still open from the original build: align `docs/showpy-api-reference.md` with the code and the
deviations below (the code is the source of truth until generated docs land in Phase 6).

## Deviations already made (put these into the reference doc)

- **core**:
  - `register_unit(registry, name, m)` returns a new registry.
  - `close_matches` was added.
  - `Selection` lives in `core.selection`.
- **geo**:
  - Curves are Pydantic models with a `kind` discriminator; `curve_from_construction` rebuilds one.
  - `point_at(scalar)` returns shape (2,).
  - Closed curves wrap.
  - Mirrored circles keep `ccw=False`.
  - `svg_outline` takes `d` or SVG text, not a file path.
  - `text_outline` uses the built-in font `simplex`.
  - `density_map`, `coverage` and `balance` take `sheet`.
  - Transforms work on Points, curves and Sets (+ sel), not Forms. Forms use `choreo.form_ops.transform_form`.
  - `snap` returns `(obj, SnapReport)`.
- **time**: `describe` was renamed to `describe_count`.
- **sheet**:
  - `sheet_templates()` replaces `templates()`.
  - `get_sheet` and `save_sheet` take an injected `SheetStore`.
  - `replace_sheet` takes `force`, moves paths and zones too, and has no `checks`.
  - Field templates live in core `surfaces.py`.
- **cast**:
  - `regroup` and `remove_group` return `(Show, mapping)`.
  - `delete_spots` takes `force`.
  - In the grammar, `-` as an operator needs a space before it, `T*` matches the exact prefix, and `sel:<name>` names a selection.
- **notes**:
  - `add_note` needs `note_id`, `at` and `author`.
  - `accept_draft` was added.
- **choreo**:
  - Engine timing:
    - Engine models live in `engine_models.py`.
    - `locate` returns `None` outside `[0, total]`.
    - `segments` returns local counts.
    - `entity_segments`, `compile_entity` and `speed` were added.
  - Path builders:
    - Show-aware builders are in `routes.py`: `make_route(spacing="keep")`, `two_part(show, spec, …)` and `rotate_path(show, spec, …)`.
    - `face_*` act on Sets; `face_along` and `face_normal` take `(show, set_id, form_id)`.
    - Time measures are in `motion_stats.py`.
  - Forms and matching:
    - Forms use `form_id`, plus the `"n"` and `"transform"` recipe keys.
    - `match(…, groups=, slot_groups=)`.
    - `MatchResult.entities` and `target_points()`.
- **show**:
  - `new_show` takes an explicit id.
  - `split_section` and `join_sections` live in `sections.py`.
  - Repository adds `shows`, `head`, `paths`, `read` and `put_show`, and `transaction` takes a batch.
  - The workspace adds `/canvas/comments.json`.
  - `Show.pins` was added.
  - Events are derived from revisions.
- **export**: `export_video(show, count_range, fps, audio: Mapping[uri, bytes], px_per_m)`.

## Traps already hit (each has a regression test)

- **Hidden test failures.** `cmd | tail` hides pytest failures. Run every gate with `set -o pipefail`. It already let one bad commit through (fixed in `7c29193`).
- **Stale curve caches.** `model_copy` on a curve kept a stale `cached_property` table. `CurveBase.model_copy` now revalidates. Never bypass it.
- **Segment aliasing.** Probes landing exactly on periodic easings (`steps`) aliased the segments. Segment edges now include `easing_breaks`. Keep the property test at 9 points per segment.
- **Mangled doctests.** Bulk `str.replace` on import lines also edits `>>>` doctest lines, so check doctests after scripted edits.
- **Doctests import the test folder.** Fixed 2026-11-11: library doctests import `showpy.examples`, and `tests/fixtures` re-exports it.
- **Ruff formats Python inside Markdown.** Run `ruff format` on `README.md` too.
- **Docker copies only what it is given.** The Dockerfile copies `pyproject.toml`, `uv.lock`, `.python-version`, `README.md`, `LICENSE`, `showpy/`, `noxfile.py`, `tests/` and `scripts/`; any new top-level file or folder that tests or gates read must be added to it.

## Working rules used on this branch

- TDD: write the test, watch it fail, implement, run the gates, commit one atomic change.
- Parallel agents work in isolated worktrees (`Agent` with `isolation: "worktree"`). The shared brief was in the session scratchpad; its rules are the "Read first" list above plus: no global mutable state, registries are immutable, `at: datetime` is explicit, and agents edit only their own files.
- Merge agent branches with `git merge --no-ff`, then run all gates, then remove the worktree.

## Bugs found by the study-guide pass (2026-11-11), fixed 2026-11-12

Fixes 1 (sandbox) and 6 (set-script `source_hash`) moved to the consumer repository on
2026-11-12 with their tests. Each of the other eight has a `test_regression_` test here.

1. **Sandbox.** Scripts now run under bubblewrap (`unshare-all`, read-only binds of only the interpreter, venv and `showpy`, private `/tmp`). `showpy.show.repositories` and `showpy.show.file_repository` are blocked. Without bubblewrap, running scripts needs an explicit `isolation="process"`. Docker needs `security_opt: seccomp=unconfined` for bubblewrap's namespaces; compose sets it.
2. `PathEngine.positions(t, sel)` resolves strings with the selection grammar.
3. `apply_fix` hint names `showpy.choreo.match_ops.apply_match`.
4. `paths_store` moved to `showpy.show.paths_store`.
5. `join_sections` merges tempo sections that restate the tempo in force; join after split round-trips a constant-tempo show. A split in the middle of a tempo ramp still leaves the ramp expressed as two sections.
6. A set's `source_hash` covers its script and the previous set; rebuilding an earlier set marks later scripted sets stale.
7. `describe_count` rounds tenths with `round_to`.
8. The SVG tokenizer unpacks packed arc flags.
9. The duplicated doctest import in `showpy/time/measures.py` is gone.
10. Selection operators must be separated by spaces or parentheses; a glued `+`, `-` or `&` raises `SchemaError` with its position. This was a designer call: it removes the silent misparse of `T1+T2-T3` at the cost of rejecting `T1+T2`.
