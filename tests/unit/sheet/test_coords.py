#!/usr/bin/env python3

"""
test_coords.py --- tests coordinate presets, wording, parsing and audience-side handling

Contains:
    test_*(): one behavior of e8s.sheet.coords each
    test_regression_zero_precision_is_rejected_not_division_by_zero(): rejects precision=0 with a
        E8sError, however the system gets into a show
    test_regression_to_coords_rejects_infinite_point(): rejects an infinite point with a
        E8sError, not decimal.InvalidOperation
    test_regression_to_coords_rejects_unrepresentable_magnitude(): rejects a finite point too
        large to round with an E8sError, not decimal.InvalidOperation
    test_regression_to_coords_rejects_nan_point(): rejects a NaN point instead of printing "nan m
        right of center"
    test_regression_from_coords_rejects_overflowing_number(): rejects a number too large for a
        float instead of returning an infinite point
    test_regression_unvalidated_origin_is_rejected(): rejects a NaN, infinite or malformed origin
        instead of returning or printing garbage
    test_regression_from_coords_rejects_overflowing_result(): rejects a point whose sum with a huge
        unchecked origin overflows, refuses that origin itself, and rejects the same text from the
        largest legal origin
    test_regression_from_coords_refuses_a_point_past_max_coord(): refuses a read point past
        MAX_COORD_M with a hint naming the bound, and reads one at the bound
    test_regression_unvalidated_audience_side_is_rejected(): rejects an audience side outside the
        four sides with an E8sError, not KeyError
    test_regression_injected_coordinate_parts_raise_schema_error(): rejects wrong-typed parts from
        set_coordinates, to_coords and from_coords with SchemaError, never TypeError
    test_regression_to_coords_rejects_unknown_style(): rejects an unknown style instead of silently
        using long style
    test_regression_short_label_with_digit_round_trips_or_is_rejected(): round-trips a short
        direction label holding a digit, or rejects it
    test_regression_colliding_direction_labels_round_trip_or_are_rejected(): round-trips two
        directions sharing one label, or rejects them
    test_regression_from_coords_reads_negative_long_number(): reads "-3.5 m right of center" as 3.5
        m to the left
    test_regression_from_coords_reads_leading_dot_number(): reads the short code "R.5" as 0.5 m
        right
    test_regression_from_coords_reads_exponent_number(): reads the short code "R1e3" as 1000 m right
    test_regression_from_coords_reads_number_glued_to_unit(): reads "3m right of center" with no
        space before the unit
"""

from typing import Any

import numpy as np
import pytest

from e8s.core.caps import MAX_COORD_M
from e8s.core.errors import E8sError, SchemaError, UnknownRefError
from e8s.core.ids import LandmarkId
from e8s.core.util import approx_equal
from e8s.sheet.coords import (
    CoordStyle,
    axis_offsets,
    axis_origin,
    coordinate_presets,
    from_coords,
    set_coordinates,
    to_coords,
)
from e8s.sheet.models import CoordinateKind, CoordinateSystem
from e8s.sheet.templates import get_sheet
from e8s.show.models import Show
from tests.fixtures.shows import two_set_show

pytestmark = pytest.mark.filterwarnings("ignore::e8s.core.errors.DeprecatedWarning")

TOL = 1e-9
STAGE = CoordinateSystem(kind=CoordinateKind.STAGE)


def _with(system: CoordinateSystem) -> Show:
    return set_coordinates(two_set_show(), system)


def test_default_long_and_short_wording() -> None:
    show = two_set_show()
    assert to_coords(show, (3.5, 2.0)) == "3.50 m right of center, 2.00 m upstage of front edge"
    assert to_coords(show, (3.5, 2.0), style="short") == "R3.50 U2.00"
    assert to_coords(show, (-3.5, 2.0), style="short") == "L3.50 U2.00"


def test_stage_kind_uses_performer_left_and_right() -> None:
    show = _with(STAGE)
    assert to_coords(show, (-3.0, 1.0)) == (
        "3.00 m stage right of center, 1.00 m upstage of front edge"
    )
    assert to_coords(show, (3.0, 1.0), style="short") == "SL3.00 US1.00"
    assert approx_equal(from_coords(show, "SR3.00 US1.00"), (-3.0, 1.0), abs_tol=TOL)


def test_audience_at_the_back_flips_both_axes() -> None:
    show = _with(CoordinateSystem(audience_side="back"))
    assert to_coords(show, (3.5, 2.0)) == "3.50 m left of origin, 2.00 m downstage of origin"


def test_audience_on_the_left_turns_the_frame() -> None:
    show = _with(CoordinateSystem(audience_side="left"))
    assert to_coords(show, (2.0, 3.0), style="short") == "L3.00 U2.00"
    assert approx_equal(from_coords(show, "R3.00 U2.00"), (2.0, -3.0), abs_tol=TOL)


