# Stage Library: API Reference

Nov 8, 2026 · @Peter

## How to read this reference

This is the complete API of `stage`, the choreography library behind the Show Canvas (2D sheet; the 3D layer has its own reference), listed module by module: every data class with its fields, and every function with its inputs, output and purpose. Spots are the dots designed on the sheet, claimed by real performers after publishing.

**Conventions used in every table**

- Distances are meters on the 2D stage plane (x audience-left to audience-right, y away from the audience); 3D world is Y-up, glTF axes.
- Facing is degrees, 0 = toward the audience, counter-clockwise positive.
- Time is `Count` (musical pulse), `Seconds`, or measures; a transition's local time is `tau`.
- All data classes are frozen Pydantic v2 models with JSON schemas. Functions are pure: "edit" functions return a new object.
- `Points` = NumPy array of shape (N, 2), float64. `Selector` = selection expression, list of IDs, or `Selection`.
- `→ X` means the function returns X. Optional arguments show their defaults.

**Module map**

| Module | Panel | What it holds |
| --- | --- | --- |
| `stage.core` | all | Units, IDs and types, errors, utilities |
| `stage.cast` | Show Canvas | Groups, spots, objects, cast, selection |
| `stage.sheet` | Canvas | The 2D design sheet: templates, customization, coordinate systems, replacement |
| `stage.notes` | Canvas | Design notes on sets, spots and groups |
| `stage.time` | Show Canvas, Preview | Count timeline: tempo, holds, markers, audio sync |
| `stage.geo` | Show Canvas | Shapes, spacing, transforms, measurement |
| `stage.choreo` | Show Canvas | Sets, forms, matching, paths, engine, actions |
| `stage.show` | Show Canvas | Show model, build, storage, collaboration, history, validation, export and publishing |
| `stagecheck` | all (locked) | Limits and checks, run only through a tool |
| `stage.packs` | all | Domain packs, starting with marching band |
| `stage.index` | agent | API index and promotion of agent-written code |

## stage.core

The foundation every other module imports: one unit system, shared types and IDs, one error family, and small utilities.

### stage.core.types (type aliases)

| Name | Definition | Represents |
| --- | --- | --- |
| `Meters`, `Seconds`, `Count`, `Degrees`, `CountsPerMinute` | `float` | Distance, wall time, musical pulse, angle, tempo |
| `Point` | `tuple[float, float]` | A stage-plane position in meters |
| `Point3` | `tuple[float, float, float]` | A world position in meters |
| `Points` | `numpy.ndarray` (N, 2) | Many stage-plane positions |
| `Transform` | `numpy.ndarray` (4, 4) | World matrix (position, rotation, scale) |
| `Affine2D` | `numpy.ndarray` (3, 3) | 2D transform between blueprint and stage plane |
| `Box2` / `Box3` | `{min, max}` | 2D or 3D bounding box |
| `Polygon` | `shapely.Polygon` | An area on the stage plane |
| `Selector` | `str \| Sequence[str] \| Selection` | Who an operation applies to |

### stage.core.ids

| Name | Format | Represents |
| --- | --- | --- |
| `SheetRef` | `sheet:<id>@<version>` | A 2D design sheet (template or saved) |
| `SpotId` | free string, e.g. `T4`, `flag-12` | Entities in a show |
| `SetId` | e.g. `12`, `12A` | A set in a show |
| `TransitionId` | `tuple[SetId, SetId]`, written `12→13` | Movement between two sets |
| `FormId`, `RouteId`, `ActionId`, `ZoneId`, `LandmarkId` | free string | Named items |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `parse_ref(text)` | `str` | `(kind, id, version \| None)` | Read a sheet reference |
| `format_ref(kind, id, version=None)` |  | `str` | Write one |
| `parse_tid(text)` | `"12→13"` or `"12->13"` | `TransitionId` | Read a transition ID |
| `format_tid(tid)` | `TransitionId` | `str` | Write one |

### stage.core.units

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `convert(value, from_unit, to_unit)` | `float`, unit names (`m`, `cm`, `mm`, `in`, `ft`, `yd`, or registered) | `float` | The only unit conversion point |
| `register_unit(name, meters_per_unit)` | `str`, `float` | `None` | Packs add units, e.g. marching step 0.5715 m |
| `units()` |  | `dict[str, float]` | List known units |
| `normalize_angle(a)` | `Degrees` | `Degrees` in \[0, 360) | Wrap an angle |
| `angle_diff(a, b)` | `Degrees`, `Degrees` | `Degrees` in (−180, 180\] | Signed shortest turn |
| `facing_vector(theta)` | `Degrees` | unit `Point` | Facing to direction (0° → (0, −1)) |
| `vector_facing(v)` | `Point` | `Degrees` | Direction to facing; raises `ZeroLengthError` on (0, 0) |
| `round_to(value, precision=0.01)` | `float`, `float` | `float` | Display rounding, half away from zero |

### stage.core.errors

Every error has `code: str`, `message: str`, `context: dict`, `hint: str`.

| Class | Parent | Raised when |
| --- | --- | --- |
| `StageError` | `Exception` | Base of all library errors |
| `UnknownRefError` | `StageError` | An ID does not resolve; hint lists close matches |
| `VersionError` | `StageError` | A pinned version is missing or incompatible |
| `SchemaError` | `StageError` | Data fails its JSON schema; names the field |
| `GeometryError` | `StageError` | Degenerate shape (zero radius, open polygon, collinear arc points) |
| `ZeroLengthError` | `GeometryError` | Direction of a zero vector requested |
| `SpacingError` | `StageError` | Items do not fit; reports the maximum that fits |
| `TimeRangeError` | `StageError` | Times outside range or start ≥ end |
| `TimelineError` | `StageError` | Inconsistent tempo, meter or fermatas |
| `SurfaceError` | `StageError` | A point is off the venue surface |
| `MatchError` | `StageError` | No feasible assignment; names the blocking constraint |
| `PathError` | `StageError` | Invalid path instruction |
| `ContinuityError` | `PathError` | A path does not end on its target placement |
| `LockedError` | `StageError` | Edit touches something the designer locked |
| `StorageError` | `StageError` | Persistence failure |
| `ConflictError` | `StorageError` | A concurrent edit landed first; lists what changed |
| `ValidationError` | `StageError` | Structural integrity failure |
| `DeprecatedWarning` | `UserWarning` | Deprecated API used; names the replacement |

### stage.core.util

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `natural_sort(ids)` | `Iterable[str]` | `list[str]` | T2 before T10 |
| `parse_range(text)` | `"T1-T8"` | `list[str]` | Expand ID ranges |
| `format_ranges(ids)` | `Iterable[str]` | `str`, e.g. `"T1–T4, T7"` | Compact display |
| `approx_equal(a, b, tol=1e-6)` | numbers or arrays | `bool` | Float comparison |
| `seeded_rng(seed)` | `int` | `numpy.random.Generator` | The only randomness allowed in scripts |
| `as_points(obj)` | tuples, lists, Shapely, placements | `Points` | Normalize point inputs |
| `content_hash(obj)` | any model or bytes | `str` | Detect stale builds and changes |
| `chunk_counts(total, parts)` | `float`, `int` | `list[float]` | Split counts evenly |
| `log(msg, **fields)` |  | `None` | Structured log captured by builds |
| `timer()` |  | context manager | Measure script timing |

## stage.cast

Who and what is in a show: group templates, spots, objects, named groups, and the selection language used everywhere to pick them.

### stage.cast.models

**`Group`** — a named set of spots with shared defaults .

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `key` | `str` | required | e.g. `trumpets`, `front-guard` |
| `name` | `str` | from key | Display name |
| `label_prefix` | `str` | required | e.g. `T` gives T1, T2 |
| `symbol`, `color` | `str` | pack default | 2D sheet glyph and hex color |
| `default_radius` | `Meters` | 0.3 | Collision footprint |
| `limits` | `dict` | {} | Per-group limit overrides (see stagecheck) |
| `moves` | `bool` | True | False for stationary groups (pit) |

**`Spot`** — a dot designed on the sheet that persists through the whole show. (stage.show.export).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | `SpotId` | required | Label shown on the sheet, stable all show |
| `group` | `str` | required | Group key |
| `symbol`, `color` | `str` | from group | Sheet display |
| `tags` | `frozenset[str]` | ∅ | e.g. `soloist`, `leader` |
| `active` | `bool` | True | False excludes from forms and checks |
| `shape` | `Shape` | circle, 0.3 m | Size and outline on the sheet and for collisions; circle or rectangle, set by the designer |
| `claimable` | `"performer" \| "crew" \| "none"` | performer; none for object-only spots | Who can claim the spot when the show is published |

**`Shape`** — `kind: "circle" \| "rectangle", radius_m: float, width_m: float, depth_m: float, rotation_deg: Degrees`. Rectangles rotate with the spot's facing; used for display and collisions.

**`LabelScheme`** — `prefixes: Mapping[str, str]` (group → prefix), `start: int = 1`, `pad: int = 0`.

**`Cast`** — every spot and object in the show.

| Field | Type | Meaning |
| --- | --- | --- |
| `groups` | `Mapping[str, Group]` | Groups and their defaults |
| `spots` | `tuple[Spot, ...]` | All spots |
| `selections` | `Mapping[str, frozenset[SpotId]]` | Named selections |
| `label_scheme` | `LabelScheme` | Labeling rules |

