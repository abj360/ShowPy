# CLAUDE.md — Eight

Eight is an AI-assisted choreography design tool. Designers draw spots on a 2D sheet (a field, stage or floor), build a show set by set, and publish it so performers can claim their spot. This repo holds `stage`, the Python library behind the canvas, and `stagecheck`, the locked checker that verifies every design.

Source of truth for the API: _Stage Library: API Reference_ (`docs/stage-library-api-reference.md`). If code and the reference disagree, flag it; do not silently pick one.

Product overview (problem, scope, non-goals, decision rules): @docs/project-overview.md

Work in progress on `feat/stage-library`: read the handoff notes before continuing: @docs/HANDOFF.md

## Commands

```bash
uv sync                          # install the stage package (stagecheck not built yet)
uv run pytest                    # all tests
uv run pytest tests/unit/choreo  # one area
uv run pytest -m golden          # real marchable drill regression
uv run ruff check . && uv run ruff format .
uv run mypy stage stagecheck     # strict typing, must pass
uv run python -m stage.index.build_docs   # regenerate docs/api_index.md
```

Run tests, ruff and mypy before calling any task done.

## Layout

```
stage/
  core/     types, ids, units, errors, util
  cast/     Group, Spot, Shape, Cast, spot tools, selection
  sheet/    Sheet, templates, coordinate systems, replace_sheet
  time/     count timeline: tempo, holds, markers, cues, audio sync
  geo/      curves, spacing, transforms, measure, analysis
  choreo/   Placement, Set, forms, match, paths, motion, engine, facing, avoid
  notes/    design notes
  show/     Show, manage, build, storage, collab, history, validate
  export/   publish (claims), video (2D), print, data
  packs/    DomainPack base + marching/
  index/    @api registry, search, draft promotion
  contrib/  reviewed agent-written code
stagecheck/ locked checker: limits, findings, waivers, checks/
schemas/    versioned JSON schemas (stage.show.v1)
tests/      unit/ property/ checker/ fixtures/ golden/
```

Each package: `models.py` holds data classes; other files hold functions.

## Core concepts (read before editing)

- **Spot**: a dot on the sheet that persists through the whole show. Same `SpotId` from first set to last; it has a position in every set (`on_field=False` before its entrance). Performers claim a spot once, for the whole show.
- **Group**: a designer-made set of spots with shared defaults and optional limit overrides. There are no fixed roles.
- **Shape**: a spot's footprint, `circle` or `rectangle` (rectangles rotate with facing). Used for display and collisions.
- **Set**: a snapshot of every spot's placement at one moment. **Transition**: the movement between two sets, described by sparse `PathRow`s (missing row = straight, full counts).
- **Form**: a stored shape construction plus ordered members. Placements are regenerated from forms; edit the form, not the dots.
- **PathEngine**: the _only_ code that turns path instructions into positions. The canvas, checker and exports all use it. Never compute motion anywhere else.
- **Timeline**: counts are the unit. Tempo sections, holds, markers and cues define timing. Audio is a playback asset with a sync point and **never** changes timing.
- **Sheet**: the 2D design surface (template + customizations). The coordinate system is a display layer: changing it changes labels, never stored positions.

## Non-negotiable rules

1. **One internal unit system.** Meters on the stage plane (x audience-left → right, y away from the audience), degrees (0 = facing the audience, counter-clockwise positive), counts for time. Convert only in `core/units.py`, `sheet/coords.py` and pack language modules.
2. **Pure functions.** Data classes are frozen Pydantic v2 models. "Edit" functions return new objects; never mutate inputs.
3. **Determinism.** Same inputs → same outputs. Randomness only through `core.util.seeded_rng(seed)`. No wall-clock reads in library logic.
4. **`stagecheck` is locked.**
   - Never import it from `stage` or from set scripts.
   - Never weaken a check, threshold or default to make a test pass.
   - Changes to `stagecheck/` require an explicit request and must add checker test cases.
   - Limits belong to the designer (`/config/limits.yaml`); never edit them in code paths the agent runs.
5. **Single motion source.** Anything needing positions over time calls `PathEngine`. If a feature seems to need its own interpolation, extend the engine instead.
6. **Respect locks and pins.** Functions that edit sets, forms or paths must raise `LockedError` on pinned items unless `force=True` is passed explicitly by the caller.
7. **Errors teach.** Raise `StageError` subclasses with `code`, `message`, `context` and a `hint` that says what is allowed. No bare `ValueError` in public APIs.

## Writing code here

- **Module headers** follow the user-level `~/.claude/CLAUDE.md`: shebang, blank line, then a docstring whose first line is `<filename> --- <role>` and whose index section is titled `Contains:` (never `Args:`), one verb-first line per exported item.

- **Public functions** need full type hints, a docstring with a runnable `>>>` example, and `@api(category=..., panel=...)` so they appear in the index.
- **New data classes** go in the package's `models.py` and must be JSON-serializable; update `schemas/` and bump the schema version if a stored model changes.
- **Coordinates in field language** (e.g. "2 outside the 35") belong in the pack's `PositionLanguage`, never in core modules.
- **Prefer extending existing primitives** (curves, spacing, transforms, path styles) over adding parallel ones. Custom movement = register a `MotionFn`; custom shapes = `register_form_fn`.
- Keep modules small and focused; if a file passes ~500 lines, split by responsibility.

## Tests

- Every bug fix starts with a failing test.
- `tests/unit/` mirrors `stage/`. `tests/property/` uses Hypothesis for invariants (spacing is even, 360° rotation is identity, paths end on targets, count↔seconds round-trips).
- `tests/checker/` holds hand-built cases with known answers, including crossings between counts. Any engine or checker change must keep these green.
- `tests/golden/` replays real marchable shows; a diff in output requires a human-reviewed fixture update.
- Numeric comparisons use `approx_equal` with explicit tolerances.

## Agent-written code

- Drafts go in `/lib/drafts` via `index.register_draft`; they must be pure, typed and documented with an example.
- Promotion to `stage/contrib/` happens only through `review_draft` + `promote` with human approval. Never write directly into core packages from a draft.

## Commits

- Commit every single atomic change as soon as it is complete. One logical change per commit; do not wait to be asked.
- Pushing still needs explicit approval.

## When unsure

- Check the API Reference first, then `docs/adr/`.
- If a request conflicts with a rule above (especially the checker or units), stop and ask rather than working around it.
- Summaries at the end of a task: what changed, which tests ran, anything left open.

## Glossary

Spot · Group · Shape · Set · Transition · PathRow · Form · Placement · Sheet · CoordinateSystem · Timeline (counts, tempo, holds, markers, cues) · Pack (domain rules and wording) · Claim (a performer on a spot after publishing) · Finding (a checker result) · Waiver (designer-approved exception)