def test_landmark_origin_names_the_landmark() -> None:
    show = _with(CoordinateSystem(origin=LandmarkId("c")))
    assert to_coords(show, (3.5, 12.0)) == "3.50 m right of center, 2.00 m upstage of center"
    assert approx_equal(from_coords(show, "R3.50 U2.00"), (3.5, 12.0), abs_tol=TOL)


def test_units_and_precision_round_for_display() -> None:
    show = _with(CoordinateSystem(units="ft", precision=0.5))
    assert to_coords(show, (1.0, 0.0), style="short") == "R3.5 U0.0"
    assert to_coords(show, (-0.001, 0.0), style="short") == "R0.0 U0.0"


def test_labels_override_words() -> None:
    show = _with(CoordinateSystem(labels={"right": "house left", "right_short": "HL"}))
    assert to_coords(show, (1.0, 1.0)).startswith("1.00 m house left of center")
    assert to_coords(show, (1.0, 1.0), style="short") == "HL1.00 U1.00"
    assert approx_equal(from_coords(show, "HL1.00 U1.00"), (1.0, 1.0), abs_tol=TOL)


def test_from_coords_reads_both_styles_and_any_order() -> None:
    show = two_set_show()
    long_text = "3.50 m left of center, 2.00 m upstage of front edge"
    assert approx_equal(from_coords(show, long_text), (-3.5, 2.0), abs_tol=TOL)
    assert approx_equal(from_coords(show, "u2 l3.5"), (-3.5, 2.0), abs_tol=TOL)
    assert approx_equal(
        from_coords(show, "1 ft right of center, 0 m upstage of front edge"),
        (0.3048, 0.0),
        abs_tol=TOL,
    )


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("3.50 m sideways of center, 2.00 m upstage of front edge", "sideways"),
        ("X3 U2", "X3"),
        ("R3 R2", "R2"),
        ("R3", "R3"),
        ("3.50 m right of the moon, 2.00 m upstage of front edge", "the moon"),
        ("3.50 parsecs right of center, 2.00 m upstage of front edge", "parsecs"),
    ],
)
def test_parse_errors_name_the_token(text: str, token: str) -> None:
    with pytest.raises(SchemaError) as info:
        from_coords(two_set_show(), text)
    assert token in info.value.message


def test_set_coordinates_never_moves_positions() -> None:
    show = two_set_show()
    changed = set_coordinates(show, STAGE)
    assert changed.coordinates == STAGE
    assert changed.sets == show.sets
    assert show.coordinates == CoordinateSystem()


def test_set_coordinates_validates_the_system() -> None:
    show = two_set_show()
    with pytest.raises(UnknownRefError):
        set_coordinates(show, CoordinateSystem(origin=LandmarkId("nowhere")))
    with pytest.raises(UnknownRefError):
        set_coordinates(show, CoordinateSystem(units="furlong"))
    with pytest.raises(SchemaError):
        set_coordinates(show, CoordinateSystem(precision=0.0))


def test_presets_start_with_the_sheet_default_and_are_distinct() -> None:
    stage_sheet = get_sheet("sheet:proscenium-stage@1")
    presets = coordinate_presets(stage_sheet)
    assert presets[0] == stage_sheet.default_coordinates
    assert len(presets) == len({p.model_dump_json() for p in presets})
    assert any(p.kind == CoordinateKind.STAGE for p in presets)
    assert any(p.origin == "center-stage" for p in presets)


ROUND_TRIP_POINT = (1.0, 2.0)


def _assert_round_trips_or_rejects(system: CoordinateSystem, style: CoordStyle) -> None:
    try:
        show = _with(system)
        text = to_coords(show, ROUND_TRIP_POINT, style)
    except E8sError:
        return
    assert approx_equal(from_coords(show, text), ROUND_TRIP_POINT, abs_tol=TOL)


def _word_with_precision(precision: float) -> str:
    system = CoordinateSystem(precision=precision)
    return to_coords(two_set_show().model_copy(update={"coordinates": system}), (1.0, 1.0))


def test_regression_zero_precision_is_rejected_not_division_by_zero() -> None:
    """Rejects precision=0 with an E8sError, however the system gets into a show (audit 9.4)."""
    with pytest.raises(E8sError):
        _word_with_precision(0.0)


def test_regression_to_coords_rejects_infinite_point() -> None:
    """Rejects an infinite point with an E8sError, not decimal.InvalidOperation (audit 9.4)."""
    with pytest.raises(E8sError):
        to_coords(two_set_show(), (float("inf"), 0.0))


def test_regression_to_coords_rejects_unrepresentable_magnitude() -> None:
    """Rejects the finite point (0.0, 1e26), too large to round, with an E8sError (UP-9.4-47)."""
    with pytest.raises(E8sError):
        to_coords(two_set_show(), (0.0, 1e26))