Methods: `spot(id) → Spot`.

### stage.cast.spots (creating spots on the sheet)

Spots are drawn directly on the sheet; there is no roster to import. A new spot exists in every set: sets before the one it was created in mark it off the field until an entrance is designed.

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `add_spot(show, point, group, set_id)` | `Point`, group key, set | `Show` | One dot |
| `add_spots_along(show, curve, n, group, set_id)` | `Curve`, count | `Show` | Dots evenly along a line or curve |
| `fill_spots(show, region, group, set_id, interval=None, n=None)` | `Polygon` | `Show` | Fill an area with dots |
| `duplicate_spots(show, sel, offset, set_id)` | selection, `Point` | `Show` | Copy dots |
| `delete_spots(show, sel)` |  | `Show` | Remove from every set |
| `regroup(show, sel, group)` |  | `Show` | Move to another group; labels update |
| `add_group(show, group)` / `edit_group(show, key, **changes)` / `remove_group(show, key, reassign_to)` | `Group` | `Show` | Manage groups |
| `relabel(show, scheme)` | `LabelScheme` | `(Show, dict[old, new])` | New labels plus mapping for history rename |
| `define_selection(show, name, sel)` |  | `Show` | Named selection |
| `set_shape(show, sel, shape)` | `Shape` | `Show` | Circle or rectangle, size and rotation |
| `set_claimable(show, sel, value)` | `"performer" \| "crew" \| "none"` | `Show` | Who can claim these spots when published |

### stage.cast.select

**`Selection`** — ordered, duplicate-free `ids: tuple[SpotId, ...]` plus `description: str`; supports `|`, `&`, `-`, `len`, iteration and `in`.

Expression grammar for `select`: ranges `T1-T8`, prefixes `T*`, `group:trumpets`, `form:arc1`, `tag:soloist`, `shape:rectangle`, `all`, combined with `+` (union), `-` (difference), `&` (intersection).

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `select(source, expr)` | `Show`, `Set` or `Cast`; expression | `Selection` | Pick entities; bad token raises with its position |
| `in_region(set, region)` | `Set`, `Points` or `Polygon` | `Selection` | Lasso and box selection |
| `near(set, point, radius)` |  | `Selection` | Within a radius |
| `where(set, predicate)` | callable on `Placement` | `Selection` | Any filter |
| `ordered_along(set, sel, by="form")` | `"form"`, `"x"`, `"y"`, or a `Curve` | `Selection` | Order for ripples, follow-the-leader, matching |
| `split(sel, k)` |  | `list[Selection]` | k consecutive groups |
| `alternate(sel, k=2)` |  | `list[Selection]` | Every k-th entity |
| `mirror_pairs(set, sel, axis_x, tol=0.1)` |  | `list[tuple[SpotId, SpotId]]` | Symmetry partners |

## stage.sheet

Used for the 2D design surface every show is drawn on. There is one standard sheet; a template fills it in (size, lines, landmarks, zones, grid), and a coordinate system on top only changes labels and dot-sheet wording, never the design.

### stage.sheet.models

**Enums**: `SurfaceType` = `FOOTBALL_FIELD`, `STAGE`, `GYM_FLOOR`, `ARENA`, `STREET`, `BLANK`. `ZoneKind` = `RESTRICTED`, `PIT`, `WINGS`, `STAGING`, `AUDIENCE`, `SAFETY`, `CUSTOM`. `CoordinateKind` = `MARCHING`, `STAGE`, `DANCE_FLOOR`, `GRID`.

| Data class | Fields | Represents |
| --- | --- | --- |
| `SheetRef` | `sheet:<id>@<version>` | A sheet template or saved sheet |
| `Grid` | `origin: Point, spacing: tuple[Meters, Meters], major_every: int, rotation_deg: Degrees, unit_name: str` | Background grid |
| `GuideLine` | `id: str, kind: str (yard, hash, sideline, center, edge, spike, custom), points: Points, label: str` | A line drawn on the sheet |
| `Landmark` | `id: LandmarkId, name: str, kind: "point" \| "line" \| "area", geometry: Point \| Points \| Polygon, aliases: tuple[str, ...]` | Named reference ("front hash", "center stage") |
| `Zone` | `id: ZoneId, kind: ZoneKind, polygon: Polygon, applies_to: Selector, active: list[tuple[Count, Count]] \| None, note: str` | An area with rules |
| `Sheet` | `ref: SheetRef, name: str, surface_type: SurfaceType, size_m: tuple[float, float], outline: Points, walkable: Polygon, guides: tuple[GuideLine, ...], landmarks: tuple[Landmark, ...], zones: tuple[Zone, ...], grid: Grid, default_coordinates: CoordinateSystem, based_on: SheetRef \| None, owner: str, thumbnail: str` | The 2D design surface |
| `CoordinateSystem` | `kind: CoordinateKind, origin: Point \| LandmarkId, audience_side: "front" \| "back" \| "left" \| "right", units: str, precision: float, labels: dict` | How positions are labeled and worded (display only) |
| `SheetReplaceReport` | `mapping: "landmarks" \| "origin" \| "scale", moved: dict[SpotId, tuple[Point, Point]], outside: list[SpotId], in_zones: list[tuple[SpotId, ZoneId]], form_notes: list[str], checks: dict[str, int]` | What changed when a sheet was replaced |

### stage.sheet functions

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `templates(pack=None, surface_type=None)` |  | `list[Sheet]` | Sheet templates (high school, college and NFL fields, stage, gym floor, ...) |
| `get_sheet(ref)` | `SheetRef` | `Sheet` | Load a template or saved sheet |
| `blank_sheet(width_m, depth_m, grid_spacing=1.0, units="m")` |  | `Sheet` | Plain rectangle for unusual spaces |
| `customize(sheet, **changes)` | name, grid, size for blank sheets | `Sheet` | Adjust a sheet without replacing it |
| `add_zone` / `add_landmark` / `add_guide(sheet, item)` |  | `Sheet` | Mark pit areas, props areas, custom references |
| `remove_zone` / `remove_landmark` / `remove_guide(sheet, id)` |  | `Sheet` |  |
| `save_sheet(sheet, scope="mine")` | `"mine"` or `"team"` | `SheetRef` | Keep a customized sheet (e.g. "Lincoln HS home field") |
| `coordinate_presets(sheet)` |  | `list[CoordinateSystem]` | Systems that suit this sheet |
| `set_coordinates(show, system)` | `CoordinateSystem` | `Show` | Change wording, origin or audience side; positions do not move |
| `to_coords(show, p, style="long")` / `from_coords(show, text)` |  | `str` / `Point` | Position in the chosen system and back |
| `replace_sheet(show, sheet, mapping="landmarks")` |  | `(Show, SheetReplaceReport)` | Swap the sheet: re-map dots, flag problems, re-run checks; undo through history |
| `on_surface(sheet, p)` / `walkable(sheet, p)` | `Point` | `bool` | Bounds tests |
| `landmark(sheet, name)` | name or alias | `Landmark` | Look up a reference |
| `nearest_landmarks(sheet, p, k=3, kinds=None)` |  | `list[(Landmark, Meters)]` | Closest references, for wording |
| `zones_at(show, p, t=None)` |  | `list[Zone]` | Active sheet and show zones at a point |
| `render_sheet(sheet, fmt="svg")` | `svg`, `pdf`, `png` | `bytes` | Printable blank sheet |

## stage.notes

Used for design notes on the canvas, replacing a separate plan: a goal for each set, sticky notes on spots or groups, and ideas the agent drafts from your concept. Review discussion stays in comments (stage.show.collab).

| Data class | Fields | Represents |
| --- | --- | --- |
| `Note` | `id: str, text: str, kind: "goal" \| "idea" \| "todo" \| "warning", anchor: SetId \| TransitionId \| Point \| Polygon \| Selection, author: str (user or "agent"), created_at: datetime, visible: bool` | One design note |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `add_note(show, text, anchor, kind="idea")` |  | `Show` | Add a note to a set, spot or group |
| `edit_note(show, id, **changes)` / `remove_note(show, id)` |  | `Show` | Change or delete |
| `notes_for(show, target)` | `SetId` or `TransitionId` | `list[Note]` | Notes for a set or transition (the set strip shows goals) |
| `notes_near(show, p, radius)` |  | `list[Note]` | Sticky notes around a spot |
| `draft_notes(show, concept)` | text describing the show | `list[Note]` | Agent proposes set goals and ideas from your concept, as suggestions |

## stage.time

Used for choreography timing: when each count happens, so sets land at the right moments, speed checks know real time, and the preview stays in sync with the audio. The music itself is only a playback asset; it never changes the timeline. The designer sets the tempo; the agent can read an uploaded track and suggest markers or tempo changes, but only accepted suggestions change anything.

### stage.time.models

**Enums**: `TimelineMode` = `COUNTS` (most music-driven shows), `SECONDS` (clock-timed shows), `CUES` (theater-style shows). `MarkerKind` = `REHEARSAL`, `CUE`, `CUSTOM`.

