# ShowPy Library: API Reference

Nov 11, 2026 · @Peter

Last updated: 2026-11-17

## How to read this reference

This is the complete API of `showpy`, the choreography library behind the Show Canvas (2D sheet; the 3D layer has its own reference), listed module by module: every data class with its fields, and every function with its inputs, output and purpose. Spots are the dots designed on the sheet, claimed by real performers after publishing.

The first build covers every module except the consumer checker (below), `showpy.packs` and `showpy.index`; their sections describe the planned design. Where this reference and the code disagree, the code and the ADRs in `docs/adr/` win; ADR 0001 lists the fixes made to the 2026-11-08 version of this document.

**Conventions used in every table**

- Distances are meters on the 2D stage plane (x audience-left to audience-right, y away from the audience); 3D world is Y-up, glTF axes.
- Origin: `(0, 0)` is the front-center of the sheet. x runs from −width/2 to +width/2 and y from 0 (the audience edge) to depth. The mirror line `x = 0` is the center line.
- Facing is degrees, 0 = toward the audience, counter-clockwise positive seen from above. The facing vector is `facing_vector(θ) = (sin θ, −cos θ)`: 0° points to −y, 90° to +x. Under any linear map `M` a facing becomes `vector_facing(M · facing_vector(θ))`, which covers rotate, mirror, scale and skew.
- Time is `Count` (musical pulse), `Seconds`, or measures; a transition's local time is `tau`, from 0 to its counts. Global count 0 is the show start and maps to 0 seconds; an audio track's `sync_offset_s` is the audio time at count 0. Measure and count labels for display are 1-based.
- All data classes are frozen Pydantic v2 models with JSON schemas. Stored geometry is JSON-safe: models hold `Point` tuples, `Ring`s and `Region`s, never NumPy arrays or Shapely objects; functions accept and return `Points` and Shapely polygons.
- Functions are pure: "edit" functions return a new object, and functions that edit pinned or locked items raise `LockedError` unless the caller passes `force=True`. Storage writes (`DocumentRepository.put_*`, `transaction`) check that the data is valid and never check pins or locks; the edit functions enforce pins and locks for every caller. Builders that take a spec (`two_part`, `set_style`, `rotate_path`) guard the spec they receive; a stored change is re-checked against the stored show when it is committed. ShowPy has no principals: permissions, authorship and commit messages belong to the host application. The library never reads the clock: every time stamp is an explicit `at: datetime` argument. Stores and services (`SheetStore`, `Repository`, `AssetStore`) are injected, and registries are immutable: registering returns a new registry.
- `Points` = NumPy array of shape (N, 2), float64. `Selector` = selection expression, list of IDs, or `Selection`.
- `import showpy` re-exports the public API lazily (PEP 562): a name is imported on first use, e.g. `showpy.line`. Internal helper modules (engine compilation, matching cost and solver internals, workspace documents) are not re-exported. One name clashes and is renamed: the motion function `spiral` is `showpy.spiral_motion`; `showpy.spiral` is the geometric spiral curve. Subpackage `__init__` files hold only a docstring, and library code imports from the submodule, e.g. `from showpy.geo.shapes import line`.
- `showpy.examples` holds small, deterministic example shows for docs, doctests and tests: `field_sheet()`, `two_set_show()`, `with_rows(rows, routes=None, show=None)`, `with_targets(updates, show=None)`, `crossing_show()`, `performer(name, role="performer")`, `live_publication(show)`, plus `EXAMPLE_TIME` and `EXAMPLE_TID`.
- `→ X` means the function returns X. Optional arguments show their defaults.

**Module map**

| Module             | Panel                | What it holds                                                                                                     |
| ------------------ | -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `showpy.core`      | all                  | Units, IDs and types, errors, validation reports, the selection model, utilities                                  |
| `showpy.cast`      | Show Canvas          | Groups, spots, cast, selection language, spot ID rewriting                                                        |
| `showpy.sheet`     | Canvas               | The 2D design sheet: templates, customization, coordinate systems, bounds, rendering                              |
| `showpy.notes`     | Canvas               | Design notes on sets, transitions, points, areas and spots                                                        |
| `showpy.time`      | Show Canvas, Preview | Count timeline: tempo, holds, markers, cues, measures, audio sync                                                 |
| `showpy.geo`       | Show Canvas          | Curves, shapes, regions, SVG and text outlines, spacing, affine maps, transforms, static measurement and analysis |
| `showpy.choreo`    | Show Canvas          | Sets, forms, matching, paths, routes, engine, facing, avoidance, time-based measures                              |
| `showpy.show`      | Show Canvas          | Show model, set management, sections, storage, collaboration, history, validation                                 |
| `showpy.export`    | Show Canvas          | Coordinate sheets, charts, PDF, JSON and CSV data, position import, publishing and claims, video                  |
| `showpy.examples`  | docs, tests          | Deterministic example shows for doctests and tests                                                                |
| Checker (consumer) | all                  | Limits and checks, a separate package pinned to a ShowPy release; not built yet                                   |
| `showpy.packs`     | all                  | Domain packs, starting with marching band; not built yet                                                          |
| `showpy.index`     | agent                | API index and promotion of agent-written code; not built yet                                                      |

## showpy.core

The foundation every other module imports: one unit system, shared types and IDs, one error family, validation reports, the selection model and small utilities.

### showpy.core.types (type aliases and base models)

| Name                                                       | Definition                                                                                                                                          | Represents                                                    |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `Meters`, `Seconds`, `Count`, `Degrees`, `CountsPerMinute` | `float`                                                                                                                                             | Distance, wall time, musical pulse, angle, tempo              |
| `Point`                                                    | `tuple[float, float]`                                                                                                                               | A stage-plane position in meters                              |
| `Point3`                                                   | `tuple[float, float, float]`                                                                                                                        | A world position in meters                                    |
| `Ring`                                                     | `tuple[Point, ...]`                                                                                                                                 | Stored outline or line points, first point not repeated       |
| `Points`                                                   | `numpy.ndarray` (N, 2)                                                                                                                              | Many stage-plane positions                                    |
| `Transform`                                                | `numpy.ndarray` (4, 4)                                                                                                                              | World matrix (position, rotation, scale)                      |
| `Affine2D`                                                 | `numpy.ndarray` (3, 3)                                                                                                                              | 2D transform between blueprint and stage plane                |
| `Polygon`                                                  | `shapely.Polygon`                                                                                                                                   | An area on the stage plane, in function arguments and results |
| `FrozenModel`                                              | Pydantic `BaseModel`, frozen, extra fields forbidden; construction and `replace(**changes)` raise `SchemaError`, never pydantic's `ValidationError` | Base of every data class                                      |
| `Box2` / `Box3`                                            | `{min, max}` model; `Box2.size`, `Box2.center`                                                                                                      | 2D or 3D bounding box                                         |
| `Region`                                                   | `{exterior: Ring, holes: tuple[Ring, ...]}` model; `Region.from_shapely(p)`, `to_shapely()`                                                         | An area stored JSON-safe                                      |

### showpy.core.selection

**`Selection`** — ordered, duplicate-free `ids: tuple[SpotId, ...]` plus `description: str`; supports `|`, `&`, `-`, `len`, iteration and `in`. `showpy.cast.select` re-exports it.

| Name            | Definition                            | Represents                                                                             |
| --------------- | ------------------------------------- | -------------------------------------------------------------------------------------- |
| `Selector`      | `str \| Sequence[str] \| Selection`   | Who an operation applies to                                                            |
| `SelectorField` | `str \| tuple[str, ...] \| Selection` | The JSON-safe selector stored on models (`Zone.applies_to`, `FacingOverride.entities`) |

### showpy.core.ids

| Name                                        | Format                                 | Represents                                         |
| ------------------------------------------- | -------------------------------------- | -------------------------------------------------- |
| `SheetRef`                                  | `str`, written `sheet:<id>@<version>`  | A sheet template or saved sheet; defined only here |
| `SpotId`                                    | free string, e.g. `T4`, `flag-12`      | Spots in a show                                    |
| `SetId`                                     | e.g. `12`, `12A`                       | A set in a show                                    |
| `TransitionId`                              | `tuple[SetId, SetId]`, written `12→13` | Movement between two sets                          |
| `FormId`, `RouteId`, `ZoneId`, `LandmarkId` | free string                            | Named items                                        |

| Function                                 | Inputs                  | Output                        | Purpose                                        |
| ---------------------------------------- | ----------------------- | ----------------------------- | ---------------------------------------------- |
| `parse_ref(text)`                        | `str`                   | `(kind, id, version \| None)` | Read a reference such as `sheet:hs-football@1` |
| `format_ref(kind, ref_id, version=None)` |                         | `str`                         | Write one                                      |
| `parse_tid(text)`                        | `"12→13"` or `"12->13"` | `TransitionId`                | Read a transition ID                           |
| `format_tid(tid)`                        | `TransitionId`          | `str`                         | Write one as `12→13`                           |

### showpy.core.units

**`UnitRegistry`** — `meters_per_unit: dict[str, float]`; `factor(name)` raises `UnknownRefError` for unknown names. `DEFAULT_UNITS` knows `m`, `cm`, `mm`, `in`, `ft` and `yd`.

| Function                                                     | Inputs                         | Output                    | Purpose                                                          |
| ------------------------------------------------------------ | ------------------------------ | ------------------------- | ---------------------------------------------------------------- |
| `convert(value, from_unit, to_unit, registry=DEFAULT_UNITS)` | `float`, unit names            | `float`                   | The only unit conversion point                                   |
| `register_unit(registry, name, meters_per_unit)`             | `UnitRegistry`, `str`, `float` | `UnitRegistry`            | New registry that also knows `name`, e.g. marching step 0.5715 m |
| `units()`                                                    |                                | `dict[str, float]`        | List the default units                                           |
| `normalize_angle(a)`                                         | `Degrees`                      | `Degrees` in \[0, 360)    | Wrap an angle                                                    |
| `angle_diff(a, b)`                                           | `Degrees`, `Degrees`           | `Degrees` in (−180, 180\] | Signed shortest turn                                             |
| `facing_vector(theta)`                                       | `Degrees`                      | unit `Point`              | Facing to direction, `(sin θ, −cos θ)`; 0° → (0, −1)             |
| `vector_facing(v)`                                           | `Point`                        | `Degrees` in \[0, 360)    | Direction to facing; raises `ZeroLengthError` on (0, 0)          |
| `round_to(value, step=0.01)`                                 | `float`, `float`               | `float`                   | Display rounding, half away from zero                            |

### showpy.core.errors

Every error has `code: str`, `message: str`, `context: dict`, `hint: str`, and is raised as `ShowPyError(message, *, hint, context=None, code=None)`; `code` defaults to the class's own code (`UNKNOWN_REF`, `LOCKED`, ...). `str(error)` reads `CODE: message (hint: ...)`.

| Class               | Parent          | Raised when                                                                                         |
| ------------------- | --------------- | --------------------------------------------------------------------------------------------------- |
| `ShowPyError`       | `Exception`     | Base of all library errors                                                                          |
| `UnknownRefError`   | `ShowPyError`   | An ID, name or unit does not resolve; hint lists close matches                                      |
| `VersionError`      | `ShowPyError`   | A pinned version is missing or incompatible                                                         |
| `SchemaError`       | `ShowPyError`   | Data fails its JSON schema or text format; names the field                                          |
| `GeometryError`     | `ShowPyError`   | Degenerate shape (zero radius, open polygon, collinear arc points)                                  |
| `ZeroLengthError`   | `GeometryError` | Direction of a zero vector requested                                                                |
| `SpacingError`      | `ShowPyError`   | Items do not fit; reports the maximum that fits                                                     |
| `TimeRangeError`    | `ShowPyError`   | Times outside range or start ≥ end                                                                  |
| `TimelineError`     | `ShowPyError`   | Inconsistent tempo, measure or hold data                                                            |
| `SurfaceError`      | `ShowPyError`   | A point is off the sheet                                                                            |
| `MatchError`        | `ShowPyError`   | No feasible assignment; names the blocking constraint                                               |
| `PathError`         | `ShowPyError`   | Invalid path instruction                                                                            |
| `ContinuityError`   | `PathError`     | A path does not end on its target placement                                                         |
| `LockedError`       | `ShowPyError`   | Edit touches something the designer locked                                                          |
| `StorageError`      | `ShowPyError`   | Persistence failure                                                                                 |
| `ConflictError`     | `StorageError`  | A concurrent edit landed first; lists what changed                                                  |
| `ValidationError`   | `ShowPyError`   | Structural integrity failure                                                                        |
| `MissingExtraError` | `ShowPyError`   | A function needs an extra that is not installed; hint is the install command (`showpy.core.extras`) |
| `DeprecatedWarning` | `UserWarning`   | Deprecated API used; names the replacement                                                          |

### Error codes beyond each class's own

A raise site may pass its own `code`; the class still says which family it belongs to. The text codes are listed under "Text error codes".

| Code              | Class           | Raised when                                                                                                                        |
| ----------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `BAD_ID`          | `StorageError`  | A stored revision ID or blob digest is not 64 lowercase hex digits                                                                 |
| `BAD_PATH`        | `StorageError`  | A show ID, workspace path or stored path cannot be stored: unknown root, `.`, `..`, empty part, a directory, a Windows device name |
| `AMBIGUOUS_PATH`  | `StorageError`  | A workspace folder names no set but matches another set's key ignoring case                                                        |
| `CORRUPT`         | `StorageError`  | A file repository holds a damaged file; the hint says to restore from a backup                                                     |
| `IO`              | `StorageError`  | Any other file-system error under a file repository's root                                                                         |
| `NAME_TOO_LONG`   | `StorageError`  | A show ID or path exceeds the file system's name limit                                                                             |
| `NO_HARDLINKS`    | `StorageError`  | The repository root's file system cannot create hard links, which a commit needs                                                   |
| `TOO_LARGE`       | `StorageError`  | A file in a commit is over `BLOB_MAX_BYTES` (2^30 bytes); the message lists the paths                                              |
| `READ_ONLY`       | `LockedError`   | A write or commit targets a derived file such as `index.csv` or `placements.csv`                                                   |
| `PROPOSAL_CLOSED` | `SchemaError`   | A proposal that is already accepted or rejected is accepted or rejected again                                                      |
| `STALE_FORM`      | `ShowPyError`   | A form's set no longer places that form; re-place the set with the form or delete the form                                         |
| `SEGMENT_BOUND`   | `PathError`     | A segment stays over the tolerance after the maximum number of splits; the hint gives a `tol` that passes                          |
| `BAD_SHAPE`       | `GeometryError` | `similarity_fit` gets arrays that are not numeric and `(N, 2)` of equal shape                                                      |
| `TOO_FEW_POINTS`  | `GeometryError` | `similarity_fit` gets fewer than two landmarks                                                                                     |
| `NOT_FINITE`      | `GeometryError` | `similarity_fit` gets NaN or infinite coordinates                                                                                  |
| `DEGENERATE`      | `GeometryError` | `similarity_fit` gets landmarks that sit on top of each other                                                                      |
| `REFLECTION`      | `GeometryError` | `similarity_fit` gets landmarks that are mirrored between the two sheets                                                           |

Models raise `SchemaError`, never pydantic's `ValidationError`: its context lists at most five failing fields, each input cut to 80 characters (`bounded_repr`, which never raises on size and echoes ints of 640 or more digits as their bit count). `field_errors(error)` returns a converted error's per-field list and re-raises an error that has none. `ValidationReport` issues carry their own codes (`UNKNOWN_SPOT`, `PINNED_CHANGED`, ...) and are not errors.

### showpy.core.caps

Output sizes are checked before anything is allocated; `require_size(requested, cap, *, what, error=SchemaError)` raises when a size is negative or over its cap. Caps are `MAX_POINTS` (1,000,000), `MAX_TABLE` (1,000,000), `MAX_GRID_CELLS` (4,000,000), `MAX_RANGE` (10,000), `MAX_DEPTH` (64), `MAX_TEXT_CHARS` (1,000,000), `MAX_IMAGE_PIXELS` (40,000,000) and `MAX_VIDEO_PIXELS` (3840 × 2160).

### showpy.core.validation

| Data class         | Fields                                                                                                          | Represents                                                                                |
| ------------------ | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `ValidationIssue`  | `level: "error" \| "warning", code: ErrorCode, where: str, message: str, hint: str, context: JsonObject`        | One structural problem, printed as `E: <code> <where>: <message> (hint: ...)` or `W: ...` |
| `ValidationReport` | `issues: tuple[ValidationIssue, ...]`; property `ok` (no error-level issue); `ValidationReport.merge(*reports)` | Every problem found, printed one line per issue                                           |

### showpy.core.util

| Function                                        | Inputs                                           | Output                                       | Purpose                                                                                                                                                                         |
| ----------------------------------------------- | ------------------------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `natural_sort(ids)`                             | `Iterable[str]`                                  | `list[str]`                                  | T2 before T10                                                                                                                                                                   |
| `parse_range(text)`                             | `"T1-T8"`                                        | `list[str]`                                  | Expand ID ranges; a single ID returns itself                                                                                                                                    |
| `format_ranges(ids)`                            | `Iterable[str]`                                  | `str`, e.g. `"T1-T4, T7"`                    | Compact display                                                                                                                                                                 |
| `approx_equal(a, b, abs_tol=1e-6)`              | numbers or arrays                                | `bool`                                       | Float comparison with an absolute tolerance; shape mismatch is unequal                                                                                                          |
| `seeded_rng(seed)`                              | `int`                                            | `numpy.random.Generator`                     | The only randomness allowed in the library and scripts                                                                                                                          |
| `as_points(obj)`                                | tuples, lists, arrays, Shapely, objects with x/y | `Points`                                     | Normalize point inputs                                                                                                                                                          |
| `content_hash(obj, *, domain="showpy.content")` | any model or bytes                               | `str` (SHA-256 hex)                          | Hash of domain-tagged canonical JSON (RFC 8785), so `1` and `1.0` hash alike and equal content in two domains differs; `domain` is lowercase ASCII letters, digits, `_` and `.` |
| `chunk_counts(total, parts)`                    | `float`, `int`                                   | `list[float]`                                | Split counts evenly; the parts sum exactly to `total`                                                                                                                           |
| `close_matches(name, candidates)`               | `str`, `Iterable[str]`                           | `list[str]`                                  | Up to three close names, best first, for error hints                                                                                                                            |
| `log(msg, **fields)`                            |                                                  | `None`                                       | `msg key=value ...` at INFO on the `showpy` logger, captured by builds                                                                                                          |
| `timer()`                                       |                                                  | context manager yielding `Timer{elapsed_ms}` | Measure script timing; never feeds library logic                                                                                                                                |

