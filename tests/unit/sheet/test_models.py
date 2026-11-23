#!/usr/bin/env python3

"""
test_models.py --- tests sheet data classes: sheets, guides, grids

Contains:
    test_*(): one behavior of e8s.sheet.models each
    test_regression_sheet_rejects_negative_size(): rejects a negative sheet size at construction
    test_regression_sheet_rejects_one_point_outline(): rejects an outline with fewer than three
        points at construction
    test_regression_sheet_rejects_malformed_ref(): rejects the ref "nonsense" at construction
    test_regression_region_rejects_two_point_ring(): rejects a two-point region ring, which has no
        area
    test_regression_region_rejects_bow_tie_ring(): rejects a self-intersecting bow-tie ring
    test_regression_guide_line_rejects_no_points(): rejects a guide line with no points at
        construction
    test_regression_sheet_rejects_zero_and_non_finite_size(): rejects a size of 0 or NaN
    test_regression_sheet_rejects_self_crossing_outline(): rejects a bow-tie outline
    test_regression_sheet_ref_must_be_a_versioned_sheet_reference(): rejects another kind or a
        missing version in ref and based_on, and accepts build metadata
    test_regression_sheet_rejects_repeated_guide_ids(): rejects two guides sharing an ID
    test_regression_item_ids_follow_the_sheet_id_rule(): rejects item IDs with spaces or `/`
    test_bundled_sheets_still_construct(): builds every bundled template
"""

import pytest

from e8s.core.errors import E8sError, GeometryError, SchemaError
from e8s.core.types import Region
from e8s.sheet.models import GuideLine, Sheet
from e8s.sheet.templates import blank_sheet, sheet_templates

pytestmark = pytest.mark.filterwarnings("ignore::e8s.core.errors.DeprecatedWarning")

SQUARE = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def _sheet_with(**changes: object) -> Sheet:
    return Sheet.model_validate({**blank_sheet(10, 6).model_dump(), **changes})


def test_regression_sheet_rejects_negative_size() -> None:
    """Rejects a negative sheet size at construction (audit 9.4)."""
    with pytest.raises(SchemaError):
        _sheet_with(size_m=(-10.0, 6.0))


def test_regression_sheet_rejects_one_point_outline() -> None:
    """Rejects an outline with fewer than three points at construction (audit 9.4)."""
    with pytest.raises(GeometryError):
        _sheet_with(outline=((0.0, 0.0),))


def test_regression_sheet_rejects_malformed_ref() -> None:
    """Rejects the ref "nonsense" at construction (audit 9.4)."""
    with pytest.raises(SchemaError):
        _sheet_with(ref="nonsense")


def test_regression_region_rejects_two_point_ring() -> None:
    """Rejects a two-point region ring, which has no area (audit 9.4)."""
    with pytest.raises(E8sError):
        Region(exterior=((0.0, 0.0), (1.0, 1.0)))


def test_regression_region_rejects_bow_tie_ring() -> None:
    """Rejects a self-intersecting bow-tie ring (audit 9.4)."""
    with pytest.raises(E8sError):
        Region(exterior=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)))


def test_regression_guide_line_rejects_no_points() -> None:
    """Rejects a guide line with no points at construction (audit 9.4)."""
    with pytest.raises(E8sError):
        GuideLine(id="empty", kind="custom", points=())


@pytest.mark.parametrize("size", [(0.0, 6.0), (10.0, 0.0), (float("nan"), 6.0), (10.0, -1.0)])
def test_regression_sheet_rejects_zero_and_non_finite_size(size: tuple[float, float]) -> None:
    """Rejects a zero, negative or NaN side of the sheet size."""
    with pytest.raises(SchemaError):
        _sheet_with(size_m=size)


def test_regression_sheet_rejects_self_crossing_outline() -> None:
    """Rejects a bow-tie outline, which has no inside."""
    with pytest.raises(SchemaError):
        _sheet_with(outline=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)))


@pytest.mark.parametrize("field", ["ref", "based_on"])
@pytest.mark.parametrize("ref", ["show:gym@1", "sheet:gym", "gym@1", "sheet:gym@a/b"])
def test_regression_sheet_ref_must_be_a_versioned_sheet_reference(field: str, ref: str) -> None:
    """Rejects a reference of another kind or without a version."""
    with pytest.raises(SchemaError):
        _sheet_with(**{field: ref})


def test_sheet_ref_accepts_build_metadata() -> None:
    sheet = _sheet_with(ref="sheet:gym@1.0.0+build.5", based_on="sheet:gym@1.0.0")
    assert sheet.ref == "sheet:gym@1.0.0+build.5"


def test_regression_sheet_rejects_repeated_guide_ids() -> None:
    """Rejects two guides with one ID, since add and remove address them by ID."""
    guide = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
    with pytest.raises(SchemaError):
        _sheet_with(guides=(guide, guide))


@pytest.mark.parametrize("bad_id", ["a b", "a/b", "..", "all", "-x"])
def test_regression_item_ids_follow_the_sheet_id_rule(bad_id: str) -> None:
    """Rejects guide IDs the sheet ID rule refuses."""
    with pytest.raises(SchemaError):
        GuideLine(id=bad_id, kind="custom", points=((0.0, 0.0), (1.0, 0.0)))
    assert GuideLine(id="end-zone", kind="custom", points=((0.0, 0.0),)).id == "end-zone"


def test_bundled_sheets_still_construct() -> None:
    assert len(sheet_templates()) >= 1