| Data class | Fields | Represents |
| --- | --- | --- |
| `TempoSection` | `at_count: Count, counts_per_minute: CountsPerMinute, ramp_to: CountsPerMinute \| None, ramp_counts: Count \| None` | How fast counts go from a count onward, with an optional gradual speed-up or slow-down |
| `Hold` | `at_count: Count, seconds: Seconds, note: str \| None` | A pause where counts stop (a held note, a freeze) |
| `Marker` | `label: str, at_count: Count, kind: MarkerKind` | A named moment ("the hit", "letter B") |
| `MeasureSetting` | `counts_per_measure: float, start_measure: int, changes: tuple[tuple[Count, float], ...]` | Optional measure numbering for designers who talk in measures |
| `MeasureLabel` | `measure: int, count_in_measure: float` | Display label for a count, e.g. m. 33, count 1 |
| `Cue` | `label: str, order: int, estimated_seconds: Seconds` | A moment in cue mode, with an estimated duration so the preview can play |
| `AudioTrack` | `file: str, sync_offset_s: Seconds, gain: float, label: str, section: tuple[Count, Count] \| None` | Music or sound that plays alongside the show; never changes timing |
| `Timeline` | `mode: TimelineMode, tempos: tuple[TempoSection, ...], holds: tuple[Hold, ...], markers: tuple[Marker, ...], measures: MeasureSetting \| None, cues: tuple[Cue, ...], audio: tuple[AudioTrack, ...], total_counts: Count \| None` | The whole show timeline |

### stage.time functions

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `new_timeline(mode, counts_per_minute=None, total=None)` |  | `Timeline` | Start a timeline |
| `add_tempo(tl, section)` / `remove_tempo(tl, at_count)` | `TempoSection` | `Timeline` | Set or change tempo from a count |
| `add_hold(tl, hold)` / `remove_hold(tl, at_count)` | `Hold` | `Timeline` | Pauses |
| `add_marker(tl, marker)` / `remove_marker(tl, label)` | `Marker` | `Timeline` | Named moments |
| `add_cue(tl, cue)` / `remove_cue(tl, label)` | `Cue` | `Timeline` | Cue mode moments |
| `set_measures(tl, setting)` / `clear_measures(tl)` | `MeasureSetting` | `Timeline` | Turn measure labels on or off |
| `attach_audio(tl, track)` / `remove_audio(tl, label)` | `AudioTrack` | `Timeline` | Add or remove playback audio |
| `set_sync(tl, track, offset_s)` | label, seconds | `Timeline` | Where count 1 (or time 0) falls in the audio |
| `tempo_at(tl, c)` | `Count` | `CountsPerMinute` | Tempo at a count, including ramps |
| `count_to_seconds(tl, c)` / `seconds_to_count(tl, s)` |  | `Seconds` / `Count` | Convert, including ramps and holds; during a hold, returns the hold's count |
| `seconds_per_count(tl, c)` |  | `Seconds` | Used by speed checks |
| `count_to_measure(tl, c)` |  | `MeasureLabel \| None` | Measure label (None if measures are off) |
| `measure_to_count(tl, measure, count_in_measure=1)` |  | `Count` | Find a count by measure |
| `marker_to_count(tl, label)` |  | `Count` | Jump to a named moment |
| `counts_from_markers(tl, labels)` | marker labels | `list[Count]` | Turn marked moments into set boundaries |
| `cue_to_seconds(tl, label)` |  | `Seconds` | Cue mode time from estimated durations |
| `describe(tl, c)` | `Count` | `str`, e.g. "count 130 (m. 33), 1:12.4, 168 cpm" | Readable time for the canvas and agent |
| `to_json(tl)` / `from_json(d)` |  | `dict` / `Timeline` | Persistence |
| `validate_timeline(tl)` |  | `ValidationReport` | Tempos positive and ordered, holds don't overlap, markers within the show, audio sections in range |

## stage.geo

The drawing toolkit on the stage plane: shapes (curves and regions), spots spaced along or inside them, transforms that move whole shapes, and measurements. Transforms and measurements accept `Points`, a `Curve`, a `Form` or a `Set` with a selector and return the same type.

### stage.geo.geometry

**`Curve`** (protocol, implemented by every shape)

| Member | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `closed` |  | `bool` | Whether the curve loops |
| `length()` |  | `Meters` | Arc length |
| `point_at(s)` | arc length (float or array), clamped | `Points` | Point at a distance along it |
| `point_at_u(u)` | 0..1 | `Points` | Point at a fraction along it |
| `tangent_at(s)` / `normal_at(s)` |  | unit `Points` | Direction and left normal |
| `project(p)` | `Point` | `Meters` | Arc length of the closest point |
| `substring(s0, s1)` |  | `Curve` | A piece (reversed if s0 > s1) |
| `reversed()` |  | `Curve` | Opposite direction |
| `to_polyline(tol=0.01)` | chord error | `Points` | Approximate as straight pieces |
| `to_shapely()` |  | `LineString` / `LinearRing` | Interop |
| `construction()` |  | `dict` | JSON recipe stored on forms |

| Shape class | Fields | Represents |
| --- | --- | --- |
| `Segment` | `a, b: Point` | Straight line |
| `Polyline` | `points: Points, closed: bool` | Connected straight pieces |
| `ArcCurve` | `center: Point, radius: Meters, start_deg: Degrees, sweep_deg: Degrees` | Circular arc (sign of sweep = direction) |
| `CircleCurve` | `center, radius, start_deg` | Circle |
| `EllipseCurve` | `center, rx, ry, rotation_deg` | Oval |
| `SplineCurve` | `points: Points, alpha=0.5, closed: bool` | Smooth curve through points (centripetal Catmull–Rom) |
| `BezierCurve` | `control: Points` | Handle-drawn curve |
| `SvgCurve` | `d: str, transform: Affine2D` | Vector-art path |
| `CompositeCurve` | `parts: tuple[Curve, ...]` | Joined curves |
| `SpiralCurve` | `center, r0, r1, turns, start_deg` | Spiral |

| Constructor | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `line(a, b)` | two `Point` | `Segment` |  |
| `polyline(*pts, closed=False)` | points | `Polyline` |  |
| `arc(center, radius, start_deg, sweep_deg)` |  | `ArcCurve` |  |
| `arc_through(a, m, b)` | three points | `ArcCurve` | Raises on collinear points |
| `arc_from_chord(a, b, bulge)` | ends, sagitta in meters | `ArcCurve` | "Bow this line by 2 m" |
| `circle(center, radius, start_deg=0)` |  | `CircleCurve` |  |
| `ellipse(center, rx, ry, rotation_deg=0)` |  | `EllipseCurve` |  |
| `curve_through(*pts, closed=False, alpha=0.5)` |  | `SplineCurve` | Smooth curve through control points |
| `bezier(*control)` |  | `BezierCurve` |  |
| `spiral(center, r0, r1, turns, start_deg=0)` |  | `SpiralCurve` |  |
| `rect(center, w, d, rotation_deg=0)` |  | closed `Polyline` | Rectangle outline |
| `wedge(apex, length, spread_deg, facing_deg=0)` |  | `Polyline` | V or arrow |
| `regular_polygon(center, radius, n, rotation_deg=0)` |  | closed `Polyline` |  |
| `star(center, r_outer, r_inner, n, rotation_deg=0)` |  | closed `Polyline` |  |
| `svg_outline(svg, fit, path_index=None, keep_aspect=True, flip_y=True)` | file or path string, target `Box2` | `list[SvgCurve]` | Pictures and logos |
| `text_outline(text, font, height, at)` |  | `list[Curve]` | Letters |
| `join(*curves, bridge=False)` |  | `CompositeCurve` | Raises if ends do not meet unless bridged |
| `offset(curve, distance, side="left")` |  | `Curve` | Parallel curve |
| `region(outline)` | `Curve` or `Points` | `Polygon` | Area; reports self-intersections |
| `buffer(region, d)`, `union(*r)`, `difference(a, b)`, `intersection(a, b)` |  | `Polygon` | Area operations |

### stage.geo.spacing

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `space_along(curve, n, start=0, end=None, endpoints=True)` |  | `Points` (n, 2) | Even spots by arc length |
| `space_by_interval(curve, interval, start=0, n=None, align="start")` |  | `Points` | Fixed spacing; raises with max that fits |
| `space_proportional(curves, n)` |  | `list[Points]` | n spots across curves at one interval |
| `space_with_gaps(curve, n, gaps)` | gaps as `[(index, extra_m)]` | `Points` | Extra space between sub-groups |
| `fill(region, interval, pattern="grid", angle_deg=0, anchor=None, n=None, margin=0)` | `grid`, `offset`, `hex` | `Points` | Fill an area |
| `block(origin, rows, cols, interval, facing=0, anchor="center")` |  | `Points` | Rectangular block, front row first |
| `ranks_and_files(points, rows, cols)` |  | `list[list[int]]` | Row and column index structure |
| `concentric(center, radii, counts, start_deg=0)` |  | `list[Points]` | Rings |
| `scatter(region, n, min_gap, seed=0)` |  | `Points` | Organic scatter (Poisson disk) |
| `distribute_on_points(candidates, n, method="farthest", seed=0)` |  | `Points` | Pick well-spread spots |

### stage.geo.transforms

