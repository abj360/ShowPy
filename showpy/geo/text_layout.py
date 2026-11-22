"""
text_layout.py --- lays text out as stroke glyphs in stage meters, with a font fallback chain

Each character takes the first font in the chain that has it. Every glyph is scaled so its
font's cap height equals `height`; spacing is measured in ems of the first font. No shaping:
characters are laid out one by one in logical order.

Contains:
    MAX_LAYOUT_CHARS: bounds the characters of one laid-out text
    MAX_LAYOUT_EXTENT_M: bounds how far from the origin a laid stroke may reach
    MAX_LAYOUT_PIECES: bounds the path pieces of all laid glyphs together
    MAX_LAYOUT_CURVE_PIECES: bounds the path pieces of all laid curved strokes together
    layout_text(): lays multi-line text out as glyph strokes on the stage plane
"""

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from showpy.core.caps import MIN_LAYOUT_HEIGHT_M, require_size
from showpy.core.errors import FontError, GeometryError, GlyphError, SchemaError
from showpy.core.types import Degrees, Meters, Point
from showpy.geo.affine import (
    Matrix,
    rotation_matrix,
    scale_matrix,
    transform_points,
    translation_matrix,
)
from showpy.geo.checks import pick, require_finite_as
from showpy.geo.curves import Polyline, SvgCurve
from showpy.geo.fonts import FontRegistry, bundled_fonts
from showpy.geo.models import (
    DEFAULT_LINE_SPACING,
    Font,
    FontGlyph,
    LaidGlyph,
    TextAlign,
    TextAnchor,
)
from showpy.geo.svg import ArcPiece, CubicPiece, LinePiece, Piece, QuadPiece, Subpath, parse_path

MAX_LAYOUT_CHARS = 1_000
# A stage is ~100 m; squared coordinates stay far below the float range the curve tables square.
MAX_LAYOUT_EXTENT_M = 1e6
# MAX_LAYOUT_CHARS copies of the most complex bundled glyph (hershey-script "8", 51 pieces)
# lay 51,000 pieces (measured 2026-11-16); the cap is about 10 times that.
MAX_LAYOUT_PIECES = 512_000
# A curved stroke becomes an SvgCurve; measuring and placing spots costs about 60 µs per piece
# against about 1 µs for a straight one (measured 2026-11-17), so this cap keeps the worst
# legal text to about 4 s. Bundled Relief needs at most 15 per glyph, 15,000 for 1,000 glyphs.
MAX_LAYOUT_CURVE_PIECES = 60_000
LINE_BREAKS = (("\r\n", "\n"), ("\r", "\n"))
ALIGN_SHARE: Mapping[str, float] = MappingProxyType({"left": 0.0, "center": 0.5, "right": 1.0})
ORIGIN = (0.0, 0.0)

type GlyphKey = tuple[str, str]


@dataclass(frozen=True)
class _ChainFont:
    uri: str
    font: Font
    scale: float


@dataclass(frozen=True)
class _Placed:
    char: str
    source: _ChainFont
    glyph: FontGlyph
    x: Meters


def _check_numbers(
    height: Meters, at: Point, rotation: Degrees, letter_spacing: float, line_spacing: float
) -> None:
    require_finite_as(
        GeometryError, height, name="height", low=MIN_LAYOUT_HEIGHT_M, high=MAX_LAYOUT_EXTENT_M
    )
    require_finite_as(GeometryError, line_spacing, name="line_spacing", low=0.0, low_open=True)
    require_finite_as(GeometryError, letter_spacing, name="letter_spacing")
    require_finite_as(GeometryError, rotation, name="rotation")
    for name, value in (("at.x", at[0]), ("at.y", at[1])):
        require_finite_as(
            GeometryError, value, name=name, low=-MAX_LAYOUT_EXTENT_M, high=MAX_LAYOUT_EXTENT_M
        )