## showpy.cast

Who is in a show: groups, spots, labeling rules, named selections, and the selection language used everywhere to pick them.

### showpy.cast.models

**`Group`** — a designer-made set of spots with shared defaults.

| Field             | Type               | Default             | Meaning                                     |
| ----------------- | ------------------ | ------------------- | ------------------------------------------- |
| `key`             | `str`              | required            | e.g. `trumpets`, `front-guard`              |
| `name`            | `str`              | key in title case   | Display name                                |
| `label_prefix`    | `str`              | required            | e.g. `T` gives T1, T2                       |
| `symbol`, `color` | `str`              | `circle`, `#808080` | 2D sheet glyph and hex color                |
| `default_radius`  | `Meters`           | 0.3                 | Footprint radius for new spots              |
| `limits`          | `dict[str, float]` | {}                  | Per-group limit overrides (see the checker) |
| `moves`           | `bool`             | True                | False for stationary groups (pit)           |

**`Spot`** — a dot designed on the sheet that persists through the whole show.

| Field             | Type                              | Default           | Meaning                                                                                    |
| ----------------- | --------------------------------- | ----------------- | ------------------------------------------------------------------------------------------ |
| `id`              | `SpotId`                          | required          | Label shown on the sheet, stable all show                                                  |
| `group`           | `str`                             | required          | Group key                                                                                  |
| `symbol`, `color` | `str \| None`                     | None (from group) | Sheet display                                                                              |
| `tags`            | `tuple[str, ...]`                 | ()                | e.g. `soloist`, `leader`; deduplicated in first-seen order                                 |
| `active`          | `bool`                            | True              | False excludes from forms and checks                                                       |
| `shape`           | `Shape`                           | circle, 0.3 m     | Size and outline on the sheet and for collisions; circle or rectangle, set by the designer |
| `claimable`       | `"performer" \| "crew" \| "none"` | performer         | Who can claim the spot when the show is published                                          |

**`Shape`** — `kind: "circle" | "rectangle" = "circle", radius_m: float = 0.3, width_m: float = 0.6, depth_m: float = 0.6, rotation_deg: Degrees = 0`; property `extent_m` is the radius of the smallest circle that holds the footprint. Rectangles rotate with the spot's facing; used for display and collisions.

**`LabelScheme`** — `prefixes: dict[str, str]` (group → prefix), `start: int = 1`, `pad: int = 0`; methods `prefix_for(group)` and `label(prefix, number)`. The scheme's prefix overrides; a group missing from `prefixes` falls back to its own `label_prefix`.

**`Cast`** — every group and spot in the show.

| Field          | Type                            | Meaning                   |
| -------------- | ------------------------------- | ------------------------- |
| `groups`       | `dict[str, Group]`              | Groups and their defaults |
| `spots`        | `tuple[Spot, ...]`              | All spots                 |
| `selections`   | `dict[str, tuple[SpotId, ...]]` | Named selections          |
| `label_scheme` | `LabelScheme`                   | Labeling rules            |

Methods: `spot(id) → Spot`, `ids() → tuple[SpotId, ...]`.

### showpy.cast.spots (creating spots on the sheet)

Spots are drawn directly on the sheet; there is no roster to import. A new spot exists in every set: sets before the one it was created in mark it off the field until an entrance is designed.

| Function                                                                         | Inputs                            | Output                   | Purpose                                                                                                                                                       |
| -------------------------------------------------------------------------------- | --------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `add_spot(show, point, group, set_id, *, force=False)`                           | `Point`, group key, set           | `Show`                   | One dot, on the field from `set_id` on; a dot that would stand on the field in a pinned set raises `LockedError` unless forced                                |
| `add_spots_along(show, curve, n, group, set_id, *, force=False)`                 | `Curve`, count                    | `Show`                   | Dots evenly by arc length along a line or curve                                                                                                               |
| `fill_spots(show, region, group, set_id, interval=None, n=None, *, force=False)` | Shapely area or `Region`          | `Show`                   | Fill an area with a grid of dots, given the interval or the count                                                                                             |
| `duplicate_spots(show, sel, offset, set_id, *, force=False)`                     | selection, `Point`                | `Show`                   | Copy dots under new labels                                                                                                                                    |
| `delete_spots(show, sel, *, force=False)`                                        |                                   | `Show`                   | Remove from the cast, every set, forms, paths, routes, notes and selections; pinned or locked items raise `LockedError`, and a forced delete drops their pins |
| `regroup(show, sel, group)`                                                      |                                   | `(Show, dict[old, new])` | Move to another group and relabel to its prefix                                                                                                               |
| `add_group(show, group)` / `edit_group(show, key, /, **changes)`                 | `Group`                           | `Show`                   | Manage groups; the key is fixed and labels stay until `relabel`                                                                                               |
| `remove_group(show, key, reassign_to=None)`                                      |                                   | `(Show, dict[old, new])` | Remove a group, first moving its spots as `regroup` does                                                                                                      |
| `relabel(show, scheme)`                                                          | `LabelScheme`                     | `(Show, dict[old, new])` | New labels plus mapping for history rename                                                                                                                    |
| `define_selection(show, name, sel)`                                              |                                   | `Show`                   | Named selection, usable later as `sel:<name>`                                                                                                                 |
| `set_shape(show, sel, shape)`                                                    | `Shape`                           | `Show`                   | Circle or rectangle, size and rotation                                                                                                                        |
| `set_claimable(show, sel, value)`                                                | `"performer" \| "crew" \| "none"` | `Show`                   | Who can claim these spots when published                                                                                                                      |

### showpy.cast.rewrite

| Function                         | Inputs                            | Output           | Purpose                                                          |
| -------------------------------- | --------------------------------- | ---------------- | ---------------------------------------------------------------- |
| `rewrite_spot_ids(show, rename)` | `rename(SpotId) → SpotId \| None` | `Show`           | Apply a rename to every spot reference; None drops the reference |
| `faces_entity(mode, value)`      | `FacingMode`, facing value        | `SpotId \| None` | The spot a facing aims at, if any                                |

### showpy.cast.select

**`Selection`** is re-exported from `showpy.core.selection`.

Expression grammar for `select`: IDs `T4`, ranges `T1-T8`, prefixes `T*` (labels whose prefix before the trailing number is exactly `T`), `all`, `group:trumpets`, `form:arc1`, `tag:soloist`, `shape:rectangle`, `sel:<name>` (a named selection), combined with `+` (union), `-` (difference), `&` (intersection) and parentheses. `+` and `-` apply left to right (`T* + B* - T3` is `(T* + B*) - T3`); `&` chains with itself (`tag:a & T* & front`), but mixing `&` with `+` or `-` at one parenthesis level raises `SchemaError` (code `MIXED_OPERATORS`) whose hint shows both parenthesized readings. Operators need spaces or parentheses on both sides (`T1 + T2 - T3`, `(T*)-(T2)`); a glued operator such as `T1+T2` raises `SchemaError` with its position, because `T1-T8` without spaces is a range. A `Set` cannot resolve `group`, `tag`, `shape` or `sel` words; a `Cast` cannot resolve `form` words.

| Function                                       | Inputs                                            | Output                        | Purpose                                                                                                                                                                      |
| ---------------------------------------------- | ------------------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `select(source, expr)`                         | `Show`, `Set` or `Cast`; `Selector`               | `Selection`                   | Pick spots; bad token raises with its position                                                                                                                               |
| `is_label_dependent(expr)`                     | expression                                        | `bool`                        | Whether it names spots by label, so relabeling changes its meaning                                                                                                           |
| `label_prefix(spot_id)`                        | `SpotId`                                          | `str \| None`                 | The part of a label before its trailing number                                                                                                                               |
| `in_region(set_, shape)`                       | `Set`; Shapely area, `Region`, `Points` or a ring | `Selection`                   | Lasso and box selection of on-field spots; the boundary counts as inside                                                                                                     |
| `near(set_, point, radius)`                    |                                                   | `Selection`                   | On-field spots within a radius                                                                                                                                               |
| `where(set_, predicate)`                       | callable on `Placement`                           | `Selection`                   | Any filter, off-field placements included                                                                                                                                    |
| `ordered_along(set_, sel, by="form")`          | `"form"`, `"x"`, `"y"`, or a `Curve`              | `Selection`                   | Order for ripples, follow-the-leader, matching; ties by ID                                                                                                                   |
| `split(sel, k)`                                |                                                   | `list[Selection]`             | k consecutive groups whose sizes differ by at most one; raises `SchemaError` when `k` is below 1, above the selection's size (1 for an empty selection) or above `MAX_RANGE` |
| `alternate(sel, k=2)`                          |                                                   | `list[Selection]`             | Every k-th spot                                                                                                                                                              |
| `mirror_pairs(set_, sel, axis_x=0.0, tol=0.1)` |                                                   | `list[tuple[SpotId, SpotId]]` | Symmetry partners across a vertical axis                                                                                                                                     |

## showpy.sheet

Used for the 2D design surface every show is drawn on. There is one standard sheet; a template fills it in (size, lines, landmarks, zones, grid), and a coordinate system on top only changes labels and dot-sheet wording, never the design.

### showpy.sheet.models

**Enums**: `SurfaceType` = `FOOTBALL_FIELD`, `STAGE`, `GYM_FLOOR`, `ARENA`, `STREET`, `BLANK`. `ZoneKind` = `RESTRICTED`, `PIT`, `WINGS`, `STAGING`, `AUDIENCE`, `SAFETY`, `CUSTOM`. `CoordinateKind` = `MARCHING`, `STAGE`, `DANCE_FLOOR`, `GRID`.

| Data class              | Fields                                                                                                                                                                                                                                                                                                                    | Represents                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `Grid`                  | `origin: Point = (0, 0), spacing: tuple[Meters, Meters] = (1, 1), major_every: int = 5, rotation_deg: Degrees = 0, unit_name: str = "m"`                                                                                                                                                                                  | Background grid                                                                             |
| `GuideLine`             | `id: str, kind: "yard" \| "hash" \| "sideline" \| "center" \| "edge" \| "spike" \| "custom", points: Ring, label: str`                                                                                                                                                                                                    | A line drawn on the sheet                                                                   |
| `Landmark`              | `id: LandmarkId, name: str, kind: "point" \| "line" \| "area", geometry: Ring, aliases: tuple[str, ...]`; property `point`                                                                                                                                                                                                | Named reference ("front hash", "center stage")                                              |
| `Zone`                  | `id: ZoneId, kind: ZoneKind, polygon: Region, applies_to: SelectorField = "all", active: tuple[tuple[Count, Count], ...] \| None, note: str`; `is_active(t)`                                                                                                                                                              | An area with rules                                                                          |
| `Sheet`                 | `ref: SheetRef, name: str, surface_type: SurfaceType, size_m: tuple[Meters, Meters], outline: Ring, walkable: Region, guides: tuple[GuideLine, ...], landmarks: tuple[Landmark, ...], zones: tuple[Zone, ...], grid: Grid, default_coordinates: CoordinateSystem, based_on: SheetRef \| None, owner: str, thumbnail: str` | The 2D design surface; (0, 0) is its front-center                                           |
| `CoordinateSystem`      | `kind: CoordinateKind = GRID, origin: Point \| LandmarkId = (0, 0), audience_side: "front" \| "back" \| "left" \| "right" = "front", units: str = "m", precision: float = 0.01, labels: dict[str, str]`                                                                                                                   | How positions are labeled and worded (display only)                                         |
| `SheetStore` (protocol) | `get(ref) → Sheet \| None`, `put(sheet, scope) → SheetRef`; `InMemorySheetStore` implements it                                                                                                                                                                                                                            | Where saved sheets live; injected, never global                                             |
| `SheetCanvas`           | `min_x, min_y, max_x, max_y: float, px_per_m: float`; `SheetCanvas.for_sheet(sheet, px_per_m=20.0, margin_m=1.0)`, `size`, `to_px(p)`, `from_px(q)`, `ring_px(ring)`                                                                                                                                                      | Meter-to-pixel mapping with the audience at the bottom of the image (`showpy.sheet.render`) |

### showpy.sheet functions

Templates and sheet edits live in `showpy.sheet.templates` (built-in surfaces in `showpy.sheet.surfaces`), coordinates in `showpy.sheet.coords`, bounds and landmark lookup in `showpy.sheet.bounds`, the landmark fit in `showpy.sheet.replace`, and rendering in `showpy.sheet.render`. A show keeps the sheet it was created on: there is no `replace_sheet` and no `SheetReplaceReport`; make a new show on the other sheet instead.