| Function | Inputs | Purpose |
| --- | --- | --- |
| `shift(obj, dx, dy)` | meters | Translate |
| `move_to(obj, anchor, to)` | `center`, `centroid`, `front-left`, ...; `Point` | Move an anchor to a spot |
| `rotate(obj, deg, about="center")` |  | Rotate; facings follow |
| `scale(obj, sx, sy=None, about="center")` |  | Resize (negative raises; use `mirror`) |
| `expand(obj, d, about="center")` / `contract(obj, d)` |  | Radial grow or shrink |
| `set_interval(obj, interval, anchor="center")` |  | Re-space along its own curve |
| `mirror(obj, axis)` | `"center"`, `"x"`, `"y"`, or two points | Reflect; facings reflect |
| `symmetrize(set, sel, source_side="left", pairs=None)` |  | Copy one side to its mirror partners |
| `skew(obj, deg_x=0, deg_y=0, about="center")` |  | Shear |
| `affine(obj, matrix)` | 2×3 matrix | General transform |
| `snap(obj, precision, mode="round", axes="xy")` |  | Snap to a grid; result carries a `SnapReport` |
| `snap_to_guides(obj, sheet, tol, kinds)` |  | Pull onto blueprint guide lines |
| `align(obj, axis, value)` | `"x"`/`"y"`, number or `mean`/`min`/`max` | Line up |
| `distribute(obj, axis, start, end)` |  | Even out along an axis |
| `interpolate_forms(a, b, u)` | equal-length `Points` | In-between preview |

### stage.geo.measure

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `length(obj)` | `Curve` or `Points` | `Meters` | Length |
| `center(points)` / `centroid(points)` |  | `Point` | Box center or mean |
| `bounds(points)` |  | `Box2` | Extent |
| `interval(points, ordered=True)` |  | `IntervalStats{mean, min, max, std, gaps}` | Spacing quality |
| `distance(a, b)` / `distance_matrix(A, B)` |  | `Meters` / ndarray | Euclidean |
| `neighbors(set, entity, k=2)` |  | `list[(SpotId, Meters)]` | Closest entities |
| `nearest(set, point, k=1)` |  | `list[(SpotId, Meters)]` | Closest to a spot |
| `where(show, entity, t)` | global `Count` | `Point` | Position at any moment (uses engine) |
| `speed(show, entity, tid, window=None)` |  | `SpeedStats{distance, counts, m_per_count, m_per_s, max_instant}` | Movement demand |
| `speeds(show, tid)` |  | DataFrame, hardest first | Ranking |
| `reachable_region(start, counts, max_speed)` |  | `Polygon` | Where an entity can get in time |
| `direction_of_travel(show, entity, tid, tau)` |  | `Degrees \| None` | Travel direction |
| `form_metrics(points)` |  | `FormMetrics{length, intervals, curvature_range, symmetry_error}` | Form quality |
| `symmetry_error(set, sel, axis_x)` |  | `Meters` | Largest mirror mismatch |

### stage.geo.analysis (design feedback, not rules)

Measures that help the agent and designer judge whether a picture or transition is interesting, not just possible. Nothing here passes or fails; results are numbers and heat maps to inform design.

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `density_map(set, cell=1.0)` |  | grid array | Where the stage is crowded or empty |
| `coverage(set, sheet)` |  | `float` 0..1 | Share of the usable stage in play |
| `balance(set, axis="x")` |  | `float` −1..1 | Visual weight left versus right, front versus back |
| `symmetry_score(set, axis_x)` |  | `float` 0..1 | How mirrored the picture is |
| `motion_contrast(show, tid)` |  | `MotionStats{moving_share, speed_spread, direction_variety}` | How varied the movement is |
| `change_amount(show, a, b)` | two sets | `float` | How different two pictures are |
| `phrase_profile(show, t0, t1)` |  | DataFrame by phrase | Change, energy and density per musical phrase |
| `energy_curve(show)` |  | series over counts | Motion intensity through the show, to compare with the music |

## stage.choreo (part 1): placements, sets, forms, matching

A set is a snapshot of where every entity is; forms are the stored shapes that generate those positions; matching decides who takes which spot.

### stage.choreo.models

**`ApprovalState`** (enum): `DRAFT`, `PROPOSED`, `APPROVED`.

**`Placement`** — one entity's state at one set.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `entity` | `SpotId` | required | Spot or object |
| `x`, `y` | `Meters` | required | Stage-plane position (objects: footprint origin) |
| `facing` | `Degrees` | 0 | Body direction or object rotation |
| `form` | `FormId \| None` | None | Shape it belongs to |
| `order` | `int \| None` | None | Index along the form |
| `pinned` | `bool` | False | Designer lock |
| `note` | `str \| None` | None | Printed on sheets |
| `on_field` | `bool` | True | False = off the field (before its entrance or after its exit); not drawn and not checked for collisions |

**`Set`** — a snapshot of the stage at one moment.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `SetId` |  |
| `counts` | `float` | Counts since the previous set (0 for the first) |
| `placements` | `tuple[Placement, ...]` | One per active spot, plus objects on stage |
| `forms` | `tuple[FormId, ...]` | Forms present |
| `label`, `cue` | `str \| None` | Designer label, rehearsal mark or cue |
| `source_hash` | `str` | Hash of the script that built it |
| `state` | `ApprovalState` | Collaboration state |

| Method | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `table()` |  | DataFrame (`entity, kind, x, y, facing, form, order, pose, pinned`) | Readable table |
| `positions(sel="all")` |  | `Points` | Coordinates in selection order |
| `get(entity)` |  | `Placement` | One row |
| `with_placements(updates, force=False)` | `{id: Placement or dict}` | `Set` | Edit; raises `LockedError` on pinned rows |

**Spot counts.** `spot_counts(show) → list[SpotCount]`, where `SpotCount{set_id, on_field: int, off_field: int, changed: bool}`, feeds the set strip and warns when the number of spots on the field changes unexpectedly between sets.

### stage.choreo.forms

| Data class | Fields | Represents |
| --- | --- | --- |
| `SpacingSpec` | `mode: "count" \| "interval" \| "proportional", interval: Meters \| None, start: Meters, end: Meters \| None, align: str, gaps: list[tuple[int, Meters]]` | How spots are placed on the shape |
| `FacingSpec` | `mode: "fixed" \| "audience" \| "toward_point" \| "along" \| "normal" \| "outward", value: Degrees \| Point \| None` | How members face |
| `Form` | `id: FormId, set: SetId, kind: str, geometry: dict, members: tuple[SpotId, ...], spacing: SpacingSpec, facing: FacingSpec, pose: str \| None, locked: bool, meta: dict` | A stored shape plus ordered members; `kind` ∈ line, arc, curve, circle, block, wedge, polyline, svg, fill, scatter, custom |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `line_form(id, members, a, b, spacing=None, facing=None)` |  | `Form` | Line |
| `arc_form(id, members, center, radius, start_deg, sweep_deg, ...)` |  | `Form` | Arc |
| `curve_form(id, members, *pts, closed=False, ...)` |  | `Form` | Smooth curve |
| `circle_form(id, members, center, radius, ...)` |  | `Form` | Circle (default facing outward) |
| `block_form(id, members, origin, rows, cols, interval, ...)` |  | `Form` | Block |
| `wedge_form(id, members, apex, length, spread_deg, ...)` |  | `Form` | Wedge |
| `polyline_form(id, members, *pts, closed=False, ...)` |  | `Form` | Corners |
| `svg_form(id, members, svg, fit, ...)` |  | `Form` | Picture outline |
| `fill_form(id, members, outline, interval, pattern="grid")` |  | `Form` | Filled area |
| `scatter_form(id, members, region, min_gap, seed=0)` |  | `Form` | Scatter |
| `custom_form(id, members, points, facings=None)` |  | `Form` | Frozen hand-placed spots |
| `placements(form)` |  | `list[Placement]` | Regenerate positions from construction |
| `slots(form)` |  | `Points` | Spots before members are assigned |
| `edit(form, force=False, **changes)` | construction fields | `Form` | Change the shape; respects locks |
| `reorder(form, order, prev_set=None)` | list, `"reverse"`, `"by_x"`, `"by_y"`, `"nearest_prev"` | `Form` | Who sits where along it |
| `place(show, set_id, forms, match_options=None)` |  | `Set` | Build a set from forms, matching unassigned slots |
| `split_form(form, at)` / `merge_forms(a, b, id)` |  | `(Form, Form)` / `Form` | Regroup |
| `morph_targets(a, b)` | two forms, same members | `Points` | Targets for one form turning into another |
| `gate(form, hinge, deg)` | `"start"`, `"end"` or index | `Form` | Rotate about one end |
| `pinwheel(forms, center, deg)` |  | `list[Form]` | Rotate spokes together |
| `templates(pack=None)` |  | `dict[str, Callable]` | Prefab shapes |
| `detect_form(points, tol=0.1)` |  | `Form` | Fit a line, arc or circle to imported spots |
| `rebuild(show, form_id)` |  | `Show` | Regenerate after an edit and invalidate neighboring transitions |

**Editable custom shapes.** A form can be generated by a registered function instead of a built-in shape, and stays editable by changing its parameters. Form `kind` gains `generated`; its `geometry` holds `{fn: qualname, params: dict}`.

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `register_form_fn(name, fn)` | `fn(params, n) → Points` (pure, typed, documented) | `None` | Add a shape generator (agent drafts go through review first) |
| `generated_form(id, members, fn, spacing=None, facing=None, **params)` | registered name, shape parameters | `Form` | A custom shape that keeps its recipe |
| `edit_params(form, **params)` |  | `Form` | Tweak a custom shape, e.g. `edit_params(f, turns=3)` |
| `form_fns()` |  | `list[ApiEntry]` | Available shape generators |

