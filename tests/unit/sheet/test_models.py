#!/usr/bin/env python3

"""
test_models.py --- tests sheet data classes: landmarks, zones, grids, coordinate systems

Contains:
    test_*(): one behavior of e8s.sheet.models each
    test_regression_sheet_rejects_negative_size(): rejects a negative sheet size at construction
    test_regression_sheet_rejects_one_point_outline(): rejects an outline with fewer than three
        points at construction
    test_regression_sheet_rejects_malformed_ref(): rejects the ref "nonsense" at construction
    test_regression_sheet_rejects_duplicate_landmark_ids(): rejects two landmarks sharing an ID at
        construction
    test_regression_region_rejects_two_point_ring(): rejects a two-point region ring, which has no
        area
    test_regression_region_rejects_bow_tie_ring(): rejects a self-intersecting bow-tie ring
    test_regression_guide_line_rejects_no_points(): rejects a guide line with no points at
        construction
    test_regression_sheet_rejects_landmark_alias_colliding_with_name(): rejects landmarks whose name
        and alias collide instead of resolving by order
    test_regression_show_rejects_zone_id_shared_with_sheet(): rejects a show zone reusing a sheet
        zone ID
    test_regression_sheet_rejects_zero_and_non_finite_size(): rejects a size of 0 or NaN
    test_regression_sheet_rejects_self_crossing_outline(): rejects a bow-tie outline
    test_regression_sheet_ref_must_be_a_versioned_sheet_reference(): rejects another kind or a
        missing version in ref and based_on, and accepts build metadata
    test_regression_sheet_rejects_repeated_zone_and_guide_ids(): rejects two zones or two guides
        sharing an ID
    test_regression_sheet_rejects_landmark_id_colliding_with_another_name(): rejects a landmark ID
        that another landmark's name answers to
    test_regression_item_ids_follow_the_sheet_id_rule(): rejects item IDs with spaces or `/`
    test_regression_coordinate_system_rejects_non_positive_precision(): rejects 0, negative and NaN
    test_regression_coordinate_labels_are_text_and_read_only(): refuses non-text labels and edits
    test_bundled_sheets_still_construct(): builds every bundled template
"""

import pytest

from e8s.core.errors import E8sError, GeometryError, SchemaError, TimeRangeError
from e8s.core.ids import LandmarkId, ZoneId
from e8s.core.types import Region
from e8s.examples import two_set_show
from e8s.sheet.models import CoordinateSystem, GuideLine, Landmark, Sheet, Zone, ZoneKind
from e8s.sheet.templates import blank_sheet, sheet_templates
from e8s.show.models import Show

pytestmark = pytest.mark.filterwarnings("ignore::e8s.core.errors.DeprecatedWarning")

SQUARE = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def test_landmark_geometry_must_match_kind() -> None:
    assert Landmark(
        id=LandmarkId("c"), name="center", kind="point", geometry=((0.0, 0.0),)
    ).point == (0, 0)
    with pytest.raises(GeometryError):
        Landmark(id=LandmarkId("c"), name="center", kind="point", geometry=((0, 0), (1, 1)))
    with pytest.raises(GeometryError):
        Landmark(id=LandmarkId("l"), name="line", kind="line", geometry=((0, 0),))
    with pytest.raises(GeometryError):
        Landmark(id=LandmarkId("a"), name="area", kind="area", geometry=((0, 0), (1, 1)))


def test_zone_active_windows_validated() -> None:
    zone = Zone(id=ZoneId("pit"), kind=ZoneKind.PIT, polygon=SQUARE, active=((0.0, 16.0),))
    assert zone.is_active(8.0)
    assert not zone.is_active(20.0)
    assert Zone(id=ZoneId("z"), kind=ZoneKind.CUSTOM, polygon=SQUARE).is_active(1e9)
    with pytest.raises(TimeRangeError):
        Zone(id=ZoneId("z"), kind=ZoneKind.CUSTOM, polygon=SQUARE, active=((5.0, 5.0),))


def test_coordinate_system_defaults() -> None:
    system = CoordinateSystem()
    assert system.units == "m"
    assert system.origin == (0.0, 0.0)


def _sheet_with(**changes: object) -> Sheet:
    return Sheet.from_json({**blank_sheet(10, 6).to_json(), **changes})


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


def test_regression_sheet_rejects_duplicate_landmark_ids() -> None:
    """Rejects two landmarks sharing an ID at construction (audit 9.4)."""
    mark = Landmark(id=LandmarkId("dup"), name="first", kind="point", geometry=((1.0, 1.0),))
    other = Landmark(id=LandmarkId("dup"), name="second", kind="point", geometry=((2.0, 2.0),))
    with pytest.raises(SchemaError):
        _sheet_with(landmarks=(mark, other))


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


