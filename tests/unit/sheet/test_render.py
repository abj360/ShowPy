#!/usr/bin/env python3

"""
test_render.py --- tests the sheet canvas mapping and SVG, PNG and PDF rendering

Contains:
    test_*(): one behavior of e8s.sheet.render each
    test_regression_pdf_embeds_no_wall_clock_dates(): writes no clock-derived CreationDate or
        ModDate into the PDF
    test_regression_svg_is_well_formed_with_control_characters_in_names(): keeps the SVG parseable
        when names and labels hold control characters
    test_regression_svg_draws_zone_holes(): draws zone holes in the SVG, so a holed zone differs
        from a solid one
    test_regression_png_draws_zone_holes(): draws zone holes in the PNG, so a holed zone differs
        from a solid one
    test_regression_canvas_shows_a_front_ensemble_below_the_sheet(): fits a point 2 m in front of
        the sheet inside the default canvas
"""

import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageDraw

from e8s.core.errors import SchemaError
from e8s.core.ids import LandmarkId, ZoneId
from e8s.core.types import Region
from e8s.core.util import approx_equal
from e8s.sheet.models import GuideLine, Landmark, Sheet, Zone, ZoneKind
from e8s.sheet.render import SheetCanvas, draw_sheet, render_sheet
from e8s.sheet.templates import add_guide, add_landmark, add_zone, blank_sheet, get_sheet
from tests.fixtures.shows import field_sheet
from tests.helpers import digests_under_hash_seeds

pytestmark = pytest.mark.filterwarnings("ignore::e8s.core.errors.DeprecatedWarning")

TOL = 1e-9
SVG_NS = "{http://www.w3.org/2000/svg}"
PX_PER_M = 10.0


def test_canvas_flips_y_so_the_audience_is_at_the_bottom() -> None:
    canvas = SheetCanvas.for_sheet(field_sheet(), px_per_m=PX_PER_M, margin_m=0.0)
    width, height = canvas.size
    assert (width, height) == (400, 230)
    front = canvas.to_px((0.0, 0.0))
    back = canvas.to_px((0.0, 20.0))
    assert back[1] < front[1]
    assert approx_equal(canvas.to_px((-20.0, 20.0)), (0.0, 0.0), abs_tol=TOL)
    assert approx_equal(canvas.from_px(canvas.to_px((3.0, 4.0))), (3.0, 4.0), abs_tol=TOL)


def test_canvas_rejects_bad_scale() -> None:
    with pytest.raises(SchemaError):
        SheetCanvas.for_sheet(field_sheet(), px_per_m=0.0)


def test_png_decodes_at_the_canvas_size() -> None:
    sheet = get_sheet("sheet:hs-football@1")
    png = render_sheet(sheet, fmt="png", px_per_m=PX_PER_M)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.size == SheetCanvas.for_sheet(sheet, px_per_m=PX_PER_M).size


def test_svg_parses_and_holds_every_guide() -> None:
    sheet = get_sheet("sheet:hs-football@1")
    root = ET.fromstring(render_sheet(sheet, fmt="svg"))
    assert root.tag == f"{SVG_NS}svg"
    ids = {element.get("id") for element in root.iter()}
    assert {f"guide-{g.id}" for g in sheet.guides} <= ids
    assert {f"landmark-{lm.id}" for lm in sheet.landmarks} <= ids
    assert {f"zone-{z.id}" for z in get_sheet("sheet:proscenium-stage@1").zones} <= {
        e.get("id")
        for e in ET.fromstring(render_sheet(get_sheet("sheet:proscenium-stage@1"))).iter()
    }


def test_pdf_has_a_pdf_header() -> None:
    assert render_sheet(field_sheet(), fmt="pdf").startswith(b"%PDF")


def test_unknown_format_raises() -> None:
    with pytest.raises(SchemaError):
        render_sheet(field_sheet(), fmt="gif")  # type: ignore[arg-type]


def test_draw_sheet_paints_guides_onto_an_image() -> None:
    sheet = get_sheet("sheet:hs-football@1")
    canvas = SheetCanvas.for_sheet(sheet, px_per_m=PX_PER_M)
    image = Image.new("RGB", canvas.size, (0, 0, 0))
    draw_sheet(ImageDraw.Draw(image), canvas, sheet)
    x, y = canvas.to_px((0.0, 20.0))
    assert image.getpixel((round(x), round(y))) != (0, 0, 0)
    assert len(image.getcolors(maxcolors=1 << 24) or []) > 2


