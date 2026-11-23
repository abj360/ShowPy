"""
render.py --- draws a blank sheet as SVG, PNG or PDF, with a meters-to-pixels canvas to reuse

Pixel space puts the audience at the bottom: y is flipped so upstage is up. Video and print
exports reuse SheetCanvas and draw_sheet() to paint the same background under their spots.
SVG is written by hand; PNG, PDF and draw_sheet() draw through Pillow, the `render` extra.

Contains:
    SheetFormat: names the output formats
    SheetCanvas: maps stage-plane meters to image pixels for one sheet
    SheetCanvas.for_sheet(): builds a canvas that fits a sheet plus a margin
    SheetCanvas.size: returns the image size in whole pixels
    SheetCanvas.to_px(): maps a stage point to pixel coordinates
    SheetCanvas.from_px(): maps pixel coordinates back to a stage point
    SheetCanvas.ring_px(): maps every point of a ring to pixels
    draw_sheet(): paints outline, walkable area, zones, guides and landmarks onto a PIL image
    render_sheet(): renders a printable blank sheet to bytes
"""

import io
import math
from typing import TYPE_CHECKING, Literal, Self
from xml.sax.saxutils import escape, quoteattr

from e8s.core.deprecation import leaving_call
from e8s.core.errors import SchemaError
from e8s.core.extras import require_extra
from e8s.core.types import FrozenModel, Point, Ring
from e8s.sheet.models import GuideLine, Landmark, Sheet, SurfaceType, Zone, ZoneKind

if TYPE_CHECKING:
    from PIL import Image, ImageDraw, ImageFont

type SheetFormat = Literal["svg", "png", "pdf"]
type Font = ImageFont.ImageFont | ImageFont.FreeTypeFont

FORMATS = ("svg", "png", "pdf")
DEFAULT_PX_PER_M = 20.0
DEFAULT_MARGIN_M = 1.0
MAX_PIXELS = 40_000_000
SIZE_EPS = 1e-9
LABEL_PX = 12
LANDMARK_RADIUS_PX = 3.0
LABEL_OFFSET_PX = 2.0

BACKGROUND = "#d8d8d8"
OFF_WALKABLE = "#bcbcbc"
SURFACE = {SurfaceType.FOOTBALL_FIELD: "#3f7d46"}
SURFACE_DEFAULT = "#f6f4ee"
GUIDE = {SurfaceType.FOOTBALL_FIELD: "#ffffff"}
GUIDE_DEFAULT = "#4a4a4a"
LANDMARK_COLOR = "#c0392b"
ZONE_COLORS = {
    ZoneKind.RESTRICTED: "#c0392b",
    ZoneKind.PIT: "#8e44ad",
    ZoneKind.WINGS: "#7f8c8d",
    ZoneKind.STAGING: "#2980b9",
    ZoneKind.AUDIENCE: "#d35400",
    ZoneKind.SAFETY: "#f39c12",
    ZoneKind.CUSTOM: "#16a085",
}
GUIDE_WIDTH_PX = {"sideline": 3, "edge": 3, "yard": 2, "center": 2}
GUIDE_WIDTH_DEFAULT_PX = 1
ZONE_WIDTH_PX = 2
SVG_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
SVG_NS = "http://www.w3.org/2000/svg"


