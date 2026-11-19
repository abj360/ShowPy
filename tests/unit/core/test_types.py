#!/usr/bin/env python3

"""
test_types.py --- tests the JSON-safe Region and Box2 models and the FrozenModel base

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
"""

import inspect
import math
from collections.abc import Callable

import pytest
import shapely
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from showpy.cast.models import Shape
from showpy.choreo.models import Set, Waypoint
from showpy.core.errors import SchemaError, ShowPyError, field_errors
from showpy.core.types import (
    Box2,
    Count,
    CountsPerMinute,
    Degrees,
    FrozenModel,
    Meters,
    NonNegCount,
    NonNegFinite,
    NonNegSeconds,
    PositiveFinite,
    PositiveMeters,
    Region,
    Seconds,
)
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
    "landmark-nan": lambda: Landmark(id="a", name="a", kind="point", geometry=((math.nan, 0.0),)),
    "landmark-inf": lambda: Landmark(id="a", name="a", kind="point", geometry=((math.inf, 0.0),)),
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


@pytest.mark.defect("UP-9.3-36")
@pytest.mark.xfail(strict=True, reason="UP-9.3-36")
def test_regression_region_rejects_fewer_than_three_points() -> None:
    """Rejects a two-point region, which has no area (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, 1.0)))


def test_regression_region_rejects_nan_points() -> None:
    """Rejects NaN coordinates (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, float("nan")), (1.0, 1.0)))


@pytest.mark.defect("UP-9.3-38")
@pytest.mark.xfail(strict=True, reason="UP-9.3-38")
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
        Set(id="1", counts=0.0).replace(counts=NEGATIVE_COUNTS)


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
