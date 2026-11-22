#!/usr/bin/env python3

"""
test_types.py --- tests the shared type aliases, JSON-safe Region and Box2, and the FrozenModel base

Contains:
    NON_FINITE_MODELS: builders that put NaN or infinity into one float field per model family
    test_*(): one behavior of showpy.core.types each
    test_regression_region_rejects_fewer_than_three_points: rejects a two-point region, which has no
        area
    test_regression_region_rejects_nan_points: rejects NaN ring coordinates
    test_regression_region_rejects_self_intersection: rejects a bow-tie ring that crosses itself
    test_regression_frozen_models_reject_non_finite_floats: rejects NaN and infinity in Box2,
        TempoSection, Waypoint, Shape and Landmark fields
    test_regression_nested_model_error_keeps_outer_locs: reports every nested field error under
        the outer model with dotted locs
    test_regression_nested_construction_error_names_the_outermost_model: names the outer model,
        not the nested one, when a constructor fails inside a nested model
    test_regression_library_model_reports_dotted_loc: reports a bad Box2 corner as `min.0`
    test_regression_replace_revalidates: rejects a negative Set.counts passed to replace()
    test_replace_returns_a_new_model_with_the_change: keeps the original and applies the change
    test_replace_rejects_an_unknown_field: raises SchemaError for a field the model lacks
    test_regression_constrained_number_types_reject_out_of_range: rejects zero, negative, NaN and
        infinity in a positive field
    test_base_number_aliases_reject_non_finite_outside_models: rejects NaN and infinity in the
        finite-only base aliases through a plain TypeAdapter
    test_non_negative_types_*(): one rule of the non-negative aliases each
    test_regression_model_validate_strings_raises_schema_error: converts the pydantic error of
        `model_validate_strings`, keeping the cause
    test_model_validate_strings_parses_valid_text: builds a model from string-leaved data
    test_regression_validators_show_and_forward_pydantic_keywords: names pydantic's keywords in
        each validator signature and forwards `strict`
    test_regression_model_validate_forwards_strict: passes `strict` through `model_validate`
    test_regression_model_copy_drops_cached_values: rebuilds a cached_property on the copy
    test_model_copy_without_cached_values_still_copies: copies a model that caches nothing
    test_counts_per_minute_must_be_positive: rejects zero, negative, NaN and infinite tempo
    test_heading_wraps_into_a_turn: wraps a facing into [0, 360) and rejects NaN
    test_region_*(): distinct points, holes inside the exterior, and valid rings with holes
    test_json_value_is_the_one_shared_alias: exports the JsonValue that canonical encoding uses
    test_json_object_stores_a_read_only_mapping: stores a JSON object as a read-only mapping
    test_json_object_*(): deep freezing, plain JSON dumps and schema, round trips and hashing
    test_regression_placement_rejects_a_huge_coordinate: refuses a 2e300 m spot coordinate
    test_regression_box2_rejects_a_huge_corner: refuses a 2e300 m corner in a coordinate tuple
    test_regression_tempo_section_rejects_a_vanishing_tempo: refuses 1e-320 counts per minute
    test_regression_spline_rejects_a_huge_control_point: refuses a 2e300 m spline point
    test_bounded_aliases_accept_their_bound: accepts each bound exactly
    test_bounded_aliases_refuse_the_next_float_past_their_bound: refuses one ulp past each bound
"""

import inspect
import json
import math
from collections.abc import Callable
from functools import cached_property

import pytest
import shapely
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from showpy.cast.models import Shape
from showpy.choreo.models import Placement, Set
from showpy.choreo.path_models import Waypoint
from showpy.core import canonical, json_types
from showpy.core.caps import MAX_COORD_M, MAX_COUNTS, MIN_TEMPO
from showpy.core.errors import SchemaError, ShowPyError, field_errors
from showpy.core.ids import LandmarkId, SetId, SpotId
from showpy.core.readonly import ReadOnlyMap
from showpy.core.types import (
    Box2,
    Count,
    CountsPerMinute,
    Degrees,
    FrozenModel,
    Heading,
    JsonObject,
    JsonValue,
    Meters,
    NonNegCount,
    NonNegFinite,
    NonNegSeconds,
    Point,
    Point3,
    PositiveFinite,
    PositiveMeters,
    Region,
    Ring,
    Seconds,
)
from showpy.geo.curves import curve_from_construction
from showpy.sheet.models import Landmark
from showpy.time.models import TempoSection

