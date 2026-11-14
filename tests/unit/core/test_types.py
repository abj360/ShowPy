#!/usr/bin/env python3

"""
test_types.py --- tests the JSON-safe Region and Box2 models and the FrozenModel base

Contains:
    NON_FINITE_MODELS: builders that put NaN or infinity into one float field per model family
    test_*(): one behavior of showpy.core.types each
    test_regression_region_rejects_fewer_than_three_points: rejects a two-point region
    test_regression_region_rejects_nan_points: rejects NaN ring coordinates
    test_regression_region_rejects_self_intersection: rejects a bow-tie ring
    test_regression_frozen_models_reject_non_finite_floats: rejects NaN and infinity in Box2,
        TempoSection, Waypoint, Shape and Landmark fields
"""

import math
from collections.abc import Callable

import pytest
import shapely
from pydantic import ValidationError as PydanticValidationError

from showpy.cast.models import Shape
from showpy.choreo.models import Waypoint
from showpy.core.errors import ShowPyError
from showpy.core.types import Box2, FrozenModel, Region
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


@pytest.mark.defect("UP-9.3-37")
@pytest.mark.xfail(strict=True, reason="UP-9.3-37")
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


@pytest.mark.defect("UP-4.1-01")
@pytest.mark.xfail(strict=True, reason="UP-4.1-01")
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