| Function                                                                                                | Inputs                                     | Output                     | Purpose                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------ | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sheet_templates(surface_type=None)`                                                                    | `SurfaceType`                              | `list[Sheet]`              | Built-in templates: blank 20 × 15 m, NFHS, NCAA and NFL football fields, proscenium stage 12 × 9 m, gym floor (basketball court)                                 |
| `get_sheet(ref, store=None)`                                                                            | `SheetRef`, `SheetStore`                   | `Sheet`                    | Resolve against the templates first, then the injected store                                                                                                     |
| `blank_sheet(width_m, depth_m, grid_spacing=1.0, units="m")`                                            |                                            | `Sheet`                    | Plain rectangle with a center line, for unusual spaces                                                                                                           |
| `customize(sheet, **changes)`                                                                           | `name`, `grid`, `size` (blank sheets only) | `Sheet`                    | Adjust a sheet without replacing it                                                                                                                              |
| `add_zone(sheet, item)` / `add_landmark(sheet, item)` / `add_guide(sheet, item)`                        | `Zone`, `Landmark`, `GuideLine`            | `Sheet`                    | Mark pit areas, props areas, custom references                                                                                                                   |
| `remove_zone(sheet, zone_id)` / `remove_landmark(sheet, landmark_id)` / `remove_guide(sheet, guide_id)` |                                            | `Sheet`                    |                                                                                                                                                                  |
| `save_sheet(store, sheet, scope="mine")`                                                                | `SheetStore`; `"mine"` or `"team"`         | `SheetRef`                 | Keep a customized sheet (e.g. "Lincoln HS home field") under the next free version, recording `based_on`                                                         |
| `coordinate_presets(sheet)`                                                                             |                                            | `list[CoordinateSystem]`   | Systems that suit this sheet, its default first                                                                                                                  |
| `set_coordinates(show, system)`                                                                         | `CoordinateSystem`                         | `Show`                     | Change wording, origin or audience side; positions do not move                                                                                                   |
| `to_coords(show, p, style="long")` / `from_coords(show, text)`                                          | `"long"` or `"short"`                      | `str` / `Point`            | Position in the chosen system and back (either part first)                                                                                                       |
| `similarity_fit(src, dst)`                                                                              | two `Points`                               | 3 × 3 matrix               | Least-squares rotation, uniform scale and translation (Umeyama); raises `GeometryError` for a reflection, fewer than two landmarks, non-finite or stacked points |
| `on_surface(sheet, p)` / `walkable(sheet, p)`                                                           | `Point`                                    | `bool`                     | Bounds tests; the outline counts as on the surface                                                                                                               |
| `landmark(sheet, name)`                                                                                 | name, ID or alias, case-insensitive        | `Landmark`                 | Look up a reference                                                                                                                                              |
| `landmark_anchor(mark)`                                                                                 | `Landmark`                                 | `Point`                    | The point a landmark stands for                                                                                                                                  |
| `nearest_landmarks(sheet, p, k=3, kinds=None)`                                                          |                                            | `list[(Landmark, Meters)]` | Closest references, for wording; ties by ID                                                                                                                      |
| `zones_at(show, p, t=None)`                                                                             |                                            | `list[Zone]`               | Sheet zones, then show zones, covering a point; with `t`, only zones active then                                                                                 |
| `render_sheet(sheet, fmt="svg", px_per_m=20.0)`                                                         | `svg`, `png`, `pdf`                        | `bytes`                    | Printable blank sheet                                                                                                                                            |
| `draw_sheet(draw, canvas, sheet)`                                                                       | PIL drawing, `SheetCanvas`                 | `None`                     | Paint outline, walkable area, zones, guides and landmarks; shared with video export                                                                              |

## showpy.notes

Used for design notes on the canvas, replacing a separate plan: a goal for each set, sticky notes on spots or groups, and ideas the agent drafts from your concept. Review discussion stays in comments (showpy.show.collab).

| Data class | Fields                                                                                                                                                                                                                               | Represents                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `Anchor`   | `kind: "set" \| "transition" \| "point" \| "area" \| "spots"` plus exactly the matching payload: `set_id`, `tid`, `point`, `area: Region` or `spots`; built with `Anchor.on_set`, `on_transition`, `at_point`, `on_area`, `on_spots` | What a note or comment is attached to, tagged so it round-trips through JSON |
| `Note`     | `id: str, text: str, kind: "goal" \| "idea" \| "todo" \| "warning" = "idea", anchor: Anchor, author: str (user or "agent"), created_at: datetime, visible: bool = True`                                                              | One design note                                                              |

| Function                                                                | Inputs                               | Output       | Purpose                                                   |
| ----------------------------------------------------------------------- | ------------------------------------ | ------------ | --------------------------------------------------------- |
| `add_note(show, text, anchor, note_id, at, author, kind="idea")`        | `Anchor`, explicit ID and time stamp | `Show`       | Add a note to a set, transition, point, area or spots     |
| `edit_note(show, note_id, /, **changes)` / `remove_note(show, note_id)` |                                      | `Show`       | Change or delete; the ID is fixed                         |
| `notes_for(show, target, include_hidden=False)`                         | `SetId` or `TransitionId`            | `list[Note]` | Notes for a set or transition (the set strip shows goals) |
| `notes_near(show, p, radius, set_id=None, include_hidden=False)`        |                                      | `list[Note]` | Sticky notes around a point                               |
| `accept_draft(show, draft)`                                             | drafted `Note`                       | `Show`       | Add a drafted note and make it visible                    |

## showpy.time

Used for choreography timing: when each count happens, so sets land at the right moments, speed checks know real time, and the preview stays in sync with the audio. The music itself is only a playback asset; it never changes the timeline. The designer sets the tempo; the agent can read an uploaded track and suggest markers or tempo changes, but only accepted suggestions change anything.

### showpy.time.models

**Enums**: `TimelineMode` = `COUNTS` (most music-driven shows), `SECONDS` (clock-timed shows), `CUES` (theater-style shows). `MarkerKind` = `REHEARSAL`, `CUE`, `CUSTOM`.

| Data class       | Fields                                                                                                                                                                                                                            | Represents                                                                             |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `TempoSection`   | `at_count: Count, counts_per_minute: CountsPerMinute, ramp_to: CountsPerMinute \| None, ramp_counts: Count \| None`                                                                                                               | How fast counts go from a count onward, with an optional gradual speed-up or slow-down |
| `Hold`           | `at_count: Count, seconds: Seconds, note: str \| None`                                                                                                                                                                            | A pause where counts stop (a held note, a freeze)                                      |
| `Marker`         | `label: str, at_count: Count, kind: MarkerKind = CUSTOM`                                                                                                                                                                          | A named moment ("the hit", "letter B")                                                 |
| `MeasureSetting` | `counts_per_measure: float, start_measure: int = 1, changes: tuple[tuple[Count, float], ...]`                                                                                                                                     | Optional measure numbering for designers who talk in measures                          |
| `MeasureLabel`   | `measure: int, count_in_measure: float`                                                                                                                                                                                           | Display label for a count, e.g. m. 33, count 1                                         |
| `Cue`            | `label: str, order: int, estimated_seconds: Seconds`                                                                                                                                                                              | A moment in cue mode, with an estimated duration so the preview can play               |
| `AudioTrack`     | `file: str, label: str, sync_offset_s: Seconds = 0, gain: float = 1, section: tuple[Count, Count] \| None`                                                                                                                        | Music or sound that plays alongside the show; never changes timing                     |
| `Timeline`       | `mode: TimelineMode, tempos: tuple[TempoSection, ...], holds: tuple[Hold, ...], markers: tuple[Marker, ...], measures: MeasureSetting \| None, cues: tuple[Cue, ...], audio: tuple[AudioTrack, ...], total_counts: Count \| None` | The whole show timeline                                                                |

### showpy.time functions

Editing, markers, cues and persistence live in `showpy.time.timeline`; tempo and conversions in `showpy.time.clock`; measures in `showpy.time.measures`; validation in `showpy.time.validate`.

| Function                                                 | Inputs               | Output                                     | Purpose                                                                                                          |
| -------------------------------------------------------- | -------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `new_timeline(mode, counts_per_minute=None, total=None)` |                      | `Timeline`                                 | Start a timeline; COUNTS mode needs a tempo, which becomes the section at count 0                                |
| `add_tempo(tl, section)` / `remove_tempo(tl, at_count)`  | `TempoSection`       | `Timeline`                                 | Set or change tempo from a count                                                                                 |
| `add_hold(tl, hold)` / `remove_hold(tl, at_count)`       | `Hold`               | `Timeline`                                 | Pauses                                                                                                           |
| `add_marker(tl, marker)` / `remove_marker(tl, label)`    | `Marker`             | `Timeline`                                 | Named moments; labels are unique                                                                                 |
| `add_cue(tl, cue)` / `remove_cue(tl, label)`             | `Cue`                | `Timeline`                                 | Cue mode moments                                                                                                 |
| `set_measures(tl, setting)` / `clear_measures(tl)`       | `MeasureSetting`     | `Timeline`                                 | Turn measure labels on or off                                                                                    |
| `attach_audio(tl, track)` / `remove_audio(tl, label)`    | `AudioTrack`         | `Timeline`                                 | Add or remove playback audio                                                                                     |
| `set_sync(tl, label, offset_s)`                          | track label, seconds | `Timeline`                                 | The audio time that plays at count 0                                                                             |
| `tempo_at(tl, c)`                                        | `Count`              | `CountsPerMinute`                          | Tempo at a count; ramps change tempo linearly in counts                                                          |
| `count_to_seconds(tl, c)` / `seconds_to_count(tl, s)`    |                      | `Seconds` / `Count`                        | Convert, including ramps and holds; during a hold, returns the hold's count                                      |
| `seconds_per_count(tl, c)`                               |                      | `Seconds`                                  | Used by speed checks                                                                                             |
| `count_to_measure(tl, c)`                                |                      | `MeasureLabel \| None`                     | Measure label (None if measures are off)                                                                         |
| `measure_to_count(tl, measure, count_in_measure=1)`      | both 1-based         | `Count`                                    | Find a count by measure                                                                                          |
| `marker_to_count(tl, label)`                             |                      | `Count`                                    | Jump to a named moment                                                                                           |
| `counts_from_markers(tl, labels)`                        | marker labels        | `list[Count]`                              | Turn marked moments into set boundaries                                                                          |
| `cue_to_seconds(tl, label)`                              |                      | `Seconds`                                  | Cue mode time: the summed estimates of every earlier cue                                                         |
| `describe_count(tl, c)`                                  | `Count`              | `str`, e.g. `"count 130, 1:05.0, 120 cpm"` | Readable time for the canvas and agent; adds the measure when measures are on                                    |
| `to_json(tl)` / `from_json(d)`                           |                      | `dict` / `Timeline`                        | Persistence; `from_json` raises `SchemaError`                                                                    |
| `validate_timeline(tl)`                                  |                      | `ValidationReport`                         | Tempos positive and ordered, holds don't overlap, markers within the show, audio sections in range; never raises |

## showpy.geo

The drawing toolkit on the stage plane: curves and regions, spots spaced along or inside them, transforms that move whole shapes, and static measurements. Transforms and measurements accept `Points`, a curve or a `Set` with a selector and return the same type; forms go through `showpy.choreo.form_ops.transform_form`. `showpy.geo` never imports the PathEngine: measures over time live in `showpy.choreo.motion_stats`.

### showpy.geo.curves (with shapes, regions, svg, text, affine)

The `Curve` protocol is in `showpy.geo.curve_base`, the curve models in `showpy.geo.curves`, constructors in `showpy.geo.shapes`, area operations in `showpy.geo.regions`, SVG path parsing in `showpy.geo.svg`, the built-in stroke font in `showpy.geo.text`, and affine matrices in `showpy.geo.affine`. Arc angles use the math convention: degrees counter-clockwise from +x.

**`Curve`** (protocol, implemented by every curve model)

| Member                           | Inputs                      | Output                            | Purpose                                                                                          |
| -------------------------------- | --------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------ |
| `closed`                         |                             | `bool`                            | Whether the curve loops                                                                          |
| `length()`                       |                             | `Meters`                          | Arc length                                                                                       |
| `point_at(s)`                    | arc length (float or array) | `Points`, shape (2,) for a scalar | Point at a distance along it; open curves clamp, closed curves wrap                              |
| `point_at_u(u)`                  | 0..1                        | `Points`                          | Point at a fraction along it                                                                     |
| `tangent_at(s)` / `normal_at(s)` |                             | unit `Points`                     | Direction and left normal                                                                        |
| `project(p)`                     | `Point`                     | `Meters`                          | Arc length of the closest point                                                                  |
| `substring(s0, s1)`              |                             | `TrimmedCurve`                    | A piece (reversed if s0 > s1)                                                                    |
| `reversed()`                     |                             | `TrimmedCurve`                    | Opposite direction                                                                               |
| `to_polyline(tol=0.01)`          | chord error                 | `Points`                          | Approximate as straight pieces                                                                   |
| `to_shapely(tol=0.01)`           |                             | `LineString`                      | Interop                                                                                          |
| `construction()`                 |                             | `dict`                            | JSON recipe with a `kind`, stored on forms; `curve_from_construction(recipe)` rebuilds the curve |

Every curve model is a frozen Pydantic model with a `kind` discriminator; `AnyCurve` is their union.

| Curve model      | Fields                                                                  | Represents                                            |
| ---------------- | ----------------------------------------------------------------------- | ----------------------------------------------------- |
| `Segment`        | `a, b: Point`                                                           | Straight line                                         |
| `Polyline`       | `points: tuple[Point, ...], closed: bool`                               | Connected straight pieces                             |
| `ArcCurve`       | `center: Point, radius: Meters, start_deg: Degrees, sweep_deg: Degrees` | Circular arc (sign of sweep = direction)              |
| `CircleCurve`    | `center, radius, start_deg = 0, ccw: bool = True`                       | Circle; mirrored circles keep `ccw=False`             |
| `EllipseCurve`   | `center, rx, ry, rotation_deg`                                          | Oval                                                  |
| `SplineCurve`    | `points: tuple[Point, ...], alpha = 0.5, closed: bool`                  | Smooth curve through points (centripetal Catmull–Rom) |
| `BezierCurve`    | `control: tuple[Point, ...]`                                            | Handle-drawn curve of degree len(control) − 1         |
| `SvgCurve`       | `d: str, transform: 3 × 3 tuple`                                        | One vector-art subpath                                |
| `CompositeCurve` | `parts: tuple[AnyCurve, ...]`                                           | Joined curves                                         |
| `SpiralCurve`    | `center, r0, r1, turns, start_deg`                                      | Archimedean spiral                                    |
| `TrimmedCurve`   | `base: AnyCurve, s0: Meters, s1: Meters`                                | A piece of another curve, reversed when s0 > s1       |

| Constructor                                                                  | Inputs                                                                                  | Output                       | Purpose                                                            |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------ |
| `line(a, b)`                                                                 | two `Point`                                                                             | `Segment`                    | The ends must differ                                               |
| `polyline(*pts, closed=False)`                                               | points                                                                                  | `Polyline`                   |                                                                    |
| `arc(center, radius, start_deg, sweep_deg)`                                  |                                                                                         | `ArcCurve`                   |                                                                    |
| `arc_through(a, m, b)`                                                       | three points                                                                            | `ArcCurve`                   | Raises on collinear points                                         |
| `arc_from_chord(a, b, bulge)`                                                | ends, sagitta in meters                                                                 | `ArcCurve`                   | "Bow this line by 2 m"; positive bows to the left of a→b           |
| `circle(center, radius, start_deg=0)`                                        |                                                                                         | `CircleCurve`                | Counter-clockwise                                                  |
| `ellipse(center, rx, ry, rotation_deg=0)`                                    |                                                                                         | `EllipseCurve`               |                                                                    |
| `curve_through(*pts, closed=False, alpha=0.5)`                               |                                                                                         | `SplineCurve`                | Smooth curve through control points                                |
| `bezier(*control)`                                                           |                                                                                         | `BezierCurve`                |                                                                    |
| `spiral(center, r0, r1, turns, start_deg=0)`                                 |                                                                                         | `SpiralCurve`                |                                                                    |
| `rect(center, w, d, rotation_deg=0)`                                         |                                                                                         | closed `Polyline`            | Rectangle outline, corners counter-clockwise from front-left       |
| `wedge(apex, length, spread_deg, facing_deg=0)`                              |                                                                                         | `Polyline`                   | V or arrow; the apex leads toward `facing_deg`                     |
| `regular_polygon(center, radius, n, rotation_deg=0)`                         |                                                                                         | closed `Polyline`            | First vertex points upstage before rotation                        |
| `star(center, r_outer, r_inner, n, rotation_deg=0)`                          |                                                                                         | closed `Polyline`            |                                                                    |
| `svg_outline(svg, fit, path_index=None, keep_aspect=True, flip_y=True)`      | a `d` string or SVG text (not a file path), target `Box2`                               | `list[SvgCurve]`             | Pictures and logos, one curve per subpath                          |
| `text_outline(text, font, height, at, *, font_registry=None)`                | `font` is a name, a URI or a fallback chain; `at` is the baseline-left; see Text layout | `list[Polyline \| SvgCurve]` | Letters                                                            |
| `join(*curves, bridge=False)`                                                |                                                                                         | `CompositeCurve`             | Raises if ends do not meet unless bridged                          |
| `offset(curve, distance, side="left")`                                       |                                                                                         | `Curve`                      | Parallel curve                                                     |
| `curve_from_construction(recipe)`                                            | `construction()` dict                                                                   | `AnyCurve`                   | Rebuild a stored curve                                             |
| `region(outline)`                                                            | closed `Curve` or ring of points                                                        | `Polygon`                    | Area; raises `GeometryError` on self-intersections                 |
| `buffer(area, d)`, `union(*areas)`, `difference(a, b)`, `intersection(a, b)` |                                                                                         | `Polygon` or `MultiPolygon`  | Area operations; a negative `d` shrinks                            |
| `parse_path(d)`                                                              | SVG path data                                                                           | `list[Subpath]`              | Evaluable line, quadratic, cubic and arc pieces (`showpy.geo.svg`) |
| `glyph_strokes(char, font)`                                                  |                                                                                         | strokes on a 4 × 6 grid      | One letter of a stroke font (`showpy.geo.text`)                    |

| Function (`showpy.geo.affine`)                                                               | Inputs     | Output       | Purpose                                                            |
| -------------------------------------------------------------------------------------------- | ---------- | ------------ | ------------------------------------------------------------------ |
| `translation_matrix(dx, dy)` / `rotation_matrix(deg, about)` / `scale_matrix(sx, sy, about)` |            | 3 × 3 matrix | Basic maps; rotation is counter-clockwise                          |
| `mirror_matrix(a, b)` / `skew_matrix(deg_x, deg_y, about)`                                   |            | 3 × 3 matrix | Reflection across the line a–b; shear                              |
| `is_similarity(matrix)`                                                                      |            | `bool`       | Rotation, translation, uniform scale or mirror                     |
| `transform_points(points, matrix)`                                                           |            | `Points`     | Map points                                                         |
| `transform_facing(theta, matrix)`                                                            | `Degrees`  | `Degrees`    | Map a facing through the linear part                               |
| `transform_curve(curve, matrix)`                                                             | `AnyCurve` | `AnyCurve`   | Map a curve, keeping its family whenever the map allows it exactly |

### Text layout and fonts (`showpy.geo.text_layout`, `showpy.geo.fonts`)

Text is drawn with single-line stroke fonts: each glyph is a set of strokes, not an outline. Five fonts ship in `showpy/fonts/` (`importlib.resources`): `relief-singleline` (423 glyphs, Latin, Latin-1, Latin Extended-A, Greek smalls), `hershey-roman` (95), `hershey-script` (95), `hershey-greek` (48) and `simplex` (65, capitals, digits and folded smalls). `simplex` is its own bundled font with its original geometry, not a Hershey alias. Hershey Cyrillic is not bundled (deferred). The library never downloads a font and never reads one from the OS; user fonts arrive as bytes through `register_font`.

A font's identity is `asset:sha256:<64 hex>` of its exact file bytes. Recipes store that URI, never the short name. A name is a convenience: it points at one font at most. A font whose name already points at other bytes, such as an older release of a bundled font read from a document, is reachable by URI only. Validating a `FontRegistry` from data (`model_validate`) loads every font from its bytes and rejects one that differs with `FONT_HASH_MISMATCH`.

| Function or class                                                                                                                              | Inputs                                                                                                                        | Output                  | Purpose                                                                               |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| `FontRegistry`                                                                                                                                 | `fonts: Mapping[uri, Font]`, `sources: Mapping[uri, bytes]`, `names: Mapping[name, uri]`; all read-only and JSON-serializable | frozen model            | Loaded fonts, their bytes and names; `resolve(ref)` turns a name or URI into a URI    |
| `bundled_fonts()`                                                                                                                              |                                                                                                                               | `FontRegistry`          | The five bundled fonts, cached and read-only                                          |
| `register_font(registry, data, *, if_name_taken="raise")`                                                                                      | bytes of a `showpy.font.v1` JSON file, at most `MAX_FONT_BYTES` (8 MiB); `if_name_taken="keep"` registers by URI only         | `(FontRegistry, uri)`   | New registry with the font added; the same registry when it already holds these bytes |
| `load_font(data)`                                                                                                                              | same bytes                                                                                                                    | `Font`                  | Validates one font; every stroke is one subpath of positive length                    |
| `layout_text(text, fonts, height, at, *, rotation=0, align="left", anchor="baseline", letter_spacing=0, line_spacing=1.2, font_registry=None)` | `fonts` is a chain of names or URIs; `align` is `left`, `center` or `right`; `anchor` is `baseline`, `middle` or `top`        | `tuple[LaidGlyph, ...]` | Glyph strokes in stage meters, in reading order                                       |

Layout rules, all measured on the first font of the chain:

- `height` is the cap height in meters. `letter_spacing` and `line_spacing` are fractions of the first font's em, where `em_m = height * units_per_em / cap`.
- Each glyph is scaled so its own cap height equals `height` and advances by its own font's advance, so mixed chains stay level.
- Each character takes the first font in the chain that has it. A character that no font has raises `MISSING_GLYPH`.
- `rotation` is degrees counter-clockwise about `at`; 0 reads left to right toward +x. Glyph y-up maps to stage +y.
- Text is NFC-normalized (`e` + U+0301 becomes `é`). `\r\n`, `\r` and `\n` each start one new line. Trailing empty lines are dropped; interior ones are kept. Whitespace-only text raises a teaching `SchemaError`.
- Slot order is reading order: lines top to bottom, glyphs in logical order, strokes in font order, each stroke from its start.
  Caps are checked before anything is built:

| Cap                       | Value   | Applies to                                            | Failure                |
| ------------------------- | ------- | ----------------------------------------------------- | ---------------------- |
| `MAX_LAYOUT_CHARS`        | 1,000   | Raw characters of `text`, before normalization        | `SchemaError`          |
| `MAX_LAYOUT_EXTENT_M`     | 1e6 m   | Laid extent of the text block                         | `GeometryError`        |
| `MAX_FONT_BYTES`          | 8 MiB   | One font file                                         | `FontError` `BAD_FONT` |
| `MAX_GLYPH_PIECES`        | 512     | Path pieces of one glyph, over all its strokes        | `FontError` `BAD_FONT` |
| `MAX_STROKE_PIECES`       | 384     | Path pieces of one stroke                             | `FontError` `BAD_FONT` |
| `MAX_LAYOUT_PIECES`       | 512,000 | Path pieces of all laid glyphs of one text            | `SchemaError`          |
| `MAX_LAYOUT_CURVE_PIECES` | 60,000  | Path pieces of all laid curved strokes of one text    | `SchemaError`          |
| `MAX_TEXT_FONTS`          | 16      | Fonts in one `TextRecipe` chain                       | `SchemaError`          |
| `MAX_TEXT_SPOTS`          | 10,000  | Spots in one text form (`showpy.choreo.form_text`)    | `TOO_MANY_SPOTS`       |
| `MAX_IMPORT_FONTS`        | 64      | Fonts embedded in one document read by `import_fonts` | `FontError` `BAD_FONT` |

The glyph, stroke and layout piece caps are about 10 times the bundled maxima measured on 2026-11-16: the most complex bundled glyph, hershey-script `8`, has 51 pieces, the longest stroke, hershey-script `Z`, has 38, and 1,000 copies of the heaviest glyph lay 51,000. A curved stroke becomes an `SvgCurve`, whose table costs about 60 µs per piece to measure and place against about 1 µs for a straight one (measured 2026-11-17), so `MAX_LAYOUT_CURVE_PIECES` bounds those separately; bundled Relief needs at most 15,000 for 1,000 glyphs. `load_font` counts pieces while parsing, before sampling; `layout_text` parses each distinct glyph once and counts before building a stroke (NFC can lay out more glyphs than raw characters, for example U+0344 lays two). The worst legal text (1,000 glyphs, 512,000 pieces, 59,904 of them curved) builds a text form in about 3 s.

`text_outline` changed with this feature: it takes a font chain and `font_registry`, returns `list[Polyline | SvgCurve]`, closes glyph loops without repeating the start point, starts a new line at `\n` (it used to raise), rejects a non-finite `height` or `at`, raises `GlyphError` or `MissingFontError` (both `UnknownRefError`) for a missing glyph or font, and shares the two layout caps.

### showpy.geo.spacing

| Function                                                                           | Inputs                                                                                  | Output            | Purpose                                           |
| ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------- |
| `space_along(curve, n, start=0, end=None, endpoints=True)`                         |                                                                                         | `Points` (n, 2)   | Even spots by arc length                          |
| `space_by_interval(curve, interval, start=0, n=None, align="start")`               | `start`, `end`, `center`                                                                | `Points`          | Fixed spacing; raises with max that fits          |
| `space_proportional(curves, n)`                                                    | all-zero lengths raise `SpacingError`; `n` below the curve count raises `TOO_FEW_SPOTS` | `list[Points]`    | n spots across curves at one interval             |
| `apportion(lengths, n, minimums=None)`                                             | exact integer arithmetic; ties by larger fraction, longer curve, lower index            | `list[int]`       | Shares n spots across curves; sums to n for any n |
| `space_with_gaps(curve, n, gaps)`                                                  | gaps as `[(index, extra_m)]`                                                            | `Points`          | Extra space between sub-groups                    |
| `fill(area, interval, pattern="grid", angle_deg=0, anchor=None, n=None, margin=0)` | `grid`, `offset`, `hex`                                                                 | `Points`          | Fill an area                                      |
| `block(origin, rows, cols, interval, facing=0, anchor="center")`                   |                                                                                         | `Points`          | Rectangular block, front row first                |
| `ranks_and_files(points, rows, cols)`                                              |                                                                                         | `list[list[int]]` | Row and column index structure                    |
| `concentric(center, radii, counts, start_deg=0)`                                   |                                                                                         | `list[Points]`    | Rings                                             |
| `scatter(area, n, min_gap, seed=0)`                                                |                                                                                         | `Points`          | Organic scatter (Bridson Poisson disk)            |
| `distribute_on_points(candidates, n, method="farthest", seed=0)`                   | `farthest` or `random`                                                                  | `Points`          | Pick well-spread spots                            |

### showpy.geo.transforms

Every transform takes `Points`, a curve or a `Set` and returns the same kind, and also takes `sel="all"` and `force=False`. On a set only the selected on-field placements move, facings follow affine maps, and pinned placements raise `LockedError` unless forced; `sel` and `force` are ignored for points and curves. `snap`, `snap_to_guides`, `align` and `distribute` live in `showpy.geo.snapping` and are re-exported here. Forms use `showpy.choreo.form_ops.transform_form`.

| Function                                                                            | Inputs                                                                                                                                              | Purpose                                                       |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `shift(obj, dx, dy)`                                                                | meters                                                                                                                                              | Translate                                                     |
| `move_to(obj, anchor, to)`                                                          | `center`, `centroid`, `front-left`, `front-center`, `front-right`, `back-left`, `back-center`, `back-right`, `left-center`, `right-center`; `Point` | Move an anchor to a spot; front is the lowest y               |
| `rotate(obj, deg, about="center")`                                                  | `"center"`, `"centroid"` or a `Point`                                                                                                               | Rotate counter-clockwise; facings follow                      |
| `scale(obj, sx, sy=None, about="center")`                                           |                                                                                                                                                     | Resize (negative raises; use `mirror`)                        |
| `expand(obj, d, about="center")` / `contract(obj, d, about="center")`               |                                                                                                                                                     | Radial grow or shrink; overshooting the anchor raises         |
| `set_interval(obj, interval, anchor="center")`                                      | `start`, `center`, `end`                                                                                                                            | Re-space along its own polyline                               |
| `mirror(obj, axis)`                                                                 | `"center"` (vertical line through the box center), `"x"` (center line x = 0), `"y"` (horizontal line through the box center), or two points         | Reflect; facings reflect                                      |
| `symmetrize(s, sel="all", source_side="left", pairs=None, axis_x=0.0, force=False)` | `Set`                                                                                                                                               | Copy one side to its mirror partners, position and facing     |
| `skew(obj, deg_x=0, deg_y=0, about="center")`                                       |                                                                                                                                                     | Shear                                                         |
| `affine(obj, matrix)`                                                               | 2×3 or 3×3 matrix                                                                                                                                   | General transform                                             |
| `snap(obj, precision, mode="round", axes="xy")`                                     | `round`, `floor`, `ceil`                                                                                                                            | Snap to a grid; returns `(obj, SnapReport{moved, max_shift})` |
| `snap_to_guides(obj, sheet, tol, kinds=None)`                                       |                                                                                                                                                     | Pull onto guide lines within `tol`                            |
| `align(obj, axis, value)`                                                           | `"x"`/`"y"`, number or `mean`/`min`/`max`                                                                                                           | Line up                                                       |
| `distribute(obj, axis, start=None, end=None)`                                       |                                                                                                                                                     | Even out along an axis, keeping order                         |
| `interpolate_forms(a, b, u)`                                                        | equal-length `Points`                                                                                                                               | In-between preview                                            |

### showpy.geo.measure

Static measures only; positions and speeds over time are in `showpy.choreo.motion_stats`.

| Function                                     | Inputs                                 | Output                                                            | Purpose                                                          |
| -------------------------------------------- | -------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------- |
| `length(obj)`                                | `Curve` or `Points` (an open polyline) | `Meters`                                                          | Length                                                           |
| `center(obj)` / `centroid(obj)`              | `Points` or `Curve`                    | `Point`                                                           | Box center or mean                                               |
| `bounds(obj)`                                | `Points` or `Curve`                    | `Box2`                                                            | Extent                                                           |
| `interval(points, ordered=True)`             |                                        | `IntervalStats{mean, min, max, std, gaps}`                        | Spacing quality                                                  |
| `distance(a, b)` / `distance_matrix(a, b)`   |                                        | `Meters` / ndarray                                                | Euclidean                                                        |
| `on_field_ids(s, sel="all")`                 | `Set`                                  | `tuple[SpotId, ...]`                                              | Selected spots that are on the field                             |
| `neighbors(s, entity, k=2)`                  |                                        | `list[(SpotId, Meters)]`                                          | Closest on-field spots; ties by natural ID                       |
| `nearest(s, point, k=1)`                     |                                        | `list[(SpotId, Meters)]`                                          | Closest to a point                                               |
| `reachable_region(start, counts, max_speed)` |                                        | `Polygon`                                                         | Disk a spot can reach in time                                    |
| `mirror_gaps(points, axis_x)`                |                                        | `Points`                                                          | Per point, how far its mirror image lands from the nearest point |
| `form_metrics(points)`                       |                                        | `FormMetrics{length, intervals, curvature_range, symmetry_error}` | Form quality                                                     |
| `symmetry_error(s, sel="all", axis_x=0.0)`   |                                        | `Meters`                                                          | Largest mirror mismatch                                          |

| Data class (`showpy.geo.models`) | Fields                                                                                                   | Represents                                      |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `SnapReport`                     | `moved: int, max_shift: Meters`                                                                          | What a snap moved                               |
| `IntervalStats`                  | `mean, min, max, std: Meters, gaps: tuple[Meters, ...]`                                                  | Gaps between spots                              |
| `FormMetrics`                    | `length: Meters, intervals: IntervalStats, curvature_range: tuple[float, float], symmetry_error: Meters` | Shape quality of a form's points                |
| `SpeedStats`                     | `distance: Meters, counts: float, m_per_count: float, m_per_s: float, max_instant: float`                | Movement demand of one spot over one transition |

### showpy.geo.analysis (design feedback, not rules)

Measures that help the agent and designer judge whether a picture is interesting, not just possible. Nothing here passes or fails; results are numbers and heat maps to inform design. Motion feedback (`motion_contrast`, `energy_curve`, `phrase_profile`) is in `showpy.choreo.motion_stats`.

| Function                          | Inputs   | Output             | Purpose                                                                    |
| --------------------------------- | -------- | ------------------ | -------------------------------------------------------------------------- |
| `density_map(s, sheet, cell=1.0)` |          | integer grid array | Where the stage is crowded or empty, over the sheet outline's bounding box |
| `coverage(s, sheet, radius=1.0)`  |          | `float` 0..1       | Share of the walkable area within `radius` of an on-field spot             |
| `balance(s, sheet, axis="x")`     |          | `float` −1..1      | Mean offset from the sheet center: left versus right, front versus back    |
| `symmetry_score(s, axis_x=0.0)`   |          | `float` 0..1       | How mirrored the picture is                                                |
| `change_amount(a, b)`             | two sets | `Meters`           | Mean distance moved by spots on the field in both sets                     |

## showpy.choreo (part 1): placements, sets, forms, matching

A set is a snapshot of where every spot is; forms are the stored shapes that generate those positions; matching decides who takes which spot.

### showpy.choreo.models

**`ApprovalState`** (enum): `DRAFT`, `PROPOSED`, `APPROVED`.

**`Placement`** — one spot's state at one set.

| Field      | Type             | Default  | Meaning                                                                                                 |
| ---------- | ---------------- | -------- | ------------------------------------------------------------------------------------------------------- |
| `entity`   | `SpotId`         | required | The spot                                                                                                |
| `x`, `y`   | `Meters`         | required | Stage-plane position                                                                                    |
| `facing`   | `Degrees`        | 0        | Body direction                                                                                          |
| `form`     | `FormId \| None` | None     | Shape it belongs to                                                                                     |
| `order`    | `int \| None`    | None     | Index along the form                                                                                    |
| `pinned`   | `bool`           | False    | Designer lock                                                                                           |
| `note`     | `str \| None`    | None     | Printed on sheets                                                                                       |
| `on_field` | `bool`           | True     | False = off the field (before its entrance or after its exit); not drawn and not checked for collisions |

Property: `point → Point`.

**`Set`** — a snapshot of the stage at one moment.

| Field          | Type                    | Meaning                                                                                                                    |
| -------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `id`           | `SetId`                 |                                                                                                                            |
| `key`          | `str \| None`           | Stable storage key: fixed at creation, kept by `renumber`; defaults to `id` and is written only when it differs (ADR 0004) |
| `counts`       | `float`                 | Counts since the previous set (0 for the first); the incoming transition's counts always equal it                          |
| `placements`   | `tuple[Placement, ...]` | One per spot, off-field spots included                                                                                     |
| `forms`        | `tuple[FormId, ...]`    | Forms present                                                                                                              |
| `label`, `cue` | `str \| None`           | Designer label, rehearsal mark or cue                                                                                      |
| `source_hash`  | `str`                   | Hash of the script that built it                                                                                           |
| `state`        | `ApprovalState`         | Collaboration state                                                                                                        |

| Method                                  | Inputs                              | Output                                                                  | Purpose                                   |
| --------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------- |
| `table()`                               |                                     | DataFrame (`entity, x, y, facing, form, order, pinned, on_field, note`) | Readable table                            |
| `entity_ids()`                          |                                     | `tuple[SpotId, ...]`                                                    | Spots in placement order                  |
| `positions(sel="all")`                  |                                     | `Points`                                                                | Coordinates in selection order            |
| `get(entity)`                           |                                     | `Placement`                                                             | One row                                   |
| `with_placements(updates, force=False)` | `{id: Placement or PlacementPatch}` | `Set`                                                                   | Edit; raises `LockedError` on pinned rows |

**Spot counts.** `SpotCount{set_id, on_field: int, off_field: int, changed: bool}` describes one set for the set strip, which warns when the number of spots on the field changes unexpectedly between sets. `showpy.show.counts.spot_counts(show) → list[SpotCount]` computes it.

### showpy.choreo.forms

Constructors and slot generation live in `showpy.choreo.forms`; registered generators in `form_fns`; layout in `form_layout`; editing in `form_ops`; fitting in `form_fit`; placing forms into sets in `form_place`. A form stores how to rebuild its shape, never the dots: member i stands on slot i, and slots past the last member are open for `place` to fill.

| Data class    | Fields                                                                                                                                                                                                 | Represents                                                                                                                            |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `SpacingSpec` | `mode: "count" \| "interval" \| "proportional" = "count", interval: Meters \| None, start: Meters = 0, end: Meters \| None, align: "start" \| "end" \| "center", gaps: tuple[tuple[int, Meters], ...]` | How spots are placed on the shape                                                                                                     |
| `FacingSpec`  | `mode: "fixed" \| "audience" \| "toward_point" \| "along" \| "normal" \| "outward" = "audience", value: Degrees \| Point \| None`                                                                      | How members face                                                                                                                      |
| `Form`        | `id: FormId, set: SetId, kind: str, geometry: dict, members: tuple[SpotId, ...], spacing: SpacingSpec, facing: FacingSpec, locked: bool, meta: dict[str, str]`                                         | A stored shape plus ordered members; `kind` ∈ line, arc, curve, circle, block, wedge, polyline, svg, fill, scatter, custom, generated |

`Form.geometry` is a JSON recipe whose keys depend on `kind`: curve kinds hold `{"curve": construction()}`; `block` holds origin, rows, cols, interval, facing and anchor; `fill` an outline `Region`, interval, pattern and angle; `scatter` a `Region`, min gap and seed; `custom` points and facings; `generated` `{"fn", "params"}`. Every recipe may hold `"n"`, the slot count (default one slot per member), and non-curve recipes may hold `"transform"`, a 3 × 3 affine matrix applied to the generated points, which keeps gated or mirrored forms editable.

Every constructor takes `form_id`, `members`, the shape arguments, then `spacing=None, facing=None` where they apply, and the keyword-only `set_id=""` and `n=None`.

| Function                                                                                                                  | Inputs                                                  | Output                                            | Purpose                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `line_form(form_id, members, a, b, ...)`                                                                                  |                                                         | `Form`                                            | Line                                                                                                                                                                                            |
| `arc_form(form_id, members, center, radius, start_deg, sweep_deg, ...)`                                                   |                                                         | `Form`                                            | Arc                                                                                                                                                                                             |
| `curve_form(form_id, members, *pts, closed=False, ...)`                                                                   |                                                         | `Form`                                            | Smooth curve                                                                                                                                                                                    |
| `circle_form(form_id, members, center, radius, start_deg=0, ...)`                                                         |                                                         | `Form`                                            | Circle (default facing outward)                                                                                                                                                                 |
| `block_form(form_id, members, origin, rows, cols, interval, direction_deg=0, anchor="center", facing=None, *, set_id="")` |                                                         | `Form`                                            | Block, front row first; members face `direction_deg`                                                                                                                                            |
| `wedge_form(form_id, members, apex, length, spread_deg, direction_deg=0, ...)`                                            |                                                         | `Form`                                            | Wedge whose apex leads toward `direction_deg`                                                                                                                                                   |
| `polyline_form(form_id, members, *pts, closed=False, ...)`                                                                |                                                         | `Form`                                            | Corners                                                                                                                                                                                         |
| `svg_form(form_id, members, svg, fit, keep_aspect=True, ...)`                                                             |                                                         | `Form`                                            | Picture outline; spots share subpaths by length                                                                                                                                                 |
| `fill_form(form_id, members, outline, interval, pattern="grid", angle_deg=0, ...)`                                        |                                                         | `Form`                                            | Filled area; with members or `n`, keeps the slots nearest its center                                                                                                                            |
| `scatter_form(form_id, members, area, min_gap, seed=0, ...)`                                                              |                                                         | `Form`                                            | Scatter                                                                                                                                                                                         |
| `custom_form(form_id, members, points, facings=None, *, set_id="")`                                                       |                                                         | `Form`                                            | Frozen hand-placed spots                                                                                                                                                                        |
| `placements(form)`                                                                                                        |                                                         | `list[Placement]`                                 | Regenerate positions from construction                                                                                                                                                          |
| `slots(form)` / `slot_poses(form)`                                                                                        |                                                         | `Points` / `(Points, list[Degrees])`              | Spots (and facings) before members are assigned                                                                                                                                                 |
| `edit(form, force=False, **changes)`                                                                                      | construction or form fields                             | `Form`                                            | Change the shape; respects locks                                                                                                                                                                |
| `reorder(form, order, prev_set=None, force=False)`                                                                        | list, `"reverse"`, `"by_x"`, `"by_y"`, `"nearest_prev"` | `Form`                                            | Who sits where along it                                                                                                                                                                         |
| `place(show, set_id, forms, match_options=None, registry=DEFAULT_FORM_FNS)`                                               |                                                         | `(Set, list[Form])`                               | Build a set from forms, matching spots without a form into open slots; returns the set and the forms filled with their matched spots, and changes nothing in the show: store the returned forms |
| `filled_forms(placed, forms)`                                                                                             | placed `Set`                                            | `list[Form]`                                      | Forms whose members are read back from a placed set                                                                                                                                             |
| `split_form(form, at, ids=None, force=False)` / `merge_forms(a, b, form_id, force=False)`                                 | index or member                                         | `(Form, Form)` / `Form`                           | Regroup; nobody moves                                                                                                                                                                           |
| `morph_targets(a, b)`                                                                                                     | two forms, same members                                 | `Points`                                          | Targets for one form turning into another                                                                                                                                                       |
| `transform_form(form, matrix, force=False)`                                                                               | 3 × 3 matrix                                            | `Form`                                            | Map a form; slots and facings map exactly                                                                                                                                                       |
| `gate(form, hinge, deg, force=False)`                                                                                     | `"start"`, `"end"` or index                             | `Form`                                            | Rotate about one end                                                                                                                                                                            |
| `pinwheel(forms, center, deg, force=False)`                                                                               |                                                         | `list[Form]`                                      | Rotate spokes together                                                                                                                                                                          |
| `form_templates()`                                                                                                        |                                                         | `dict[str, Callable]`                             | Form constructors by kind; packs may add prefabs later                                                                                                                                          |
| `detect_form(points, tol=0.1, form_id="detected", members=())`                                                            |                                                         | `Form`                                            | Fit a line, arc or circle to imported spots, else a custom form                                                                                                                                 |
| `fit_line(points)` / `fit_circle(points)`                                                                                 |                                                         | `(a, b, residual)` / `(center, radius, residual)` | Total least squares line through its extreme points; Kåsa circle refined by Gauss–Newton; the largest residual                                                                                  |
| `rebuild(show, form_id, force=False)`                                                                                     |                                                         | `Show`                                            | Regenerate placements after an edit and reset its members' neighboring paths                                                                                                                    |

Functions that read a recipe also take `registry=DEFAULT_FORM_FNS`.

**Text forms.** `text_form` writes text on the field with one spot per slot along the glyph strokes (`mode="centerline"`). Its recipe is a `TextRecipe` stored in `Form.geometry` (`kind="text"`, `"n"` and `"transform"` keys shared with other kinds).

| Function or class                                                                                                                                                                                            | Output       | Purpose                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TextRecipe`: `text`, `fonts: tuple[uri, ...]`, `mode="centerline"`, `height`, `at`, `rotation=0`, `align="left"`, `anchor="baseline"`, `letter_spacing=0`, `line_spacing=1.2`, `spacing=None`               | model        | Frozen construction recipe of a text form; fonts are URIs                                                                                           |
| `text_form(form_id, members, text, *, fonts, height, at, rotation=0, align="left", anchor="baseline", letter_spacing=0, line_spacing=1.2, facing=None, set_id="", n=None, spacing=None, font_registry=None)` | `Form`       | Text form; give exactly one of `n` and `spacing`; `fonts` are names or URIs, stored as URIs                                                         |
| `edit(form, text=..., fonts=..., height=..., ...)`                                                                                                                                                           | `Form`       | Edits the recipe; keeps `n`, `spacing` and members; `n` or `spacing` raise `TEXT_COUNT_EDIT`; `members` must keep the count                         |
| `edit_text_count(form, *, add=(), remove=(), n=None, spacing=None, force=False, registry=DEFAULT_FORM_FNS, font_registry=None)`                                                                              | `Form`       | The only way to change a text form's count; removes `remove`, appends `add` in order, then sets `n` (or the count `spacing` derives)                |
| `text_spot_count(text, *, fonts, height, spacing, rotation=0, align="left", anchor="baseline", letter_spacing=0, line_spacing=1.2, font_registry=None)`                                                      | `int`        | The `n` that `text_form(spacing=...)` derives, so the caller can name exactly that many members first                                               |
| `text_report(form, *, font_registry=None)`                                                                                                                                                                   | `TextReport` | Lists the `CrowdedStroke`s (char, line, column, stroke, first slot, spots, achieved gap, wanted gap) whose spots sit closer than their junction gap |