NON_FINITE_MODELS: dict[str, Callable[[], FrozenModel]] = {
    "box2-nan": lambda: Box2(min=(math.nan, 0.0), max=(1.0, 1.0)),
    "box2-inf": lambda: Box2(min=(0.0, 0.0), max=(math.inf, 1.0)),
    "tempo-nan-ramp": lambda: TempoSection(at_count=0.0, counts_per_minute=120.0, ramp_to=math.nan),
    "tempo-inf-ramp-counts": lambda: TempoSection(
        at_count=0.0, counts_per_minute=120.0, ramp_to=90.0, ramp_counts=math.inf
    ),
    "waypoint-nan": lambda: Waypoint(x=math.nan, y=0.0),
    "waypoint-inf": lambda: Waypoint(x=0.0, y=-math.inf),
    "shape-nan-rotation": lambda: Shape(kind="rectangle", rotation_deg=math.nan),
    "shape-inf-radius": lambda: Shape(radius_m=math.inf),
    "landmark-nan": lambda: Landmark(
        id=LandmarkId("a"), name="a", kind="point", geometry=((math.nan, 0.0),)
    ),
    "landmark-inf": lambda: Landmark(
        id=LandmarkId("a"), name="a", kind="point", geometry=((math.inf, 0.0),)
    ),
}


def test_region_round_trips_through_shapely() -> None:
    poly = shapely.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (2, 1), (2, 2)]])
    region = Region.from_shapely(poly)
    assert region.to_shapely().equals(poly)


def test_region_round_trips_through_json() -> None:
    region = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)))
    assert Region.model_validate_json(region.model_dump_json()) == region


def test_box2_size_and_center() -> None:
    box = Box2(min=(0.0, 0.0), max=(4.0, 2.0))
    assert box.size == (4.0, 2.0)
    assert box.center == (2.0, 1.0)


def test_regression_region_rejects_fewer_than_three_points() -> None:
    """Rejects a two-point region, which has no area (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, 1.0)))


def test_regression_region_rejects_nan_points() -> None:
    """Rejects NaN coordinates (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, float("nan")), (1.0, 1.0)))