### stage.choreo.match

**`MatchOptions`**

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `keep_groups` | `bool` | True | Entities only take slots for their group |
| `group_slots` | `Mapping[str, Sequence[int]] \| None` | None | Explicit slot ranges per group |
| `pinned` | `Mapping[SpotId, int]` | {} | Forced assignments; designer pins added automatically |
| `forbid` | `Sequence[tuple[SpotId, int]]` | () | Disallowed pairs |
| `no_backward` | `bool \| float` | False | Forbid, or penalize by meters, travel against facing |
| `max_speed` | `float \| None` | None | Forbid moves faster than this (m/count) |
| `objective` | `"sum" \| "sum_squares" \| "bottleneck" \| "bottleneck_then_sum"` | `bottleneck_then_sum` | Minimize largest move, then total |
| `power` | `float` | 1.0 | Cost exponent for `sum` |
| `preserve_order` | `bool` | False | Keep order along ordered forms |
| `weights` | `Mapping[str, float]` | {} | Penalties: crossing, backward, turn, group\_spread |
| `cost_fn` | callable \| None | None | Custom added cost |
| `counts` | `float \| None` | transition counts | For speed limits |

**`MatchResult`** — `assignment: dict[SpotId, int]`, `targets: Points`, `total_distance: Meters`, `max_distance: Meters`, `max_speed: float`, `crossings: int`, `unassigned_slots: list[int]`, `explain: list[str]`.

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `match(from_, to_slots, entities=None, options=MatchOptions())` | `Set` or `Points`; `Points` or forms | `MatchResult` | Who goes where (SciPy assignment; threshold search for bottleneck); deterministic |
| `apply_match(show, tid, result)` |  | `Show` | Write targets into the next set and reset those path rows |
| `cost_matrix(from_pts, to_pts, options)` |  | ndarray | Inspect costs |
| `bottleneck_value(cost)` |  | `float` | Smallest possible largest cost |
| `match_by_order(ordered_sel, form)` |  | `MatchResult` | i-th entity to i-th slot |
| `swap(show, set_id, a, b)` |  | `Show` | Manual swap keeping paths valid |
| `suggest_swaps(show, tid, k=5)` |  | `list[(a, b, gain)]` | Swaps that reduce the largest move or crossings |

## stage.choreo (part 2): paths, engine, avoidance

Path rows are instructions for how each entity travels between two sets; the path engine is the single piece of code that turns them into exact positions, facings and states at any moment, used by the canvas, checker, preview and exports.

### stage.choreo.paths — models

**Enums**: `PathStyle` = `STRAIGHT`, `HOLD`, `CURVE`, `POLYLINE`, `FOLLOW`, `ROTATE`, `CUSTOM` (registered motion function). `FacingMode` = `FIXED`, `TO_TARGET`, `TRAVEL`, `AGAINST_TRAVEL`, `TOWARD_POINT`, `TOWARD_ENTITY`, `AUDIENCE`, `INTERPOLATE`. `TurnDirection` = `SHORTEST`, `CLOCKWISE`, `COUNTERCLOCKWISE`.

| Data class | Fields | Represents |
| --- | --- | --- |
| `Waypoint` | `x: Meters, y: Meters, at: float \| None` | A point a path passes through; `at` = local count, `None` = constant speed |
| `PathRow` | `entity: SpotId, style: PathStyle = STRAIGHT, start: float = 0, end: float \| None = None (counts), waypoints: tuple[Waypoint, ...] = (), route: RouteId \| None, pivot: Point \| None, sweep_deg: Degrees \| None, facing_mode: FacingMode = TO_TARGET, facing_value: Degrees \| Point \| SpotId \| None, turn_counts: float = 0, turn_direction: TurnDirection = SHORTEST, pace: float = 1.0, locked: bool = False, note: str \| None` | One entity's instruction for one transition |
| `Route` | `id: RouteId, curve: dict, members: tuple[SpotId, ...] (leader first), offsets: tuple[Meters, ...], travel: Meters, start: float, end: float` | Shared path for follow-the-leader |
| `TransitionSpec` | `id: TransitionId, counts: float, rows: Mapping[SpotId, PathRow] (sparse; missing = straight full counts), routes: Mapping[RouteId, Route], notes: str, source_hash: str` | All instructions for one transition |

### stage.choreo.paths — editing functions (pure; return `TransitionSpec` unless noted)

| Function | Inputs | Purpose |
| --- | --- | --- |
| `default_paths(show, tid)` |  | Straight over full counts for everyone |
| `set_style(spec, sel, style, **params)` | style-specific params | Change path style; raises `PathError` if params missing |
| `delay(spec, sel, counts, keep_end=True)` |  | Start later (faster move) |
| `hold(spec, sel, until=None, from_=None)` | local counts | Hold for part or all of the transition |
| `arrive_early(spec, sel, counts)` |  | Finish early |
| `window(spec, sel, start, end)` |  | Set both ends |
| `add_waypoint(spec, entity, point, at=None, index=None)` |  | Bend the path |
| `remove_waypoint(spec, entity, index)` / `clear_waypoints(spec, sel)` |  | Undo bends |
| `curve_path(spec, entity, *control)` |  | Smooth path through control points |
| `two_part(spec, sel, corner, split=None)` | `"x_first"`, `"y_first"` or `Point` | L-shaped moves |
| `make_route(show, tid, leader, waypoints, members=None, spacing="keep", interval=None)` |  | → `Route` for follow-the-leader |
| `follow(spec, route, start=0, end=None)` |  | Assign FOLLOW rows |
| `follow_and_set_targets(show, tid, route)` |  | → `Show`; writes followers' end positions into the next set |
| `counter_march(show, tid, sel, turn_at, counts_per_turn=2)` |  | → `Show`; line turns back on itself |
| `rotate_path(spec, sel, pivot, deg)` | `Point` or `"center"` | Rotation paths about a pivot |
| `stagger(spec, ordered_sel, step=1.0, direction="start", keep_end=True)` |  | Staggered starts |
| `ripple(spec, ordered_sel, total, curve="linear")` |  | Spread starts over a total |
| `set_facing(spec, sel, mode, value=None, turn_counts=0, direction=SHORTEST)` |  | Facing while moving |
| `set_pace(spec, sel, pace)` |  | Double or half time |
| `lock(spec, sel)` / `unlock(spec, sel)` |  | Designer locks |
| `save_paths(ws, spec)` |  | → revision `str`; triggers checks through the tool hook |
| `load_paths(ws, tid)` |  | → `TransitionSpec` |
| `paths_table(spec)` |  | → DataFrame of non-default rows |

### stage.choreo.paths — custom motion, timing and height

Two additions free movement from the built-in list: custom path styles and non-even timing. `PathRow` gains these fields:

| New `PathRow` field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `motion_fn` | `str \| None` | None | Registered motion function for `CUSTOM` style |
| `motion_params` | `dict` | {} | Its parameters, e.g. `{"turns": 2}` |
| `easing` | `Easing` | linear | How speed changes across the window |

| Data class | Fields | Represents |
| --- | --- | --- |
| `Easing` | `kind: "linear" \| "ease_in" \| "ease_out" \| "ease_in_out" \| "hold_then_burst" \| "burst_then_settle" \| "steps" \| "custom", strength: float, points: tuple[tuple[float, float], ...] (custom curve: time share → distance share)` | How progress through a move maps to time |
| `MotionFn` (protocol) | `__call__(u: ndarray, start: Point, end: Point, params: dict) → Points` | Pure custom motion; must return `start` at u=0 and `end` at u=1 |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `register_motion(name, fn)` | `MotionFn` | `None` | Add a movement style (agent drafts reviewed first) |
| `motions()` |  | `list[ApiEntry]` | Available custom styles, e.g. weave, spiral, figure-eight, zigzag |
| `custom_path(spec, sel, motion, **params)` | registered name | `TransitionSpec` | Use a custom style |
| `set_easing(spec, sel, easing)` | `Easing` or kind name | `TransitionSpec` | Change pacing within the move |

Engine rules for these: progress u passes through the easing curve before positions are computed; `CUSTOM` styles are sampled by the engine into straight segments (chord error ≤ tol), so the checker works unchanged. Height (jumps, lifts, flight) belongs to the 3D layer.

### stage.choreo.engine

| Data class | Fields | Represents |
| --- | --- | --- |
| `MotionPiece` | `t0: float, t1: float, kind: "hold" \| "line" \| "arc" \| "curve" \| "route" \| "custom", params: dict, length: Meters` | One piece of motion in local counts |
| `MotionPlan` | `entity: SpotId, pieces: tuple[MotionPiece, ...], start: Point, end: Point` | Full motion of one entity in a transition |
| `CompiledTransition` | `id: TransitionId, counts: float, t_global0: Count, plans: Mapping[SpotId, MotionPlan], warnings: list[str]` | Cached compile |
| `MotionSegment` | `entity, tid, t0, t1, p0: Point, p1: Point` | Straight, time-linear piece for exact collision checks |
| `EntityState` | `entity, position: Point, facing: Degrees, velocity: Point (m/count), moving: bool, on_field: bool` | Everything about an entity at one moment |
| `Frame` | `t: Count, tid: TransitionId \| None, tau: float, states: tuple[EntityState, ...]` | Everyone at one moment |