def test_regression_to_coords_rejects_nan_point() -> None:
    """Rejects a NaN point instead of printing "nan m right of center" (audit 9.4)."""
    with pytest.raises(E8sError):
        to_coords(two_set_show(), (float("nan"), 0.0))


@pytest.mark.defect("UP-9.4-18")
@pytest.mark.xfail(strict=True, reason="UP-9.4-18")
def test_regression_to_coords_rejects_unknown_style() -> None:
    """Rejects an unknown style instead of silently using long style (audit 9.4)."""
    with pytest.raises(SchemaError):
        to_coords(two_set_show(), (1.0, 1.0), style="bogus")  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.4-19")
@pytest.mark.xfail(strict=True, reason="UP-9.4-19")
def test_regression_short_label_with_digit_round_trips_or_is_rejected() -> None:
    """Round-trips a short direction label holding a digit, or rejects it (audit 9.4)."""
    _assert_round_trips_or_rejects(CoordinateSystem(labels={"right_short": "R2"}), "short")


@pytest.mark.defect("UP-9.4-20")
@pytest.mark.xfail(strict=True, reason="UP-9.4-20")
def test_regression_colliding_direction_labels_round_trip_or_are_rejected() -> None:
    """Round-trips two directions sharing one label, or rejects them (audit 9.4)."""
    _assert_round_trips_or_rejects(CoordinateSystem(labels={"up": "right"}), "long")


@pytest.mark.defect("UP-9.4-21")
@pytest.mark.xfail(strict=True, reason="UP-9.4-21")
def test_regression_from_coords_reads_negative_long_number() -> None:
    """Reads "-3.5 m right of center" as 3.5 m to the left (audit 9.4)."""
    text = "-3.5 m right of center, 2.00 m upstage of front edge"
    assert approx_equal(from_coords(two_set_show(), text), (-3.5, 2.0), abs_tol=TOL)


@pytest.mark.defect("UP-9.4-22")
@pytest.mark.xfail(strict=True, reason="UP-9.4-22")
def test_regression_from_coords_reads_leading_dot_number() -> None:
    """Reads the short code "R.5" as 0.5 m right (audit 9.4)."""
    assert approx_equal(from_coords(two_set_show(), "R.5 U2"), (0.5, 2.0), abs_tol=TOL)


@pytest.mark.defect("UP-9.4-23")
@pytest.mark.xfail(strict=True, reason="UP-9.4-23")
def test_regression_from_coords_reads_exponent_number() -> None:
    """Reads the short code "R1e3" as 1000 m right (audit 9.4)."""
    assert approx_equal(from_coords(two_set_show(), "R1e3 U2"), (1000.0, 2.0), abs_tol=TOL)


@pytest.mark.defect("UP-9.4-24")
@pytest.mark.xfail(strict=True, reason="UP-9.4-24")
def test_regression_from_coords_reads_number_glued_to_unit() -> None:
    """Reads "3m right of center" with no space before the unit (audit 9.4)."""
    text = "3m right of center, 2.00 m upstage of front edge"
    assert approx_equal(from_coords(two_set_show(), text), (3.0, 2.0), abs_tol=TOL)


HUGE_ORIGIN = (1.7e308, 0.0)
HUGE_SHIFTED_ORIGIN = (1.7e308, -1.7e308)


def _injected(fields: dict[str, Any]) -> Show:  # model_construct takes unchecked values
    system = CoordinateSystem.model_construct(None, **fields)
    return two_set_show().model_copy(update={"coordinates": system})


@pytest.mark.parametrize(
    "text",
    [
        "R" + "9" * 400 + " U1",
        "1" + "0" * 400 + " m right of center, 1 m upstage of front edge",
    ],
)
def test_regression_from_coords_rejects_overflowing_number(text: str) -> None:
    """Rejects a 400-digit number, not an infinite point (audit 9.4, UP-9.4-48)."""
    with pytest.raises(E8sError):
        from_coords(two_set_show(), text)


@pytest.mark.parametrize("origin", [(float("nan"), 0.0), (float("inf"), 0.0), (1.0,)])
def test_regression_unvalidated_origin_is_rejected(origin: tuple[float, ...]) -> None:
    """Rejects a NaN, infinite or one-value origin in both directions (audit 9.4, UP-9.4-49)."""
    show = _injected({"origin": origin})
    with pytest.raises(E8sError):
        from_coords(show, "R1 U1")
    with pytest.raises(E8sError):
        to_coords(show, (1.0, 1.0))


def test_regression_unvalidated_audience_side_is_rejected() -> None:
    """Rejects audience_side="x" in both directions, not KeyError (audit 9.4, UP-9.4-50)."""
    show = _injected({"audience_side": "x"})
    with pytest.raises(E8sError):
        from_coords(show, "R1 U1")
    with pytest.raises(E8sError):
        to_coords(show, (1.0, 1.0))


