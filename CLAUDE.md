# CLAUDE.md — ShowPy

ShowPy (import name `showpy`, repo `https://github.com/abj360/ShowPy`) is a Python choreography library for designing shows on a 2D sheet (a field, stage or floor): spots, groups, sets, forms, motion, a count timeline, export and publishing. This repo holds only the library.

Consumers (apps, agents, checkers) depend on tagged ShowPy releases through the public API only. ShowPy never imports a consumer. A breaking change to a public name needs a version bump and a release-notes entry.

Source of truth for the API: the code. _ShowPy Library: API Reference_ (`docs/showpy-api-reference.md`) trails it until the generated docs land in Phase 6. If the two disagree, flag the disagreement; do not silently pick one. Read the handoff notes before continuing work: @docs/HANDOFF.md

## Commands

```bash
uv sync                          # install the showpy package and dev tools
uv run pytest                    # all tests (doctests included, warnings are errors)
uv run pytest tests/unit/choreo  # one area
uv run pytest -m golden          # real marchable drill regression
uv run ruff check . && uv run ruff format .
uv run nox -s types              # mypy --strict and pyright basic, must pass
docker compose -f docker/docker-compose.yml up --build   # full suite in a clean container
```

Run tests, ruff and both type checkers before calling any task done. Prettier formats Markdown, YAML and JSON (`npm ci` installs the pinned `prettier` 3.6.2; `uv run nox -s lint` checks it). Node is under `~/.nvm/versions/node/v22.23.5/bin`; put that `bin/` on `PATH` first.

## Layout

```
showpy/
  core/     types, ids, units, errors, util, selection, validation
  geo/      curves, shapes, svg, text, regions, spacing, affine, transforms, measure, analysis
  time/     count timeline: tempo, holds, markers, cues, measures, audio sync
  cast/     Group, Spot, Shape, Cast, spot tools, selection grammar
  sheet/    Sheet, templates, coordinate systems, bounds, replace_sheet, render
  notes/    design notes
  choreo/   Placement, Set, forms, match, paths, routes, motion, engine, facing, avoid, motion_stats
  show/     Show, manage, sections, storage, repositories, history, collab, validate, paths_store
  export/   publish (claims), print sheets, PDF, data, positions, video (2D)
  examples.py  deterministic example shows for docs and tests
  schemas/  versioned JSON schemas (showpy.show.v1), shipped in the wheel
tests/      unit/ property/ golden/ fixtures/
docs/       API reference, ADRs, handoff notes, study guide
```

Each package: `models.py` holds data classes; other files hold functions. Subpackage `__init__.py` files hold only a docstring; `showpy/__init__.py` re-exports the public API lazily.

## Core concepts (read before editing)

- **Spot**: a dot on the sheet that persists through the whole show. Same `SpotId` from first set to last; it has a position in every set (`on_field=False` before its entrance). Performers claim a spot once, for the whole show.
- **Group**: a designer-made set of spots with shared defaults and optional limit overrides. There are no fixed roles.
- **Shape**: a spot's footprint, `circle` or `rectangle` (rectangles rotate with facing). Used for display and collisions.
- **Set**: a snapshot of every spot's placement at one moment. **Transition**: the movement between two sets, described by sparse `PathRow`s (missing row = straight, full counts).
- **Form**: a stored shape construction plus ordered members. Placements are regenerated from forms; edit the form, not the dots.
- **PathEngine**: the _only_ code that turns path instructions into positions. Every consumer and every export uses it. Never compute motion anywhere else.
- **Timeline**: counts are the unit. Tempo sections, holds, markers and cues define timing. Audio is a playback asset with a sync point and **never** changes timing.
- **Sheet**: the 2D design surface (template + customizations). The coordinate system is a display layer: changing it changes labels, never stored positions.

## Non-negotiable rules

