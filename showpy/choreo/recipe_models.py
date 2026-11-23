"""
recipe_models.py --- typed construction recipes of the non-text form kinds

A form stores how to rebuild its shape, never the dots. Each kind has its own recipe model,
tagged by `kind` and versioned by `recipe_version`; the union of every recipe, the text recipe
included, is `showpy.choreo.form_models.Recipe`.

Contains:
    RECIPE_VERSION: the version every recipe model stores today
    MAX_ARC_SWEEP_DEG: largest arc sweep a form may hold, one full turn
    Affine: a 3x3 affine matrix stored as three rows of three finite numbers
    RecipeBase: holds the fields every recipe shares: version, slot count and transform
    CurveRecipeBase: holds the curve that a curve-kind recipe spaces its slots along
    LineRecipe, CurveRecipe, CircleRecipe, WedgeRecipe, PolylineRecipe: hold the curve of
        their kind
    SvgRecipe: holds a picture outline as one curve per subpath
    ArcRecipe: holds an arc's curve, refusing a sweep past one full turn
    BlockRecipe: holds a rows x cols block
    FillRecipe: holds an area filled with a lattice
    ScatterRecipe: holds an area scattered with slots
    CustomRecipe: holds frozen hand-placed points and optional facings
    GeneratedRecipe: holds a registered generator's name and its JSON parameters
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from showpy.core.types import Degrees, Finite, FrozenModel, JsonObject, Meters, Point, Region
from showpy.geo.curves import AnyCurve, ArcCurve
from showpy.geo.spacing import BlockAnchor, FillPattern

RECIPE_VERSION: Final = 1
MAX_ARC_SWEEP_DEG: Final = 360.0

type AffineRow = tuple[Finite, Finite, Finite]
type Affine = tuple[AffineRow, AffineRow, AffineRow]


class RecipeBase(FrozenModel):
    """Holds the fields every form recipe shares.

    Attributes:
        recipe_version: Shape version of the recipe; 1 for every kind.
        n: Slot count, 0 or more; None gives one slot per member, or what the kind decides (interval
            spacing fits as many as it can, a fill takes every lattice point).
        transform: 3x3 affine matrix applied to the generated slots and their directions, which
            keeps gated or mirrored forms editable; None for none.
    """

    recipe_version: Literal[1] = RECIPE_VERSION
    n: int | None = Field(default=None, ge=0)
    transform: Affine | None = None


class CurveRecipeBase(RecipeBase):
    """Holds the curve that a curve-kind recipe spaces its slots along.

    Attributes:
        curve: The curve, rebuilt exactly from its stored construction.
    """

    curve: AnyCurve


class LineRecipe(CurveRecipeBase):
    """Holds the curve of a line form, a segment unless an edit reshaped it.

    >>> from showpy.geo.shapes import line
    >>> LineRecipe(curve=line((0, 0), (4, 0))).kind
    'line'
    """

    kind: Literal["line"] = "line"


class ArcRecipe(CurveRecipeBase):
    """Holds the curve of an arc form, refusing a sweep past one full turn.

    A sweep past 360 degrees would put slots on top of each other, so a sweep lies in (0, 360]
    degrees either way; the arc curve itself refuses a zero sweep.

    >>> from showpy.geo.shapes import arc
    >>> ArcRecipe(curve=arc((0, 0), 2.0, 0.0, 90.0)).kind
    'arc'
    """

    kind: Literal["arc"] = "arc"

    @model_validator(mode="after")
    def _sweep_within_one_turn(self) -> Self:
        if isinstance(self.curve, ArcCurve) and abs(self.curve.sweep_deg) > MAX_ARC_SWEEP_DEG:
            raise ValueError(
                f"arc sweep {self.curve.sweep_deg} is past one turn; give a sweep of at most "
                f"{MAX_ARC_SWEEP_DEG:g} degrees either way, or use a circle"
            )
        return self


class CurveRecipe(CurveRecipeBase):
    """Holds the smooth curve of a curve form."""

    kind: Literal["curve"] = "curve"


class CircleRecipe(CurveRecipeBase):
    """Holds the curve of a circle form."""

    kind: Literal["circle"] = "circle"


class WedgeRecipe(CurveRecipeBase):
    """Holds the two-sided polyline of a wedge form."""

    kind: Literal["wedge"] = "wedge"


class PolylineRecipe(CurveRecipeBase):
    """Holds the straight-sided curve of a polyline form."""

    kind: Literal["polyline"] = "polyline"


class SvgRecipe(RecipeBase):
    """Holds the fitted picture outline of an svg form, one curve per subpath.

    Subpaths stay separate curves, never one curve that jumps the gaps between them; layout
    shares slots among them by length, or walks them in order for count and interval spacing.

    Attributes:
        curves: The outline's subpaths in drawing order, each rebuilt from its construction.

    >>> from showpy.geo.shapes import line
    >>> len(SvgRecipe(curves=(line((0, 0), (1, 0)), line((0, 1), (3, 1)))).curves)
    2
    """

    kind: Literal["svg"] = "svg"
    curves: tuple[AnyCurve, ...] = Field(min_length=1)


class BlockRecipe(RecipeBase):
    """Holds a rows x cols block, front row first.

    Attributes:
        origin: Point the anchor sits on.
        rows: Rows from front to back.
        cols: Columns from left to right.
        interval: Meters between neighbouring slots.
        facing: Direction the block faces, which turns the rows and columns.
        anchor: Which slot or edge of the block sits on `origin`.

    >>> BlockRecipe(origin=(0, 0), rows=1, cols=2, interval=1.0).anchor
    'center'
    """

    kind: Literal["block"] = "block"
    origin: Point
    rows: int = Field(gt=0)
    cols: int = Field(gt=0)
    interval: Meters
    facing: Degrees = 0.0
    anchor: BlockAnchor = "center"


class FillRecipe(RecipeBase):
    """Holds an area filled with a lattice; with a count, the slots nearest its center are kept.

    Attributes:
        outline: Area to fill.
        interval: Meters between lattice neighbours.
        pattern: `grid`, `offset` or `hex`.
        angle_deg: Lattice rotation, counter-clockwise.
    """

    kind: Literal["fill"] = "fill"
    outline: Region
    interval: Meters
    pattern: FillPattern = "grid"
    angle_deg: Degrees = 0.0


class ScatterRecipe(RecipeBase):
    """Holds an area scattered with slots, at least `min_gap` apart.

    Attributes:
        region: Area to scatter in.
        min_gap: Smallest distance between two slots, in meters.
        seed: Seed of the scatter, so the same recipe gives the same slots.
    """

    kind: Literal["scatter"] = "scatter"
    region: Region
    min_gap: Meters
    seed: int = 0


class CustomRecipe(RecipeBase):
    """Holds frozen hand-placed points and, optionally, one stored facing per point.

    Attributes:
        points: Slot positions in slot order.
        facings: One facing per point, which wins over the form's FacingSpec; None for none.

    >>> CustomRecipe(points=((1.0, 2.0),), facings=(45.0,)).facings
    (45.0,)
    """

    kind: Literal["custom"] = "custom"
    points: tuple[Point, ...]
    facings: tuple[Degrees, ...] | None = None

    @model_validator(mode="after")
    def _one_facing_per_point(self) -> Self:
        if self.facings is not None and len(self.facings) != len(self.points):
            raise ValueError(
                f"{len(self.points)} points but {len(self.facings)} facings; give one facing "
                "per point, or none"
            )
        return self


class GeneratedRecipe(RecipeBase):
    """Holds a registered generator's name and the JSON parameters it is called with.

    The parameters stay an open, read-only JSON object: the generator checks them.

    Attributes:
        fn: Registered generator name.
        params: Parameters passed to the generator, as plain JSON.

    >>> GeneratedRecipe(fn="spiral", params={"turns": 2.0}).params["turns"]
    2.0
    """

    kind: Literal["generated"] = "generated"
    fn: str = Field(min_length=1)
    params: JsonObject = Field(default={}, validate_default=True)