def test_regression_sheet_rejects_landmark_alias_colliding_with_name() -> None:
    """Rejects landmarks whose name and alias collide instead of resolving by order (audit 9.4)."""
    first = Landmark(id=LandmarkId("a"), name="spike", kind="point", geometry=((1.0, 1.0),))
    second = Landmark(
        id=LandmarkId("b"), name="other", kind="point", geometry=((2.0, 2.0),), aliases=("Spike",)
    )
    with pytest.raises(SchemaError):
        _sheet_with(landmarks=(first, second))


@pytest.mark.defect("UP-9.4-45")
@pytest.mark.xfail(strict=True, reason="UP-9.4-45")
def test_regression_show_rejects_zone_id_shared_with_sheet() -> None:
    """Rejects a show zone reusing a sheet zone ID (audit 9.4)."""
    base = two_set_show()
    sheet_zone = base.sheet.zones[0]
    clash = Zone(id=sheet_zone.id, kind=ZoneKind.CUSTOM, polygon=SQUARE)
    with pytest.raises(SchemaError):
        Show.from_json({**base.to_json(), "zones": (clash,)})


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


def test_regression_sheet_rejects_repeated_zone_and_guide_ids() -> None:
    """Rejects two zones or two guides with one ID, since add and remove address them by ID."""
    zone = Zone(id=ZoneId("pit"), kind=ZoneKind.PIT, polygon=SQUARE)
    guide = GuideLine(id="tape", kind="custom", points=((0.0, 1.0), (1.0, 1.0)))
    with pytest.raises(SchemaError):
        _sheet_with(zones=(zone, zone))
    with pytest.raises(SchemaError):
        _sheet_with(guides=(guide, guide))


def test_regression_sheet_rejects_landmark_id_colliding_with_another_name() -> None:
    """Rejects a landmark whose ID is the name of another, which lookup could not tell apart."""
    first = Landmark(id=LandmarkId("hash"), name="a", kind="point", geometry=((1.0, 1.0),))
    second = Landmark(id=LandmarkId("b"), name="Hash", kind="point", geometry=((2.0, 2.0),))
    with pytest.raises(SchemaError):
        _sheet_with(landmarks=(first, second))


def test_landmark_may_repeat_its_own_name_as_id_and_alias() -> None:
    mark = Landmark(
        id=LandmarkId("center"),
        name="Center",
        kind="point",
        geometry=((1.0, 1.0),),
        aliases=("center",),
    )
    assert _sheet_with(landmarks=(mark,)).landmarks == (mark,)


@pytest.mark.parametrize("bad_id", ["a b", "a/b", "..", "all", "-x"])
def test_regression_item_ids_follow_the_sheet_id_rule(bad_id: str) -> None:
    """Rejects landmark, zone and guide IDs the sheet ID rule refuses."""
    with pytest.raises(SchemaError):
        Landmark(id=LandmarkId(bad_id), name="n", kind="point", geometry=((0.0, 0.0),))
    with pytest.raises(SchemaError):
        Zone(id=ZoneId(bad_id), kind=ZoneKind.CUSTOM, polygon=SQUARE)
    with pytest.raises(SchemaError):
        GuideLine(id=bad_id, kind="custom", points=((0.0, 0.0), (1.0, 0.0)))
    assert GuideLine(id="end-zone", kind="custom", points=((0.0, 0.0),)).id == "end-zone"


@pytest.mark.parametrize("precision", [0.0, -0.5, float("nan")])
def test_regression_coordinate_system_rejects_non_positive_precision(precision: float) -> None:
    """Rejects a rounding step of 0, below 0 or NaN at construction."""
    with pytest.raises(SchemaError):
        CoordinateSystem(precision=precision)


def test_regression_coordinate_labels_are_text_and_read_only() -> None:
    """Refuses a non-text label value and an edit of the stored labels."""
    with pytest.raises(SchemaError):
        CoordinateSystem.from_json({"labels": {"left": 3}})
    system = CoordinateSystem(labels={"left": "SR"})
    assert system.labels["left"] == "SR"
    assert hash(system) == hash(CoordinateSystem(labels={"left": "SR"}))
    with pytest.raises(TypeError):
        system.labels["left"] = "SL"  # type: ignore[index]


def test_bundled_sheets_still_construct() -> None:
    assert len(sheet_templates()) >= 1
