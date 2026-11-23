"""
form_models.py --- form data classes: spacing, facing, text recipes and stored shape constructions

Contains:
    FONT_URI_PATTERN: matches the content-addressed URI of a stored font
    FontUri: names one font by its content-addressed URI
    MAX_TEXT_FONTS: bounds the font chain of one text recipe
    SpacingSpec: describes how members are spaced on a form's shape
    FacingSpec: describes how form members face
    TextRecipe: holds the construction recipe of a text form: text, font chain and layout
    Recipe: the typed construction recipe of any form, tagged by `kind`
    FormKind: names the shape families a form can have
    CrowdedStroke: names a glyph stroke whose spots sit closer than its junction gap
    TextReport: reports how a text form's spots were spread, listing its crowded strokes
    Form: holds a stored shape construction plus its ordered members
    Form.kind: returns the shape family, the recipe's kind
    Form.spots: returns the spots on the form in slot order, without open slots
    Form.geometry: returns the recipe as JSON; deprecated, read `Form.recipe`
    FormFnInfo: describes one registered shape generator
    SpiralFormParams: holds the parameters of the bundled `spiral` generator
    SineWaveFormParams: holds the parameters of the bundled `sine_wave` generator
    RoseFormParams: holds the parameters of the bundled `rose` generator
"""

from collections.abc import Mapping
from typing import Annotated, ClassVar, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from e8s.choreo.recipe_models import (
    ArcRecipe,
    BlockRecipe,
    CircleRecipe,
    CurveRecipe,
    CustomRecipe,
    FillRecipe,
    GeneratedRecipe,
    LineRecipe,
    PolylineRecipe,
    RecipeBase,
    ScatterRecipe,
    SvgRecipe,
    WedgeRecipe,
)
from e8s.core.caps import MAX_COORD_M
from e8s.core.deprecation import renamed_field
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, SpacingError
from e8s.core.ids import FormId, SetId, SpotId, SpotIdField
from e8s.core.numbers import normalize_angle
from e8s.core.readonly import ReadOnly
from e8s.core.types import (
    Degrees,
    Finite,
    FrozenModel,
    JsonValue,
    Meters,
    NonNegFinite,
    Point,
    PositiveFinite,
    PositiveMeters,
)
from e8s.geo.models import DEFAULT_LINE_SPACING, TextAlign, TextAnchor
from e8s.geo.text_layout import MAX_LAYOUT_CHARS

FONT_URI_PATTERN = r"^asset:sha256:[0-9a-f]{64}$"
# Layout resolves every chain font and measures its cap height, so a stored chain is bounded.
MAX_TEXT_FONTS = 16
type NonNegMeters = Annotated[float, Field(ge=0, le=MAX_COORD_M, allow_inf_nan=False)]
type FontUri = Annotated[str, Field(pattern=FONT_URI_PATTERN)]
DEFAULT_GENERATOR_CENTER: Point = (0.0, 10.0)
DEFAULT_SINE_START: Point = (-8.0, 10.0)


class SpacingSpec(FrozenModel):
    """Describes how members are spaced on a form's shape.

    Attributes:
        mode: `count` (even over the window), `interval` (fixed gap) or `proportional`.
        interval: Gap in meters for interval mode.
        start: Arc length where spacing starts.
        end: Arc length where spacing ends; None means the end of the shape.
        align: `start`, `end` or `center` for interval mode.
        gaps: Extra meters, at least 0, after member indexes.
    """

    mode: Literal["count", "interval", "proportional"] = "count"
    interval: Meters | None = None
    start: Meters = 0.0
    end: Meters | None = None
    align: Literal["start", "end", "center"] = "start"
    gaps: tuple[tuple[int, NonNegMeters], ...] = ()

    @model_validator(mode="after")
    def _interval_needed(self) -> "SpacingSpec":
        if self.mode == "interval" and not (self.interval and self.interval > 0):
            raise SchemaError("interval spacing needs interval > 0", hint="set interval in meters")
        return self


class FacingSpec(FrozenModel):
    """Describes how form members face.

    Attributes:
        mode: `fixed`, `audience`, `toward_point`, `along`, `normal` or `outward`.
        value: Heading for fixed, wrapped into [0, 360); a point for toward_point; for along,
            normal and outward an optional offset in degrees added to the computed facing.
    """

    mode: Literal["fixed", "audience", "toward_point", "along", "normal", "outward"] = "audience"
    value: Degrees | Point | None = None

    @field_validator("value")
    @classmethod
    def _wrap_fixed_heading(
        cls, value: float | Point | None, info: ValidationInfo
    ) -> float | Point | None:
        if info.data.get("mode") == "fixed" and isinstance(value, float):
            return normalize_angle(value)
        return value

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


class TextRecipe(RecipeBase):
    r"""Holds the construction recipe of a text form: text, font chain and layout.

    Fonts are stored as URIs, never names, so a recipe always names the exact font bytes. A
    text form's `n` is always set: it is the exact spot count, one member per slot.

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

    >>> from e8s.geo.fonts import bundled_fonts
    >>> simplex = bundled_fonts().resolve("simplex")
    >>> TextRecipe(text="HI", fonts=(simplex,), height=2.0, at=(0.0, 10.0)).align
    'left'
    """

    kind: Literal["text"] = "text"
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