class SheetCanvas(FrozenModel):
    """Maps stage-plane meters to image pixels; the audience sits at the bottom of the image.

    Attributes:
        min_x: Stage x at the left image edge.
        min_y: Stage y at the bottom image edge.
        max_x: Stage x at the right image edge.
        max_y: Stage y at the top image edge.
        px_per_m: Pixels per meter.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    px_per_m: float

    @classmethod
    def for_sheet(
        cls, sheet: Sheet, px_per_m: float = DEFAULT_PX_PER_M, margin_m: float = DEFAULT_MARGIN_M
    ) -> Self:
        """Builds a canvas that fits everything drawn on the sheet plus a margin.

        >>> from e8s.sheet.templates import _blank_sheet
        >>> SheetCanvas.for_sheet(_blank_sheet(10, 6), px_per_m=10, margin_m=1).size
        (120, 80)
        """
        if not (px_per_m > 0 and margin_m >= 0):
            raise SchemaError(
                f"canvas needs px_per_m > 0 and margin_m >= 0, got {px_per_m}, {margin_m}",
                hint="pass a positive scale such as px_per_m=20",
                context={"px_per_m": px_per_m, "margin_m": margin_m},
            )
        points = _all_points(sheet)
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return cls(
            min_x=min(xs) - margin_m,
            min_y=min(ys) - margin_m,
            max_x=max(xs) + margin_m,
            max_y=max(ys) + margin_m,
            px_per_m=px_per_m,
        )

    @property
    def size(self) -> tuple[int, int]:
        """Returns (width, height) in whole pixels."""
        width = (self.max_x - self.min_x) * self.px_per_m
        height = (self.max_y - self.min_y) * self.px_per_m
        return max(1, math.ceil(width - SIZE_EPS)), max(1, math.ceil(height - SIZE_EPS))

    def to_px(self, p: Point) -> tuple[float, float]:
        """Maps a stage point to pixel coordinates (origin top-left, y down)."""
        return (p[0] - self.min_x) * self.px_per_m, (self.max_y - p[1]) * self.px_per_m

    def from_px(self, q: tuple[float, float]) -> Point:
        """Maps pixel coordinates back to a stage point."""
        return self.min_x + q[0] / self.px_per_m, self.max_y - q[1] / self.px_per_m

    def ring_px(self, ring: Ring) -> list[tuple[float, float]]:
        """Maps every point of a ring to pixels."""
        return [self.to_px(p) for p in ring]


def _all_points(sheet: Sheet) -> list[Point]:
    return [
        *sheet.outline,
        *sheet.walkable.exterior,
        *(p for z in sheet.zones for p in z.polygon.exterior),
        *(p for g in sheet.guides for p in g.points),
        *(p for lm in sheet.landmarks for p in lm.geometry),
    ]


def _guide_style(sheet: Sheet, guide: GuideLine) -> tuple[str, int]:
    color = GUIDE.get(sheet.surface_type, GUIDE_DEFAULT)
    return color, GUIDE_WIDTH_PX.get(guide.kind, GUIDE_WIDTH_DEFAULT_PX)


def _draw_areas(draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, sheet: Sheet) -> None:
    surface = SURFACE.get(sheet.surface_type, SURFACE_DEFAULT)
    draw.polygon(canvas.ring_px(sheet.outline), fill=OFF_WALKABLE)
    draw.polygon(canvas.ring_px(sheet.walkable.exterior), fill=surface)
    for hole in sheet.walkable.holes:
        draw.polygon(canvas.ring_px(hole), fill=OFF_WALKABLE)
    for zone in sheet.zones:
        ring = canvas.ring_px(zone.polygon.exterior)
        draw.polygon(ring, outline=ZONE_COLORS[zone.kind], width=ZONE_WIDTH_PX)


def _draw_marks(
    draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, sheet: Sheet, kinds: tuple[str, ...]
) -> None:
    for mark in (m for m in sheet.landmarks if m.kind in kinds):
        ring = canvas.ring_px(mark.geometry)
        if mark.kind == "point":
            (x, y), r = ring[0], LANDMARK_RADIUS_PX
            draw.ellipse((x - r, y - r, x + r, y + r), outline=LANDMARK_COLOR)
        elif mark.kind == "line":
            draw.line(ring, fill=LANDMARK_COLOR, width=1)
        else:
            draw.polygon(ring, outline=LANDMARK_COLOR)


def _label_at(canvas: SheetCanvas, guide: GuideLine) -> tuple[float, float]:
    x, y = canvas.to_px(guide.points[0])
    return x + LABEL_OFFSET_PX, y - LABEL_OFFSET_PX - LABEL_PX


def _draw_guides(
    draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, sheet: Sheet, font: "Font"
) -> None:
    for guide in sheet.guides:
        color, width = _guide_style(sheet, guide)
        draw.line(canvas.ring_px(guide.points), fill=color, width=width)
        if guide.label:
            draw.text(_label_at(canvas, guide), guide.label, fill=color, font=font)


def _draw_sheet(draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, sheet: Sheet) -> None:
    """Paints outline, walkable area, zones, guides and landmarks onto a PIL drawing.

    Guides paint over line and area landmarks, which usually trace them; point landmarks go
    on top. Drawing is a side effect on the image behind `draw`; the sheet is not changed.
    Needs the `render` extra.

    >>> from PIL import Image, ImageDraw
    >>> from e8s.sheet.templates import _blank_sheet
    >>> sheet = _blank_sheet(4, 4)
    >>> canvas = SheetCanvas.for_sheet(sheet)
    >>> image = Image.new("RGB", canvas.size)
    >>> _draw_sheet(ImageDraw.Draw(image), canvas, sheet)
    >>> image.getpixel((40, 40))
    (246, 244, 238)
    """
    require_extra("PIL", extra="render", needed_by="draw_sheet")
    from PIL import ImageFont

    font = ImageFont.load_default(size=LABEL_PX)
    _draw_areas(draw, canvas, sheet)
    _draw_marks(draw, canvas, sheet, ("line", "area"))
    _draw_guides(draw, canvas, sheet, font)
    _draw_marks(draw, canvas, sheet, ("point",))


@leaving_call(since="0.4.0", remove="0.6.0")
def draw_sheet(draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, sheet: Sheet) -> None:
    """Paints outline, walkable area, zones, guides and landmarks onto a PIL drawing.

    Deprecated: this name leaves the library in 0.6.0.
    """
    return _draw_sheet(draw, canvas, sheet)


def _raster(sheet: Sheet, px_per_m: float) -> "Image.Image":
    from PIL import Image, ImageDraw

    canvas = SheetCanvas.for_sheet(sheet, px_per_m=px_per_m)
    width, height = canvas.size
    if width * height > MAX_PIXELS:
        raise SchemaError(
            f"{width} x {height} px is above the {MAX_PIXELS} px limit",
            hint="lower px_per_m",
            context={"px_per_m": px_per_m, "size": (width, height)},
        )
    image = Image.new("RGB", canvas.size, BACKGROUND)
    _draw_sheet(ImageDraw.Draw(image), canvas, sheet)
    return image


def _points_attr(canvas: SheetCanvas, ring: Ring) -> str:
    return quoteattr(" ".join(f"{x:.2f},{y:.2f}" for x, y in canvas.ring_px(ring)))


def _svg_zone(canvas: SheetCanvas, zone: Zone) -> str:
    color = ZONE_COLORS[zone.kind]
    return (
        f"<polygon id={quoteattr('zone-' + zone.id)}"
        f" points={_points_attr(canvas, zone.polygon.exterior)}"
        f' fill="none" stroke="{color}" stroke-width="{ZONE_WIDTH_PX}"/>'
    )


def _svg_guide(canvas: SheetCanvas, sheet: Sheet, guide: GuideLine) -> str:
    color, width = _guide_style(sheet, guide)
    line = (
        f"<polyline id={quoteattr('guide-' + guide.id)} data-kind={quoteattr(guide.kind)}"
        f' points={_points_attr(canvas, guide.points)} fill="none" stroke="{color}"'
        f' stroke-width="{width}"/>'
    )
    if not guide.label:
        return line
    x, y = canvas.to_px(guide.points[0])
    return (
        f'{line}<text x="{x + LABEL_OFFSET_PX:.2f}" y="{y - LABEL_OFFSET_PX:.2f}" fill="{color}"'
        f' font-size="{LABEL_PX}">{escape(guide.label)}</text>'
    )


def _svg_landmark(canvas: SheetCanvas, mark: Landmark) -> str:
    mark_id = quoteattr("landmark-" + mark.id)
    title = f"<title>{escape(mark.name)}</title>"
    if mark.kind == "point":
        x, y = canvas.to_px(mark.point)
        return (
            f'<circle id={mark_id} cx="{x:.2f}" cy="{y:.2f}" r="{LANDMARK_RADIUS_PX}"'
            f' fill="none" stroke="{LANDMARK_COLOR}">{title}</circle>'
        )
    tag = "polyline" if mark.kind == "line" else "polygon"
    return (
        f'<{tag} id={mark_id} points={_points_attr(canvas, mark.geometry)} fill="none"'
        f' stroke="{LANDMARK_COLOR}" stroke-dasharray="4 3">{title}</{tag}>'
    )


def _walkable_path(canvas: SheetCanvas, sheet: Sheet) -> str:
    rings = (sheet.walkable.exterior, *sheet.walkable.holes)
    parts = (
        "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in canvas.ring_px(r)) + " Z" for r in rings
    )
    return " ".join(parts)


def _svg(sheet: Sheet, px_per_m: float) -> bytes:
    canvas = SheetCanvas.for_sheet(sheet, px_per_m=px_per_m)
    width, height = canvas.size
    surface = SURFACE.get(sheet.surface_type, SURFACE_DEFAULT)
    body = [
        f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>{escape(sheet.name)}</title>",
        f'<rect width="{width}" height="{height}" fill="{BACKGROUND}"/>',
        (
            f'<polygon id="outline" points={_points_attr(canvas, sheet.outline)}'
            f' fill="{OFF_WALKABLE}"/>'
        ),
        (
            f'<path id="walkable" d="{_walkable_path(canvas, sheet)}" fill="{surface}"'
            ' fill-rule="evenodd"/>'
        ),
        '<g id="zones">',
        *(_svg_zone(canvas, z) for z in sheet.zones),
        '</g><g id="landmark-traces">',
        *(_svg_landmark(canvas, lm) for lm in sheet.landmarks if lm.kind != "point"),
        '</g><g id="guides">',
        *(_svg_guide(canvas, sheet, g) for g in sheet.guides),
        '</g><g id="landmark-points">',
        *(_svg_landmark(canvas, lm) for lm in sheet.landmarks if lm.kind == "point"),
        "</g></svg>",
    ]
    return (SVG_HEADER + "\n".join(body) + "\n").encode()


def _render_sheet(
    sheet: Sheet, fmt: SheetFormat = "svg", px_per_m: float = DEFAULT_PX_PER_M
) -> bytes:
    r"""Renders a printable blank sheet: hand-written SVG, or PNG and PDF through Pillow.

    PNG and PDF need the `render` extra; SVG needs no extra.

    Args:
        sheet: Sheet to draw.
        fmt: `svg`, `png` or `pdf`.
        px_per_m: Pixels per meter; also sets the SVG's pixel size.

    Returns:
        document: The encoded file.

    >>> from e8s.sheet.templates import _blank_sheet
    >>> _render_sheet(_blank_sheet(4, 4), fmt="png")[:4]
    b'\x89PNG'
    """
    if fmt not in FORMATS:
        raise SchemaError(
            f"unknown sheet format {fmt!r}", hint="use svg, png or pdf", context={"fmt": fmt}
        )
    if fmt == "svg":
        return _svg(sheet, px_per_m)
    require_extra("PIL", extra="render", needed_by=f"render_sheet(fmt={fmt!r})")
    buffer = io.BytesIO()
    _raster(sheet, px_per_m).save(buffer, format=fmt.upper())
    return buffer.getvalue()


@leaving_call(since="0.4.0", remove="0.6.0")
def render_sheet(
    sheet: Sheet, fmt: SheetFormat = "svg", px_per_m: float = DEFAULT_PX_PER_M
) -> bytes:
    """Renders a printable blank sheet: hand-written SVG, or PNG and PDF through Pillow.

    Deprecated: this name leaves the library in 0.6.0.
    """
    return _render_sheet(sheet, fmt, px_per_m)
