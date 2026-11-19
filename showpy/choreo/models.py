"""
models.py --- choreography data classes: placements, sets, forms, path rows and transitions

Contains:
    ApprovalState: names the collaboration states of a set
    Placement: holds one spot's state at one set
    Set: holds a snapshot of every spot's placement at one moment
    Set.table(): returns placements as a DataFrame
    Set.positions(): returns coordinates in selection order
    Set.get(): returns one placement
    Set.with_placements(): returns a set with edited placements, respecting pins
    SpotCount: counts spots on and off the field in one set
    SpacingSpec: describes how members are spaced on a form's shape
    FacingSpec: describes how form members face
    MAX_TEXT_FONTS: bounds the font chain of one text recipe
    TextRecipe: holds the construction recipe of a text form: text, font chain and layout
    CrowdedStroke: names a glyph stroke whose spots sit closer than its junction gap
    TextReport: reports how a text form's spots were spread, listing its crowded strokes
    Form: holds a stored shape construction plus its ordered members
    FacingOverride: forces a facing on spots over a global count range
    PathStyle: names the built-in path styles
    FacingMode: names how a spot faces while it moves
    TurnDirection: names which way a facing turn goes
    Waypoint: holds a point a path passes through, optionally at a local count
    Easing: maps progress through a move to progress in time
    PathRow: holds one spot's instructions for one transition
    Route: holds a shared path for follow-the-leader
    TransitionSpec: holds every path instruction for one transition
    TransitionSpec.row(): returns a spot's row, defaulting to straight
    MatchOptions: holds the constraints, objective and penalties for matching spots to slots
    MatchResult: holds who goes where, with move statistics and an explanation
    MatchResult.target_points(): returns targets as an (N, 2) array in entity order
    FormFnInfo: describes one registered shape generator
"""

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

import numpy as np
from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from showpy.core.errors import LockedError, PathError, SchemaError, UnknownRefError
from showpy.core.extras import require_extra
from showpy.core.ids import FormId, RouteId, SetId, SpotId, TransitionId, check_id
from showpy.core.selection import Selection, SelectorField
from showpy.core.types import Count, Degrees, FrozenModel, Meters, Point, Points, PositiveMeters
from showpy.geo.models import DEFAULT_LINE_SPACING, TextAlign, TextAnchor
from showpy.geo.text_layout import MAX_LAYOUT_CHARS

if TYPE_CHECKING:
    import pandas as pd

TABLE_COLUMNS = ("entity", "x", "y", "facing", "form", "order", "pinned", "on_field", "note")
DEFAULT_EASING_STRENGTH = 2.0
MATCH_WEIGHT_KEYS = ("backward", "crossing", "group_spread", "turn")
FONT_URI_PATTERN = r"^asset:sha256:[0-9a-f]{64}$"
# Layout resolves every chain font and measures its cap height, so a stored chain is bounded.
MAX_TEXT_FONTS = 16
EasingKind = Literal[
    "linear", "ease_in", "ease_out", "ease_in_out", "hold_then_burst", "burst_then_settle",
    "steps", "custom",
]  # fmt: skip
type FontUri = Annotated[str, Field(pattern=FONT_URI_PATTERN)]


class ApprovalState(StrEnum):
    """Names the collaboration states of a set."""

    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"


class Placement(FrozenModel):
    """Holds one spot's state at one set.

    Attributes:
        entity: Spot ID.
        x: Meters from the center line, audience-left negative.
        y: Meters upstage from the front edge.
        facing: Degrees, 0 toward the audience, counter-clockwise positive.
        form: Form the spot belongs to in this set.
        order: Index along that form.
        pinned: Designer lock; edits need force=True.
        note: Text printed on sheets.
        on_field: False before an entrance or after an exit; not drawn or collision-checked.
    """

    entity: SpotId
    x: Meters
    y: Meters
    facing: Degrees = 0.0
    form: FormId | None = None
    order: int | None = None
    pinned: bool = False
    note: str | None = None
    on_field: bool = True

    @model_validator(mode="after")
    def _finite(self) -> "Placement":
        if not all(math.isfinite(v) for v in (self.x, self.y, self.facing)):
            raise SchemaError(
                f"placement of {self.entity} has a non-finite value",
                hint="give finite x, y and facing",
                context={"entity": self.entity, "x": self.x, "y": self.y},
            )
        return self

    @property
    def point(self) -> Point:
        """Returns (x, y)."""
        return self.x, self.y