1. **One internal unit system.** Meters on the stage plane (x audience-left → right, y away from the audience, origin at the sheet's front-center), degrees (0 = facing the audience, counter-clockwise positive), counts for time. Convert only in `core/units.py`, `sheet/coords.py` and pack language modules.
2. **Pure functions.** Data classes are frozen Pydantic v2 models. "Edit" functions return new objects; never mutate inputs.
3. **Determinism.** Same inputs → same outputs. Randomness only through `core.util.seeded_rng(seed)`. No wall-clock reads in library logic; time stamps are explicit `at` arguments.
4. **Checks are never weakened.**
   - Never weaken a validation check, threshold or default to make a test pass.
   - Limits are user-owned configuration; library code never edits them.
5. **Single motion source.** Anything needing positions over time calls `PathEngine`. If a feature seems to need its own interpolation, extend the engine instead.
6. **Respect locks and pins.** Functions that edit sets, forms or paths must raise `LockedError` on pinned items unless `force=True` is passed explicitly by the caller.
7. **Errors teach.** Raise `ShowPyError` subclasses with `code`, `message`, `context` and a `hint` that says what is allowed. No bare `ValueError` in public APIs.
8. **Shared domain logic only.** Code specific to one consumer (UI, prompts, agent sandboxes, drafting) stays out of ShowPy.

## Writing code here

- **Module headers** follow the user-level `~/.claude/CLAUDE.md`, except that library modules under `showpy/` have no shebang; scripts and tests keep it (shebang, blank line). The module docstring's first line is `<filename> --- <role>` and its index section is titled `Contains:` (never `Args:`), one verb-first line per exported item.
- **Public functions** need full type hints and a docstring with a runnable `>>>` example; examples import `showpy.examples`, never the `tests/` folder. Once `showpy.index` exists, they also need `@api(category=..., panel=...)`.
- **New public names** go in `showpy/_api_table.py`. Run `uv run python scripts/gen_public_api.py` to regenerate `showpy/__init__.py`; never edit that file by hand. Refresh the snapshot with `SHOWPY_UPDATE_API=1 uv run pytest tests/unit/test_api_snapshot.py`. A changed public name must appear backticked in the CHANGELOG "Unreleased" section, via the commit subject or a `BREAKING CHANGE` footer.
- **New data classes** go in the package's `models.py` and must be JSON-serializable; regenerate `showpy/schemas/` with `SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py` and bump the schema version if a stored model changes incompatibly.
- **Coordinates in field language** (e.g. "2 outside the 35") belong in the pack's `PositionLanguage`, never in core modules.
- **Prefer extending existing primitives** (curves, spacing, transforms, path styles) over adding parallel ones. Custom movement = register a `MotionFn`; custom shapes = `register_form_fn`.
- Keep modules small and focused; if a file passes ~500 lines, split by responsibility.

## Tests

- Every bug fix starts with a failing test named `test_regression_...`.
- `tests/unit/` mirrors `showpy/`. `tests/property/` uses Hypothesis for invariants (spacing is even, 360° rotation is identity, paths end on targets, count↔seconds round-trips).
- `tests/golden/` replays real marchable shows; a diff in output requires a human-reviewed fixture update (`SHOWPY_UPDATE_GOLDEN=1`).
- Numeric comparisons use `approx_equal` with explicit tolerances.
- Run every gate with `set -o pipefail`; piping pytest into `tail` hides failures otherwise.

## Releases

- Tag releases `vMAJOR.MINOR.PATCH` on `main` after CI passes. Consumers pin a tag; a breaking change to a public name needs a major bump while above 1.0, a minor bump before.

## Commits

- Commit every single atomic change as soon as it is complete. One logical change per commit; do not wait to be asked.
- Work on a branch and merge through a pull request; never commit directly on `main`.
- Pushing still needs explicit approval.

## When unsure

- Check the API Reference first, then `docs/adr/`.
- If a request conflicts with a rule above (especially checks or units), stop and ask rather than working around it.
- Summaries at the end of a task: what changed, which tests ran, anything left open.

## Glossary

Spot · Group · Shape · Set · Transition · PathRow · Form · Placement · Sheet · CoordinateSystem · Timeline (counts, tempo, holds, markers, cues) · Pack (domain rules and wording) · Claim (a performer on a spot after publishing) · Finding (a validation result) · Waiver (designer-approved exception)