**`PathEngine`**

| Method | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `PathEngine(show, curve_tol=0.01, cache=True)` |  | engine | Create for a show |
| `compile(tid)` |  | `CompiledTransition` | Build motion plans; raises `ContinuityError` if a plan misses its target |
| `compile_all()` |  | `dict[TransitionId, CompiledTransition]` | Whole show |
| `position(entity, t, local=None)` | global count, or local with `local=tid` | `Point` | Where an entity is |
| `positions(t, sel="all")` |  | `Points` | Everyone, vectorized |
| `facing_at(entity, t)` |  | `Degrees` | Facing |
| `velocity_at(entity, t)` |  | `Point` | Meters per count |
| `state_at(entity, t)` |  | `EntityState` | Full state for preview |
| `sample(t)` |  | `Frame` | One moment |
| `frames(t0, t1, per_count=8, fps=None)` |  | iterator of `Frame` | Animation and checks |
| `segments(tid, tol=0.02)` |  | `list[MotionSegment]` | Input for the checker |
| `speed(entity, tid, window=None)` |  | `SpeedStats` | Movement demand |
| `path_geometry(entity, tid)` |  | `Points` | Drawn path for the canvas |
| `invalidate(sets=(), transitions=())` |  | `None` | Clear caches after edits |

Compile rules: progress u = (tau − start)/(end − start), clamped, then passed through the row's easing; position by arc length (constant speed) with sub-windows for timed waypoints; centripetal Catmull–Rom for CURVE; arc about pivot for ROTATE; route offset for FOLLOW; registered motion function for CUSTOM; curves split into segments with chord error ≤ tol.

### stage.choreo.avoid (advisory; the locked checker decides)

| Data class / function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `GapInfo` | `a, b: SpotId, tau: float, gap: Meters` |  | A tight pair |
| `Fix` | `kind: "delay" \| "waypoint" \| "reassign" \| "stagger", params: dict, new_gap: Meters, max_speed_after: float` |  | A suggested change |
| `min_gaps(show, tid, sel="all", top=10)` |  | `list[GapInfo]` | Quick preview inside scripts |
| `suggest_delays(show, tid, pair, max_delay=4)` |  | `list[Fix]` | Timing fixes |
| `suggest_waypoint(show, tid, entity, around, clearance=0.8)` |  | `list[Fix]` | Path bends |
| `suggest_reassignment(show, tid, sel)` |  | `MatchResult` | Re-match with crossing penalty |
| `separate_in_time(spec, ordered_sel, gap_counts=1)` |  | `TransitionSpec` | Automatic stagger |
| `apply_fix(spec, fix)` |  | `TransitionSpec` | Apply a suggestion |

## stage.choreo (part 3): facing

Facing tools for sets and time ranges. Actions, moves and objects over time belong to the 3D layer (Stage Library: 3D Layer Reference).

**`FacingOverride`** — `entities: Selector, mode: FacingMode, value, start: Count, end: Count, turn_counts: float`. Facing for a time range regardless of paths.

### stage.choreo.facing

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `face_audience(target, sel)` | `Set` or time range on a show | same type | Face 0° |
| `face_point(target, sel, point)` |  | same | Face a spot (a soloist, a conductor) |
| `face_entity(target, sel, entity)` |  | same | Track another entity over time |
| `face_direction(target, sel, deg)` |  | same | Fixed facing |
| `face_along(set, form_id, reverse=False)` |  | `Set` | Follow the form's line |
| `face_normal(set, form_id, outward=True)` |  | `Set` | Perpendicular to the form |
| `add_facing_override(show, override)` | `FacingOverride` | `Show` | Facing over a time range |
| `turn(start, end, u, direction=SHORTEST)` | `Degrees`, `Degrees`, 0..1 | `Degrees` | Interpolate facing |

## stage.show

The show as a living document: the aggregate model, set management, script builds, storage exposed to the agent as files, live collaboration, history, structural validation and printouts.

### stage.show.models

**`Show`**

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `title` | `str` |  |
| `pack` | `str` | Domain pack key |
| `sheet` | `Sheet` | The 2D design surface (template plus customizations) |
| `coordinates` | `CoordinateSystem` | How positions are labeled and worded; display only |
| `notes` | `tuple[Note, ...]` | Design notes on sets, spots and groups (stage.notes) |
| `cast` | `Cast` | Spots, objects, groups |
| `timeline` | `Timeline` | Show clock |
| `sets` | `tuple[Set, ...]` | In order |
| `forms` | `Mapping[FormId, Form]` | Shapes |
| `transitions` | `Mapping[TransitionId, TransitionSpec]` | Path instructions |
| `facing_overrides` | tuples | Timed poses and facings |
| `zones` | `tuple[Zone, ...]` | Show-specific zones |
| `dependencies` | `Mapping[str, str]` | Every sheet ref pinned to a version |
| `revision` | `str` | Storage revision |

Derived members: `index → ShowIndex`, `set(id) → Set`, `transition(tid) → TransitionSpec`, `entities(sel) → Selection`.

**`ShowIndexEntry`** — `set: SetId, counts: float, start_count: Count, measure: MeasureLabel | None, seconds: Seconds, cue: str \| None`. **`ShowIndex`** — `entries` plus `set_at(count) → SetId`, `transition_at(count) → (TransitionId, tau)`, `next(set)`, `prev(set)`, `total_counts`.

### stage.show.manage

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `new_show(title, pack, sheet, cast, timeline)` |  | `Show` | Create; pins dependency versions; empty first set |
| `add_set(show, id, counts, after=None, placements=None)` |  | `Show` | Insert; missing placements copy the previous set |
| `insert_subset(show, after, counts)` |  | `(Show, SetId)` | Add "12A", keeping total time |
| `remove_set(show, id, merge_counts=True)` |  | `Show` | Delete |
| `set_counts(show, id, counts, rescale=False)` |  | `Show` | Retime |
| `renumber(show, scheme="sequential")` |  | `(Show, dict)` | New set numbers plus mapping |
| `transitions(show)` |  | `list[TransitionId]` | Consecutive pairs |
| `index(show)` |  | `ShowIndex` | Counts, measures, seconds |
| `set_at_count(show, c)` / `transition_at_count(show, c)` |  | `SetId` / `(TransitionId, tau)` | Time lookups |
| `split_section(show, at)` / `join_sections(a, b)` |  | `(Show, Show)` / `Show` | Movements or scenes |
| `upgrade_dependency(show, ref, version)` |  | `Show` | Adopt a newer sheet version |
| `replace_sheet(show, sheet, mapping="landmarks")` | `SheetRef` | `(Show, SheetReplaceReport)` | Swap the design sheet (see stage.sheet) |

### stage.show.build

| Item | Inputs / fields | Output | Purpose |
| --- | --- | --- | --- |
| `SetScript` | `set_id, path, source, hash, depends_on` |  | A set's source |
| `BuildContext` | `show (read-only), prev: Set \| None, set_id, counts, surface, timeline, cast, select, engine, log` |  | Passed to a script's `build(ctx) → Set \| list[Form]` |
| `BuildResult` | `set_id, set: Set, logs, warnings, duration_ms, stale_dependents` |  | Build outcome |
| `ScriptResult` | `stdout, result, duration_ms` |  | Analysis script outcome |
| `build_set(ws, set_id)` |  | `BuildResult` | Run in sandbox, validate, store |
| `build_all(ws, from_set=None)` |  | `list[BuildResult]` | Rebuild in dependency order |
| `stale(ws)` |  | `list[SetId]` | Sets whose script or inputs changed |
| `new_set_script(ws, set_id, template="copy_prev")` | `hold`, `from_forms`, `copy_prev`, `blank` | path `str` | Start a script |
| `run_script(ws, path, timeout_s=60)` |  | `ScriptResult` | Agent analysis scripts |

### stage.show.storage

| Item | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `Repository` (protocol) | `get_show, list_sets, get_set/put_set, get_script/put_script, get_paths/put_paths, get_forms/put_forms, get_actions/put_actions, get_timeline, get_limits, revisions, transaction`; writes take `expected_revision` |  | Database abstraction |
| `AssetStore` (protocol) | `put(bytes) → uri`, `get(uri) → bytes` |  | Binary storage for captured videos, move clips and audio |
| `Revision` | `id, parent, at, actor, message, changed` |  | One saved state |
| `open_workspace(repo, show_id, principal)` | `"agent"`, `"designer"`, `"system"` | `Workspace` | File view of the show |
| `Workspace.read(path)` / `write(path, data, expected_revision=None)` / `ls(path)` / `revision` |  | `str \| bytes` / revision / `list[str]` | Agent file access: `/show/show.json`, `/show/index.csv`, /show/sheet.json, /show/notes.json, /publish/claims.json, `/time/timeline.json`, `/sets/<id>/set.py`, `/sets/<id>/placements.csv` (read-only), `/sets/<id>/forms.json`, `/transitions/<a>-<b>/paths.json`, `/actions/actions.json`, `/config/limits.yaml` (designer-owned), `/renders/`, `/exports/`, `/lib/drafts/` |
| `load_show(ws)` / `save_show(ws, show)` |  | `Show` / revision | Whole-show access; `ConflictError` lists the designer's changes |

