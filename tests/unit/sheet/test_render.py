#!/usr/bin/env python3

"""
test_render.py --- tests the sheet canvas mapping and SVG, PNG and PDF rendering
"""

import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageDraw

from stage.core.errors import SchemaError
from stage.core.util import approx_equal
from stage.sheet.render import SheetCanvas, draw_sheet, render_sheet
from stage.sheet.templates import get_sheet
from tests.fixtures.shows import field_sheet

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
    assert approx_equal(canvas.to_px((-20.0, 20.0)), (0.0, 0.0), TOL)
    assert approx_equal(canvas.from_px(canvas.to_px((3.0, 4.0))), (3.0, 4.0), TOL)


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