def _chain(fonts: Sequence[str], registry: FontRegistry, height: Meters) -> list[_ChainFont]:
    """Resolves every font in the chain, used or not, with its scale to the cap height.

    Args:
        fonts: Font names or URIs as the caller passed them.
        registry: Registry holding the fonts.
        height: Cap height in meters that every font is scaled to.

    Returns:
        chain: One entry per font, in chain order, with `scale = height / cap`.
    """
    if not fonts:
        raise SchemaError(
            "the font chain is empty",
            hint='name at least one font, such as fonts=["simplex"]',
            context={"fonts": []},
        )
    chain = []
    for ref in fonts:
        uri = registry.resolve(ref)
        font = registry.get(uri)
        cap = font.cap()
        if not cap > 0:
            raise FontError(
                f"font {font.name!r} has no positive cap height",
                hint="set cap_height in the font file",
                context={"font": uri, "cap_height": cap},
            )
        chain.append(_ChainFont(uri, font, height / cap))
    return chain


def _pick(char: str, chain: Sequence[_ChainFont], fonts: Sequence[str]) -> _ChainFont:
    """Returns the first chain font with a glyph for `char`, or raises a teaching GlyphError."""
    for entry in chain:
        if char in entry.font.glyphs:
            return entry
    codepoint = f"U+{ord(char):04X}"
    raise GlyphError(
        f"no font in the chain has a glyph for {char!r} ({codepoint})",
        hint=f"add a font covering {codepoint} to the chain, for example with register_font()",
        context={"char": char, "codepoint": codepoint, "fonts": list(fonts)},
    )


def _place_line(
    line: str, chain: Sequence[_ChainFont], fonts: Sequence[str], gap: Meters
) -> tuple[list[_Placed], Meters]:
    """Places each character of one line along a cursor that starts at 0.

    Args:
        line: Characters of the line, without line breaks.
        chain: Resolved fallback chain.
        fonts: Chain as the caller passed it, named in a GlyphError.
        gap: Extra space in meters between glyphs, none after the last.

    Returns:
        placed: Each glyph with its font and cursor x, and the line's advance width.
    """
    placed = []
    cursor = 0.0
    width = 0.0
    for char in line:
        source = _pick(char, chain, fonts)
        glyph = source.font.glyphs[char]
        placed.append(_Placed(char, source, glyph, cursor))
        width = cursor + glyph.advance * source.scale
        cursor = width + gap
    return placed, width


def _stroke(subpath: Subpath, matrix: Matrix, d: str) -> Polyline | SvgCurve:
    """Maps one stroke to stage meters as a Polyline when straight, else as an SvgCurve.

    A closed straight stroke drops its last vertex, which `Z` always puts back on the start.
    """
    if not _is_straight(subpath):
        transform = tuple((float(a), float(b), float(c)) for a, b, c in matrix.tolist())
        return SvgCurve(d=d, transform=transform, closed=subpath.closed)
    ends = [subpath.start, *(piece.end for piece in subpath.pieces)]
    vertices = ends[:-1] if subpath.closed else ends
    mapped = transform_points(np.asarray(vertices, dtype=float), matrix)
    points = tuple((float(x), float(y)) for x, y in mapped.tolist())
    return Polyline(points=points, closed=subpath.closed)


def _piece_hull(piece: Piece) -> list[Point]:
    """Returns points whose convex hull holds the piece: Bézier control points, an arc's box."""
    if isinstance(piece, QuadPiece):
        return [piece.control, piece.end]
    if isinstance(piece, CubicPiece):
        return [piece.c1, piece.c2, piece.end]
    if isinstance(piece, ArcPiece):
        radius = max(piece.rx, piece.ry)
        cx, cy = piece.center
        return [(cx + dx, cy + dy) for dx in (-radius, radius) for dy in (-radius, radius)]
    return [piece.end]