def test_regression_region_rejects_self_intersection() -> None:
    """Rejects a bow-tie ring that crosses itself (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)))


@pytest.mark.parametrize("build", NON_FINITE_MODELS.values(), ids=NON_FINITE_MODELS.keys())
def test_regression_frozen_models_reject_non_finite_floats(
    build: Callable[[], FrozenModel],
) -> None:
    """Rejects NaN and infinity in float fields of every FrozenModel family (audit 4.1).

    The root cause is FrozenModel's config: it lacks allow_inf_nan=False, so each family that
    has no hand-written finiteness check accepts them.
    """
    with pytest.raises((ShowPyError, PydanticValidationError)):
        build()


class _Inner(FrozenModel):
    a: int
    b: int


class _Outer(FrozenModel):
    inner: _Inner
    n: int


def test_regression_nested_model_error_keeps_outer_locs() -> None:
    with pytest.raises(SchemaError) as info:
        _Outer.model_validate({"inner": {"a": "x", "b": "y"}, "n": "z"})
    assert info.value.context["model"] == "_Outer"
    assert [e["loc"] for e in field_errors(info.value)] == ["inner.a", "inner.b", "n"]


def test_regression_nested_construction_error_names_the_outermost_model() -> None:
    with pytest.raises(SchemaError) as info:
        _Outer(inner={"a": "x", "b": 1}, n="z")  # pyright: ignore[reportArgumentType]
    assert info.value.context["model"] == "_Outer"
    assert [e["loc"] for e in field_errors(info.value)] == ["inner.a", "n"]


def test_regression_library_model_reports_dotted_loc() -> None:
    with pytest.raises(SchemaError) as info:
        Box2.model_validate({"min": ("x", 0), "max": (1, 1)})
    assert field_errors(info.value)[0]["loc"] == "min.0"


NEGATIVE_COUNTS = -4.0


def test_regression_replace_revalidates() -> None:
    """Rejects an edit that breaks a model validator, instead of copying it unchecked."""
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=0.0).replace(counts=NEGATIVE_COUNTS)


def test_replace_returns_a_new_model_with_the_change() -> None:
    box = Box2(min=(0.0, 0.0), max=(1.0, 1.0))
    moved = box.replace(max=(2.0, 3.0))
    assert moved.max == (2.0, 3.0)
    assert moved.min == box.min
    assert box.max == (1.0, 1.0)


def test_replace_rejects_an_unknown_field() -> None:
    with pytest.raises(SchemaError):
        Box2(min=(0.0, 0.0), max=(1.0, 1.0)).replace(centre=(0.0, 0.0))


class _Constrained(FrozenModel):
    positive_meters: PositiveMeters = 1.0
    positive_finite: PositiveFinite = 1.0
    non_neg_finite: NonNegFinite = 0.0
    non_neg_count: NonNegCount = 0.0
    non_neg_seconds: NonNegSeconds = 0.0
    tempo: CountsPerMinute = 120.0


@pytest.mark.parametrize("field", ["positive_meters", "positive_finite"])
@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_regression_constrained_number_types_reject_out_of_range(field: str, bad: float) -> None:
    with pytest.raises(SchemaError):
        _Constrained(**{field: bad})


@pytest.mark.parametrize("field", ["non_neg_finite", "non_neg_count", "non_neg_seconds", "tempo"])
@pytest.mark.parametrize("bad", [math.inf, math.nan])
def test_non_negative_types_reject_non_finite(field: str, bad: float) -> None:
    with pytest.raises(SchemaError):
        _Constrained(**{field: bad})


def test_non_negative_types_accept_zero() -> None:
    assert _Constrained(non_neg_finite=0.0, non_neg_count=0.0, non_neg_seconds=0.0)


@pytest.mark.parametrize(
    "adapter",
    [TypeAdapter(Meters), TypeAdapter(Seconds), TypeAdapter(Count), TypeAdapter(Degrees)],
)
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_base_number_aliases_reject_non_finite_outside_models(
    adapter: TypeAdapter[float], bad: float
) -> None:
    with pytest.raises(PydanticValidationError):
        adapter.validate_python(bad)
    assert adapter.validate_python(-3.5) == -3.5


@pytest.mark.parametrize("field", ["non_neg_finite", "non_neg_count", "non_neg_seconds"])
def test_non_negative_types_reject_negative(field: str) -> None:
    with pytest.raises(SchemaError):
        _Constrained(**{field: -0.5})


def test_regression_model_validate_strings_raises_schema_error() -> None:
    with pytest.raises(SchemaError) as caught:
        TempoSection.model_validate_strings({"at_count": "soon", "counts_per_minute": "120"})
    assert caught.value.context["model"] == "TempoSection"
    assert field_errors(caught.value)[0]["loc"] == "at_count"
    assert isinstance(caught.value.__cause__, PydanticValidationError)


def test_model_validate_strings_parses_valid_text() -> None:
    tempo = TempoSection.model_validate_strings({"at_count": "8", "counts_per_minute": "120"})
    assert (tempo.at_count, tempo.counts_per_minute) == (8.0, 120.0)


@pytest.mark.parametrize(
    "validator", [Box2.model_validate, Box2.model_validate_json, Box2.model_validate_strings]
)
def test_regression_validators_show_and_forward_pydantic_keywords(
    validator: Callable[..., Box2],
) -> None:
    """Names pydantic's keywords instead of hiding them behind `**options`."""
    parameters = inspect.signature(validator).parameters
    assert {"strict", "extra", "context", "by_alias", "by_name"} <= parameters.keys()
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in parameters.values())


def test_regression_model_validate_forwards_strict() -> None:
    with pytest.raises(SchemaError):
        Box2.model_validate({"min": ("0", 0), "max": (1, 1)}, strict=True)
    assert Box2.model_validate({"min": ("0", 0), "max": (1, 1)}).min == (0.0, 0.0)
    with pytest.raises(SchemaError):
        Box2.model_validate_json('{"min": ["0", 0], "max": [1, 1]}', strict=True)


