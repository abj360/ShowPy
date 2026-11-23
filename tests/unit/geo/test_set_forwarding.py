#!/usr/bin/env python3

"""
test_set_forwarding.py --- tests that geo functions given a Set warn and match their choreo twin

Contains:
    test_geo_set_overloads_warn_and_forward: calls each geo function with a Set, expects a
        DeprecatedWarning at the caller and the choreo twin's result
    test_regression_geo_set_stubs_reject_other_types_with_a_teaching_error: raises SchemaError for
        balance(5, 6) and on_field_ids(5) instead of AttributeError
    test_regression_geo_transform_rejects_a_list_without_a_deprecation_warning: raises SchemaError
        for shift([[1.0, 2.0]], ...) and never warns that a Set is replaced
"""

import importlib
from collections.abc import Callable
from typing import cast

import numpy as np
import pytest

from e8s.choreo.set_transforms import shift
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.errors import SchemaError
from e8s.geo import analysis, measure, transforms
from e8s.sheet.models import GuideLine
from tests.fixtures.shows import field_sheet
from tests.helpers import make_facing_set

ROWS = (("A", 0.0, 0.0, 0.0), ("B", 3.0, 1.0, 0.0), ("C", -2.0, 2.0, 0.0))
SPOTS = make_facing_set(*ROWS)
SHEET = field_sheet().model_copy(
    update={"guides": (GuideLine(id="y5", kind="yard", points=((5.0, 0.0), (5.0, 20.0))),)}
)
SHIFTED = shift(SPOTS, 1.0, 0.0)
AFFINE_MATRIX = [[1.0, 0.0, 1.0], [0.0, 1.0, 0.0]]
TRANSFORMS = ("transforms", "set_transforms")
SNAPPING = ("snapping", "set_snapping")
MEASURE = ("measure", "set_measure")
ANALYSIS = ("analysis", "set_analysis")

type Call = tuple[tuple[str, str], str, tuple[object, ...], dict[str, object]]

CALLS: list[Call] = [
    (TRANSFORMS, "shift", (SPOTS, 1.0, 2.0), {}),
    (TRANSFORMS, "move_to", (SPOTS, "front-left", (5.0, 5.0)), {}),
    (TRANSFORMS, "rotate", (SPOTS, 30.0), {}),
    (TRANSFORMS, "scale", (SPOTS, 2.0), {}),
    (TRANSFORMS, "expand", (SPOTS, 1.0), {}),
    (TRANSFORMS, "contract", (SPOTS, 0.1), {}),
    (TRANSFORMS, "set_interval", (SPOTS, 1.0), {}),
    (TRANSFORMS, "mirror", (SPOTS, "center"), {}),
    (TRANSFORMS, "symmetrize", (SPOTS,), {}),
    (TRANSFORMS, "skew", (SPOTS, 10.0), {}),
    (TRANSFORMS, "affine", (SPOTS, AFFINE_MATRIX), {}),
    (SNAPPING, "snap", (SPOTS, 0.5), {}),
    (SNAPPING, "snap_to_guides", (SPOTS, SHEET, 6.0), {}),
    (SNAPPING, "align", (SPOTS, "y", "min"), {}),
    (SNAPPING, "distribute", (SPOTS, "x"), {}),
    (MEASURE, "on_field_ids", (SPOTS,), {}),
    (MEASURE, "neighbors", (SPOTS, "A"), {}),
    (MEASURE, "nearest", (SPOTS, (0.0, 0.0)), {}),
    (MEASURE, "symmetry_error", (SPOTS,), {}),
    (ANALYSIS, "density_map", (SPOTS, SHEET), {}),
    (ANALYSIS, "coverage", (SPOTS, SHEET), {}),
    (ANALYSIS, "balance", (SPOTS, SHEET), {}),
    (ANALYSIS, "symmetry_score", (SPOTS,), {}),
    (ANALYSIS, "change_amount", (SPOTS, SHIFTED), {}),
]


def _same(a: object, b: object) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return bool(np.array_equal(cast(np.ndarray, a), cast(np.ndarray, b)))
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _function(package: str, module: str, name: str) -> Callable[..., object]:
    loaded = importlib.import_module(f"e8s.{package}.{module}")
    return cast(Callable[..., object], getattr(loaded, name))


@pytest.mark.parametrize(("modules", "name", "args", "kwargs"), CALLS, ids=[c[1] for c in CALLS])
def test_geo_set_overloads_warn_and_forward(
    modules: tuple[str, str], name: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    geo_module, choreo_module = modules
    old = _function("geo", geo_module, name)
    twin = _function("choreo", choreo_module, name)
    with pytest.warns(DeprecatedWarning, match=f"geo.{geo_module}.{name}") as caught:
        forwarded = old(*args, **kwargs)
    assert len(caught) == 1
    assert caught[0].category is DeprecatedWarning
    assert caught[0].filename == __file__
    assert f"choreo.{choreo_module}.{name}" in str(caught[0].message)
    assert _same(forwarded, twin(*args, **kwargs))


def test_regression_geo_set_stubs_reject_other_types_with_a_teaching_error() -> None:
    with pytest.raises(SchemaError, match="`s` must be"), pytest.warns(DeprecatedWarning):
        analysis.balance(5, 6)
    with pytest.raises(SchemaError, match="`s` must be"), pytest.warns(DeprecatedWarning):
        measure.on_field_ids(5)


def test_regression_geo_transform_rejects_a_list_without_a_deprecation_warning() -> None:
    not_points = cast(np.ndarray, [[1.0, 2.0]])
    with pytest.raises(SchemaError, match="`obj` must be"):
        transforms.shift(not_points, 1.0, 1.0)