def _require_within_extent(subpath: Subpath, matrix: Matrix, placed: _Placed) -> None:
    """Rejects a stroke whose mapped hull reaches past `MAX_LAYOUT_EXTENT_M` from the origin."""
    hull = [subpath.start, *(p for piece in subpath.pieces for p in _piece_hull(piece))]
    reach = float(np.abs(transform_points(np.asarray(hull, dtype=float), matrix)).max())
    if reach <= MAX_LAYOUT_EXTENT_M:
        return
    raise GeometryError(
        f"glyph {placed.char!r} reaches {reach:.3g} m from the origin, over the "
        f"{MAX_LAYOUT_EXTENT_M:g} m layout extent",
        hint="use a smaller height or spacing, or fewer lines",
        context={"char": placed.char, "reach": reach, "max_extent": MAX_LAYOUT_EXTENT_M},
    )


def _parse_strokes(placed: _Placed) -> tuple[tuple[Subpath, str], ...]:
    """Parses each stroke of a placed glyph, rejecting one that is not one subpath."""
    strokes = []
    for d in placed.glyph.strokes:
        subpaths = parse_path(d)
        if len(subpaths) != 1:
            raise FontError(
                f"glyph {placed.char!r} has a stroke with {len(subpaths)} subpaths",
                hint="write each stroke as one subpath starting with M",
                context={"font": placed.source.uri, "glyph": placed.char, "stroke": d},
            )
        strokes.append((subpaths[0], d))
    return tuple(strokes)


def _parse_glyphs(placed: Iterable[_Placed]) -> dict[GlyphKey, tuple[tuple[Subpath, str], ...]]:
    """Parses each distinct glyph once, then rejects text over either piece cap.

    Returns:
        parsed: Each glyph's subpaths with their path data, keyed by font URI and character.
    """
    parsed: dict[GlyphKey, tuple[tuple[Subpath, str], ...]] = {}
    pieces = curved = 0
    for item in placed:
        key = (item.source.uri, item.char)
        if key not in parsed:
            parsed[key] = _parse_strokes(item)
        for subpath, _ in parsed[key]:
            pieces += len(subpath.pieces)
            curved += 0 if _is_straight(subpath) else len(subpath.pieces)
    _require_pieces(pieces, MAX_LAYOUT_PIECES, "path pieces")
    _require_pieces(curved, MAX_LAYOUT_CURVE_PIECES, "curved path pieces")
    return parsed


def _require_pieces(pieces: int, cap: int, what: str) -> None:
    try:
        require_size(pieces, cap, what=f"laid {what}")
    except SchemaError as exc:
        raise SchemaError(
            f"the text's glyphs hold over {cap} {what}",
            hint=f"shorten the text, or use a font with simpler glyphs ({what})",
            context=exc.context,
        ) from exc


def _is_straight(subpath: Subpath) -> bool:
    return all(isinstance(piece, LinePiece) for piece in subpath.pieces)


def _glyph_strokes(
    placed: _Placed, matrix: Matrix, strokes: Sequence[tuple[Subpath, str]]
) -> tuple[Polyline | SvgCurve, ...]:
    """Maps every parsed stroke of a placed glyph, rejecting one that reaches too far."""
    for subpath, _ in strokes:
        _require_within_extent(subpath, matrix, placed)
    return tuple(_stroke(subpath, matrix, d) for subpath, d in strokes)


def _split_lines(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text)
    for old, new in LINE_BREAKS:
        normalized = normalized.replace(old, new)
    lines = normalized.split("\n")
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


def _lay_line(
    placed: Sequence[_Placed],
    index: int,
    origin: Point,
    turn: Matrix,
    parsed: Mapping[GlyphKey, Sequence[tuple[Subpath, str]]],
) -> list[LaidGlyph]:
    laid = []
    for item in placed:
        scale = item.source.scale
        shift = translation_matrix(origin[0] + item.x, origin[1])
        matrix = turn @ shift @ scale_matrix(scale, scale, ORIGIN)
        if not np.isfinite(matrix).all():
            raise FloatingPointError("glyph matrix is not finite")
        strokes = _glyph_strokes(item, matrix, parsed[(item.source.uri, item.char)])
        laid.append(LaidGlyph(char=item.char, font=item.source.uri, line=index, strokes=strokes))
    return laid


