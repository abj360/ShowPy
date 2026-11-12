# Handoff: building the `stage` library

Audience: the next agent or engineer continuing this branch. Scope: state of work, what is
left, and the traps already hit.

Last updated: 2026-11-11

## Where things stand

| Item | Value |
| --- | --- |
| Branch | `feat/stage-library` (from `chore/add-claude-md`; no local `main`) |
| Gates | `uv run pytest` about 1380 green (doctests, warnings as errors); ruff and mypy `--strict` clean |
| Docker | `docker compose -f docker/docker-compose.yml up --build` passed |
| Out of scope | `stagecheck`, `stage.packs` and `stage.index`, by the user's decision (2026-11-11) |
| Not pushed | Pushing needs explicit approval |

The user's instructions, in their words:
- "the reference is just a suggestion, if there's a much better algorithm or material to strengthen this library then build it"
- "fix every contradiction"

Use `uv` only, never pip.

## Read first

1. `CLAUDE.md` (project) and `~/.claude/CLAUDE.md` (user style: file headers with a `Contains:` section, verb-first docstrings, no AI trailers on commits).
2. `docs/adr/0001-library-layout-and-reference-fixes.md`: 24 fixes to the reference, the origin convention (front-center), the facing vector `(sin θ, −cos θ)`, and why subpackage `__init__` files are empty.
3. `docs/adr/0002-path-engine.md`, plus its two addenda: segment probe density, and time-based measures living in `stage.choreo`.

## Module map (what exists)

| Package | Modules |
| --- | --- |
| `core` | errors, ids, types (`FrozenModel`, `Region`, `Box2`), units (immutable `UnitRegistry`), util (`close_matches`, `content_hash`, `seeded_rng`), selection, validation |
| `geo` | curve_base, curves (11 kinds, arc-length param), svg, text (stroke font), shapes, regions, spacing (Bridson scatter), affine, transforms + transform_base + snapping, measure, analysis, models |
| `time` | models, timeline, clock (ramps and holds, analytic inverse), measures, validate |
| `cast` | models, spots, select (expression grammar), rewrite (renames IDs everywhere) |
| `sheet` | models, templates + surfaces (NFHS, NCAA and NFL fields, stage, gym), coords, bounds, replace, render (`SheetCanvas`, `draw_sheet`) |
| `notes` | models (tagged `Anchor`), notes (`draft_notes` takes an injected `NoteDrafter`) |
| `choreo` | models, motion (easing, `MotionRegistry`), engine, engine_compile, engine_facing, engine_models, paths, routes, facing, avoid, motion_stats, forms, form_fns, form_layout, form_ops, form_fit, form_place, match, match_cost, match_solve, match_ops |
| `show` | models (`Show`, `Pin`, `ShowIndex`, diffs, `Revision`, `CanvasEvent`, `Proposal`), manage, sections, storage, repositories, file_repository, documents, files, history, collab, pinning, changes, locks, validate, sandbox, sandbox_child, build_models |
| `export` | video only so far (see in-flight work) |

## In flight

An agent was building `stage/export/{models,data,print,publish}.py` in a git worktree under
`.claude/worktrees/` (branch `worktree-agent-…`). Its spec:
- `CoordinateSheet` instead of `Sheet`.
- Claims stored inside an immutable `Publication`.
- A dependency-free PDF writer.
- `segno` for QR codes.
- `export_json` tagged `stage.show.v1`.

If its branch exists:
1. Run `git merge --no-ff` on it.
2. Resolve `stage/export/__init__.py` if both sides added it (keep the docstring-only version).
3. Run the gates.
4. Remove the worktree and branch.

If the branch is gone or half done, rebuild from that spec. `git worktree list` shows its state.

## Remaining work, in order

1. Done 2026-11-11: `stage/show/build.py` (`build_set`, `build_all`, `stale`, `new_set_script`, `run_script`).
2. Done 2026-11-11: `stage/choreo/paths_store.py` (`save_paths`, `load_paths`).
3. **`stage/__init__.py`**: re-export the public API. Names must not clash; ADR 0001 renamed the clashing ones. Add a test that every re-export resolves and that `__all__` is unique.
4. Done 2026-11-11: `schemas/stage.show.v1.json`, guarded by `tests/unit/show/test_schema.py` (regenerate with `STAGE_UPDATE_SCHEMA=1`).
5. **Update `docs/stage-library-api-reference.md`**, which the user asked for ("fix every contradiction"). Apply ADR 0001's table and every agent deviation listed below, section by section. Keep the doc's format.
6. **`README.md`**, in the order from the user's global CLAUDE.md:
   1. pitch
   2. why, with numbers
   3. features
   4. one-command dockerized quickstart, with a ports comment
   5. usage
   6. ASCII architecture diagram
   7. config table
   8. structure tree
   9. development
   10. testing and CI
   11. docs list
   12. MIT license, "Maintained by abj360"
7. **Prettier** on Markdown, YAML and JSON. No system Node: put the vendored Node `bin/` on `PATH` first, per the user's global notes.
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
  - `draft_notes(show, concept, drafter, at)`.
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
- **Doctests import the test folder.** Many library doctests import `tests.fixtures.shows`, which works because pytest has `pythonpath = ["."]`. This is known coupling. Fixing it means a small `stage` example-show helper.

## Working rules used on this branch

- TDD: write the test, watch it fail, implement, run the gates, commit one atomic change.
- Parallel agents work in isolated worktrees (`Agent` with `isolation: "worktree"`). The shared brief was in the session scratchpad; its rules are the "Read first" list above plus: no global mutable state, registries are immutable, `at: datetime` is explicit, and agents edit only their own files.
- Merge agent branches with `git merge --no-ff`, then run all gates, then remove the worktree.
