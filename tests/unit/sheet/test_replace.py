#!/usr/bin/env python3

"""
test_replace.py --- tests sheet replacement: fits, placements, forms, paths, locks and the report
"""

import warnings

import numpy as np
import pytest

from showpy.choreo.models import Form, PathRow, PathStyle, TransitionSpec, Waypoint
from showpy.core.errors import GeometryError, LockedError, SchemaError, ShowPyError
from showpy.core.types import Region
from showpy.core.util import approx_equal
from showpy.geo.affine import rotation_matrix
from showpy.geo.curves import Segment, curve_from_construction
from showpy.sheet.models import Landmark, Sheet, Zone, ZoneKind
from showpy.sheet.replace import replace_sheet, similarity_fit
from showpy.sheet.templates import add_zone, blank_sheet, get_sheet
from showpy.show.models import Show
from tests.fixtures.shows import field_sheet, two_set_show

TOL = 1e-9
LEFT = Landmark(id="l", name="left mark", kind="point", geometry=((-10.0, 10.0),))
RIGHT = Landmark(id="r", name="right mark", kind="point", geometry=((10.0, 10.0),))
BOX = Region(exterior=((-1.0, 4.0), (1.0, 4.0), (1.0, 6.0), (-1.0, 6.0)))


def _shifted_sheet(dy: float) -> Sheet:
    base = field_sheet()
    moved = tuple(
        lm.model_copy(update={"geometry": ((lm.point[0], lm.point[1] + dy),)})
        for lm in (*base.landmarks, LEFT, RIGHT)
    )
    return base.model_copy(update={"ref": "sheet:shifted@3", "landmarks": moved})


def _show_with_marks() -> Show:
    show = two_set_show()
    sheet = show.sheet.model_copy(update={"landmarks": (*show.sheet.landmarks, LEFT, RIGHT)})
    return show.model_copy(update={"sheet": sheet})


def test_origin_mapping_keeps_coordinates_and_flags_outside() -> None:
    show = two_set_show()
    replaced, report = replace_sheet(show, blank_sheet(10.0, 8.0), mapping="origin")
    assert replaced.sets == show.sets
    assert report.mapping == "origin"
    assert report.moved == {}
    assert report.outside == ("T1", "T2", "T3", "T4")
    assert replaced.sheet.ref == "sheet:blank-10x8@1"
    assert replaced.dependencies == {"sheet:blank-10x8": "1"}


def test_scale_mapping_scales_by_size_ratio() -> None:
    show = two_set_show()
    replaced, report = replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")
    assert replaced.set("1").get("T2").point == (4.0, 10.0)
    assert report.moved["T2"] == ((2.0, 9.0), (4.0, 18.0))
    assert report.outside == ()


def test_landmark_mapping_fits_shared_points() -> None:
    replaced, report = replace_sheet(_show_with_marks(), _shifted_sheet(2.0))
    assert report.mapping == "landmarks"
    assert approx_equal(replaced.set("1").get("T3").point, (4.0, 7.0), TOL)
    assert approx_equal(replaced.set("2").get("T3").facing, 0.0, TOL)
    assert replaced.dependencies == {"sheet:shifted": "3"}


def test_landmark_mapping_rotates_facings() -> None:
    show = _show_with_marks()
    turned = rotation_matrix(90.0, (0.0, 0.0))
    marks = tuple(
        lm.model_copy(
            update={"geometry": (tuple(float(v) for v in turned[:2, :2] @ np.array(lm.point)),)}
        )
        for lm in show.sheet.landmarks
    )
    sheet = show.sheet.model_copy(update={"ref": "sheet:turned@1", "landmarks": marks})
    replaced, _ = replace_sheet(show, sheet)
    assert approx_equal(replaced.set("1").get("T2").point, (-5.0, 2.0), TOL)
    assert approx_equal(replaced.set("1").get("T2").facing, 90.0, TOL)