class Set(FrozenModel):
    """Holds a snapshot of every spot's placement at one moment.

    Attributes:
        id: Set ID, e.g. `12` or `12A`.
        key: Stable storage key, fixed when the set is created; defaults to the ID.
            A key that differs from the ID must pass check_id.
        counts: Counts since the previous set; 0 for the first.
        placements: One placement per spot in the cast.
        forms: Forms present in this set.
        label: Designer label.
        cue: Rehearsal mark or cue.
        source_hash: Hash of the script that built the set.
        state: Collaboration state.
    """

    id: SetId
    key: str | None = None
    counts: float
    placements: tuple[Placement, ...] = ()
    forms: tuple[FormId, ...] = ()
    label: str | None = None
    cue: str | None = None
    source_hash: str = ""
    state: ApprovalState = ApprovalState.DRAFT

    @model_validator(mode="after")
    def _valid(self) -> "Set":
        if self.counts < 0:
            raise SchemaError(f"set {self.id} has negative counts", hint="use counts >= 0")
        entities = [p.entity for p in self.placements]
        if len(set(entities)) != len(entities):
            raise SchemaError(f"set {self.id} places a spot twice", hint="one placement per spot")
        # A key equal to the ID is a stored ID, left unchecked until stored IDs are migrated;
        # constructors check new keys through free_set_key().
        key = self.id if self.key in (None, self.id) else check_id(self.key, kind="set key")
        # The model is frozen; the validator is the one place that fills the defaulted key.
        object.__setattr__(self, "key", key)
        return self

    # Any: the dump is the JSON-like mapping pydantic built for this model.
    @model_serializer(mode="wrap")
    def _omit_default_key(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        dumped: dict[str, Any] = handler(self)
        if self.key == self.id:
            dumped.pop("key", None)
        return dumped

    def _index(self) -> dict[SpotId, int]:
        return {p.entity: i for i, p in enumerate(self.placements)}

    def table(self) -> "pd.DataFrame":
        """Returns placements as a DataFrame, one row per spot; needs the `tables` extra."""
        require_extra("pandas", extra="tables", needed_by="Set.table")
        import pandas as pd

        rows = [{column: getattr(p, column) for column in TABLE_COLUMNS} for p in self.placements]
        return pd.DataFrame(rows, columns=list(TABLE_COLUMNS))

    def entity_ids(self) -> tuple[SpotId, ...]:
        """Returns the spot IDs in placement order."""
        return tuple(p.entity for p in self.placements)

    def positions(self, sel: Literal["all"] | Sequence[str] | Selection = "all") -> Points:
        """Returns coordinates (N, 2) in selection order; "all" uses placement order."""
        ids = self.entity_ids() if sel == "all" else tuple(sel)
        if not ids:
            return np.zeros((0, 2))
        return np.array([self.get(i).point for i in ids], dtype=float)

    def get(self, entity: str) -> Placement:
        """Returns one placement, raising UnknownRefError when the spot is absent."""
        index = self._index()
        if entity not in index:
            raise UnknownRefError(
                f"spot {entity!r} is not in set {self.id}",
                hint="check the spot id or add the spot first",
                context={"set": self.id, "entity": entity},
            )
        return self.placements[index[SpotId(entity)]]

    # Any: updates may be a Placement or a partial dict of any placement fields.
    def with_placements[K: str](
        self, updates: Mapping[K, Placement | Mapping[str, Any]], *, force: bool = False
    ) -> "Set":
        """Returns a set with placements replaced or patched; pinned rows need force=True."""
        rows = list(self.placements)
        index = self._index()
        for entity, change in updates.items():
            current = self.get(entity)
            if current.pinned and not force:
                raise LockedError(
                    f"{entity} is pinned in set {self.id}",
                    hint="unpin it first, or pass force=True as the designer",
                    context={"set": self.id, "entity": entity},
                )
            rows[index[current.entity]] = _apply_change(current, change)
        return self.replace(placements=tuple(rows))


# Any: a partial update may touch any placement field.
def _apply_change(current: Placement, change: Placement | Mapping[str, Any]) -> Placement:
    if isinstance(change, Placement):
        if change.entity != current.entity:
            raise SchemaError(
                f"update for {current.entity} names {change.entity}",
                hint="key updates by the placement's own entity",
            )
        return change
    return Placement.model_validate({**current.model_dump(), **change})


class SpotCount(FrozenModel):
    """Counts spots on and off the field in one set.

    Attributes:
        set_id: Set counted.
        on_field: Spots on the field.
        off_field: Spots off the field.
        changed: Whether the on-field count differs from the previous set.
    """

    set_id: SetId
    on_field: int
    off_field: int
    changed: bool


class SpacingSpec(FrozenModel):
    """Describes how members are spaced on a form's shape.

    Attributes:
        mode: `count` (even over the window), `interval` (fixed gap) or `proportional`.
        interval: Gap in meters for interval mode.
        start: Arc length where spacing starts.
        end: Arc length where spacing ends; None means the end of the shape.
        align: `start`, `end` or `center` for interval mode.
        gaps: Extra meters after member indexes.
    """

    mode: Literal["count", "interval", "proportional"] = "count"
    interval: Meters | None = None
    start: Meters = 0.0
    end: Meters | None = None
    align: Literal["start", "end", "center"] = "start"
    gaps: tuple[tuple[int, Meters], ...] = ()

    @model_validator(mode="after")
    def _interval_needed(self) -> "SpacingSpec":
        if self.mode == "interval" and not (self.interval and self.interval > 0):
            raise SchemaError("interval spacing needs interval > 0", hint="set interval in meters")
        return self


class FacingSpec(FrozenModel):
    """Describes how form members face.

    Attributes:
        mode: `fixed`, `audience`, `toward_point`, `along`, `normal` or `outward`.
        value: Degrees for fixed, a point for toward_point; for along, normal and outward an
            optional offset in degrees added to the computed facing.
    """

    mode: Literal["fixed", "audience", "toward_point", "along", "normal", "outward"] = "audience"
    value: Degrees | Point | None = None

    @model_validator(mode="after")
    def _value_matches_mode(self) -> "FacingSpec":
        needs = {"fixed": float, "toward_point": tuple}
        if self.mode in needs and not isinstance(self.value, needs[self.mode]):
            raise SchemaError(
                f"facing mode {self.mode} needs a value",
                hint="give degrees for fixed or (x, y) for toward_point",
                context={"mode": self.mode},
            )
        return self


class TextRecipe(FrozenModel):
    r"""Holds the construction recipe of a text form: text, font chain and layout.

    Fonts are stored as URIs, never names, so a recipe always names the exact font bytes.

    Attributes:
        text: Characters to write, `\n` between lines.
        fonts: Fallback chain of 1 to `MAX_TEXT_FONTS` font URIs, `asset:sha256:<64 hex>`.
        mode: How glyphs become slots; `centerline` puts spots on the strokes.
        height: Cap height in meters.
        at: Layout point on the stage plane.
        rotation: Degrees counter-clockwise about `at`; 0 reads left to right toward +x.
        align: Where each line sits horizontally on `at`.
        anchor: Which line of the text block sits vertically on `at`.
        letter_spacing: Extra gap between glyphs, in ems of the first font.
        line_spacing: Baseline-to-baseline distance, in ems of the first font.
        spacing: Meters between spots that last set the form's count, or None when the count
            was given as `n`; the stored `n` stays the count.

    >>> from showpy.geo.fonts import bundled_fonts
    >>> simplex = bundled_fonts().resolve("simplex")
    >>> TextRecipe(text="HI", fonts=(simplex,), height=2.0, at=(0.0, 10.0)).align
    'left'
    """

    text: str = Field(min_length=1, max_length=MAX_LAYOUT_CHARS)
    fonts: tuple[FontUri, ...] = Field(min_length=1, max_length=MAX_TEXT_FONTS)
    mode: Literal["centerline"] = "centerline"
    height: Meters = Field(gt=0)
    at: Point
    rotation: Degrees = 0.0
    align: TextAlign = "left"
    anchor: TextAnchor = "baseline"
    letter_spacing: float = 0.0
    line_spacing: float = Field(default=DEFAULT_LINE_SPACING, gt=0)
    spacing: PositiveMeters | None = None


type FormKind = Literal[
    "line", "arc", "curve", "circle", "block", "wedge", "polyline", "svg", "fill", "scatter",
    "custom", "generated", "text",
]  # fmt: skip


class CrowdedStroke(FrozenModel):
    """Names a glyph stroke whose spots sit closer than its junction gap to another slot.

    Attributes:
        char: Character the stroke draws.
        line: Line of the text, 0 at the top.
        column: Position of the character in its line, after normalization.
        stroke: Index of the stroke in its glyph, in font order.
        first_slot: Slot index of the stroke's first spot.
        spots: Spots on the stroke.
        gap: Closest distance from one of its spots to another slot, in meters before any
            stored transform.
        wanted: Junction gap the stroke should keep, in meters before any stored transform.
    """

    char: str
    line: int = Field(ge=0)
    column: int = Field(ge=0)
    stroke: int = Field(ge=0)
    first_slot: int = Field(ge=0)
    spots: int = Field(ge=1)
    gap: Meters
    wanted: Meters


class TextReport(FrozenModel):
    """Reports how a text form's spots were spread on its strokes.

    Attributes:
        crowded: Strokes whose spots sit closer than their junction gap, in slot order.
    """

    crowded: tuple[CrowdedStroke, ...] = ()


class Form(FrozenModel):
    """Holds a stored shape construction plus its ordered members.

    Attributes:
        id: Form ID, unique across the show.
        set: Set the form belongs to.
        kind: Shape family.
        geometry: JSON construction recipe the slots are generated from.
        members: Spots in slot order.
        spacing: How members are spaced on the shape.
        facing: How members face.
        locked: Designer lock; edits need force=True.
        meta: Free designer metadata.
    """

    id: FormId
    set: SetId
    kind: FormKind
    # Any: construction recipes are JSON objects whose fields depend on the kind.
    geometry: dict[str, Any]
    members: tuple[SpotId, ...]
    spacing: SpacingSpec = SpacingSpec()
    facing: FacingSpec = FacingSpec()
    locked: bool = False
    meta: dict[str, str] = {}

    @model_validator(mode="after")
    def _unique_members(self) -> "Form":
        if len(set(self.members)) != len(self.members):
            raise SchemaError(f"form {self.id} lists a member twice", hint="list each spot once")
        return self


class PathStyle(StrEnum):
    """Names the built-in path styles."""

    STRAIGHT = "straight"
    HOLD = "hold"
    CURVE = "curve"
    POLYLINE = "polyline"
    FOLLOW = "follow"
    ROTATE = "rotate"
    CUSTOM = "custom"


class FacingMode(StrEnum):
    """Names how a spot faces while it moves."""

    FIXED = "fixed"
    TO_TARGET = "to_target"
    TRAVEL = "travel"
    AGAINST_TRAVEL = "against_travel"
    TOWARD_POINT = "toward_point"
    TOWARD_ENTITY = "toward_entity"
    AUDIENCE = "audience"
    INTERPOLATE = "interpolate"


class TurnDirection(StrEnum):
    """Names which way a facing turn goes."""

    SHORTEST = "shortest"
    CLOCKWISE = "clockwise"
    COUNTERCLOCKWISE = "counterclockwise"


class FacingOverride(FrozenModel):
    """Forces a facing on spots over a global count range, regardless of paths.

    Attributes:
        entities: Spots affected.
        mode: Facing mode during the range.
        value: Degrees, point or spot ID, depending on the mode.
        start: Global count where the override starts.
        end: Global count where it ends.
        turn_counts: Counts spent turning into and out of the override.
    """

    entities: SelectorField
    mode: FacingMode
    value: Degrees | Point | SpotId | None = None
    start: Count
    end: Count
    turn_counts: float = 0.0

    @model_validator(mode="after")
    def _ordered(self) -> "FacingOverride":
        if self.end <= self.start:
            raise SchemaError("override end must follow start", hint="use start < end")
        return self


class Waypoint(FrozenModel):
    """Holds a point a path passes through.

    Attributes:
        x: Meters.
        y: Meters.
        at: Local count to pass the point; None keeps constant speed.
    """

    x: Meters
    y: Meters
    at: float | None = None


class Easing(FrozenModel):
    """Maps progress through a move (time share) to progress along it (distance share).

    Attributes:
        kind: Easing family; `custom` uses `points`.
        strength: Exponent or step count, depending on the kind.
        points: Custom curve as (time share, distance share) pairs from (0, 0) to (1, 1).
    """

    kind: EasingKind = "linear"
    strength: float = DEFAULT_EASING_STRENGTH
    points: tuple[tuple[float, float], ...] = ()

    @model_validator(mode="after")
    def _valid_custom(self) -> "Easing":
        if self.kind != "custom":
            return self
        pts = self.points
        ordered = all(b[0] > a[0] and b[1] >= a[1] for a, b in itertools.pairwise(pts))
        if len(pts) < 2 or pts[0] != (0.0, 0.0) or pts[-1] != (1.0, 1.0) or not ordered:
            raise SchemaError(
                "custom easing needs increasing points from (0, 0) to (1, 1)",
                hint="pass points like ((0, 0), (0.5, 0.2), (1, 1))",
            )
        return self


class PathRow(FrozenModel):
    """Holds one spot's instructions for one transition; times are local counts.

    Attributes:
        entity: Spot ID.
        style: Path style.
        start: Local count the move starts.
        end: Local count the move ends; None means the end of the transition.
        waypoints: Points passed on the way, for CURVE and POLYLINE.
        route: Route followed, for FOLLOW.
        pivot: Rotation center, for ROTATE.
        sweep_deg: Signed rotation, for ROTATE; positive is counter-clockwise.
        facing_mode: How the spot faces during the move.
        facing_value: Degrees, point or spot ID, depending on the facing mode.
        turn_counts: Counts spent turning into and out of the move's facing.
        turn_direction: Which way turns go.
        pace: Step pace multiplier for step-size reporting; positions ignore it.
        locked: Designer lock.
        note: Free text.
        motion_fn: Registered motion function, for CUSTOM.
        motion_params: Parameters for the motion function.
        easing: How progress maps to time across the window.
    """

    entity: SpotId
    style: PathStyle = PathStyle.STRAIGHT
    start: float = 0.0
    end: float | None = None
    waypoints: tuple[Waypoint, ...] = ()
    route: RouteId | None = None
    pivot: Point | None = None
    sweep_deg: Degrees | None = None
    facing_mode: FacingMode = FacingMode.TO_TARGET
    facing_value: Degrees | Point | SpotId | None = None
    turn_counts: float = 0.0
    turn_direction: TurnDirection = TurnDirection.SHORTEST
    pace: float = 1.0
    locked: bool = False
    note: str | None = None
    motion_fn: str | None = None
    # Any: motion parameters are JSON values defined by each registered motion function.
    motion_params: dict[str, Any] = {}
    easing: Easing = Easing()

    @model_validator(mode="after")
    def _window(self) -> "PathRow":
        if self.start < 0 or (self.end is not None and self.end <= self.start):
            raise PathError(
                f"path window for {self.entity} is invalid",
                hint="use 0 <= start < end",
                context={"start": self.start, "end": self.end},
            )
        if self.turn_counts < 0 or self.pace <= 0:
            raise PathError("turn_counts must be >= 0 and pace > 0", hint="fix the row")
        return self

    def window(self, counts: float) -> tuple[float, float]:
        """Returns (start, end) in local counts, filling a missing end with `counts`."""
        return self.start, counts if self.end is None else self.end


class Route(FrozenModel):
    """Holds a shared path for follow-the-leader.

    Members start on the route at arc lengths `offsets` and all advance `travel` meters.

    Attributes:
        id: Route ID.
        curve: JSON construction recipe of the route curve.
        members: Spots on the route, leader first.
        offsets: Each member's starting arc length on the route.
        travel: Meters every member advances.
        start: Local count the move starts.
        end: Local count the move ends; None means the end of the transition.
    """

    id: RouteId
    # Any: curve recipes are JSON objects whose fields depend on the curve kind.
    curve: dict[str, Any]
    members: tuple[SpotId, ...]
    offsets: tuple[Meters, ...]
    travel: Meters
    start: float = 0.0
    end: float | None = None

    @model_validator(mode="after")
    def _aligned(self) -> "Route":
        if len(self.members) != len(self.offsets):
            raise PathError("route needs one offset per member", hint="align members and offsets")
        return self


class TransitionSpec(FrozenModel):
    """Holds every path instruction for one transition.

    Rows are sparse: a spot without a row moves straight over the full counts.

    Attributes:
        id: (from set, to set).
        counts: Length of the transition; equals the destination set's counts.
        rows: Spot ID to path row.
        routes: Route ID to route.
        notes: Free text.
        source_hash: Hash of the script that wrote the paths.
    """

    id: TransitionId
    counts: float
    rows: dict[SpotId, PathRow] = {}
    routes: dict[RouteId, Route] = {}
    notes: str = ""
    source_hash: str = ""

    @model_validator(mode="after")
    def _keys_match(self) -> "TransitionSpec":
        for key, row in self.rows.items():
            if key != row.entity:
                raise SchemaError(f"row key {key} holds {row.entity}", hint="key rows by entity")
        return self

    def row(self, entity: str) -> PathRow:
        """Returns the spot's row, or a default straight row when it has none."""
        if entity in self.rows:
            return self.rows[SpotId(entity)]
        return PathRow.model_validate({"entity": entity})


type MatchObjective = Literal["sum", "sum_squares", "bottleneck", "bottleneck_then_sum"]
type CostFn = Callable[[str, int, Point, Point], float]


class MatchOptions(FrozenModel):
    """Holds the constraints, objective and penalties for matching spots to slots.

    Penalties are in meters and add to a move's length before the objective is applied.

    Attributes:
        keep_groups: Spots only take slots labeled with their own group.
        group_slots: Group key to the slot indexes reserved for it.
        pinned: Forced assignments, spot to slot index.
        forbid: Disallowed (spot, slot index) pairs.
        no_backward: True forbids travel against facing; a number adds that many meters.
        max_speed: Fastest allowed move in meters per count; needs `counts`.
        objective: `sum`, `sum_squares`, `bottleneck` or `bottleneck_then_sum`.
        power: Exponent applied to move costs by the `sum` objective.
        preserve_order: Spots, in the order given, take slots in increasing index order.
        weights: Penalties: `backward` (per meter against facing), `turn` (per degree of turn
            toward the travel direction), `crossing` (per crossing), `group_spread` (per meter
            of mean distance from the group's target centroid).
        cost_fn: Extra cost per (spot, slot index, start, target); infinity forbids the pair.
            Not serialized.
        counts: Counts the move takes, for speed limits and reporting.
    """

    keep_groups: bool = True
    group_slots: dict[str, tuple[int, ...]] | None = None
    pinned: dict[SpotId, int] = {}
    forbid: tuple[tuple[SpotId, int], ...] = ()
    no_backward: bool | float = False
    max_speed: float | None = None
    objective: MatchObjective = "bottleneck_then_sum"
    power: float = 1.0
    preserve_order: bool = False
    weights: dict[str, float] = {}
    cost_fn: CostFn | None = Field(default=None, exclude=True)
    counts: float | None = None

    @model_validator(mode="after")
    def _valid(self) -> "MatchOptions":
        unknown = sorted(set(self.weights) - set(MATCH_WEIGHT_KEYS))
        if unknown:
            raise SchemaError(
                f"unknown match weight {unknown[0]!r}",
                hint=f"use weights from {', '.join(MATCH_WEIGHT_KEYS)}",
                context={"weights": sorted(self.weights)},
            )
        positive = {"power": self.power, "max_speed": self.max_speed, "counts": self.counts}
        for name, value in positive.items():
            if value is not None and not value > 0:
                raise SchemaError(f"{name} must be above 0", hint=f"give a positive {name}")
        if any(w < 0 for w in self.weights.values()):
            raise SchemaError("match weights must be >= 0", hint="use non-negative weights")
        return self


class MatchResult(FrozenModel):
    """Holds who goes where, with move statistics and an explanation.

    Attributes:
        entities: Matched spots, in the order the solver used.
        assignment: Spot to slot index.
        targets: Each spot's slot position, aligned with `entities`.
        total_distance: Sum of straight-line move lengths.
        max_distance: Longest straight-line move.
        max_speed: Longest move divided by counts; None when counts are unknown.
        crossings: Pairs of straight-line moves that cross.
        unassigned_slots: Slot indexes nobody takes.
        explain: Plain-language lines describing the result.
        slots: Spot to (form id, slot index in that form); empty when matched onto points.
    """

    entities: tuple[SpotId, ...] = ()
    assignment: dict[SpotId, int] = {}
    targets: tuple[Point, ...] = ()
    total_distance: Meters = 0.0
    max_distance: Meters = 0.0
    max_speed: float | None = None
    crossings: int = 0
    unassigned_slots: tuple[int, ...] = ()
    explain: tuple[str, ...] = ()
    slots: dict[SpotId, tuple[FormId, int]] = {}

    @model_validator(mode="after")
    def _aligned(self) -> "MatchResult":
        is_misaligned = len(self.entities) != len(self.targets) or set(self.entities) != set(
            self.assignment
        )
        if is_misaligned or (self.slots and set(self.slots) != set(self.assignment)):
            raise SchemaError(
                "match result entities, targets and assignment disagree",
                hint="give one target and one slot per entity",
            )
        return self

    def target_points(self) -> Points:
        """Returns targets as an (N, 2) array in entity order."""
        return np.asarray(self.targets, dtype=float).reshape(-1, 2)


class FormFnInfo(FrozenModel):
    """Describes one registered shape generator.

    Attributes:
        name: Registered name used in a generated form's recipe.
        summary: First line of the generator's docstring.
    """

    name: str
    summary: str