class _Indexed(FrozenModel):
    values: tuple[int, ...]

    @cached_property
    def total(self) -> int:
        return sum(self.values)


def test_regression_model_copy_drops_cached_values() -> None:
    """Rebuilds a cached_property on the copy instead of carrying the stale value across."""
    original = _Indexed(values=(1, 2))
    assert original.total == 3
    updated = original.model_copy(update={"values": (5, 5)})
    assert updated.total == 10
    assert original.total == 3
    assert original.model_copy(deep=True).total == 3


def test_model_copy_without_cached_values_still_copies() -> None:
    box = Box2(min=(0.0, 0.0), max=(1.0, 1.0))
    assert box.model_copy() == box


def test_counts_per_minute_must_be_positive() -> None:
    adapter = TypeAdapter(CountsPerMinute)
    assert adapter.validate_python(120.0) == 120.0
    for bad in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(PydanticValidationError):
            adapter.validate_python(bad)


def test_heading_wraps_into_a_turn() -> None:
    adapter = TypeAdapter(Heading)
    assert adapter.validate_python(-90.0) == 270.0
    assert adapter.validate_python(360.0) == 0.0
    assert math.copysign(1.0, adapter.validate_python(-0.0)) == 1.0
    with pytest.raises(PydanticValidationError):
        adapter.validate_python(math.nan)


def test_region_requires_three_distinct_points() -> None:
    with pytest.raises(SchemaError):
        Region(exterior=((0.0, 0.0), (1.0, 1.0), (1.0, 1.0)))


def test_region_rejects_a_hole_outside_the_exterior() -> None:
    square = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))
    far = ((10.0, 10.0), (11.0, 10.0), (11.0, 11.0))
    with pytest.raises(SchemaError):
        Region(exterior=square, holes=(far,))


def test_region_accepts_a_valid_ring_with_a_hole() -> None:
    square = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))
    hole = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0))
    assert Region(exterior=square, holes=(hole,)).to_shapely().area == 15.5


def test_json_value_is_the_one_shared_alias() -> None:
    assert JsonValue is json_types.JsonValue is canonical.JsonValue


def test_json_object_stores_a_read_only_mapping() -> None:
    class Holder(FrozenModel):
        payload: JsonObject

    holder = Holder(payload={"a": [1, {"b": None}]})
    assert holder.payload == Holder(payload={"a": (1, {"b": None})}).payload
    assert holder.model_dump() == {"payload": {"a": [1, {"b": None}]}}
    with pytest.raises(TypeError):
        holder.payload["c"] = 1  # type: ignore[index]


class _JsonHolder(FrozenModel):
    payload: JsonObject


def test_json_object_freezes_nested_values() -> None:
    holder = _JsonHolder(payload={"k": [1, 2], "inner": {"n": [{"m": 1}]}})
    with pytest.raises(AttributeError):
        holder.payload["k"].append(3)  # type: ignore[union-attr]
    inner = holder.payload["inner"]
    assert isinstance(inner, ReadOnlyMap)
    with pytest.raises(TypeError):
        inner["n"] = 2  # type: ignore[index]
    assert holder.model_dump()["payload"]["k"] == [1, 2]


def test_json_object_dumps_plain_json_and_round_trips() -> None:
    holder = _JsonHolder(payload={"k": [1, {"n": [True, None]}], "s": "x"})
    dumped = holder.model_dump()
    assert dumped == {"payload": {"k": [1, {"n": [True, None]}], "s": "x"}}
    assert type(dumped["payload"]) is dict
    assert type(dumped["payload"]["k"]) is list
    assert type(dumped["payload"]["k"][1]) is dict
    assert json.loads(holder.model_dump_json()) == dumped
    assert _JsonHolder.model_validate(dumped) == holder
    assert _JsonHolder.model_validate_json(holder.model_dump_json()) == holder


def test_json_object_schema_is_a_plain_json_object() -> None:
    schema = _JsonHolder.model_json_schema()
    name = schema["properties"]["payload"]["$ref"].rsplit("/", 1)[-1]
    assert schema["$defs"][name] == {
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/JsonValue"},
    }
    assert {"type": "array", "items": {"$ref": "#/$defs/JsonValue"}} in schema["$defs"][
        "JsonValue"
    ]["anyOf"]