def test_landmark_mapping_falls_back_to_origin_with_a_note() -> None:
    show = two_set_show()
    replaced, report = replace_sheet(show, blank_sheet(40.0, 20.0))
    assert report.mapping == "origin"
    assert replaced.sets == show.sets
    assert any("fewer than 2" in note for note in report.form_notes)


def test_form_curves_are_mapped_and_others_noted() -> None:
    curve = Segment(a=(0.0, 5.0), b=(6.0, 5.0)).construction()
    forms = {
        "line": Form(id="line", set="1", kind="line", geometry={"curve": curve}, members=("T1",)),
        "fill": Form(id="fill", set="1", kind="fill", geometry={"rows": 2}, members=("T2",)),
    }
    show = two_set_show().model_copy(update={"forms": forms})
    replaced, report = replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")
    mapped = curve_from_construction(replaced.forms["line"].geometry["curve"])
    assert isinstance(mapped, Segment)
    assert mapped.b == (12.0, 10.0)
    assert replaced.forms["fill"] == forms["fill"]
    assert any("fill" in note for note in report.form_notes)


def test_bad_form_curve_raises() -> None:
    form = Form(id="bad", set="1", kind="curve", geometry={"curve": {"kind": "?"}}, members=())
    show = two_set_show().model_copy(update={"forms": {"bad": form}})
    with pytest.raises(SchemaError):
        replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")


def test_path_waypoints_and_pivots_follow_the_map() -> None:
    row = PathRow(entity="T1", style=PathStyle.CURVE, waypoints=(Waypoint(x=1.0, y=7.0),))
    spin = PathRow(entity="T2", style=PathStyle.ROTATE, pivot=(2.0, 7.0), sweep_deg=90.0)
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": row, "T2": spin})
    show = two_set_show().model_copy(update={"transitions": (spec,)})
    replaced, _ = replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")
    rows = replaced.transitions[0].rows
    assert rows["T1"].waypoints[0].x == 2.0
    assert rows["T1"].waypoints[0].y == 14.0
    assert rows["T2"].pivot == (4.0, 14.0)


def test_pinned_placements_need_force() -> None:
    show = two_set_show()
    first = show.set("1").with_placements({"T2": {"pinned": True}})
    show = show.model_copy(update={"sets": (first, show.sets[1])})
    with pytest.raises(LockedError):
        replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")
    replaced, _ = replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale", force=True)
    assert replaced.set("1").get("T2").point == (4.0, 10.0)
    same, _ = replace_sheet(show, blank_sheet(10.0, 8.0), mapping="origin")
    assert same.sets == show.sets


def test_locked_forms_need_force() -> None:
    curve = Segment(a=(0.0, 5.0), b=(6.0, 5.0)).construction()
    form = Form(id="f", set="1", kind="line", geometry={"curve": curve}, members=(), locked=True)
    show = two_set_show().model_copy(update={"forms": {"f": form}})
    with pytest.raises(LockedError):
        replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")


def test_restricted_zones_are_reported_but_staging_is_not() -> None:
    sheet = add_zone(blank_sheet(40.0, 20.0), Zone(id="no", kind=ZoneKind.RESTRICTED, polygon=BOX))
    sheet = add_zone(sheet, Zone(id="props", kind=ZoneKind.STAGING, polygon=BOX))
    _, report = replace_sheet(two_set_show(), sheet, mapping="origin")
    assert report.in_zones == (("T1", "no"),)


def test_zone_windows_use_the_set_count() -> None:
    late = Zone(id="late", kind=ZoneKind.SAFETY, polygon=BOX, active=((100.0, 200.0),))
    _, report = replace_sheet(two_set_show(), add_zone(blank_sheet(40.0, 20.0), late), "origin")
    assert report.in_zones == ()


def test_input_show_is_untouched() -> None:
    show = two_set_show()
    snapshot = show.model_dump()
    replace_sheet(show, blank_sheet(80.0, 40.0), mapping="scale")
    assert show.model_dump() == snapshot