def _anchor_shift(anchor: TextAnchor, height: Meters, last_baseline: Meters) -> Meters:
    shifts = {"baseline": 0.0, "top": -height, "middle": -(height + last_baseline) / 2}
    return pick("text anchor", anchor, shifts)


def layout_text(
    text: str,
    fonts: Sequence[str],
    height: Meters,
    at: Point,
    *,
    rotation: Degrees = 0.0,
    align: TextAlign = "left",
    anchor: TextAnchor = "baseline",
    letter_spacing: float = 0.0,
    line_spacing: float = DEFAULT_LINE_SPACING,
    font_registry: FontRegistry | None = None,
) -> tuple[LaidGlyph, ...]:
    r"""Lays multi-line text out as glyph strokes on the stage plane, in reading order.

    `MAX_LAYOUT_CHARS` bounds the raw input characters, checked before NFC normalization, which
    at most triples them. Then `\r\n` or `\r` count as one line break, and empty lines at the
    end of the text are dropped; interior empty lines stay as spacing. Each distinct glyph is
    parsed once; `MAX_LAYOUT_PIECES` bounds the path pieces of all laid glyphs together and
    `MAX_LAYOUT_CURVE_PIECES` those of curved strokes, both checked before any stroke is built.
    Every font in the chain is resolved, even one no character uses. No laid stroke may reach
    farther than `MAX_LAYOUT_EXTENT_M` from the origin on either axis, so every returned curve
    evaluates without overflow.

    Args:
        text: Characters to lay out; `\n` starts a new line.
        fonts: Fallback chain of font names or URIs; each character takes the first that has it.
        height: Cap height in meters, the same for every font in the chain.
        at: Layout point on the stage plane, placed by `align` and `anchor`.
        rotation: Degrees counter-clockwise about `at`; 0 reads left to right toward +x.
        align: Where each line's advance box sits on `at` horizontally.
        anchor: Whether the first baseline, the first cap line, or the midpoint between the
            first cap line and the last baseline sits on `at`.
        letter_spacing: Extra gap between glyphs, in ems of the first font.
        line_spacing: Baseline-to-baseline distance, in ems of the first font.
        font_registry: Registry holding the fonts; None means `bundled_fonts()`.

    Returns:
        glyphs: One laid glyph per character, lines top to bottom, characters in logical order.

    Examples:
        >>> from showpy.geo.text_layout import layout_text
        >>> glyphs = layout_text("HI", ["simplex"], 6.0, (0.0, 0.0))
        >>> [(glyph.char, len(glyph.strokes)) for glyph in glyphs]
        [('H', 3), ('I', 3)]
        >>> glyphs[1].strokes[0].points
        ((6.0, 6.0), (8.0, 6.0))
    """
    require_size(len(text), MAX_LAYOUT_CHARS, what="text characters")
    _check_numbers(height, at, rotation, letter_spacing, line_spacing)
    chain = _chain(fonts, bundled_fonts() if font_registry is None else font_registry, height)
    em = chain[0].scale * chain[0].font.units_per_em
    lines = _split_lines(text)
    pitch = line_spacing * em
    align_share = pick("text align", align, ALIGN_SHARE)
    lift = _anchor_shift(anchor, height, -(len(lines) - 1) * pitch)
    turn = translation_matrix(*at) @ rotation_matrix(rotation, ORIGIN)
    laid: list[LaidGlyph] = []
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            rows = [_place_line(line, chain, fonts, letter_spacing * em) for line in lines]
            parsed = _parse_glyphs(item for placed, _ in rows for item in placed)
            for index, (placed, width) in enumerate(rows):
                origin = (-align_share * width, lift - index * pitch)
                laid.extend(_lay_line(placed, index, origin, turn, parsed))
    except FloatingPointError as exc:
        raise GeometryError(
            "laid-out text overflows the float range",
            hint="use a smaller height, spacing or position",
            context={
                "height": height,
                "at": at,
                "letter_spacing": letter_spacing,
                "line_spacing": line_spacing,
            },
        ) from exc
    return tuple(laid)