Rules:

- **No open slots.** A text form needs `len(members) == n` at construction and after every edit. A mismatch raises `MEMBER_COUNT_MISMATCH` with `add k more spots` or `remove k spots` in the hint. `delete_spots` may still leave a text form short; `placements` (so `place` and `rebuild`) then raises `MEMBER_COUNT_MISMATCH` before any layout, and `apply_match` raises it when a match takes a text form's member away. Both hints point at `edit_text_count`.
- **`spacing=` derives the count.** The count comes from the stroke lengths and is stored as `n`; `TextRecipe.spacing` records the request that last set the count, not the current spacing. A text edit keeps both. Lengths are measured after the form's stored `transform`, so `spacing` is stage meters; `text_spot_count` returns the count before the form exists.
- **Minimums.** Each stroke gets at least 2 spots when open and 3 when closed. A count below the sum raises `TOO_FEW_SPOTS`; after `edit(text=...)` the hint says how many spots to add with `edit_text_count`. A spacing far from letter size is clamped up to the minimums and emits `LegibilityWarning`, measured in stage meters after the stored transform.
- **Junction gap.** A stroke's spots keep `JUNCTION_GAP_SHARE` (0.25) of its spot spacing from other strokes' spots wherever some spread allows it (`showpy.choreo.text_spread`). Where none does, as with Hershey doubled or converging strokes, the stroke takes the spread whose closest spot is farthest away and `text_report(form)` lists it; nothing falls back silently. Two spots never lie within `COINCIDENT_TOL_M` (1e-6 m); if every spread would, `COINCIDENT_SLOTS` is raised.
- **Order.** Member i stands on slot i, in reading order.
- **Transforms.** `transform_form` maps slots and tangents; `along` facings turn with the text.
- **Stage-1 limits.** `gate`, `split_form`, `merge_forms`, `match`, `reorder`, `place`, `rebuild`, `apply_match`, `swap` and `morph_targets` take no `font_registry`, so a text form that uses a user font fails closed there with `MissingFontError` naming the hash. `split_form` and `merge_forms` on a text form return custom forms (the recipe is dropped). Repositories do not store fonts in the asset store yet (stage 2): a show reopened from a repository rebuilds a user-font text form only with a registry that holds the font, else `MissingFontError`, never a substitute; `export_json` and `import_fonts` carry fonts with the show today.

