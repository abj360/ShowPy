# ADR 0001: Library layout, conventions and fixes to the API reference

- Status: Accepted
- Date: 2026-11-11
- Deciders: abj360

## Context

The _Stage Library: API Reference_ (2026-11-08) describes about 350 public items across 14
modules. Read as an implementation spec it has name clashes, import cycles, stateful functions
in a library that promises purity, wall-clock reads in a library that promises determinism,
3D concepts in the 2D core, and several undefined conventions (where the origin is, where count
0 falls). The first build covers every module except `stagecheck`, `stage.packs` and
`stage.index`; these fixes have to land before the code that depends on them.

## Decision

**Layout.**

1. The library lives in `stage/` at the repo root. Subpackages: `core`, `cast`, `sheet`, `time`,
   `geo`, `notes`, `choreo`, `show`, `export`.
2. Subpackage `__init__.py` files hold only a docstring. Library code always imports from the
   submodule (`from stage.geo.curves import line`). `stage/__init__.py` re-exports the public API.
   That keeps the module graph a DAG and avoids importing half-built packages.
3. Modules that act on a `Show` but sit below `stage.show` in the graph (`cast.spots`,
   `sheet.coords`, `notes.notes`) import `Show` only under `TYPE_CHECKING`. They edit shows
   through `model_copy`, so they never need the class at runtime.

**Conventions the reference left open.**

4. Origin: `(0, 0)` is the front-centre of the sheet. x runs from −width/2 to +width/2 and y
   from 0 to depth. The mirror axis `x = 0` is the centre line.
5. Time: global count `0.0` is the show start and maps to `0.0` seconds. Audio `sync_offset_s`
   is the audio time at count 0. Measure and count labels for display are 1-based.
6. Facing vector: `facing_vector(θ) = (sin θ, −cos θ)`. So 0° faces the audience (−y) and
   90° faces +x (counter-clockwise seen from above). Under any linear map `M`, a facing
   transforms as `vector_facing(M · facing_vector(θ))`. This one rule covers rotate, mirror,
   scale and skew.
7. Stored geometry is JSON-safe. Data classes hold `tuple[Point, ...]` and a `Region` model
   (exterior plus holes), never NumPy arrays or Shapely objects. Functions accept and return
   `Points` arrays and Shapely polygons.
8. Every library time stamp (`Note.created_at`, `Revision.at`, `Claim.claimed_at`,
   `Publication.published_at`, `CanvasEvent.at`) is an explicit `at: datetime` argument. The
   library never reads the clock.

**Fixes to clashes and contradictions.**

| #   | Reference says                                                                | Problem                                | Fix                                                                                                                                                     |
| --- | ----------------------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `Sheet` in `stage.sheet` and `Sheet{entity, rows}` in export                  | Same name, two things                  | Export type is `CoordinateSheet`                                                                                                                        |
| 2   | `SheetRef` is an ID format and a sheet data class                             | Duplicate                              | `SheetRef` is a `str` alias in `core.ids` only                                                                                                          |
| 3   | `templates()` in `sheet` and in `choreo.forms`                                | Clash on re-export                     | `sheet_templates()`, `form_templates()`                                                                                                                 |
| 4   | `where()` in `cast.select` and `geo.measure`                                  | Clash, different meaning               | `geo.measure.position_at(show, entity, t)`                                                                                                              |
| 5   | `describe(tl, c)` in `time`                                                   | Clash with `PositionLanguage.describe` | `describe_count(tl, c)`                                                                                                                                 |
| 6   | `replace_sheet` in `sheet` and `show.manage`                                  | Duplicate                              | Lives in `stage.sheet.replace` only                                                                                                                     |
| 7   | Export under `stage.show.export`, CLAUDE.md says `stage/export/`              | Two layouts                            | `stage.export.{data,print,publish,video}`                                                                                                               |
| 8   | `stage.show/1` export tag vs `stage.show.v1` schema                           | Two version strings                    | `stage.show.v1`                                                                                                                                         |
| 9   | `ValidationReport` defined in `show.validate`, returned by `time`             | Upward import                          | `stage.core.validation`                                                                                                                                 |
| 10  | `Selector` in `core.types` names `Selection` from `cast`                      | Upward import                          | `Selection` model lives in `stage.core.selection`; `cast.select` re-exports                                                                             |
| 11  | Actions, poses, `stage.choreo.contact` in 2D storage, history, export         | 3D concepts in 2D core                 | Removed from the 2D API; `facing_overrides` is `tuple[FacingOverride, ...]`                                                                             |
| 12  | `Set.table()` columns `kind`, `pose`                                          | No such fields                         | Columns: `entity, group, x, y, facing, form, order, pinned, on_field`                                                                                   |
| 13  | `claim_spot`, `save_sheet`, `accept` mutate hidden state                      | Breaks the purity rule                 | Claims are fields of an immutable `Publication`; claim functions return a new one. Sheets save through an injected `SheetStore`. `accept(ws, proposal)` |
| 14  | `face_audience(target, ...)` takes a `Set` _or_ a time range                  | Ambiguous polymorphism                 | `face_*` act on a `Set`; time ranges go through `add_facing_override`                                                                                   |
| 15  | Show derived member `index`                                                   | Model would import manage logic        | `stage.show.manage.index(show)`; `Show.set(id)` and `Show.transition(tid)` stay                                                                         |
| 16  | `TransitionSpec.counts` and `Set.counts`                                      | Two sources of truth                   | The destination set's `counts` wins. `set_counts` updates both; validation flags a mismatch                                                             |
| 17  | `LabelScheme.prefixes` and `Group.label_prefix`                               | Which wins?                            | The scheme overrides; a missing key falls back to the group prefix                                                                                      |
| 18  | `snap` "result carries a `SnapReport`"                                        | Return shape undefined                 | Returns `(obj, SnapReport)`                                                                                                                             |
| 19  | `Note.anchor`, `Comment.anchor` untagged unions of `str`, tuples and polygons | Not round-trippable in JSON            | Tagged `Anchor` model (`kind` + payload)                                                                                                                |
| 20  | `publish(show, notify=True)` sends notifications                              | Side effect in a pure function         | Removed `notify`; `notify_changes` returns the message list                                                                                             |
| 21  | `coordinate_sheet` "worded by the pack language"                              | Packs are out of scope                 | Worded by the show's `CoordinateSystem`; packs can override later                                                                                       |
| 22  | `replace_sheet` "re-runs checks"                                              | Would import the locked checker        | Report has no `checks`; callers run `stagecheck`                                                                                                        |
| 23  | `balance(set, axis)`                                                          | Centre undefined                       | `balance(set, sheet, axis)`                                                                                                                             |
| 24  | `draft_notes(show, concept)`                                                  | Hidden LLM call                        | Takes an injected `NoteDrafter` protocol                                                                                                                |