def test_json_object_models_hash_by_content() -> None:
    first = _JsonHolder(payload={"a": [1, {"b": 2}], "c": None})
    second = _JsonHolder(payload={"c": None, "a": [1, {"b": 2}]})
    assert hash(first) == hash(second)


HUGE_COORD_M = 2e300
VANISHING_TEMPO = 1e-320


def test_regression_placement_rejects_a_huge_coordinate() -> None:
    with pytest.raises(SchemaError):
        Placement(entity=SpotId("A1"), x=HUGE_COORD_M, y=0.0)


def test_regression_box2_rejects_a_huge_corner() -> None:
    with pytest.raises(SchemaError):
        Box2(min=(0.0, 0.0), max=(HUGE_COORD_M, 1.0))


def test_regression_tempo_section_rejects_a_vanishing_tempo() -> None:
    with pytest.raises(SchemaError):
        TempoSection(at_count=0.0, counts_per_minute=VANISHING_TEMPO)


def test_regression_spline_rejects_a_huge_control_point() -> None:
    recipe = {
        "kind": "spline",
        "points": [[0, 0], [1, 1], [HUGE_COORD_M, 0], [0, 0]],
        "alpha": 0.5,
        "closed": False,
    }
    with pytest.raises(SchemaError):
        curve_from_construction(recipe)


class _Bounded(FrozenModel):
    meters: Meters = 0.0
    positive_meters: PositiveMeters = 1.0
    count: Count = 0.0
    non_neg_count: NonNegCount = 0.0
    tempo: CountsPerMinute = 120.0
    point: Point = (0.0, 0.0)
    point3: Point3 = (0.0, 0.0, 0.0)
    ring: Ring = ()


def _past(bound: float, direction: float) -> float:
    return math.nextafter(bound, direction)


BOUND_CASES: dict[str, tuple[str, object, object]] = {
    "meters-max": ("meters", MAX_COORD_M, _past(MAX_COORD_M, math.inf)),
    "meters-min": ("meters", -MAX_COORD_M, _past(-MAX_COORD_M, -math.inf)),
    "positive-meters-max": ("positive_meters", MAX_COORD_M, _past(MAX_COORD_M, math.inf)),
    "count-max": ("count", MAX_COUNTS, _past(MAX_COUNTS, math.inf)),
    "count-min": ("count", -MAX_COUNTS, _past(-MAX_COUNTS, -math.inf)),
    "non-neg-count-max": ("non_neg_count", MAX_COUNTS, _past(MAX_COUNTS, math.inf)),
    "tempo-min": ("tempo", MIN_TEMPO, _past(MIN_TEMPO, 0.0)),
    "point-max": ("point", (MAX_COORD_M, 0.0), (_past(MAX_COORD_M, math.inf), 0.0)),
    "point-min": ("point", (0.0, -MAX_COORD_M), (0.0, _past(-MAX_COORD_M, -math.inf))),
    "point3-max": (
        "point3",
        (0.0, 0.0, MAX_COORD_M),
        (0.0, 0.0, _past(MAX_COORD_M, math.inf)),
    ),
    "ring-max": ("ring", ((MAX_COORD_M, 0.0),), ((_past(MAX_COORD_M, math.inf), 0.0),)),
}


@pytest.mark.parametrize(
    ("field", "accepted", "refused"), BOUND_CASES.values(), ids=BOUND_CASES.keys()
)
def test_bounded_aliases_accept_their_bound(field: str, accepted: object, refused: object) -> None:
    del refused
    assert getattr(_Bounded.model_validate({field: accepted}), field) == accepted


@pytest.mark.parametrize(
    ("field", "accepted", "refused"), BOUND_CASES.values(), ids=BOUND_CASES.keys()
)
def test_bounded_aliases_refuse_the_next_float_past_their_bound(
    field: str, accepted: object, refused: object
) -> None:
    del accepted
    with pytest.raises(SchemaError):
        _Bounded.model_validate({field: refused})