**Editable custom shapes.** A form can be generated by a registered function instead of a built-in shape, and stays editable by changing its parameters. Its `kind` is `generated`; its `geometry` holds `{fn: name, params: dict}`. Built-in generators: `spiral`, `sine_wave`, `rose`.

| Function                                                                                                                     | Inputs                                                               | Output                            | Purpose                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `register_form_fn(registry, name, fn)`                                                                                       | `FormFnRegistry`, `fn(params, n) → Points` (pure, typed, documented) | `FormFnRegistry`                  | New registry with a shape generator, after probing `fn({}, 4)` (agent drafts go through review first) |
| `generated_form(form_id, members, fn, spacing=None, facing=None, registry=DEFAULT_FORM_FNS, *, set_id="", n=None, **params)` | registered name, shape parameters                                    | `Form`                            | A custom shape that keeps its recipe                                                                  |
| `edit_params(form, force=False, registry=DEFAULT_FORM_FNS, **params)`                                                        |                                                                      | `Form`                            | Tweak a custom shape, e.g. `edit_params(f, turns=3)`                                                  |
| `form_fns(registry=DEFAULT_FORM_FNS)`                                                                                        |                                                                      | `list[FormFnInfo{name, summary}]` | Available shape generators                                                                            |

### Text error codes

All are `ShowPyError` subclasses with `code`, `message`, `context` and `hint`.

| Code                    | Class                                  | Raised when                                                                             |
| ----------------------- | -------------------------------------- | --------------------------------------------------------------------------------------- |
| `MISSING_GLYPH`         | `GlyphError` (`UnknownRefError`)       | No font in the chain has a character                                                    |
| `MISSING_FONT`          | `MissingFontError` (`UnknownRefError`) | A name or URI is not in the registry; the hint names `register_font` and `import_fonts` |
| `BAD_FONT`              | `FontError`                            | Font bytes are not a valid `showpy.font.v1` file, or an embedded entry is malformed     |
| `FONT_NAME_TAKEN`       | `FontError`                            | `register_font` finds the name pointing at other bytes; `import_fonts` keeps that name  |
| `FONT_HASH_MISMATCH`    | `FontError`                            | A URI is not the SHA-256 of its bytes, or a registry font is not what its bytes load to |
| `TOO_FEW_SPOTS`         | `SpacingError`                         | The count is below the stroke minimums (2 open, 3 closed), also in `space_proportional` |
| `TOO_MANY_SPOTS`        | `SpacingError`                         | The count exceeds `MAX_TEXT_SPOTS`                                                      |
| `MEMBER_COUNT_MISMATCH` | `SpacingError`                         | Members differ from `n`; the hint says how many to add or remove                        |
| `N_OR_SPACING`          | `SchemaError`                          | Neither or both of `n` and `spacing` were given                                         |
| `TEXT_COUNT_EDIT`       | `SchemaError`                          | `edit(n=)` or `edit(spacing=)` on a text form; the hint names `edit_text_count`         |
| `ALREADY_MEMBER`        | `SchemaError`                          | `edit_text_count(add=)` names a current member                                          |
| `NOT_A_MEMBER`          | `SchemaError`                          | `edit_text_count(remove=)` names a spot that is not a member                            |
| `NOT_A_TEXT_FORM`       | `SchemaError`                          | `edit_text_count` on another kind                                                       |
| `COINCIDENT_SLOTS`      | `GeometryError`                        | Every spread of a stroke puts a spot within `COINCIDENT_TOL_M` of another slot          |

`LegibilityWarning` (a `UserWarning`, in `showpy.core.errors`) is not an error: it warns that a count or spacing is below what the text reads well at. The test suite turns warnings into errors, so tests near minimum counts filter it.

### showpy.choreo.match

**`MatchOptions`**

| Field            | Type                                                              | Default                  | Meaning                                               |
| ---------------- | ----------------------------------------------------------------- | ------------------------ | ----------------------------------------------------- |
| `keep_groups`    | `bool`                                                            | True                     | Spots only take slots for their group                 |
| `group_slots`    | `dict[str, tuple[int, ...]] \| None`                              | None                     | Explicit slot ranges per group                        |
| `pinned`         | `dict[SpotId, int]`                                               | {}                       | Forced assignments; designer pins added automatically |
| `forbid`         | `tuple[tuple[SpotId, int], ...]`                                  | ()                       | Disallowed pairs                                      |
| `no_backward`    | `bool \| float`                                                   | False                    | Forbid, or penalize by meters, travel against facing  |
| `max_speed`      | `float \| None`                                                   | None                     | Forbid moves faster than this (m/count)               |
| `objective`      | `"sum" \| "sum_squares" \| "bottleneck" \| "bottleneck_then_sum"` | `bottleneck_then_sum`    | Minimize largest move, then total                     |
| `power`          | `float`                                                           | 1.0                      | Cost exponent for `sum`                               |
| `preserve_order` | `bool`                                                            | False                    | Keep order along ordered forms                        |
| `weights`        | `MatchWeights`                                                    | all 0.0                  | Penalties: crossing, backward, turn, group_spread     |
| `cost_fn`        | `(SpotId, slot, start, slot point) → float` \| None               | None                     | Custom added cost; not stored in JSON                 |
| `counts`         | `float \| None`                                                   | None (transition counts) | For speed limits                                      |

**`MatchWeights`** — `backward`, `crossing`, `group_spread`, `turn`: each a finite `float` of at least 0 (default 0.0); an unknown name raises `SchemaError`.

**`MatchResult`** — `entities: tuple[SpotId, ...]`, `assignment: dict[SpotId, int]`, `targets: tuple[Point, ...]`, `total_distance: Meters`, `max_distance: Meters`, `max_speed: float | None`, `crossings: int`, `unassigned_slots: tuple[int, ...]`, `explain: tuple[str, ...]`; `target_points() → Points` in entity order.

| Function                                                                                   | Inputs                                                                 | Output               | Purpose                                                                                     |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------- |
| `match(from_, to_slots, entities=None, options=None, groups=None, slot_groups=None)`       | `Set` or `Points`; `Points` or forms; spot → group key; group per slot | `MatchResult`        | Who goes where (SciPy assignment; threshold search for bottleneck); exact and deterministic |
| `apply_match(show, tid, result, force=False)`                                              |                                                                        | `Show`               | Write targets into the next set and reset those path rows (`showpy.choreo.match_ops`)       |
| `cost_matrix(from_, to_slots, options=None, entities=None, groups=None, slot_groups=None)` |                                                                        | ndarray              | Inspect costs: meters plus penalties, infinity where forbidden                              |
| `bottleneck_value(cost)`                                                                   |                                                                        | `float`              | Smallest possible largest cost                                                              |
| `match_by_order(ordered_sel, form, from_=None)`                                            | `Form` or `Points`; starting `Set` for distances                       | `MatchResult`        | i-th spot to i-th slot                                                                      |
| `swap(show, set_id, a, b, force=False)`                                                    |                                                                        | `Show`               | Manual swap with facing and form slot, keeping paths valid                                  |
| `suggest_swaps(show, tid, k=5, same_group=True)`                                           |                                                                        | `list[(a, b, gain)]` | Swaps that reduce the largest move or crossings                                             |

`showpy.choreo.match_cost` builds the problem and cost matrices; `showpy.choreo.match_solve` holds the solvers (`solve_sum`, `min_max_threshold`, `solve_ordered`, `improve_by_swaps`) and `count_crossings(starts, ends)`.

## showpy.choreo (part 2): paths, engine, avoidance

Path rows are instructions for how each spot travels between two sets; the path engine is the single piece of code that turns them into exact positions, facings and states at any moment, used by every consumer, preview and exports.

### showpy.choreo.paths — models

The path models live in `showpy.choreo.models`.

**Enums**: `PathStyle` = `STRAIGHT`, `HOLD`, `CURVE`, `POLYLINE`, `FOLLOW`, `ROTATE`, `CUSTOM` (registered motion function). `FacingMode` = `FIXED`, `TO_TARGET`, `TRAVEL`, `AGAINST_TRAVEL`, `TOWARD_POINT`, `TOWARD_ENTITY`, `AUDIENCE`, `INTERPOLATE`. `TurnDirection` = `SHORTEST`, `CLOCKWISE`, `COUNTERCLOCKWISE`.

| Data class       | Fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Represents                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `Waypoint`       | `x: Meters, y: Meters, at: float \| None`                                                                                                                                                                                                                                                                                                                                                                                                                                 | A point a path passes through; `at` = local count, `None` = constant speed        |
| `PathRow`        | `entity: SpotId, style: PathStyle = STRAIGHT, start: float = 0, end: float \| None = None (counts), waypoints: tuple[Waypoint, ...] = (), route: RouteId \| None, pivot: Point \| None, sweep_deg: Degrees \| None, facing_mode: FacingMode = TO_TARGET, facing_value: Degrees \| Point \| SpotId \| None, turn_counts: float = 0, turn_direction: TurnDirection = SHORTEST, pace: float = 1.0, locked: bool = False, note: str \| None`; `window(counts) → (start, end)` | One spot's instruction for one transition                                         |
| `Route`          | `id: RouteId, curve: dict, members: tuple[SpotId, ...] (leader first), offsets: tuple[Meters, ...], travel: Meters, start: float = 0, end: float \| None`                                                                                                                                                                                                                                                                                                                 | Shared path for follow-the-leader                                                 |
| `TransitionSpec` | `id: TransitionId, counts: float, rows: dict[SpotId, PathRow] (sparse; missing = straight full counts), routes: dict[RouteId, Route], notes: str, source_hash: str`; `row(entity)` defaults to straight                                                                                                                                                                                                                                                                   | All instructions for one transition; `counts` equals the destination set's counts |

### showpy.choreo.paths — editing functions (pure; return `TransitionSpec` unless noted)

`sel` is a list of spot IDs or a `Selection`; resolve expressions first with `showpy.cast.select.select`. Every function except `default_paths`, `lock`, `unlock`, `paths_table` and `make_route` takes `force=False` and raises `LockedError` on a locked row unless forced; the route builders in `showpy.choreo.routes` take it keyword-only. The builders that take a spec (`two_part`, `set_style`, `rotate_path`) guard the spec they receive; a stored change is re-checked against the stored show at commit. Show-aware builders live in `showpy.choreo.routes`, persistence in `showpy.show.paths_store`.

| Function                                                                                   | Inputs                                                 | Purpose                                                                                                                      |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `default_paths(show, tid)`                                                                 |                                                        | Straight over full counts for everyone (no stored rows)                                                                      |
| `set_style(spec, sel, style, force=False, **params)`                                       | style-specific params                                  | Change path style; raises `PathError` if params missing (ROTATE: `pivot`, `sweep_deg`; FOLLOW: `route`; CUSTOM: `motion_fn`) |
| `delay(spec, sel, counts, keep_end=True)`                                                  |                                                        | Start later (faster move, or shifted without `keep_end`)                                                                     |
| `hold(spec, sel, until=None, from_=None)`                                                  | local counts                                           | Hold for part or all of the transition                                                                                       |
| `arrive_early(spec, sel, counts)`                                                          |                                                        | Finish early                                                                                                                 |
| `window(spec, sel, start, end)`                                                            |                                                        | Set both ends                                                                                                                |
| `add_waypoint(spec, entity, point, at=None, index=None)`                                   |                                                        | Bend the path                                                                                                                |
| `remove_waypoint(spec, entity, index)` / `clear_waypoints(spec, sel)`                      |                                                        | Undo bends; POLYLINE and CURVE rows go back to STRAIGHT                                                                      |
| `curve_path(spec, entity, *control)`                                                       |                                                        | Smooth path through control points                                                                                           |
| `two_part(show, spec, sel, corner, split=None, *, force=False)`                            | `"x_first"`, `"y_first"` or `Point`                    | L-shaped moves, reaching the corner at local count `split` when given                                                        |
| `make_route(show, tid, leader, waypoints, members=None, spacing="keep", route_id="route")` |                                                        | → `Route` for follow-the-leader, running back through the file, then the leader's waypoints                                  |
| `follow(spec, route, start=0, end=None, *, force=False)`                                   |                                                        | Store the route and assign FOLLOW rows to its members                                                                        |
| `follow_and_set_targets(show, tid, route, start=0, end=None, *, force=False)`              |                                                        | → `Show`; writes followers' end positions into the next set                                                                  |
| `counter_march(show, tid, sel, turn_at, counts_per_turn=2, *, force=False)`                |                                                        | → `Show`; a file (leader first) turns back on itself, passing 1 m to the leader's left                                       |
| `rotate_path(show, spec, sel, pivot, deg, *, force=False)`                                 | `Point` or `"center"` (centroid of the starting spots) | Rotation paths about a pivot                                                                                                 |
| `stagger(spec, ordered_sel, step=1.0, direction="start", keep_end=True)`                   | `start` or `end`                                       | Staggered starts                                                                                                             |
| `ripple(spec, ordered_sel, total, curve="linear")`                                         | `linear`, `ease_in`, `ease_out`, `ease_in_out`         | Spread starts over a total                                                                                                   |
| `set_facing(spec, sel, mode, value=None, turn_counts=0, direction=SHORTEST)`               |                                                        | Facing while moving                                                                                                          |
| `set_pace(spec, sel, pace)`                                                                |                                                        | Double or half time; positions do not change                                                                                 |
| `lock(spec, sel)` / `unlock(spec, sel)`                                                    |                                                        | Designer locks                                                                                                               |
| `save_paths(ws, spec, at)`                                                                 |                                                        | → revision `str`; writes `/transitions/<a>-<b>/paths.json`; checks run through the tool hook                                 |
| `load_paths(ws, tid)`                                                                      |                                                        | → `TransitionSpec`; a transition without a document moves straight                                                           |
| `paths_table(spec)`                                                                        |                                                        | → DataFrame of non-default rows                                                                                              |

### showpy.choreo.paths — custom motion, timing and height

Two additions free movement from the built-in list: custom path styles and non-even timing. `PathRow` also has these fields:

| `PathRow` field | Type          | Default | Meaning                                       |
| --------------- | ------------- | ------- | --------------------------------------------- |
| `motion_fn`     | `str \| None` | None    | Registered motion function for `CUSTOM` style |
| `motion_params` | `dict`        | {}      | Its parameters, e.g. `{"turns": 2}`           |
| `easing`        | `Easing`      | linear  | How speed changes across the window           |

| Data class            | Fields                                                                                                                                                                                                                                      | Represents                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `Easing`              | `kind: "linear" \| "ease_in" \| "ease_out" \| "ease_in_out" \| "hold_then_burst" \| "burst_then_settle" \| "steps" \| "custom", strength: float = 2.0, points: tuple[tuple[float, float], ...] (custom curve: time share → distance share)` | How progress through a move maps to time                        |
| `MotionFn` (protocol) | `__call__(u: ndarray, start: Point, end: Point, params: Mapping) → Points`                                                                                                                                                                  | Pure custom motion; must return `start` at u=0 and `end` at u=1 |
| `MotionRegistry`      | `get(name)`, `names()`, `with_motion(name, fn)`; `DEFAULT_MOTIONS` holds `weave`, `zigzag`, `figure_eight`, `spiral` (exported from `showpy` as `spiral_motion`)                                                                            | Named motions; immutable, passed to `PathEngine(motions=...)`   |
| `MotionInfo`          | `name: str, summary: str`                                                                                                                                                                                                                   | One registered motion for listings                              |

Motion functions live in `showpy.choreo.motion`.

| Function                                                                          | Inputs                       | Output             | Purpose                                                                                                       |
| --------------------------------------------------------------------------------- | ---------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------- |
| `register_motion(registry, name, fn)`                                             | `MotionRegistry`, `MotionFn` | `MotionRegistry`   | New registry with a movement style, after checking it starts and ends on target (agent drafts reviewed first) |
| `motions(registry)`                                                               |                              | `list[MotionInfo]` | Available custom styles                                                                                       |
| `custom_path(spec, sel, motion, registry=DEFAULT_MOTIONS, force=False, **params)` | registered name              | `TransitionSpec`   | Use a custom style; unknown names raise `UnknownRefError`                                                     |
| `set_easing(spec, sel, easing, force=False)`                                      | `Easing` or kind name        | `TransitionSpec`   | Change pacing within the move; a kind name uses the default strength                                          |
| `ease(easing, u)`                                                                 | `Easing`, time shares        | distance shares    | Apply an easing                                                                                               |
| `easing_breaks(easing)`                                                           |                              | `list[float]`      | Time shares where an easing kinks or pauses; segment edges include them                                       |
| `turn(start, end, u, direction=SHORTEST)`                                         | `Degrees`, `Degrees`, 0..1   | `Degrees`          | Interpolate facing the chosen way                                                                             |

Engine rules for these: progress u passes through the easing curve before positions are computed; `CUSTOM` motions are sampled by the engine at 257 points, then simplified into a polyline within `curve_tol`, so the checker works unchanged. Height (jumps, lifts, flight) belongs to the 3D layer.

### showpy.choreo.engine

Engine data classes live in `showpy.choreo.engine_models`; compiling in `engine_compile` and facing in `engine_facing`.

| Data class           | Fields                                                                                                                                                                                                                                                                                        | Represents                                             |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `MotionPiece`        | `t0: float, t1: float, kind: "hold" \| "line" \| "arc" \| "curve" \| "route" \| "custom", params: dict, length: Meters`                                                                                                                                                                       | One piece of motion in local counts                    |
| `MotionPlan`         | `entity: SpotId, pieces: tuple[MotionPiece, ...], start: Placement, end: Placement, style: PathStyle, curve: AnyCurve \| None, window: (start, end), knots_t, knots_s (time map: local count → arc length), easing, facing_mode, facing_value, turn_counts, turn_direction`; property `moves` | Full motion of one spot in a transition                |
| `CompiledTransition` | `id: TransitionId, counts: float, t_global0: Count, plans: dict[SpotId, MotionPlan], warnings: tuple[str, ...]`                                                                                                                                                                               | Cached compile                                         |
| `MotionSegment`      | `entity, tid, t0, t1 (local counts), p0: Point, p1: Point`                                                                                                                                                                                                                                    | Straight, time-linear piece for exact collision checks |
| `EntityState`        | `entity, position: Point, facing: Degrees, velocity: Point (m/count), moving: bool, on_field: bool`                                                                                                                                                                                           | Everything about a spot at one moment                  |
| `Frame`              | `t: Count, tid: TransitionId \| None, tau: float, states: tuple[EntityState, ...]`                                                                                                                                                                                                            | Everyone at one moment                                 |

**`PathEngine`**

| Method                                                                  | Inputs                                  | Output                                   | Purpose                                                                  |
| ----------------------------------------------------------------------- | --------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------ |
| `PathEngine(show, curve_tol=0.01, cache=True, motions=DEFAULT_MOTIONS)` |                                         | engine                                   | Create for a show                                                        |
| `compile(tid)`                                                          |                                         | `CompiledTransition`                     | Build motion plans; raises `ContinuityError` if a plan misses its target |
| `compile_entity(entity, tid)`                                           |                                         | `MotionPlan`                             | One spot's plan, uncached                                                |
| `compile_all()`                                                         |                                         | `dict[TransitionId, CompiledTransition]` | Whole show                                                               |
| `locate(t)`                                                             | global count                            | `(TransitionId \| None, tau)`            | Map a global count to a transition; None outside \[0, total\]            |
| `position(entity, t, local=None)`                                       | global count, or local with `local=tid` | `Points`, shape (2,)                     | Where a spot is                                                          |
| `positions(t, sel="all")`                                               |                                         | `Points`                                 | Everyone, vectorized                                                     |
| `facing_at(entity, t, local=None)`                                      |                                         | `Degrees`                                | Facing                                                                   |
| `velocity_at(entity, t, local=None)`                                    |                                         | `Points`, shape (2,)                     | Meters per count, a central difference of the plan's positions           |
| `on_field_at(entity, t, local=None)`                                    |                                         | `bool`                                   | Whether the spot is on the field                                         |
| `state_at(entity, t, local=None)`                                       |                                         | `EntityState`                            | Full state for preview                                                   |
| `sample(t)`                                                             |                                         | `Frame`                                  | One moment                                                               |
| `frames(t0, t1, per_count=8, fps=None)`                                 |                                         | iterator of `Frame`                      | Animation and checks                                                     |
| `segments(tid, tol=0.02)`                                               |                                         | `list[MotionSegment]`                    | Input for the checker                                                    |
| `entity_segments(entity, tid, tol=0.02)`                                |                                         | `list[MotionSegment]`                    | One spot's segments                                                      |
| `speed(entity, tid, window=None)`                                       |                                         | `SpeedStats`                             | Movement demand, summed over the time-linear segments the checker sees   |
| `path_geometry(entity, tid)`                                            |                                         | `Points`                                 | Drawn path for the canvas                                                |
| `invalidate(sets=(), transitions=())`                                   |                                         | `None`                                   | Clear this engine's caches after edits                                   |

Compile rules (ADR 0002): each move compiles to a curve plus a piecewise-linear time map from local count to arc length, with knots at the window ends and every timed waypoint. Position is `curve.point_at(map(ease(u)))` with u = (tau − start)/(end − start), clamped. The curve is a segment, polyline, centripetal Catmull–Rom spline, arc about the pivot, trimmed route, or sampled custom polyline. Outside its window a spot stands at the previous or next placement; `on_field` comes from the previous set before the window, the next set after it, and is the OR of both during it. Facing runs in three phases: the previous facing, then the row's `FacingMode` blended in over `turn_counts`, then the next set's facing; `FacingOverride`s win inside their range. HOLD, ROTATE, FOLLOW and CUSTOM must end within 1e-6 m of the target. `segments` subdivides time adaptively from every knot and window edge, probing every eighth of a segment until each probe is within `tol / 2`, so closest-approach checks are exact to `tol`, including crossings between counts.

### showpy.choreo.avoid (advisory; a consumer checker decides)

`GapInfo` and `Fix` live in `showpy.choreo.engine_models`.

| Data class / function                                        | Inputs                                                                                                          | Output             | Purpose                                                                           |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- | ------------------ | --------------------------------------------------------------------------------- |
| `GapInfo`                                                    | `a, b: SpotId, tau: float, gap: Meters`                                                                         |                    | A tight pair                                                                      |
| `Fix`                                                        | `kind: "delay" \| "waypoint" \| "reassign" \| "stagger", params: dict, new_gap: Meters, max_speed_after: float` |                    | A suggested change                                                                |
| `closest_approach(times_a, points_a, times_b, points_b)`     | two time-linear tracks                                                                                          | `(time, distance)` | Exact closest approach                                                            |
| `min_gaps(show, tid, sel="all", top=10)`                     |                                                                                                                 | `list[GapInfo]`    | Tightest on-field pairs, for a quick preview inside scripts                       |
| `suggest_delays(show, tid, pair, max_delay=4.0)`             |                                                                                                                 | `list[Fix]`        | Timing fixes in 0.5-count steps, best gap first                                   |
| `suggest_waypoint(show, tid, entity, around, clearance=0.8)` | `around` is a `SpotId`                                                                                          | `list[Fix]`        | A timed bend around another spot                                                  |
| `suggest_reassignment(show, tid, sel, crossing_weight=10.0)` |                                                                                                                 | `MatchResult`      | Re-match with crossing penalty                                                    |
| `separate_in_time(spec, ordered_sel, gap_counts=1.0)`        |                                                                                                                 | `TransitionSpec`   | Automatic stagger                                                                 |
| `apply_fix(spec, fix)`                                       |                                                                                                                 | `TransitionSpec`   | Apply a delay, waypoint or stagger fix; a reassignment goes through `apply_match` |

## showpy.choreo (part 3): facing

Facing tools for sets and time ranges. Actions, moves and objects over time belong to the 3D layer (ShowPy Library: 3D Layer Reference).

**`FacingOverride`** (`showpy.choreo.models`) — `entities: SelectorField, mode: FacingMode, value: Degrees | Point | SpotId | None, start: Count, end: Count, turn_counts: float = 0`. Facing for a global count range regardless of paths, stored in `Show.facing_overrides`.

### showpy.choreo.facing

The `face_*` tools edit placements in one `Set` and raise `LockedError` on pinned placements unless `force=True`; facing over a time range goes through `add_facing_override`.

| Function                                                        | Inputs                     | Output    | Purpose                                                                    |
| --------------------------------------------------------------- | -------------------------- | --------- | -------------------------------------------------------------------------- |
| `face_audience(target, sel, force=False)`                       | `Set`                      | `Set`     | Face 0°                                                                    |
| `face_point(target, sel, point, force=False)`                   | `Set`                      | `Set`     | Face a spot (a soloist, a conductor); a spot on the point keeps its facing |
| `face_entity(target, sel, entity, force=False)`                 | `Set`                      | `Set`     | Face another spot's placement in the same set                              |
| `face_direction(target, sel, deg, force=False)`                 | `Set`                      | `Set`     | Fixed facing                                                               |
| `face_along(show, set_id, form_id, reverse=False, force=False)` |                            | `Set`     | Follow the form's line                                                     |
| `face_normal(show, set_id, form_id, outward=True, force=False)` |                            | `Set`     | Perpendicular to the form; outward is the right of travel                  |
| `add_facing_override(show, override)`                           | `FacingOverride`           | `Show`    | Facing over a time range within the show's counts                          |
| `turn(start, end, u, direction=SHORTEST)`                       | `Degrees`, `Degrees`, 0..1 | `Degrees` | Interpolate facing (from `showpy.choreo.motion`)                           |

## showpy.choreo (part 4): measures over time

### showpy.choreo.motion_stats

Measures that need the PathEngine. They live here, not in `showpy.geo`, so geometry never imports the engine. Speeds use exactly the motion the checker sees.

| Function                                      | Inputs         | Output                                                            | Purpose                                                         |
| --------------------------------------------- | -------------- | ----------------------------------------------------------------- | --------------------------------------------------------------- |
| `position_at(show, entity, t)`                | global `Count` | `Points`, shape (2,)                                              | Position at any moment                                          |
| `speed(show, entity, tid, window=None)`       | local window   | `SpeedStats{distance, counts, m_per_count, m_per_s, max_instant}` | Movement demand                                                 |
| `speeds(show, tid)`                           |                | DataFrame, hardest first                                          | Ranking by peak, then mean speed                                |
| `direction_of_travel(show, entity, tid, tau)` |                | `Degrees \| None`                                                 | Travel direction as a facing; None when still                   |
| `motion_contrast(show, tid)`                  |                | `MotionStats{moving_share, speed_spread, direction_variety}`      | How varied the movement is                                      |
| `energy_curve(show, per_count=1)`             |                | `pandas.Series` indexed by global count                           | Mean on-field speed through the show, to compare with the music |
| `phrase_profile(show, t0, t1)`                |                | DataFrame by phrase                                               | Change, energy and density per musical phrase                   |

## showpy.show

The show as a living document: the aggregate model, set management, sections, storage exposed to the agent as files, live collaboration, history and structural validation. Exports live in `showpy.export`.

### showpy.show.models

**`Show`**

| Field              | Type                         | Meaning                                                                     |
| ------------------ | ---------------------------- | --------------------------------------------------------------------------- |
| `id`, `title`      | `str`                        |                                                                             |
| `pack`             | `str`                        | Domain pack key; empty until packs exist                                    |
| `sheet`            | `Sheet`                      | The 2D design surface (template plus customizations)                        |
| `coordinates`      | `CoordinateSystem`           | How positions are labeled and worded; display only                          |
| `notes`            | `tuple[Note, ...]`           | Design notes (showpy.notes)                                                 |
| `cast`             | `Cast`                       | Spots and groups                                                            |
| `timeline`         | `Timeline`                   | Show clock                                                                  |
| `sets`             | `tuple[Set, ...]`            | In order                                                                    |
| `forms`            | `dict[FormId, Form]`         | Shapes                                                                      |
| `transitions`      | `tuple[TransitionSpec, ...]` | Stored path instructions in show order; a missing transition moves straight |
| `facing_overrides` | `tuple[FacingOverride, ...]` | Timed facings                                                               |
| `zones`            | `tuple[Zone, ...]`           | Show-specific zones                                                         |
| `dependencies`     | `dict[str, str]`             | Every sheet ref pinned to a version                                         |
| `pins`             | `tuple[Pin, ...]`            | Designer locks, in the order they were made                                 |
| `revision`         | `str`                        | Storage revision the show was loaded at                                     |

Methods: `set_ids()`, `set_index(id)`, `set(id) → Set`, `transition_ids()`, `transition(tid) → TransitionSpec` (straight moves when none are stored). The index is computed by `showpy.show.manage.index(show)`; pick spots with `showpy.cast.select.select(show, sel)`.

**`ShowIndexEntry`** — `set: SetId, counts: float, start_count: Count, measure: MeasureLabel | None, seconds: Seconds, cue: str | None`. **`ShowIndex`** — `entries` plus `set_at(count) → SetId | None`, `transition_at(count) → (TransitionId, tau) | None`, `next(set_id)`, `prev(set_id)`, `total_counts`.

**`Pin`** — `target: "entity" | "form" | "set" | "transition", id: str, by: str = "designer", reason: str`. Records one designer lock; the flags it sets live on placements, forms and path rows.

The other show models (`Revision`, `SetDiff`, `PathDiff`, `Comment`, `CanvasEvent`, `Proposal`, `ProposalPreview` and the change ops) are listed with the functions that use them below.

### showpy.show.manage

| Function                                                                  | Inputs                           | Output                                          | Purpose                                                                                          |
| ------------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `new_show(show_id, title, sheet, timeline, cast=None, pack="")`           | explicit ID; versioned sheet ref | `Show`                                          | Create; pins the sheet version; first set "1" holds every spot off the field at the front-center |
| `add_set(show, set_id, counts, after=None, placements=None, force=False)` |                                  | `Show`                                          | Insert; missing placements copy the previous set                                                 |
| `insert_subset(show, after, counts, force=False)`                         |                                  | `(Show, SetId)`                                 | Add "12A", taking its counts from the following set                                              |
| `remove_set(show, set_id, merge_counts=True, force=False)`                |                                  | `Show`                                          | Delete with its forms, pins and stored paths                                                     |
| `set_counts(show, set_id, counts, rescale=False, force=False)`            |                                  | `Show`                                          | Retime; the incoming transition's counts follow                                                  |
| `renumber(show, scheme="sequential")`                                     |                                  | `(Show, dict[SetId, SetId])`                    | New set numbers plus mapping                                                                     |
| `transitions(show)`                                                       |                                  | `list[TransitionId]`                            | Consecutive pairs                                                                                |
| `index(show)`                                                             |                                  | `ShowIndex`                                     | Counts, measures, seconds; the first set starts at 0                                             |
| `set_at_count(show, c)` / `transition_at_count(show, c)`                  |                                  | `SetId \| None` / `(TransitionId, tau) \| None` | Time lookups                                                                                     |
| `upgrade_dependency(show, ref, version, sheet=None)`                      |                                  | `Show`                                          | Adopt a newer sheet version, swapping in the sheet when given                                    |

### showpy.show.sections

| Function                  | Inputs  | Output         | Purpose                                                                          |
| ------------------------- | ------- | -------------- | -------------------------------------------------------------------------------- |
| `split_section(show, at)` | `SetId` | `(Show, Show)` | Movements or scenes; both shows hold `at`, and the second starts its clock there |
| `join_sections(a, b)`     |         | `Show`         | Append `b` after `a`; the inverse of `split_section` for sets and paths          |

Set scripts, their builds and the script sandbox live in a separate consumer repository; `showpy` only stores a set's `source_hash`.

### showpy.show.storage