### stage.show.collab

| Item | Inputs / fields | Output | Purpose |
| --- | --- | --- | --- |
| `CanvasEvent` | `id, at, actor: "designer" \| "agent", kind: move \| edit_form \| pin \| unpin \| edit_paths \| add_action \| comment \| set_counts, payload, revision` |  | One edit from either side |
| `Pin` | `target: "entity" \| "form" \| "set" \| "transition", id, by, reason` |  | A designer lock |
| `Comment` | `id, anchor: Point \| SpotId \| SetId, text, resolved` |  | A note on the canvas |
| `Proposal` | `id, base_revision, change: Show diff, note, state` |  | An agent suggestion |
| `apply_event(show, event)` |  | `Show` | Replay an edit as a model operation |
| `events_since(ws, revision)` |  | `list[CanvasEvent]` | What changed |
| `summarize_events(events)` |  | `str` | Compact text for the agent |
| `pin(show, target, id, reason)` / `unpin(...)` / `pins(show)` |  | `Show` / `list[Pin]` | Locks |
| `propose(show, change, note)` |  | `Proposal` | Suggest instead of overwrite |
| `preview_proposal(proposal)` |  | ghost placements and paths | Show suggestion on the canvas |
| `accept(proposal)` / `reject(proposal, reason)` |  | revision / `None` | Designer decision |
| `highlight(target, ttl_s=30)` | entities, zone or pair | `CanvasEvent` | Point the designer at something |
| `selection(ws)` |  | `Selection` | What the designer has selected |

### stage.show.history

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `diff_sets(a, b, tol=1e-6)` | two `Set` | `SetDiff{moved, facing_changed, added, removed, forms_changed}` | Compare sets |
| `diff_paths(a, b)` | two `TransitionSpec` | `PathDiff{changed_rows, routes_changed}` | Compare paths |
| `diff_actions(a, b)` |  | `ActionDiff{added, removed, changed}` | Compare actions |
| `diff_show(a, b)` |  | `dict` | Whole-show diff |
| `snapshot(ws, message)` / `revert(ws, revision_id)` / `undo(ws)` / `redo(ws)` |  | `Revision` | Versioning |
| `rename_entities(show, mapping)` | old → new IDs | `Show` | Rewrite everywhere after relabeling |
| `changelog(ws, since)` |  | `str` | Readable history |

### stage.show.validate

| Function | Output | Checks |
| --- | --- | --- |
| `validate_set(show, set)` | `ValidationReport` | Every active spot once, known IDs, finite values, facing range, form membership, pinned rows unchanged |
| `validate_paths(show, spec)` | `ValidationReport` | Known entities, windows within counts, style params, routes, continuity |
| `validate_dependencies(show)` | `ValidationReport` | The sheet ref resolves |
| `validate_show(show)` | `ValidationReport` | Everything plus set sequence and timeline |

`ValidationReport{ok: bool, issues: list[ValidationIssue]}`; `ValidationIssue{level: "error" \| "warning", code, where, message, hint}`, printed one line per issue.

### stage.show.export

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `coordinate_sheet(show, entity, precision=None)` |  | `Sheet{entity, rows: list[SheetRow]}`; `SheetRow{set, counts, cumulative, measure (optional), position_text, xy, facing, behavior, crossings, note}` | Individual sheet worded by the pack language |
| `coordinate_sheets(show, sel="all")` |  | `list[Sheet]` | Batch |
| `chart(show, set_id)` |  | `Chart{set_id, rows}` | Designer sheet for one set |
| `cue_sheet(show)` |  | `Table` | Actions, object events and markers in time order |
| `to_pdf(obj, layout="sheet")` | `sheet`, `book`, `chart_pages`, `cue_sheet` | `bytes` | Printable output |
| `to_csv(obj)` / `to_text(obj)` |  | `str` | Data and plain-text sheets |
| `export_json(show, include=...)` / `import_json(d)` |  | `dict` (`stage.show/1`) / `Show` | Native interchange |
| `export_frames_csv(show, per_count=1)` |  | `str` | Positions per count for any tool |
| `import_positions(file, mapping, pack=None)` | CSV or coordinate text | `Show` | Bring in existing shows |
| `register_exporter(name, fn)` / `register_importer(name, fn)` / `formats()` |  |  | Format plugins |

Print and data exports are generated per spot and show the performer's name once the spot is claimed. Print is optional; publishing is the main way a show leaves the app.

### Publish (default export)

A live link and QR code. Performers claim one spot each and get their own view of the show; when the designer changes the show, the view updates and affected performers are notified.

| Data class | Fields | Represents |
| --- | --- | --- |
| `Publication` | `id: str, show_id: str, revision: str, link: str, status: "live" \| "paused" \| "closed", published_at: datetime` | A published show |
| `Performer` | `id: str, name: str, contact: str \| None` | A real person who joins through the link |
| `Claim` | `spot: SpotId, performer: Performer, claimed_at: datetime, locked: bool = True` | A performer on a spot |
| `PerformerView` | `performer: Performer, spot: SpotId, sets: list[tuple[SetId, str (position text), float (counts)]], path_animation_url: str` | What a performer sees: their dot on every set and their path |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `publish(show, notify=True)` |  | `Publication` | Publish the current revision; republishing updates the live view |
| `unpublish(pub)` / `pause(pub)` |  | `Publication` | Close or pause the link |
| `qr_code(pub, size=512)` |  | `bytes` (PNG) | Code for performers to scan |
| `claim_spot(pub, performer, spot)` |  | `Claim` | One spot per performer and one performer per spot; only spots marked performer (or crew, for crew members); locked on claim; refuses if taken |
| `claims(pub)` |  | `list[Claim]` | Everyone and their spot |
| `who_is_on(pub, spot)` | `SpotId` | `Performer \| None` | Who holds a spot |
| `unclaimed(pub)` |  | `list[SpotId]` | Claimable spots still empty |
| `swap_claims(pub, a, b)` | two `SpotId` | `list[Claim]` | Designer only: swap two performers |
| `reassign(pub, spot, performer)` |  | `Claim` | Designer only: move a performer to a spot |
| `release(pub, spot)` |  | `None` | Designer only: free a spot |
| `performer_view(pub, performer)` |  | `PerformerView` | A performer's own part |
| `notify_changes(pub, old_rev, new_rev)` |  | `list[tuple[Performer, str]]` | Tell performers whose spots changed, with a short summary |

### Video

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `export_video(show, range=None, fps=30, audio=True)` | count range | `bytes` (MP4) | Animated 2D sheet with music, for rehearsal or sharing (3D video comes with the 3D layer) |

## stagecheck (locked)

A separate package run in its own process. The agent cannot import or edit it; it is reached only through the `run_checks` tool and hooks. It reads the show through the `Repository` and computes motion with the same `PathEngine`.

### stagecheck.models

**Enums**: `Severity` = `ERROR`, `WARNING`, `INFO`. `CheckName` = `COLLISIONS`, `NEAR_MISSES`, `SPEED`, `TURN_RATE`, `CONTINUITY`, `INTERVAL`, `BOUNDS`, `ZONES`, `FACING` (object, behavior, contact and sightline checks are added by the 3D layer).

**`LimitsConfig`** (`/config/limits.yaml`; designer-owned, pack defaults)

| Field | Type | Meaning |
| --- | --- | --- |
| `collision_gap` | `Meters` | Gap after footprints below which two entities collide |
| `near_miss_gap` | `Meters` | Warning threshold |
| `radius_by_group` | `Mapping[str, Meters]` | Extra footprint by group |
| `max_speed` | `list[tuple[CountsPerMinute, float]]` or m/s | Tempo-dependent speed limit (m/count) |
| `max_speed_backward_pct`, `max_speed_slide_pct` | `float` | Direction-dependent limits |
| `reference_height_m` | `float` | Height the limits are written for |
| `max_turn_rate` | `Degrees` per count | Facing change limit |
| `interval_tolerance`, `min_interval` | `Meters` | Form spacing rules |
| `bounds` | `"surface"`, `"surface+margins"`, `Polygon` | Allowed area |
| `zones` | `list[Zone]` | Extra restricted areas |
| `facing_rules` | `list[FacingRule{at: SetId \| Count, entities: Selector, expect: Degrees \| str, tol: Degrees}]` | Required facings |
| `checks_enabled` | `list[CheckName]` | Active checks |
| `severity` | `Mapping[CheckName, Severity]` | Error versus warning |
| `group_overrides` | `Mapping[str, dict]` | Per-group values for any limit above, e.g. looser steps for the guard; taken from each Group's limits |

**`Finding`** — `check: CheckName, severity: Severity, tid: TransitionId \| None, set_id: SetId \| None, entities: tuple[SpotId, ...], at: float \| None, value: float \| None, limit: float \| None, message: str` (rendered as one line). **`CheckReport`** — `findings: list[Finding], summary: dict[CheckName, int], scope: str, revision: str`.

**Shapes in checks.** Collisions use each spot's shape: circle to circle, circle to rectangle and rectangle to rectangle, with rectangles rotated by the spot's facing at that moment.