def test_render_is_deterministic() -> None:
    sheet = field_sheet()
    assert render_sheet(sheet, fmt="png") == render_sheet(sheet, fmt="png")
    assert render_sheet(sheet) == render_sheet(sheet)


def test_png_render_ignores_hash_seed() -> None:
    """Renders the same PNG twice in one run and under two hash seeds (audit 4.3)."""
    digests = digests_under_hash_seeds(
        "from e8s.sheet.render import _render_sheet", "_render_sheet(show.sheet, fmt='png')"
    )
    assert all(first == second for first, second in digests), digests
    assert len({first for first, _ in digests}) == 1, digests


FRONT_ENSEMBLE_Y = -2.0
OUTER = ((2.0, 1.0), (8.0, 1.0), (8.0, 5.0), (2.0, 5.0))
HOLE = ((4.0, 2.0), (6.0, 2.0), (6.0, 4.0), (4.0, 4.0))


def _sheet_with_zone(holes: tuple[tuple[tuple[float, float], ...], ...]) -> Sheet:
    zone = Zone(id=ZoneId("pit"), kind=ZoneKind.PIT, polygon=Region(exterior=OUTER, holes=holes))
    return add_zone(blank_sheet(10, 6), zone)


@pytest.mark.defect("UP-9.4-38")
@pytest.mark.xfail(strict=True, reason="UP-9.4-38")
def test_regression_pdf_embeds_no_wall_clock_dates() -> None:
    """Writes no clock-derived CreationDate or ModDate into the PDF (audit 9.4; rule 3)."""
    pdf = render_sheet(blank_sheet(4, 4), fmt="pdf")
    assert b"CreationDate" not in pdf
    assert b"ModDate" not in pdf


@pytest.mark.defect("UP-9.4-39")
@pytest.mark.xfail(strict=True, reason="UP-9.4-39")
def test_regression_svg_is_well_formed_with_control_characters_in_names() -> None:
    """Keeps the SVG parseable when names and labels hold control characters (audit 9.4)."""
    sheet = blank_sheet(10, 6).model_copy(update={"name": "bad\x01name"})
    sheet = add_landmark(
        sheet, Landmark(id=LandmarkId("m"), name="mark\x02", kind="point", geometry=((1.0, 1.0),))
    )
    sheet = add_guide(
        sheet,
        GuideLine(id="g", kind="custom", points=((0.0, 1.0), (1.0, 1.0)), label="tape\x03"),
    )
    ET.fromstring(render_sheet(sheet, fmt="svg"))


@pytest.mark.defect("UP-9.4-40")
@pytest.mark.xfail(strict=True, reason="UP-9.4-40")
def test_regression_svg_draws_zone_holes() -> None:
    """Draws zone holes in the SVG, so a holed zone differs from a solid one (audit 9.4)."""
    solid = render_sheet(_sheet_with_zone(()), fmt="svg")
    holed = render_sheet(_sheet_with_zone((HOLE,)), fmt="svg")
    assert solid != holed


@pytest.mark.defect("UP-9.4-41")
@pytest.mark.xfail(strict=True, reason="UP-9.4-41")
def test_regression_png_draws_zone_holes() -> None:
    """Draws zone holes in the PNG, so a holed zone differs from a solid one (audit 9.4)."""
    solid = render_sheet(_sheet_with_zone(()), fmt="png")
    holed = render_sheet(_sheet_with_zone((HOLE,)), fmt="png")
    assert solid != holed


@pytest.mark.defect("UP-9.4-42")
@pytest.mark.xfail(strict=True, reason="UP-9.4-42")
def test_regression_canvas_shows_a_front_ensemble_below_the_sheet() -> None:
    """Fits a point 2 m in front of the sheet inside the default canvas (audit 9.4)."""
    canvas = SheetCanvas.for_sheet(blank_sheet(10, 6), px_per_m=PX_PER_M)
    width, height = canvas.size
    x, y = canvas.to_px((0.0, FRONT_ENSEMBLE_Y))
    assert 0.0 <= x <= width
    assert 0.0 <= y <= height