| Item                                                                                                                                                                                             | Inputs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Output                                                                                                   | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Repository` (protocol)                                                                                                                                                                          | `shows, head, revisions, get_revision, paths, read, documents(show_id, revision=None), transaction(show_id, changes, *, expected_revision, at), get_show/put_show, list_sets, get_set/put_set, get_script/put_script, get_paths/put_paths, get_forms/put_forms, get_timeline, get_limits, get_waivers`; `transaction` applies a batch of path → bytes (or None to delete); writes take `expected_revision` and `at`; `documents` returns a read-only mapping that reads each blob on lookup, so a host's own implementation must add it; `put_forms` refuses a form ID another set holds, as a `FormOp` does |                                                                                                          | Database abstraction; `DocumentRepository` implements it over a `RepositoryBackend`, with `InMemoryRepository` and `FileRepository`                                                                                                                                                                                                                                                                                                          |
| `AssetStore` (protocol)                                                                                                                                                                          | `put(bytes) → uri`, `get(uri) → bytes`; `InMemoryAssetStore` uses `asset:sha256:<hex>` URIs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |                                                                                                          | Binary storage for captured videos and audio                                                                                                                                                                                                                                                                                                                                                                                                 |
| `Revision`                                                                                                                                                                                       | `id, parent, at, changed`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |                                                                                                          | One saved state: what changed and when; who and why belong to the host (ADR 0003)                                                                                                                                                                                                                                                                                                                                                            |
| `open_workspace(repo, show_id)`                                                                                                                                                                  |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `Workspace`                                                                                              | File view of the show at its head revision                                                                                                                                                                                                                                                                                                                                                                                                   |
| `Workspace.read(path)` / `write(path, data, expected_revision=None, *, at)` / `commit(changes, *, at, expected_revision=None)` / `ls(path="/")` / `revision` / `refresh()` / `tree_at(revision)` |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `str \| bytes` / `Revision` / `Revision` / `list[str]` / `str` / `str` / read-only `Mapping[str, bytes]` | File access: `/show/show.json`, `/show/sheet.json`, `/show/notes.json`, `/show/index.csv` (derived, read-only), `/time/timeline.json`, `/sets/<id>/set.py`, `/sets/<id>/placements.csv` (derived, read-only), `/sets/<id>/forms.json`, `/transitions/<a>-<b>/paths.json`, `/canvas/selection.json`, `/canvas/comments.json`, `/config/limits.yaml`, `/config/waivers.yaml`, `/publish/claims.json`, `/renders/`, `/exports/`, `/lib/drafts/` |
| `load_show(ws)` / `save_show(ws, show, at)`                                                                                                                                                      |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `Show` / `Revision`                                                                                      | Whole-show access against `show.revision`; `ConflictError` lists each landed revision's short ID, time and paths                                                                                                                                                                                                                                                                                                                             |

`showpy.show.documents` splits a show into documents and back (`encode_show`, `decode_show`); `showpy.show.files` maps workspace paths onto them.

Stored paths address sets by key (`/sets/<key>/…`, `/transitions/<key a>-<key b>/…`, and the set order in `/show/show.json` lists keys); `Repository.read` and `paths` return stored paths. `Workspace` paths address sets by display ID and map onto keys through `showpy.show.set_keys`, so `renumber` followed by `save_show` moves no file. A workspace folder that names no set but matches another set's key, ignoring case, raises `StorageError` (code `AMBIGUOUS_PATH`). `save_show` and `revert` commit stored paths as they are. `add_set`, `insert_subset`, `import_positions` and `join_sections` give a new set the key `<id>`, or the first free `<id>.<n>` (n from 2) when a live set holds it; a key that differs from the ID must pass `check_id`, while stored IDs that predate ADR 0006 keep loading. Stored v0.2 set IDs that differ only by case (`1a`, `1A`) load, edit and save, but `renumber` refuses such a show, and a new set folder that matches another ignoring case is refused at commit. `Repository.put_set` keeps the replaced set's key.

### showpy.show.collab

| Item                                                                                                 | Inputs / fields                                                                                                          | Output                        | Purpose                                                                                                                                |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `CanvasEvent`                                                                                        | `id, at, kind: move \| edit_form \| pin \| unpin \| edit_paths \| comment \| set_counts \| highlight, payload, revision` |                               | One edit or pointer from either side                                                                                                   |
| `Comment`                                                                                            | `id, anchor: Anchor, text, resolved, author`                                                                             |                               | A note on the canvas                                                                                                                   |
| Change ops                                                                                           | `PlacementOp{set_id, placement}`, `FormOp{form}`, `PathsOp{spec}`, `CountsOp{set_id, counts, rescale}`, tagged by `op`   |                               | One model edit in a proposal or event                                                                                                  |
| `Proposal`                                                                                           | `id, base_revision, change: tuple[ChangeOp, ...], note, state: open \| accepted \| rejected, reason`                     |                               | An agent suggestion                                                                                                                    |
| `ProposalPreview`                                                                                    | `proposal_id, placements, paths, counts, forms, blocked`                                                                 |                               | Ghost placements and paths                                                                                                             |
| `apply_event(show, event, *, force=False)`                                                           |                                                                                                                          | `Show`                        | Replay an edit as a model operation; pinned items, an unpin event and a pin event that replaces a pin raise LockedError unless `force` |
| `apply_change(show, change, force=False)`                                                            | change ops                                                                                                               | `Show`                        | Apply ops in order, refusing a result that touches pinned items (`showpy.show.changes`)                                                |
| `events_since(ws, revision)`                                                                         |                                                                                                                          | `list[CanvasEvent]`           | What changed, derived from revisions; replaying unpin events needs `apply_event(..., force=True)`                                      |
| `pin(show, target, pin_id, reason="", by="designer")` / `unpin(show, target, pin_id)` / `pins(show)` |                                                                                                                          | `Show` / `Show` / `list[Pin]` | Locks (`pin` and `unpin` in `showpy.show.pinning`)                                                                                     |
| `locked_changes(before, after)` / `require_unlocked(before, after, force=False)`                     |                                                                                                                          | `list[str]` / `None`          | What an edit would change under a pin or lock (`showpy.show.locks`)                                                                    |
| `propose(show, change, note, proposal_id)`                                                           |                                                                                                                          | `Proposal`                    | Suggest instead of overwrite; checks the ops apply                                                                                     |
| `preview_proposal(show, proposal)`                                                                   |                                                                                                                          | `ProposalPreview`             | Show suggestion on the canvas                                                                                                          |
| `accept(ws, proposal, at)` / `reject(proposal, reason)`                                              |                                                                                                                          | `Revision` / `Proposal`       | Designer decision                                                                                                                      |
| `highlight(target, ttl_s=30, *, at, event_id)`                                                       | `Anchor` or `ZoneId`                                                                                                     | `CanvasEvent`                 | Point the designer at something                                                                                                        |
| `selection(ws)`                                                                                      |                                                                                                                          | `Selection`                   | What the designer has selected                                                                                                         |

### showpy.show.history

**Removed in 0.3.0.** ShowPy records what changed and when, not who or why, and enforces no permissions. Gone: `actor=` and `message=` on every write, `Principal`, `principal` and `Workspace.principal`, `RevisionKind`, `undo`, `redo`, `snapshot`, `CanvasEvent.actor`, and the `actor`, `message`, `kind` and `target` fields of `Revision`; reading an older repository drops those stored keys. A host builds undo on `revert`.

| Function                         | Inputs               | Output                                                          | Purpose                                                                         |
| -------------------------------- | -------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `diff_sets(a, b, tol=1e-6)`      | two `Set`            | `SetDiff{moved, facing_changed, added, removed, forms_changed}` | Compare sets                                                                    |
| `diff_paths(a, b)`               | two `TransitionSpec` | `PathDiff{changed_rows, routes_changed}`                        | Compare paths; a missing row equals a default row                               |
| `diff_show(a, b)`                |                      | `dict`                                                          | Whole-show diff; keys without differences are left out                          |
| `revert(ws, revision_id, at)`    |                      | `Revision`                                                      | Saves an earlier revision's documents as a new revision; hosts build undo on it |
| `rename_entities(show, mapping)` | old → new IDs        | `Show`                                                          | Rewrite everywhere after relabeling                                             |
| `changelog(ws, since="")`        |                      | `str`                                                           | Readable history, one `<short id> <at> (<paths>)` line per revision             |

### showpy.show.validate

| Function                                  | Output             | Checks                                                                                              |
| ----------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------- |
| `validate_set(show, candidate)`           | `ValidationReport` | Every spot once, known IDs, facing range, form membership, pinned rows unchanged                    |
| `validate_paths(show, spec)`              | `ValidationReport` | Known spots, counts equal to the destination set's, windows within counts, style params, continuity |
| `validate_dependencies(show, store=None)` | `ValidationReport` | The sheet ref resolves and its version is pinned                                                    |
| `validate_show(show, store=None)`         | `ValidationReport` | Everything plus set sequence and timeline; dependencies only when a store is given                  |

`ValidationReport` and `ValidationIssue` are defined in `showpy.core.validation`.

## showpy.export

How a show leaves the app: printed sheets, data files, a live published link, and video. Data classes live in `showpy.export.models`; sheets and charts in `showpy.export.print_sheets`; PDF output in `showpy.export.pdf`; JSON, CSV and text in `showpy.export.data`; position import in `showpy.export.positions`; publishing in `showpy.export.publish`; video in `showpy.export.video`.

### Print and data

| Data class        | Fields                                                                                                                                                                                                                                                   | Represents                                                           |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `SheetRow`        | `set: SetId, counts: float, cumulative: Count, measure: MeasureLabel \| None, position_text: str, xy: Point, facing: Degrees, behavior: str (e.g. "delay 2, move 6"), crossings: tuple[str, ...] (guide lines crossed on the way in), note: str \| None` | One set on a spot's coordinate sheet                                 |
| `CoordinateSheet` | `entity: SpotId, performer_name: str \| None, rows: tuple[SheetRow, ...]`                                                                                                                                                                                | One spot's part through the whole show                               |
| `ChartRow`        | `entity: SpotId, group: str, performer_name: str \| None, position_text: str, xy: Point, facing: Degrees, form: str \| None, on_field: bool, note: str \| None`                                                                                          | One spot on a set chart                                              |
| `Chart`           | `set_id: SetId, rows: tuple[ChartRow, ...]`                                                                                                                                                                                                              | Every spot of one set, for the designer                              |
| `TextTable`       | `title: str, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]`                                                                                                                                                                                 | A titled table of display strings shared by CSV, text and PDF output |

| Function                                                                                                            | Inputs                                                                                                   | Output                                                       | Purpose                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `coordinate_sheet(show, entity, precision=None, publication=None)`                                                  | `Publication` fills in the performer's name                                                              | `CoordinateSheet`                                            | Individual sheet, one row per set, worded by the show's `CoordinateSystem`; facing, behavior and crossings come from the PathEngine                                                                                         |
| `coordinate_sheets(show, sel="all", publication=None, precision=None)`                                              |                                                                                                          | `list[CoordinateSheet]`                                      | Batch, sharing one engine                                                                                                                                                                                                   |
| `chart(show, set_id, publication=None)`                                                                             |                                                                                                          | `Chart`                                                      | Designer sheet for one set: position, facing and form of every spot                                                                                                                                                         |
| `cue_sheet(show)`                                                                                                   |                                                                                                          | DataFrame                                                    | Set labels and cues, timeline markers and cue-mode cues in count order                                                                                                                                                      |
| `to_pdf(obj, layout="sheet")`                                                                                       | `sheet`, `book`, `chart_pages`, `cue_sheet`; a sheet, chart, DataFrame or a sequence of sheets or charts | `bytes`                                                      | Printable output from a dependency-free PDF 1.4 writer: US Letter landscape, Helvetica, deterministic bytes; characters outside WinAnsi print as `?`                                                                        |
| `to_csv(obj, *, neutralize=True)` / `to_text(obj)` / `as_table(obj)`                                                | `CoordinateSheet`, `Chart` or DataFrame                                                                  | `str` / `str` / `TextTable`                                  | Data and plain-text sheets; `neutralize` prefixes `'` to cells that start with `=`, `+`, `-`, `@`, tab or carriage return and are not plain decimals, so spreadsheets show them as text; pass `False` for machine pipelines |
| `export_json(show, include=None, *, font_registry=None)` / `import_json(d)` / `import_fonts(d, font_registry=None)` | top-level fields to keep (the ID always stays)                                                           | `{"schema": "showpy.show.v1", "show": {...}}` / `Show`       | Native interchange; `import_json` refuses a wrong or missing schema tag                                                                                                                                                     |
| `export_frames_csv(show, per_count=1)`                                                                              |                                                                                                          | `str`                                                        | Positions per count for any tool, sampled through the PathEngine; `entity` cells are neutralized as `to_csv` does                                                                                                           |
| `import_positions(base, text, group, columns=None, force=False)`                                                    | CSV of `set, counts, entity, x, y` and optional `facing`; header renames                                 | `Show`                                                       | Bring in existing shows; missing sets are created and new spots join `group`; a set inserted between stored sets drops the paths of the transition it splits, and a locked one raises LockedError unless `force`            |
| `register_exporter(registry, name, fn)` / `register_importer(registry, name, fn)` / `formats(registry)`             | `FormatRegistry`                                                                                         | `FormatRegistry` / `FormatRegistry` / `dict[str, list[str]]` | Format plugins; `DEFAULT_FORMATS` holds `json` and `frames_csv` exporters and a `json` importer                                                                                                                             |

**Fonts in documents.** `export_json(..., font_registry=)` embeds the fonts of every text form under a top-level `fonts` key (base64 bytes keyed by URI). `import_json` ignores that key; `import_fonts(d, font_registry=None)` returns the registry extended with the embedded fonts, verifies each SHA-256 against its URI, and raises `FontError` (`BAD_FONT`, `FONT_HASH_MISMATCH`) on a malformed entry, a mismatch, or more than `MAX_IMPORT_FONTS` (64) fonts. An embedded font whose name already points at other bytes (an older release of a bundled font, say) is registered by URI and the existing name is kept, so old documents import after a font upgrade. Each encoded entry is bounded by `MAX_FONT_BYTES` before decoding.

Print and data exports are generated per spot and show the performer's name once the spot is claimed, when a `Publication` is passed. Print is optional; publishing is the main way a show leaves the app.

### Publish (default export)

A live link and QR code. Performers claim one spot each and get their own view of the show. Every function is pure: claims live inside the immutable `Publication`, edits return a new one, time stamps are explicit `at` arguments, and notifications are returned, never sent.

| Data class      | Fields                                                                                                                                                                                | Represents                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `Publication`   | `id: str, show_id: str, revision: str, link: str, join_token_sha256: str \| None, status: "live" \| "paused" \| "closed" = "live", published_at: datetime, claims: tuple[Claim, ...]` | A published show and the claims made on it; `link` has no token, and the token itself is never stored, only its SHA-256 digest |
| `Performer`     | `id: str, name: str, contact: str \| None, role: "performer" \| "crew" = "performer"`                                                                                                 | A real person who joins through the link                                                                                       |
| `Claim`         | `spot: SpotId, performer: Performer, claimed_at: datetime, locked: bool = True`                                                                                                       | A performer on a spot                                                                                                          |
| `PerformerView` | `performer: Performer, spot: SpotId, sets: tuple[tuple[SetId, str (position text), float (counts)], ...], path_animation_url: str`                                                    | What a performer sees: their dot on every set and their path                                                                   |

| Function                                                                         | Inputs                                                                               | Output                        | Purpose                                                                                                                                                                                                      |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `publish(show, publication_id, base_url, at, previous=None, *, join_token=None)` | `join_token`: 22 to 256 characters of `[A-Za-z0-9_-]`, required on the first publish | `Publication`                 | Publish the current revision; stores only the token's digest; on a republish `None` keeps the digest and a token rotates it; carries over claims whose spot still exists and is still claimable by that role |
| `unpublish(pub)` / `pause(pub)`                                                  |                                                                                      | `Publication`                 | Close the link (claims stay on record) or pause it (nobody can claim)                                                                                                                                        |
| `join_link(pub, token)`                                                          | the token issued at publish                                                          | `str`                         | `<pub.link>?t=<token>`; raises when the token does not match the stored digest, or the stored link is not https or has a query or fragment                                                                   |
| `verify_join_token(pub, token)`                                                  |                                                                                      | `bool`                        | Whether the token is well formed and hashes to the stored digest; ignores status                                                                                                                             |
| `qr_code(pub, token, size=512)`                                                  | a token that must verify                                                             | `bytes` (PNG)                 | Code for performers to scan; encodes `join_link(pub, token)`                                                                                                                                                 |
| `claim_spot(pub, show, performer, spot, at)`                                     |                                                                                      | `(Publication, Claim)`        | One spot per performer and one performer per spot; only spots whose `claimable` matches the performer's role; locked on claim; refuses if taken                                                              |
| `claims(pub)`                                                                    |                                                                                      | `list[Claim]`                 | Everyone and their spot, in natural spot order                                                                                                                                                               |
| `who_is_on(pub, spot)`                                                           | `SpotId`                                                                             | `Performer \| None`           | Who holds a spot                                                                                                                                                                                             |
| `unclaimed(pub, show)`                                                           |                                                                                      | `list[SpotId]`                | Active, claimable spots still empty                                                                                                                                                                          |
| `swap_claims(pub, a, b)`                                                         | two `SpotId`                                                                         | `Publication`                 | Designer only: swap two performers; roles must match                                                                                                                                                         |
| `reassign(pub, spot, performer, at)`                                             |                                                                                      | `Publication`                 | Designer only: move a performer to a spot; whoever held it is unassigned                                                                                                                                     |
| `release(pub, spot)`                                                             |                                                                                      | `Publication`                 | Designer only: free a spot                                                                                                                                                                                   |
| `performer_view(pub, show, performer)`                                           |                                                                                      | `PerformerView`               | A performer's own part                                                                                                                                                                                       |
| `notify_changes(pub, old_show, new_show)`                                        |                                                                                      | `list[tuple[Performer, str]]` | Performers whose spot moved, turned, lost its path or was removed, with a short summary; callers deliver the messages                                                                                        |

### Video

| Function                                                                  | Inputs                                                         | Output                    | Purpose                                                                                                                                                                                      |
| ------------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `export_video(show, count_range=None, fps=30, audio=None, px_per_m=10.0)` | `(start, end)` global counts; `audio` is `Mapping[uri, bytes]` | `bytes` (MP4)             | Animated 2D sheet with music, for rehearsal or sharing (3D video comes with the 3D layer); the timeline's first track is muxed from its sync offset, and without `audio` the video is silent |
| `video_canvas(show, px_per_m=10.0)` / `render_frame(show, frame, canvas)` | `Frame` from the PathEngine                                    | `SheetCanvas` / PIL image | One frame over the sheet, even pixel size for H.264; off-field spots are not drawn                                                                                                           |

## Additions in 0.3.0

Public names that are not listed in the sections above, grouped by module.