The reference doc is updated to match this table in the same branch.

## Alternatives

- **Keep re-exports in every subpackage `__init__`.** Rejected: `geo.transforms` needs
  `choreo.models`, and `choreo.engine` needs `geo`. Package-level re-exports turn that into an
  import cycle.
- **Store NumPy arrays in models with custom serializers.** Rejected: arrays are mutable inside
  frozen models, and they are unhashable.
- **A module-level claim registry.** Rejected: hidden global state, and the result depends on
  call order.

## Consequences

- Positive: the module graph is a DAG, every stored model round-trips through JSON, and every
  function is deterministic given its arguments.
- Negative: callers pass `at=` time stamps and stores explicitly, and the public names differ
  from the reference in the places listed above.

## Numbers

- 24 reference conflicts resolved before the first module.
- The scope excludes 3 of 14 modules (`stagecheck`, `packs`, `index`).

## What would change our mind

- A 3D layer that needs actions in the shared show model. That would be a new ADR adding an
  extension point, not a return to item 11.

## References

- `docs/stage-library-api-reference.md` (2026-11-08)
- `CLAUDE.md`, rules 1 to 7

## Addendum 2026-11-11: corrections after the first build

The API reference rewrite compared this ADR against the code. It found four places where the
ADR is now stale. The code is right in each case:

- **Item 12.** `Set.table()` columns are `entity, x, y, facing, form, order, pinned, on_field,
note`. A `Set` has no group information; join with `show.cast` for it.
- **Item 4.** `position_at` lives in `stage.choreo.motion_stats`, not `stage.geo.measure`; see
  the ADR 0002 addendum. `stage.geo` stays static geometry.
- **Decision 2.** The import example should read `from stage.geo.shapes import line`;
  constructors live in `shapes`, and curve classes in `curves`.
- **Item 7.** The export modules are `models`, `data`, `positions`, `print_sheets`, `pdf`,
  `publish` and `video`.

Also: `import stage` lazily re-exports 533 public names (PEP 562), so the top-level
re-export that decision 2 avoided now exists without the import-cycle risk. Library examples
live in `stage.examples`.

## Addendum 2026-11-12: three repositories

Eight is now three private repositories with separate histories:

- **`stage`** (this one): the library.
- **`Eight`**: the product.
- **`Eight-agent`**: agent-only code, namely set-script builds and the bubblewrap sandbox,
  agent note drafting (`draft_notes`, `NoteDrafter`, `DraftedNote`) and `summarize_events`.

Item 24's injected drafter therefore lives in Eight-agent. `stage` keeps only what designer and
agent share: the workspace, proposals, pins, `accept_draft` and `events_since`.

`stage` imports nothing from Eight-agent. Eight-agent uses `stage` only through a pinned
release and its public API. `stagecheck` will be a fourth repository that also depends on a
pinned `stage` release.

## Addendum 2026-11-12: src layout

The package now lives at `src/stage/`, not `stage/`. The import name is unchanged: `import
stage`. Paths named in the sections above (`stage/__init__.py`, `stage/core/...`) now sit
under `src/`. A src layout means tests import the installed package rather than the checkout
by accident. Type-check with `uv run mypy src tests`.

## Addendum 2026-11-12: flat layout restored

The src layout was reverted the same day at the designer's request. The package is back at
`stage/` in the repo root, and the paths in the sections above are current again. The cost:
because pytest runs from the repo root, tests can import the checkout even when the built
wheel is missing a file. `tests/unit/test_public_api.py` checks that `py.typed` ships, and the
Docker build installs the package before testing.