def test_regression_from_coords_rejects_overflowing_result() -> None:
    """Rejects "R999..9 U1" (308 digits) from origin 1.7e308, not (inf, 1.0) (UP-9.4-51).

    The coordinate bound refuses that origin, so the test injects it unchecked to reach the
    overflow guard; from the largest legal origin the same text lands past MAX_COORD_M.
    """
    text = "R" + "9" * 308 + " U1"
    with pytest.raises(SchemaError):
        CoordinateSystem(origin=HUGE_ORIGIN)
    with pytest.raises(E8sError):
        from_coords(_injected({"origin": HUGE_ORIGIN}), text)
    edge = set_coordinates(two_set_show(), CoordinateSystem(origin=(MAX_COORD_M, 0.0)))
    with pytest.raises(E8sError):
        from_coords(edge, text)


def test_regression_from_coords_refuses_a_point_past_max_coord() -> None:
    """Refuses a point one meter past MAX_COORD_M and reads one exactly at the bound."""
    edge = set_coordinates(two_set_show(), CoordinateSystem(origin=(MAX_COORD_M, 0.0)))
    assert from_coords(edge, "R0 U0") == (MAX_COORD_M, 0.0)
    with pytest.raises(SchemaError) as raised:
        from_coords(edge, "R1 U0")
    assert "MAX_COORD_M" in raised.value.hint


def test_regression_to_coords_rejects_non_finite_wording() -> None:
    """Rejects points whose local offset or rounded value is nan or inf (UP-9.4-52).

    The coordinate bound refuses the 1.7e308 origin, so the test injects it unchecked to reach
    the point-minus-origin overflow, then shifts from the largest legal origin too.
    """
    with pytest.raises(SchemaError):
        CoordinateSystem(origin=HUGE_SHIFTED_ORIGIN)
    edge = set_coordinates(two_set_show(), CoordinateSystem(origin=(MAX_COORD_M, -MAX_COORD_M)))
    for shifted in (_injected({"origin": HUGE_SHIFTED_ORIGIN}), edge):
        with pytest.raises(E8sError):
            to_coords(shifted, (-1.7e308, 1.7e308))
    coarse = two_set_show().model_copy(update={"coordinates": CoordinateSystem(precision=1e308)})
    with pytest.raises(E8sError):
        to_coords(coarse, (1.7976931348623157e308, 0.0))


def test_regression_to_coords_rounding_failure_hint_names_precision() -> None:
    """Teaches the precision as a possible cause when rounding exceeds Decimal's digits."""
    show = two_set_show().model_copy(update={"coordinates": CoordinateSystem(precision=1e-300)})
    with pytest.raises(E8sError) as raised:
        to_coords(show, (1.0, 0.0))
    assert "precision" in raised.value.hint


@pytest.mark.parametrize(
    "fields",
    [
        {"origin": 5},
        {"origin": None},
        {"origin": (1, "x")},
        {"labels": None},
        {"labels": {"left": 3}},
        {"labels": {1: "x"}},
        {"audience_side": ["front"]},
        {"kind": "nonsense"},
        {"precision": 0},
        {"precision": None},
        {"units": None},
    ],
)
def test_regression_injected_coordinate_parts_raise_schema_error(fields: dict[str, Any]) -> None:
    """Rejects wrong-typed parts of an unvalidated system with SchemaError, never TypeError."""
    system = CoordinateSystem.model_construct(None, **fields)
    show = two_set_show().model_copy(update={"coordinates": system})
    with pytest.raises(SchemaError):
        set_coordinates(two_set_show(), system)
    with pytest.raises(SchemaError):
        to_coords(show, (1.0, 1.0))
    with pytest.raises(SchemaError):
        from_coords(show, "R1 U1")


@pytest.mark.parametrize(
    ("side", "expected"),
    [("front", (3.5, 2.0)), ("back", (-3.5, -2.0)), ("left", (-2.0, 3.5)), ("right", (2.0, -3.5))],
)
def test_axis_offsets_follow_the_audience_side(side: str, expected: tuple[float, float]) -> None:
    system = CoordinateSystem.model_validate({"audience_side": side})
    across, depth = axis_offsets(two_set_show().sheet, system, np.array([[3.5, 2.0]]))
    assert (float(across[0]), float(depth[0])) == expected


def test_axis_origin_resolves_a_landmark() -> None:
    sheet = two_set_show().sheet
    system = CoordinateSystem(origin=LandmarkId("c"))
    assert axis_origin(sheet, system) == (0.0, 10.0)
    across, depth = axis_offsets(sheet, system, np.array([[1.0, 12.0]]))
    assert (across.tolist(), depth.tolist()) == ([1.0], [2.0])