type Recipe = Annotated[
    LineRecipe
    | ArcRecipe
    | CurveRecipe
    | CircleRecipe
    | BlockRecipe
    | WedgeRecipe
    | PolylineRecipe
    | SvgRecipe
    | FillRecipe
    | ScatterRecipe
    | CustomRecipe
    | GeneratedRecipe
    | TextRecipe,
    Field(discriminator="kind"),
]

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
    gap: Finite
    wanted: Finite


class TextReport(FrozenModel):
    """Reports how a text form's spots were spread on its strokes.

    Attributes:
        crowded: Strokes whose spots sit closer than their junction gap, in slot order.
    """

    crowded: tuple[CrowdedStroke, ...] = ()


def _recipe_json(recipe: RecipeBase) -> dict[str, JsonValue]:
    dumped: dict[str, JsonValue] = recipe.model_dump(mode="json")
    return dumped


class Form(FrozenModel):
    """Holds a stored shape construction plus its ordered members.

    Attributes:
        id: Form ID, unique across the show.
        set: Set the form belongs to.
        recipe: Typed construction recipe the slots are generated from; its `kind` is the
            form's shape family.
        members: One entry per slot in slot order: the spot standing on it, or None for an open
            slot that `place` fills. At most `recipe.n` entries when `n` is set.
        spacing: How members are spaced on the shape.
        facing: How members face.
        locked: Designer lock; edits need force=True.
        meta: Free designer metadata, read-only once stored.
    """

    id: FormId
    set: SetId
    recipe: Recipe
    members: tuple[SpotIdField | None, ...]
    spacing: SpacingSpec = SpacingSpec()
    facing: FacingSpec = FacingSpec()
    locked: bool = False
    meta: Annotated[Mapping[str, str], ReadOnly()] = Field(default={}, validate_default=True)

    geometry: ClassVar[property] = renamed_field(
        "recipe", since="0.4.0", remove="0.6.0", read=_recipe_json
    )

    @property
    def kind(self) -> FormKind:
        """Returns the shape family, the recipe's kind."""
        return self.recipe.kind

    @property
    def spots(self) -> tuple[SpotId, ...]:
        """Returns the spots standing on the form, in slot order, without the open slots.

        >>> Form(id="f", set="1", recipe={"kind": "custom", "points": [], "n": 3},
        ...     members=("A", None, "B")).spots
        ('A', 'B')
        """
        return tuple(member for member in self.members if member is not None)

    @model_validator(mode="after")
    def _unique_members(self) -> "Form":
        spots = [member for member in self.members if member is not None]
        if len(set(spots)) != len(spots):
            raise SchemaError(f"form {self.id} lists a member twice", hint="list each spot once")
        return self

    @model_validator(mode="after")
    def _text_has_no_open_slots(self) -> "Form":
        slots = self.recipe.n
        if self.recipe.kind != "text" or slots is None or len(self.spots) == len(self.members):
            return self
        raise SpacingError(
            f"text form {self.id} has {len(self.spots)} spots for {slots} slots",
            hint="stand a spot on every slot of a text form, or change its count with "
            "edit_text_count",
            context={"form": self.id, "n": slots, "members": len(self.spots)},
            code=ErrorCode.MEMBER_COUNT_MISMATCH,
        )

    @model_validator(mode="after")
    def _members_fit_slots(self) -> "Form":
        slots = self.recipe.n
        if slots is not None and len(self.members) > slots:
            raise SpacingError(
                f"form {self.id} has {len(self.members)} members but {slots} slots",
                hint=f"use at most {slots} members, or raise the slot count n",
                context={"form": self.id, "n": slots, "members": len(self.members)},
            )
        return self


class FormFnInfo(FrozenModel):
    """Describes one registered shape generator.

    Attributes:
        name: Registered name used in a generated form's recipe.
        summary: First line of the generator's docstring.
    """

    name: str
    summary: str


class SpiralFormParams(FrozenModel):
    """Holds the parameters of the bundled `spiral` generator.

    Attributes:
        center: Spiral center.
        r0: Start radius in meters.
        r1: End radius in meters.
        turns: Counter-clockwise turns, above 0.
        start_deg: Start angle, counter-clockwise from +x.

    >>> SpiralFormParams(turns=1.5).r1
    5.0
    """

    center: Point = DEFAULT_GENERATOR_CENTER
    r0: NonNegFinite = 1.0
    r1: NonNegFinite = 5.0
    turns: PositiveFinite = 2.0
    start_deg: Degrees = 0.0


class SineWaveFormParams(FrozenModel):
    """Holds the parameters of the bundled `sine_wave` generator.

    Attributes:
        start: Where the wave starts; it runs toward +x.
        length: Run along x in meters, above 0.
        amplitude: Peak offset from the center line in meters.
        wavelength: Meters per full wave, above 0.
        phase_deg: Phase at the start, in degrees.

    >>> SineWaveFormParams().wavelength
    8.0
    """

    start: Point = DEFAULT_SINE_START
    length: PositiveMeters = 16.0
    amplitude: Finite = 2.0
    wavelength: PositiveMeters = 8.0
    phase_deg: Degrees = 0.0


class RoseFormParams(FrozenModel):
    """Holds the parameters of the bundled `rose` generator.

    Attributes:
        center: Flower center.
        radius: Petal tip radius in meters, above 0.
        petals: Number of petals, above 0.
        inner: Radius between petals as a share of `radius`, in (0, 1], so petals never meet
            at the center.

    >>> RoseFormParams(petals=3).inner
    0.4
    """

    center: Point = DEFAULT_GENERATOR_CENTER
    radius: PositiveMeters = 5.0
    petals: PositiveFinite = 5.0
    inner: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.4