### stagecheck functions

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `run(repo, show_id, scope="all", checks=None, limits=None)` | `all`, `set:<id>`, `transition:<a>-><b>`, `changed` | `CheckReport` | Entry point (called by the tool and hooks) |
| `check_collisions(engine, tid, limits)` |  | `list[Finding]` | Grid broadphase, then exact closest approach per segment pair: t\* = clamp(−(d·u)/(u·u)); constant gap when u·u ≈ 0 |
| `check_near_misses(...)` |  | `list[Finding]` | Same, between the two thresholds |
| `check_speed(engine, tid, show, limits)` |  | `list[Finding]` | Speed versus tempo and direction limits |
| `check_turn_rate(engine, tid, limits)` |  | `list[Finding]` | Facing change per count |
| `check_continuity(engine, tid)` |  | `list[Finding]` | Paths end on placements |
| `check_interval(show, set_id, limits)` |  | `list[Finding]` | Spacing within forms |
| `check_bounds(engine, show, tid, limits)` |  | `list[Finding]` | Inside the allowed area |
| `check_zones(engine, show, tid, limits)` |  | `list[Finding]` | No active zone violations |
| `check_facing(show, engine, limits)` |  | `list[Finding]` | Facing rules |
| `format_report(report, max_lines=50)` |  | `str` | Sorted lines plus `SUMMARY`, e.g. `COLLISION 12→13: T4 & M2, count 6.4, gap 0.12 m` |
| `register_check(name, fn, reviewed_by)` |  | `None` | Packs add reviewed checks |

### stagecheck.waivers (breaking rules on purpose)

A waiver records that the designer deliberately allows something the checker would flag, such as a tight weave or a crowded moment. Only the designer can create one; the agent may request it.

| Data class | Fields | Represents |
| --- | --- | --- |
| `Waiver` | `id: str, check: CheckName, entities: Selector, scope: SetId \| TransitionId, window: tuple[float, float] \| None (local counts), limit_override: float \| None, reason: str, approved_by: str, created_at: datetime, expires_on_change: bool` | An approved exception, optionally with a relaxed limit instead of a full skip |
| `WaiverRequest` | `check, entities, scope, window, limit_override, reason, requested_by` | The agent asking for an exception |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `request_waiver(ws, request)` | `WaiverRequest` | request id | Agent proposes; shows on the canvas for the designer |
| `approve_waiver(ws, request_id, approver)` / `deny_waiver(ws, request_id, reason)` |  | `Waiver` / `None` | Designer decision (designer principal only) |
| `list_waivers(ws, scope=None)` |  | `list[Waiver]` | Current exceptions |
| `revoke_waiver(ws, id)` |  | `None` | Remove an exception |

Waived findings are reported as `WAIVED` lines with the reason instead of errors, never silently hidden. If `expires_on_change` is true, editing the waived transition voids the waiver. Contacts from `stage.choreo.contact` are honored automatically and need no waiver. Waivers live in `/config/waivers.yaml`, which the agent can read but not write.

### Tool and hook contract

| Interface | Input | Output / effect |
| --- | --- | --- |
| `run_checks` tool | `{scope, checks?, max_lines?}` | Formatted report text; never stack traces |
| After-save hook | writes to sets, transitions, actions | Runs `scope="changed"`, appends the summary to the tool result |
| Before-tool hook | any write or script | Denies writes to `limits.yaml` and pinned items, and scripts that import `stagecheck` |
| Stop hook | agent tries to finish | Blocks while error findings remain in its changes, unless waived by the designer |

## stage.packs

Domain packs add a discipline's units, venues, groups, position language, rules and printouts without changing the core. Marching band is the first pack.

### stage.packs.base

**`PositionLanguage`** (protocol)

| Member | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `describe(point, sheet, style="long", precision=None)` | `Point`, `Sheet` | `str` | Position in the discipline's words |
| `parse(text, sheet)` | `str` | `Point` | Read a position; raises with the failing token |
| `parse_sheet(text, sheet)` | coordinate-sheet text | `list[SheetRow]` | Import printed sheets |
| `styles()` |  | `list[str]` | Supported wordings |

**`DomainPack`**

| Field | Type | Meaning |
| --- | --- | --- |
| `key`, `name`, `version` | `str` | e.g. `marching` |
| `units` | `list[tuple[str, float]]` | Units in meters |
| `grid` | `Grid` | Default blueprint grid |
| `sheet_templates` | `Mapping[str, SheetRef]` | Sheet templates the pack ships (e.g. fields with NFHS, NCAA and NFL hashes) |
| `groups` | `Mapping[str, Group]` | Default groups |
| `motion_thresholds` | `dict` | Angles and speeds for forward, backward, slide, crab, run classification |
| `limits_defaults` | `LimitsConfig` | Starting limits |
| `language` | `PositionLanguage` | Position wording |
| `form_templates` | `Mapping[str, Callable]` | Prefab shapes |
| `sheets` | `Mapping[str, Callable]` | Printouts |
| `skills` | `list[str]` | Agent skill files |
| `extra_checks` | `list[str]` | Reviewed checks registered in `stagecheck` |
| `default_timeline_mode` | `TimelineMode` | Default timing for new shows: counts for marching and dance, cues for theater, seconds for drone shows |

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `register_pack(pack)` | `DomainPack` | `None` | Add a pack |
| `get_pack(key)` / `list_packs()` |  | `DomainPack` / `list[str]` | Lookup |
| `apply_pack(show, key)` |  | `Show` | Set units, limits defaults, language and templates |
| `describe_position(show, point, style="long")` |  | `str` | Pack wording through a show |
| `parse_position(show, text)` |  | `Point` | Pack parsing through a show |

### stage.packs.marching

| Item | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| Unit `step85` |  | 0.5715 m | One 8-to-5 step (22.5 in) |
| `n_to_5(distance_m, counts, pace=1.0)` |  | `float` | Step size in N-to-5 terms (N = 8·counts·pace / distance in steps) |
| `step_inches(distance_m, counts, pace=1.0)` |  | `float` | Stride in inches |
| `step_label(n)` |  | `str`, e.g. `"6-to-5"`, `"hold"` | Readable step size |
| `FieldStandard` (enum) | `NFHS`, `NCAA`, `NFL`, `TEXAS_HS`, `CUSTOM` |  | Hash placement standard |
| `MarchingLanguage` | implements `PositionLanguage` |  | Side 1/2, inside/outside yard lines, in front of/behind sidelines and hashes, quarter-step precision |
| `yard_line_crossings(show, spot, tid)` |  | `list[(yard_line, side, count)]` | For drill books |
| Groups | flute, clarinet, saxes, trumpet, mellophone, baritone, trombone, tuba, snare, tenors, bass, cymbals, front ensemble, guard, drum major |  | Default groups and label prefixes |
| Limits defaults | stride warning above 5-to-5; backward limit as a percentage of forward; height scaling from 5'8" |  | Starting limits for the designer |
| Templates | company front, block, wedge, staggered arcs, gate, pinwheel, follow-the-leader, counter-march |  | Prefabs |
| Sheets | performer coordinate sheet, designer chart, personal drill book |  | Printouts |

Other packs (`theater`, `guard`, `dance`, `cheer`, `parade`) implement the same `DomainPack` interface with their own venues, language and printouts.

## stage.index

How the agent finds and learns the library, and how its own reusable code is reviewed and promoted.

### stage.index.models

**`ApiStatus`** (enum): `DRAFT`, `REVIEWED`, `STABLE`, `DEPRECATED`.

**`ApiEntry`**

| Field | Type | Meaning |
| --- | --- | --- |
| `qualname` | `str` | e.g. `stage.geo.spacing.space_along` |
| `signature` | `str` | Typed signature |
| `summary` | `str` | First docstring line |
| `category` | `str` | Module group |
| `panel` | `str` | Canvas, Export, or all |
| `status` | `ApiStatus` | Lifecycle |
| `since` | `str` | Version added |
| `deprecated` | `str \| None` | Replacement qualname |
| `tags` | `tuple[str, ...]` | Search terms |
| `example` | `str` | Runnable example |

### stage.index functions

| Function | Inputs | Output | Purpose |
| --- | --- | --- | --- |
| `@api(category, panel="all", status=STABLE, since="1.0", tags=())` |  | decorator | Register a function in the index |
| `@deprecated(replacement, since, remove_in)` |  | decorator | Warn and point to the replacement |
| `api_index(category=None, panel=None, status_min=REVIEWED)` |  | `str` | Compact one-line-per-function listing |
| `describe(qualname)` |  | `str` | Full docstring, signature, example, related functions |
| `search_api(query, k=10)` |  | `list[ApiEntry]` | Keyword and tag search |
| `categories()` / `panels()` |  | `list[str]` | Navigation |
| `register_draft(ws, path)` | path in `/lib/drafts` | `list[ApiEntry]` | Load agent code as `[draft]`; must be pure, typed, documented with an example |
| `review_draft(qualname, reviewer, approve, notes)` |  | `ApiEntry` | Doctests and generated tests must pass; a human approves; becomes REVIEWED |
| `promote(qualname, target_module)` | `stage.contrib.<module>` or a pack | `ApiEntry` | Becomes STABLE and indexed; core modules stay maintainer-only |
| `retire(qualname, replacement)` |  | `ApiEntry` | Deprecate with a pointer |
