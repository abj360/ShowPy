#!/usr/bin/env python3

"""
test_coords.py --- tests coordinate presets, wording, parsing and audience-side handling
"""

import pytest

from showpy.core.errors import SchemaError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.sheet.coords import coordinate_presets, from_coords, set_coordinates, to_coords
from showpy.sheet.models import CoordinateKind, CoordinateSystem
from showpy.sheet.templates import get_sheet
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

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
    assert approx_equal(from_coords(show, "SR3.00 US1.00"), (-3.0, 1.0), TOL)


def test_audience_at_the_back_flips_both_axes() -> None:
    show = _with(CoordinateSystem(audience_side="back"))
    assert to_coords(show, (3.5, 2.0)) == "3.50 m left of origin, 2.00 m downstage of origin"


def test_audience_on_the_left_turns_the_frame() -> None:
    show = _with(CoordinateSystem(audience_side="left"))
    assert to_coords(show, (2.0, 3.0), style="short") == "L3.00 U2.00"
    assert approx_equal(from_coords(show, "R3.00 U2.00"), (2.0, -3.0), TOL)


def test_landmark_origin_names_the_landmark() -> None:
    show = _with(CoordinateSystem(origin="c"))
    assert to_coords(show, (3.5, 12.0)) == "3.50 m right of center, 2.00 m upstage of center"
    assert approx_equal(from_coords(show, "R3.50 U2.00"), (3.5, 12.0), TOL)


def test_units_and_precision_round_for_display() -> None:
    show = _with(CoordinateSystem(units="ft", precision=0.5))
    assert to_coords(show, (1.0, 0.0), style="short") == "R3.5 U0.0"
    assert to_coords(show, (-0.001, 0.0), style="short") == "R0.0 U0.0"


def test_labels_override_words() -> None:
    show = _with(CoordinateSystem(labels={"right": "house left", "right_short": "HL"}))
    assert to_coords(show, (1.0, 1.0)).startswith("1.00 m house left of center")
    assert to_coords(show, (1.0, 1.0), style="short") == "HL1.00 U1.00"
    assert approx_equal(from_coords(show, "HL1.00 U1.00"), (1.0, 1.0), TOL)


def test_from_coords_reads_both_styles_and_any_order() -> None:
    show = two_set_show()
    long_text = "3.50 m left of center, 2.00 m upstage of front edge"
    assert approx_equal(from_coords(show, long_text), (-3.5, 2.0), TOL)
    assert approx_equal(from_coords(show, "u2 l3.5"), (-3.5, 2.0), TOL)
    assert approx_equal(
        from_coords(show, "1 ft right of center, 0 m upstage of front edge"), (0.3048, 0.0), TOL
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
        set_coordinates(show, CoordinateSystem(origin="nowhere"))
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