def test_similarity_fit_needs_spread_points() -> None:
    with pytest.raises(GeometryError):
        similarity_fit(np.array([[1.0, 1.0], [1.0, 1.0]]), np.array([[0.0, 0.0], [2.0, 2.0]]))


SPOT_TOL = 1e-6


@pytest.mark.defect("UP-9.4-01")
@pytest.mark.xfail(strict=True, reason="UP-9.4-01")
def test_regression_football_template_swap_keeps_positions() -> None:
    """Keeps every spot still when swapping football fields (audit 9.4; P0 #11)."""
    show = two_set_show().model_copy(update={"sheet": get_sheet("sheet:hs-football")})
    replaced, report = replace_sheet(show, get_sheet("sheet:nfl-football"))
    assert report.moved == {}
    for before, after in zip(show.sets, replaced.sets, strict=True):
        for a, b in zip(before.placements, after.placements, strict=True):
            assert approx_equal(a.point, b.point, SPOT_TOL)


@pytest.mark.defect("UP-9.4-02")
@pytest.mark.xfail(strict=True, reason="UP-9.4-02")
def test_regression_similarity_fit_rejects_mirrored_landmarks() -> None:
    """Rejects a mirrored landmark set instead of fitting a wrong scale (audit 9.4; P0 #11)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 3.0]])
    dst = np.array([[0.0, 0.0], [-4.0, 0.0], [0.0, 3.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-03")
@pytest.mark.xfail(strict=True, reason="UP-9.4-03")
def test_regression_similarity_fit_rejects_coincident_targets() -> None:
    """Rejects coincident target landmarks instead of collapsing the show (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[1.0, 1.0], [1.0, 1.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-04")
@pytest.mark.xfail(strict=True, reason="UP-9.4-04")
def test_regression_similarity_fit_empty_input_raises_without_warning() -> None:
    """Raises GeometryError for no matches without a numpy RuntimeWarning (audit 9.4)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(GeometryError):
            similarity_fit(np.empty((0, 2)), np.empty((0, 2)))


@pytest.mark.defect("UP-9.4-05")
@pytest.mark.xfail(strict=True, reason="UP-9.4-05")
def test_regression_similarity_fit_rejects_nan_input() -> None:
    """Raises GeometryError for NaN points instead of leaking LinAlgError (audit 9.4)."""
    src = np.array([[0.0, 0.0], [4.0, 0.0]])
    dst = np.array([[np.nan, 0.0], [1.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, dst)


@pytest.mark.defect("UP-9.4-06")
@pytest.mark.xfail(strict=True, reason="UP-9.4-06")
def test_regression_similarity_fit_rejects_wrong_shape() -> None:
    """Raises GeometryError for non-2D points instead of leaking numpy errors (audit 9.4)."""
    src = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]])
    with pytest.raises(GeometryError):
        similarity_fit(src, src.copy())


@pytest.mark.defect("UP-9.4-07")
@pytest.mark.xfail(strict=True, reason="UP-9.4-07")
def test_regression_scale_mapping_rejects_zero_size_sheet() -> None:
    """Raises a ShowPyError for a zero-size source sheet, not ZeroDivisionError (audit 9.4)."""
    flat = blank_sheet(10, 6).model_copy(update={"size_m": (0.0, 0.0)})
    show = two_set_show().model_copy(update={"sheet": flat})
    with pytest.raises(ShowPyError):
        replace_sheet(show, blank_sheet(20, 12), "scale")


@pytest.mark.defect("UP-9.4-43")
@pytest.mark.xfail(strict=True, reason="UP-9.4-43")
def test_regression_blank_to_stage_does_not_match_unrelated_centers() -> None:
    """Keeps positions when blank and stage centers mean different points (audit 9.4)."""
    show = two_set_show().model_copy(update={"sheet": blank_sheet(20.0, 15.0)})
    _, report = replace_sheet(show, get_sheet("sheet:proscenium-stage"))
    assert report.moved == {}