| Name                                                                                          | Module                                       | Purpose                                                                                                                                                   |
| --------------------------------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ID_PATTERN`, `ID_MAX_LENGTH` (64), `RESERVED_IDS`                                            | `showpy.core.ids`                            | The one ID shape (check with `fullmatch`, never `match`), its length cap, and names no ID may take                                                        |
| `is_device_name(text)` / `find_case_clashes(ids)`                                             | `showpy.core.ids`                            | Whether text is a Windows device name, alone or before a dot; pairs of IDs equal once case is folded                                                      |
| `canonical_value(obj)` / `jcs(obj)`                                                           | `showpy.core.canonical`                      | Normalize a value into the JSON data model; serialize it as RFC 8785 canonical JSON `bytes`                                                               |
| `split_id(text)`                                                                              | `showpy.core.util`                           | `(prefix, trailing number or None)`, in time linear in the length                                                                                         |
| `asset_uri(data)`                                                                             | `showpy.core.util`                           | The `asset:sha256:<hex>` URI of bytes                                                                                                                     |
| `require_finite(value, *, name, low=None, low_open=False, high=None)`                         | `showpy.core.validation`                     | Check a numeric parameter is finite and inside optional bounds; returns it                                                                                |
| `require_size(requested, cap, *, what, error=SchemaError)`, the `MAX_*` caps                  | `showpy.core.caps`                           | See "showpy.core.caps"                                                                                                                                    |
| `schema_path(name)` / `font_path(name)`                                                       | `showpy.core.resources`                      | Resolve a bundled JSON schema or font file                                                                                                                |
| `ArcPiece`, `CubicPiece`, `LinePiece`, `QuadPiece`                                            | `showpy.geo.svg`                             | One evaluated piece of an SVG outline                                                                                                                     |
| `CurveBase`, `CurveModel`                                                                     | `showpy.geo.curve_base`, `showpy.geo.curves` | Base classes of the curves; `CurveModel` adds trimming and reversal                                                                                       |
| `FontGlyph`, `TextAlign`, `TextAnchor`, `DEFAULT_LINE_SPACING` (1.2)                          | `showpy.geo.models`                          | A character's advance and stroke paths; `"left" \| "center" \| "right"` and `"baseline" \| "middle" \| "top"` for `layout_text`; its default line spacing |
| `BUNDLED_FONTS`                                                                               | `showpy.geo.fonts`                           | Read-only map of bundled font names to their `asset:sha256:` URIs                                                                                         |
| `football_field(ref_id, name, hash_ft_in)`, `proscenium_stage()`, `gym_floor()`               | `showpy.sheet.surfaces`                      | Build the built-in sheets that `sheet_templates` lists                                                                                                    |
| `template_ref(ref_id)`, `rect_ring(x0, y0, x1, y1)`                                           | `showpy.sheet.surfaces`                      | First-version `SheetRef` of a built-in sheet; a rectangle's corners, counter-clockwise                                                                    |
| `point_landmark(mark_id, name, p, *aliases)` / `line_landmark(mark_id, name, a, b, *aliases)` | `showpy.sheet.surfaces`                      | Build a point or a straight line `Landmark`                                                                                                               |
| `landmark_keys(mark)`                                                                         | `showpy.sheet.bounds`                        | Name and aliases of a landmark, case-folded with spaces collapsed, for matching                                                                           |
| `check_unlocked(form, force)`                                                                 | `showpy.choreo.forms`                        | Raise `LockedError` when the form is locked and `force` is false                                                                                          |
| `spiral_points(params, n)`, `sine_wave_points(params, n)`, `rose_points(params, n)`           | `showpy.choreo.form_fns`                     | The generators behind the `spiral`, `sine_wave` and `rose` form functions                                                                                 |
| `with_set(show, new)` / `clear_rows(show, tid, entities, force=False)`                        | `showpy.choreo.match_ops`                    | Replace the set with the same ID; drop stored path rows (locked rows need `force`)                                                                        |
| `store_paths(show, op)`                                                                       | `showpy.show.changes`                        | Store a transition's paths in show order; empty paths are not stored                                                                                      |
| `digest(data)` / `conflict_error(show_id, expected, landed)`                                  | `showpy.show.repositories`                   | The SHA-256 hex digest blobs are addressed by; the `ConflictError` that lists each landed revision                                                        |
| `escape_text(text)`                                                                           | `showpy.export.pdf`                          | Encode text as the body of a PDF literal string                                                                                                           |

## Checker (consumer, planned)

Not built yet (out of scope for the first build, 2026-11-11).

A separate consumer package, run in its own process and depending on a pinned ShowPy release. ShowPy never imports it; it is reached only through the `run_checks` tool and hooks. It reads the show through the `Repository` and computes motion with the same `PathEngine`.

### Checker models

**Enums**: `Severity` = `ERROR`, `WARNING`, `INFO`. `CheckName` = `COLLISIONS`, `NEAR_MISSES`, `SPEED`, `TURN_RATE`, `CONTINUITY`, `INTERVAL`, `BOUNDS`, `ZONES`, `FACING` (object, behavior, contact and sightline checks are added by the 3D layer).

**`LimitsConfig`** (`/config/limits.yaml`; designer-owned, pack defaults)

| Field                                           | Type                                                                                             | Meaning                                                                                               |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `collision_gap`                                 | `Meters`                                                                                         | Gap after footprints below which two entities collide                                                 |
| `near_miss_gap`                                 | `Meters`                                                                                         | Warning threshold                                                                                     |
| `radius_by_group`                               | `Mapping[str, Meters]`                                                                           | Extra footprint by group                                                                              |
| `max_speed`                                     | `list[tuple[CountsPerMinute, float]]` or m/s                                                     | Tempo-dependent speed limit (m/count)                                                                 |
| `max_speed_backward_pct`, `max_speed_slide_pct` | `float`                                                                                          | Direction-dependent limits                                                                            |
| `reference_height_m`                            | `float`                                                                                          | Height the limits are written for                                                                     |
| `max_turn_rate`                                 | `Degrees` per count                                                                              | Facing change limit                                                                                   |
| `interval_tolerance`, `min_interval`            | `Meters`                                                                                         | Form spacing rules                                                                                    |
| `bounds`                                        | `"surface"`, `"surface+margins"`, `Polygon`                                                      | Allowed area                                                                                          |
| `zones`                                         | `list[Zone]`                                                                                     | Extra restricted areas                                                                                |
| `facing_rules`                                  | `list[FacingRule{at: SetId \| Count, entities: Selector, expect: Degrees \| str, tol: Degrees}]` | Required facings                                                                                      |
| `checks_enabled`                                | `list[CheckName]`                                                                                | Active checks                                                                                         |
| `severity`                                      | `Mapping[CheckName, Severity]`                                                                   | Error versus warning                                                                                  |
| `group_overrides`                               | `Mapping[str, dict]`                                                                             | Per-group values for any limit above, e.g. looser steps for the guard; taken from each Group's limits |

**`Finding`** — `check: CheckName, severity: Severity, tid: TransitionId \| None, set_id: SetId \| None, entities: tuple[SpotId, ...], at: float \| None, value: float \| None, limit: float \| None, message: str` (rendered as one line). **`CheckReport`** — `findings: list[Finding], summary: dict[CheckName, int], scope: str, revision: str`.

**Shapes in checks.** Collisions use each spot's shape: circle to circle, circle to rectangle and rectangle to rectangle, with rectangles rotated by the spot's facing at that moment.

### Checker functions

| Function                                                    | Inputs                                              | Output          | Purpose                                                                                                             |
| ----------------------------------------------------------- | --------------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------- |
| `run(repo, show_id, scope="all", checks=None, limits=None)` | `all`, `set:<id>`, `transition:<a>-><b>`, `changed` | `CheckReport`   | Entry point (called by the tool and hooks)                                                                          |
| `check_collisions(engine, tid, limits)`                     |                                                     | `list[Finding]` | Grid broadphase, then exact closest approach per segment pair: t\* = clamp(−(d·u)/(u·u)); constant gap when u·u ≈ 0 |
| `check_near_misses(...)`                                    |                                                     | `list[Finding]` | Same, between the two thresholds                                                                                    |
| `check_speed(engine, tid, show, limits)`                    |                                                     | `list[Finding]` | Speed versus tempo and direction limits                                                                             |
| `check_turn_rate(engine, tid, limits)`                      |                                                     | `list[Finding]` | Facing change per count                                                                                             |
| `check_continuity(engine, tid)`                             |                                                     | `list[Finding]` | Paths end on placements                                                                                             |
| `check_interval(show, set_id, limits)`                      |                                                     | `list[Finding]` | Spacing within forms                                                                                                |
| `check_bounds(engine, show, tid, limits)`                   |                                                     | `list[Finding]` | Inside the allowed area                                                                                             |
| `check_zones(engine, show, tid, limits)`                    |                                                     | `list[Finding]` | No active zone violations                                                                                           |
| `check_facing(show, engine, limits)`                        |                                                     | `list[Finding]` | Facing rules                                                                                                        |
| `format_report(report, max_lines=50)`                       |                                                     | `str`           | Sorted lines plus `SUMMARY`, e.g. `COLLISION 12→13: T4 & M2, count 6.4, gap 0.12 m`                                 |
| `register_check(name, fn, reviewed_by)`                     |                                                     | `None`          | Packs add reviewed checks                                                                                           |

### Checker waivers (breaking rules on purpose)

A waiver records that the designer deliberately allows something the checker would flag, such as a tight weave or a crowded moment. The checker is a consumer package, so ShowPy has no principals and does not decide who may record a waiver: the host application owns that rule and stores the result in `/config/waivers.yaml`, an ordinary file to ShowPy.

| Data class      | Fields                                                                                                                                                                                                                                         | Represents                                                                    |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `Waiver`        | `id: str, check: CheckName, entities: Selector, scope: SetId \| TransitionId, window: tuple[float, float] \| None (local counts), limit_override: float \| None, reason: str, approved_by: str, created_at: datetime, expires_on_change: bool` | An approved exception, optionally with a relaxed limit instead of a full skip |
| `WaiverRequest` | `check, entities, scope, window, limit_override, reason, requested_by`                                                                                                                                                                         | The agent asking for an exception                                             |

| Function                                                                           | Inputs          | Output            | Purpose                                              |
| ---------------------------------------------------------------------------------- | --------------- | ----------------- | ---------------------------------------------------- |
| `request_waiver(ws, request)`                                                      | `WaiverRequest` | request id        | Agent proposes; shows on the canvas for the designer |
| `approve_waiver(ws, request_id, approver)` / `deny_waiver(ws, request_id, reason)` |                 | `Waiver` / `None` | Decision, recorded by whoever the host allows        |
| `list_waivers(ws, scope=None)`                                                     |                 | `list[Waiver]`    | Current exceptions                                   |
| `revoke_waiver(ws, id)`                                                            |                 | `None`            | Remove an exception                                  |

Waived findings are reported as `WAIVED` lines with the reason instead of errors, never silently hidden. If `expires_on_change` is true, editing the waived transition voids the waiver. Contacts from `showpy.choreo.contact` are honored automatically and need no waiver. Waivers live in `/config/waivers.yaml`; ShowPy enforces no write permission on it.

### Tool and hook contract

| Interface         | Input                                | Output / effect                                                                      |
| ----------------- | ------------------------------------ | ------------------------------------------------------------------------------------ |
| `run_checks` tool | `{scope, checks?, max_lines?}`       | Formatted report text; never stack traces                                            |
| After-save hook   | writes to sets, transitions, actions | Runs `scope="changed"`, appends the summary to the tool result                       |
| Before-tool hook  | any write or script                  | Denies writes to `limits.yaml` and pinned items, and scripts that import the checker |
| Stop hook         | agent tries to finish                | Blocks while error findings remain in its changes, unless waived by the designer     |

## showpy.packs

Not built yet (out of scope for the first build, 2026-11-11).

Domain packs add a discipline's units, venues, groups, position language, rules and printouts without changing the core. Marching band is the first pack.

### showpy.packs.base

**`PositionLanguage`** (protocol)

| Member                                                 | Inputs                | Output           | Purpose                                        |
| ------------------------------------------------------ | --------------------- | ---------------- | ---------------------------------------------- |
| `describe(point, sheet, style="long", precision=None)` | `Point`, `Sheet`      | `str`            | Position in the discipline's words             |
| `parse(text, sheet)`                                   | `str`                 | `Point`          | Read a position; raises with the failing token |
| `parse_sheet(text, sheet)`                             | coordinate-sheet text | `list[SheetRow]` | Import printed sheets                          |
| `styles()`                                             |                       | `list[str]`      | Supported wordings                             |

**`DomainPack`**

| Field                    | Type                      | Meaning                                                                                                |
| ------------------------ | ------------------------- | ------------------------------------------------------------------------------------------------------ |
| `key`, `name`, `version` | `str`                     | e.g. `marching`                                                                                        |
| `units`                  | `list[tuple[str, float]]` | Units in meters                                                                                        |
| `grid`                   | `Grid`                    | Default blueprint grid                                                                                 |
| `sheet_templates`        | `Mapping[str, SheetRef]`  | Sheet templates the pack ships (e.g. fields with NFHS, NCAA and NFL hashes)                            |
| `groups`                 | `Mapping[str, Group]`     | Default groups                                                                                         |
| `motion_thresholds`      | `dict`                    | Angles and speeds for forward, backward, slide, crab, run classification                               |
| `limits_defaults`        | `LimitsConfig`            | Starting limits                                                                                        |
| `language`               | `PositionLanguage`        | Position wording                                                                                       |
| `form_templates`         | `Mapping[str, Callable]`  | Prefab shapes                                                                                          |
| `sheets`                 | `Mapping[str, Callable]`  | Printouts                                                                                              |
| `skills`                 | `list[str]`               | Agent skill files                                                                                      |
| `extra_checks`           | `list[str]`               | Reviewed checks registered in the checker                                                              |
| `default_timeline_mode`  | `TimelineMode`            | Default timing for new shows: counts for marching and dance, cues for theater, seconds for drone shows |

| Function                                       | Inputs       | Output                     | Purpose                                            |
| ---------------------------------------------- | ------------ | -------------------------- | -------------------------------------------------- |
| `register_pack(pack)`                          | `DomainPack` | `None`                     | Add a pack                                         |
| `get_pack(key)` / `list_packs()`               |              | `DomainPack` / `list[str]` | Lookup                                             |
| `apply_pack(show, key)`                        |              | `Show`                     | Set units, limits defaults, language and templates |
| `describe_position(show, point, style="long")` |              | `str`                      | Pack wording through a show                        |
| `parse_position(show, text)`                   |              | `Point`                    | Pack parsing through a show                        |

### showpy.packs.marching

| Item                                        | Inputs                                                                                                                                 | Output                           | Purpose                                                                                              |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Unit `step85`                               |                                                                                                                                        | 0.5715 m                         | One 8-to-5 step (22.5 in)                                                                            |
| `n_to_5(distance_m, counts, pace=1.0)`      |                                                                                                                                        | `float`                          | Step size in N-to-5 terms (N = 8·counts·pace / distance in steps)                                    |
| `step_inches(distance_m, counts, pace=1.0)` |                                                                                                                                        | `float`                          | Stride in inches                                                                                     |
| `step_label(n)`                             |                                                                                                                                        | `str`, e.g. `"6-to-5"`, `"hold"` | Readable step size                                                                                   |
| `FieldStandard` (enum)                      | `NFHS`, `NCAA`, `NFL`, `TEXAS_HS`, `CUSTOM`                                                                                            |                                  | Hash placement standard                                                                              |
| `MarchingLanguage`                          | implements `PositionLanguage`                                                                                                          |                                  | Side 1/2, inside/outside yard lines, in front of/behind sidelines and hashes, quarter-step precision |
| `yard_line_crossings(show, spot, tid)`      |                                                                                                                                        | `list[(yard_line, side, count)]` | For drill books                                                                                      |
| Groups                                      | flute, clarinet, saxes, trumpet, mellophone, baritone, trombone, tuba, snare, tenors, bass, cymbals, front ensemble, guard, drum major |                                  | Default groups and label prefixes                                                                    |
| Limits defaults                             | stride warning above 5-to-5; backward limit as a percentage of forward; height scaling from 5'8"                                       |                                  | Starting limits for the designer                                                                     |
| Templates                                   | company front, block, wedge, staggered arcs, gate, pinwheel, follow-the-leader, counter-march                                          |                                  | Prefabs                                                                                              |
| Sheets                                      | performer coordinate sheet, designer chart, personal drill book                                                                        |                                  | Printouts                                                                                            |

Other packs (`theater`, `guard`, `dance`, `cheer`, `parade`) implement the same `DomainPack` interface with their own venues, language and printouts.

## showpy.index

Not built yet (out of scope for the first build, 2026-11-11).

How the agent finds and learns the library, and how its own reusable code is reviewed and promoted.

### showpy.index.models

**`ApiStatus`** (enum): `DRAFT`, `REVIEWED`, `STABLE`, `DEPRECATED`.

**`ApiEntry`**

| Field        | Type              | Meaning                               |
| ------------ | ----------------- | ------------------------------------- |
| `qualname`   | `str`             | e.g. `showpy.geo.spacing.space_along` |
| `signature`  | `str`             | Typed signature                       |
| `summary`    | `str`             | First docstring line                  |
| `category`   | `str`             | Module group                          |
| `panel`      | `str`             | Canvas, Export, or all                |
| `status`     | `ApiStatus`       | Lifecycle                             |
| `since`      | `str`             | Version added                         |
| `deprecated` | `str \| None`     | Replacement qualname                  |
| `tags`       | `tuple[str, ...]` | Search terms                          |
| `example`    | `str`             | Runnable example                      |

### showpy.index functions

| Function                                                           | Inputs                              | Output           | Purpose                                                                       |
| ------------------------------------------------------------------ | ----------------------------------- | ---------------- | ----------------------------------------------------------------------------- |
| `@api(category, panel="all", status=STABLE, since="1.0", tags=())` |                                     | decorator        | Register a function in the index                                              |
| `@deprecated(replacement, since, remove_in)`                       |                                     | decorator        | Warn and point to the replacement                                             |
| `api_index(category=None, panel=None, status_min=REVIEWED)`        |                                     | `str`            | Compact one-line-per-function listing                                         |
| `describe(qualname)`                                               |                                     | `str`            | Full docstring, signature, example, related functions                         |
| `search_api(query, k=10)`                                          |                                     | `list[ApiEntry]` | Keyword and tag search                                                        |
| `categories()` / `panels()`                                        |                                     | `list[str]`      | Navigation                                                                    |
| `register_draft(ws, path)`                                         | path in `/lib/drafts`               | `list[ApiEntry]` | Load agent code as `[draft]`; must be pure, typed, documented with an example |
| `review_draft(qualname, reviewer, approve, notes)`                 |                                     | `ApiEntry`       | Doctests and generated tests must pass; a human approves; becomes REVIEWED    |
| `promote(qualname, target_module)`                                 | `showpy.contrib.<module>` or a pack | `ApiEntry`       | Becomes STABLE and indexed; core modules stay maintainer-only                 |
| `retire(qualname, replacement)`                                    |                                     | `ApiEntry`       | Deprecate with a pointer                                                      |
