# Handoff: building the `stage` library

Audience: the next agent or engineer continuing this repository. Scope: state of work, what is
left, and the traps already hit.

Last updated: 2026-11-12

## Where things stand

| Item         | Value                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------- |
| Repository   | `abj360/stage` (private); split from `abj360/Eight` on 2026-11-12 with full history       |
| Gates        | `uv run pytest` green (doctests, warnings as errors); ruff and mypy `--strict` clean      |
| CI           | lint, types, tests, dependency scan, docker build, on every pull request and push to main |
| Out of scope | `stagecheck`, `stage.packs` and `stage.index`, by the user's decision (2026-11-11)        |
| Pushing      | needs explicit approval                                                                   |

**Repository split (2026-11-12).** Agent-only code moved to `abj360/Eight-agent` with its
history: set-script builds and the bubblewrap sandbox (`show/build.py`, `build_models.py`,
`sandbox.py`, `sandbox_child.py`), agent note drafting (`draft_notes`, `NoteDrafter`,
`DraftedNote`) and `summarize_events`. Product docs stay in `abj360/Eight`. `stage` must never
import from either; they pin a `stage` release tag.

The user's instructions, in their words:

- "the reference is just a suggestion, if there's a much better algorithm or material to strengthen this library then build it"
- "fix every contradiction"

Use `uv` only, never pip.

## Read first

1. `CLAUDE.md` (project) and `~/.claude/CLAUDE.md` (user style: file headers with a `Contains:` section, verb-first docstrings, no AI trailers on commits).
2. `docs/adr/0001-library-layout-and-reference-fixes.md`: 24 fixes to the reference, the origin convention (front-center), the facing vector `(sin θ, −cos θ)`, and why subpackage `__init__` files are empty.
3. `docs/adr/0002-path-engine.md`, plus its two addenda: segment probe density, and time-based measures living in `stage.choreo`.

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
| `export` | video only so far (see in-flight work)                                                                                                                                                                                                               |

## In flight

- **API reference rewrite.** An agent is working in `.claude/worktrees/agent-ab17bb0302cd0a89a` (branch `worktree-agent-ab17bb0302cd0a89a`) to align `docs/stage-library-api-reference.md` with the code, export sections included. When it is done:
  1. Merge with `git merge --no-ff`.
  2. Remove `docs/stage-library-api-reference.md` from `.prettierignore` and run Prettier.
  3. Remove the worktree.
- **Study guide PDF.** An agent is writing it directly on this branch under `docs/guide/` (uncommitted until it finishes): `docs/guide/build_guide.py` produces `docs/guide/stage-library-guide.pdf`.

## Remaining work, in order

1. Done 2026-11-11, moved to Eight-agent 2026-11-12: set-script builds (`build_set`, `build_all`, `stale`, `new_set_script`, `run_script`).
2. Done 2026-11-11: `stage/choreo/paths_store.py` (`save_paths`, `load_paths`).
3. Done 2026-11-11: `stage/__init__.py` lazily re-exports 533 names (PEP 562). Internal modules are excluded and the motion `spiral` is `spiral_motion`. Tested by `tests/unit/test_public_api.py`.
4. Done 2026-11-11: `schemas/stage.show.v1.json`, guarded by `tests/unit/show/test_schema.py` (regenerate with `STAGE_UPDATE_SCHEMA=1`).
5. **Update `docs/stage-library-api-reference.md`**, which the user asked for ("fix every contradiction"). Apply ADR 0001's table and every agent deviation listed below, section by section. Keep the doc's format.
6. Done 2026-11-11: `README.md`, `.env.example` and the dockerized quickstart (Docker run: 1492 passed).
7. Done 2026-11-11: Prettier (`npx --yes prettier@3.6.2`, Node at `/home/uapb-ai/.nvm/versions/node/v22.23.5/bin`), gated in pre-commit and CI.
8. **Final gates and Docker run**, then a summary to the user: what changed, tests run, open items.

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
- **Doctests import the test folder.** Fixed 2026-11-11: library doctests import `stage.examples`, and `tests/fixtures` re-exports it.
- **Ruff formats Python inside Markdown.** Run `ruff format` on `README.md` too.
- **Docker copies only what it is given.** The Dockerfile copies `stage/`, `tests/` and `schemas/`; any new top-level data folder that tests read must be added to it.

## Working rules used on this branch

- TDD: write the test, watch it fail, implement, run the gates, commit one atomic change.
- Parallel agents work in isolated worktrees (`Agent` with `isolation: "worktree"`). The shared brief was in the session scratchpad; its rules are the "Read first" list above plus: no global mutable state, registries are immutable, `at: datetime` is explicit, and agents edit only their own files.
- Merge agent branches with `git merge --no-ff`, then run all gates, then remove the worktree.

## Bugs found by the study-guide pass (2026-11-11), fixed 2026-11-12

Each fix has a `test_regression_` test.

1. **Sandbox.** Scripts now run under bubblewrap (`unshare-all`, read-only binds of only the interpreter, venv and `stage`, private `/tmp`). `stage.show.repositories` and `stage.show.file_repository` are blocked. Without bubblewrap, running scripts needs an explicit `isolation="process"`. Docker needs `security_opt: seccomp=unconfined` for bubblewrap's namespaces; compose sets it.
2. `PathEngine.positions(t, sel)` resolves strings with the selection grammar.
3. `apply_fix` hint names `stage.choreo.match_ops.apply_match`.
4. `paths_store` moved to `stage.show.paths_store`.
5. `join_sections` merges tempo sections that restate the tempo in force; join after split round-trips a constant-tempo show. A split in the middle of a tempo ramp still leaves the ramp expressed as two sections.
6. A set's `source_hash` covers its script and the previous set; rebuilding an earlier set marks later scripted sets stale.
7. `describe_count` rounds tenths with `round_to`.
8. The SVG tokenizer unpacks packed arc flags.
9. The duplicated doctest import in `stage/time/measures.py` is gone.
10. Selection operators must be separated by spaces or parentheses; a glued `+`, `-` or `&` raises `SchemaError` with its position. This was a designer call: it removes the silent misparse of `T1+T2-T3` at the cost of rejecting `T1+T2`.
