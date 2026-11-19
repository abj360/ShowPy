#!/usr/bin/env python3

"""
build_fonts.py --- converts pinned single-line font sources into the bundled showpy.font.v1 files

Run it by hand when a bundled font changes. CI never runs it and the library never downloads:

    uv run python scripts/build_fonts.py --cache <scratch>/font-sources --sheets <scratch>/sheets

Every source is fetched from a commit-pinned URL and checked against its SHA-256 before it is
parsed. Output is byte-deterministic. See docs/adr/0016-fonts-as-content-hashed-assets.md.

Contains:
    FontMeta: names, licenses and source of one output font
    JhfGlyph: one parsed Hershey glyph in the jhf frame (y down)
    Source: one pinned download
    SOURCES: pinned URLs and checksums of every input file
    GREEK_BY_HERSHEY: maps Hershey glyph numbers to Greek letters
    parse_jhf(): parses jhf text into glyphs, joining wrapped lines
    jhf_font(): builds a showpy.font.v1 document from jhf glyphs
    svg_font(): builds a showpy.font.v1 document from an SVG font
    simplex_font(): builds a showpy.font.v1 document from the built-in simplex table
    serialize_font(): writes a font document as canonical ASCII JSON bytes
    verified(): returns downloaded bytes after checking their SHA-256
    main(): fetches, converts and writes every bundled font, licence text and notice
"""

import argparse
import hashlib
import itertools
import json
import math
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from showpy.geo.svg import (
    ArcPiece,
    CubicPiece,
    LinePiece,
    Piece,
    QuadPiece,
    Subpath,
    parse_path,
)
from showpy.geo.text import SIMPLEX

REPO_LICENSE = Path(__file__).resolve().parents[1] / "LICENSE"
FONT_FORMAT = "showpy.font.v1"
HERSHEY_UNITS_PER_EM = 32.0
CLOSE_TOL_EM = 0.005
JHF_ORIGIN = "R"
JHF_PEN_UP = " R"
JHF_HEADER_WIDTH = 8
HERSHEY_FIRST_CHAR = 32
HERSHEY_CHAR_COUNT = 95
DECIMALS = 6
MIN_SNAPPED_PIECES = 2
DOWNLOAD_TIMEOUT_S = 60
SHEET_COLUMNS = 16
SHEET_CELL_PX = 96
SHEET_GLYPH_PX = 56
SHEET_CURVE_STEPS = 12
SHEET_NAME_CHARS = 14
HERSHEY_LICENSE = "LicenseRef-Hershey-Fonts"
GREEK_CAPITALS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
GREEK_SMALLS = "αβγδεζηθικλμνξοπρστυφχψω"
GREEK_CAPITAL_BASE = 527
GREEK_SMALL_BASE = 627
REFERENCE_CAPITALS = ("H", "\u0397")

type Point = tuple[float, float]
type IntPoint = tuple[int, int]
type FontDoc = dict[str, object]


@dataclass(frozen=True)
class FontMeta:
    """Names one output font.

    Attributes:
        name: Registry name and output file stem.
        family: Human-readable family name.
        license: SPDX identifier or LicenseRef of the font data.
        source: URL or module path the glyphs come from.
    """

    name: str
    family: str
    license: str
    source: str


@dataclass(frozen=True)
class JhfGlyph:
    """Holds one Hershey glyph in the jhf frame (x right, y down, origin at the file's R).

    Attributes:
        number: Hershey glyph number.
        left: Left bound of the advance box.
        right: Right bound of the advance box.
        strokes: Polylines of integer points, pen lifts removed.
    """

    number: int
    left: int
    right: int
    strokes: tuple[tuple[IntPoint, ...], ...]


@dataclass(frozen=True)
class Source:
    """Pins one download.

    Attributes:
        url: Commit-pinned URL.
        sha256: Expected SHA-256 of the file, in hex.
    """

    url: str
    sha256: str


RELIEF_BASE = (
    "https://raw.githubusercontent.com/isdat-type/Relief-SingleLine/"
    "01dfc5779ec1e9e4b288d96c6c96c23bfccbaf9d"
)
HERSHEY_BASE = (
    "https://raw.githubusercontent.com/kamalmostafa/hershey-fonts/"
    "1356bf2f83d380fcef68c887e88675eb9d445d86/hershey-fonts"
)

SOURCES: Mapping[str, Source] = {
    "relief.svg": Source(
        f"{RELIEF_BASE}/fonts/open_svg/ReliefSingleLineSVG-Regular.svg",
        "75f05a5b64ed6039c9816628ee051d98e16c19148a8268c63f5eccf8382479e2",
    ),
    "relief.ofl": Source(
        f"{RELIEF_BASE}/OFL.txt",
        "005834fd32a01fb021a44167cafd4fa3df8058a7b0fb7319bd19b445d2fe24a3",
    ),
    "rowmans.jhf": Source(
        f"{HERSHEY_BASE}/rowmans.jhf",
        "8718fb129c0f6bce89c84fe41bc467e39534d215a6f7c3220cc5789a8a7d8618",
    ),
    "scripts.jhf": Source(
        f"{HERSHEY_BASE}/scripts.jhf",
        "6b391b2ea3a0771cf18caff0ed111db3d05681fa586d423a740cc0b2b155a873",
    ),
    "greeks.jhf": Source(
        f"{HERSHEY_BASE}/greeks.jhf",
        "309298debb0e472047f8329d31f849b14a98108a706d0f4f684e40d1eb023a76",
    ),
    "hershey.txt": Source(
        f"{HERSHEY_BASE}/hershey.txt",
        "6ffb639e2d2799e987cec4f913473d4119e4270b65c13ceafe1cd502f904d855",
    ),
}

GREEK_BY_HERSHEY: Mapping[int, str] = {
    **{GREEK_CAPITAL_BASE + i: letter for i, letter in enumerate(GREEK_CAPITALS)},
    **{GREEK_SMALL_BASE + i: letter for i, letter in enumerate(GREEK_SMALLS)},
}


def _clean(value: float) -> float:
    return round(value, DECIMALS) + 0.0


def _clean_point(point: Point) -> Point:
    return (_clean(point[0]), _clean(point[1]))


def parse_jhf(text: str) -> list[JhfGlyph]:
    """Parses jhf text into glyphs, joining lines a long glyph wrapped onto.

    Args:
        text: Contents of a .jhf file.

    Returns:
        glyphs: Glyphs in file order.
    """
    lines = [line.rstrip("\r\n") for line in text.splitlines() if line.strip()]
    glyphs: list[JhfGlyph] = []
    index = 0
    while index < len(lines):
        header = lines[index]
        number, pair_count = int(header[:5]), int(header[5:JHF_HEADER_WIDTH])
        data = header[JHF_HEADER_WIDTH:]
        index += 1
        while len(data) < 2 * pair_count:
            if index >= len(lines):
                raise SystemExit(f"E: truncated jhf glyph {number}")
            data += lines[index]
            index += 1
        glyphs.append(_jhf_glyph(number, data[: 2 * pair_count]))
    return glyphs


def _jhf_coord(pair: str) -> IntPoint:
    return (ord(pair[0]) - ord(JHF_ORIGIN), ord(pair[1]) - ord(JHF_ORIGIN))


def _jhf_glyph(number: int, data: str) -> JhfGlyph:
    left, right = _jhf_coord(data[:2])
    strokes: list[tuple[IntPoint, ...]] = []
    current: list[IntPoint] = []
    for at in range(2, len(data), 2):
        pair = data[at : at + 2]
        if pair == JHF_PEN_UP:
            strokes.append(tuple(current))
            current = []
        else:
            current.append(_jhf_coord(pair))
    strokes.append(tuple(current))
    return JhfGlyph(number, left, right, tuple(s for s in strokes if len(s) > 1))


def _polyline_stroke(points: Sequence[Point]) -> str | None:
    cleaned = [_clean_point(p) for p in points]
    closed = len(cleaned) > 2 and cleaned[-1] == cleaned[0]
    if closed:
        cleaned.pop()
    if len(set(cleaned)) < 2:
        return None
    pieces = tuple(LinePiece(a, b) for a, b in itertools.pairwise(cleaned))
    return Subpath(cleaned[0], pieces, closed=closed).to_d()


def _font_doc(
    meta: FontMeta,
    metrics: tuple[float, float, float, float],
    glyphs: Mapping[str, dict[str, object]],
) -> FontDoc:
    units_per_em, cap_height, ascender, descender = metrics
    return {
        "format": FONT_FORMAT,
        "name": meta.name,
        "family": meta.family,
        "license": meta.license,
        "source": meta.source,
        "units_per_em": units_per_em,
        "cap_height": cap_height,
        "ascender": ascender,
        "descender": descender,
        "glyphs": dict(glyphs),
    }


def _reference_capital(glyphs: Sequence[JhfGlyph], chars: Mapping[int, str]) -> JhfGlyph:
    for capital in REFERENCE_CAPITALS:
        for at, char in chars.items():
            if char == capital:
                return glyphs[at]
    raise SystemExit(f"E: no reference capital among {REFERENCE_CAPITALS}")


def jhf_font(meta: FontMeta, glyphs: Sequence[JhfGlyph], chars: Mapping[int, str]) -> FontDoc:
    """Builds a font document from jhf glyphs, flipping y so the baseline sits at 0.

    Args:
        meta: Names and licence of the output font.
        glyphs: Parsed glyphs in file order.
        chars: Maps a glyph's index in the file to its character.

    Returns:
        font: The showpy.font.v1 document. Baseline and cap height come from the glyph for H,
            or for Greek capital Eta when the file has no Latin H.
    """
    reference = _reference_capital(glyphs, chars)
    ys = [y for stroke in reference.strokes for _, y in stroke]
    baseline, cap_height = max(ys), max(ys) - min(ys)
    table: dict[str, dict[str, object]] = {}
    heights: list[float] = [0.0]
    depths: list[float] = [0.0]
    for at, char in chars.items():
        glyph = glyphs[at]
        strokes = [
            _polyline_stroke([(x - glyph.left, baseline - y) for x, y in stroke])
            for stroke in glyph.strokes
        ]
        kept = [s for s in strokes if s is not None]
        flat = [baseline - y for stroke in glyph.strokes for _, y in stroke]
        heights += flat
        depths += flat
        table[char] = {"advance": _clean(glyph.right - glyph.left), "strokes": kept}
    metrics = (HERSHEY_UNITS_PER_EM, float(cap_height), float(max(heights)), float(min(depths)))
    return _font_doc(meta, metrics, table)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _round_piece(piece: Piece) -> Piece:
    match piece:
        case LinePiece():
            return LinePiece(_clean_point(piece.start), _clean_point(piece.end))
        case QuadPiece():
            return QuadPiece(
                _clean_point(piece.start), _clean_point(piece.control), _clean_point(piece.end)
            )
        case CubicPiece():
            return CubicPiece(
                _clean_point(piece.start),
                _clean_point(piece.c1),
                _clean_point(piece.c2),
                _clean_point(piece.end),
            )
        case ArcPiece():
            return replace(
                piece,
                start=_clean_point(piece.start),
                end=_clean_point(piece.end),
                rx=_clean(piece.rx),
                ry=_clean(piece.ry),
                phi_deg=_clean(piece.phi_deg),
            )


def _snap_closed(subpath: Subpath, tolerance: float) -> Subpath:
    if not subpath.pieces:
        return subpath
    *head, last = subpath.pieces
    gap = math.dist(last.end, subpath.start)
    if not subpath.closed and gap > tolerance:
        return subpath
    pieces = (
        tuple(head) if isinstance(last, LinePiece) else (*head, replace(last, end=subpath.start))
    )
    if len(pieces) < MIN_SNAPPED_PIECES and not subpath.closed:
        return subpath
    return Subpath(subpath.start, pieces, closed=True)


def _is_degenerate(subpath: Subpath) -> bool:
    return all(isinstance(p, LinePiece) and p.start == p.end for p in subpath.pieces)


def _svg_strokes(d: str, tolerance: float) -> list[str]:
    strokes: list[str] = []
    for subpath in parse_path(d):
        if _is_degenerate(subpath):
            continue
        snapped = _snap_closed(subpath, tolerance)
        pieces = tuple(_round_piece(p) for p in snapped.pieces)
        if pieces:
            strokes.append(
                Subpath(_clean_point(snapped.start), pieces, closed=snapped.closed).to_d()
            )
    return strokes


def svg_font(meta: FontMeta, svg_text: str) -> FontDoc:
    """Builds a font document from an SVG font, closing nearly closed strokes.

    Args:
        meta: Names and licence of the output font.
        svg_text: Checksum-verified SVG font text.

    Returns:
        font: The showpy.font.v1 document.
    """
    root = ET.fromstring(svg_text)
    elements = {_local(el.tag): el for el in root.iter() if _local(el.tag) in {"font", "font-face"}}
    face, font = elements["font-face"], elements["font"]
    units = float(face.attrib["units-per-em"])
    default_advance = float(font.attrib.get("horiz-adv-x", units))
    table: dict[str, dict[str, object]] = {}
    for glyph in (el for el in root.iter() if _local(el.tag) == "glyph"):
        char = glyph.attrib.get("unicode", "")
        if len(char) != 1:
            continue
        advance = float(glyph.attrib.get("horiz-adv-x", default_advance))
        strokes = _svg_strokes(glyph.attrib.get("d", ""), CLOSE_TOL_EM * units)
        table[char] = {"advance": _clean(advance), "strokes": strokes}
    metrics = (
        units,
        float(face.attrib["cap-height"]),
        float(face.attrib["ascent"]),
        float(face.attrib["descent"]),
    )
    return _font_doc(meta, metrics, table)


def simplex_font(meta: FontMeta) -> FontDoc:
    """Builds a font document from the built-in simplex table, folding case.

    Args:
        meta: Names and licence of the output font.

    Returns:
        font: The showpy.font.v1 document.
    """
    table: dict[str, dict[str, object]] = {}
    for char, glyph in SIMPLEX.items():
        strokes = [_polyline_stroke([(float(x), float(y)) for x, y in s]) for s in glyph]
        entry: dict[str, object] = {
            "advance": 5.0,
            "strokes": [s for s in strokes if s is not None],
        }
        table[char] = entry
        if char.isalpha() and char.lower() != char:
            table[char.lower()] = entry
    return _font_doc(meta, (6.0, 6.0, 6.0, 0.0), table)


def serialize_font(font: Mapping[str, object]) -> bytes:
    """Writes a font document as canonical JSON.

    Args:
        font: A showpy.font.v1 document.

    Returns:
        data: Sorted-key, compact, ASCII JSON followed by a newline.
    """
    text = json.dumps(font, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("ascii")


def verified(data: bytes, sha256: str, url: str) -> bytes:
    """Returns downloaded bytes after checking their SHA-256.

    Args:
        data: Downloaded or cached file contents.
        sha256: Expected digest in hex.
        url: Where the bytes came from, for the error message.

    Returns:
        data: The same bytes.
    """
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise SystemExit(f"E: checksum mismatch for {url}: expected {sha256}, got {actual}")
    return data


def _fetch(source: Source, cache: Path, name: str) -> bytes:
    cached = cache / name
    if cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest() == source.sha256:
        return cached.read_bytes()
    if not source.url.startswith("https://"):
        raise SystemExit(f"E: refusing non-https source {source.url}")
    print(f"Fetching {source.url}")
    with urllib.request.urlopen(source.url, timeout=DOWNLOAD_TIMEOUT_S) as response:
        data: bytes = response.read()
    verified(data, source.sha256, source.url)
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return data


@dataclass(frozen=True)
class FontPlan:
    """Describes how one output font is built.

    Attributes:
        meta: Names and licence of the output font.
        source_key: Key into SOURCES, or None for the built-in simplex table.
        license_file: Path of the shipped licence text under the output directory.
    """

    meta: FontMeta
    source_key: str | None
    license_file: str


def _plans() -> list[FontPlan]:
    def hershey(name: str, family: str, key: str) -> FontPlan:
        return FontPlan(
            FontMeta(name, family, HERSHEY_LICENSE, SOURCES[key].url), key, "licenses/HERSHEY.txt"
        )

    return [
        FontPlan(
            FontMeta(
                "relief-singleline",
                "Relief SingleLine",
                "OFL-1.1",
                SOURCES["relief.svg"].url,
            ),
            "relief.svg",
            "licenses/OFL-1.1.txt",
        ),
        hershey("hershey-roman", "Hershey Roman Simplex", "rowmans.jhf"),
        hershey("hershey-script", "Hershey Script Simplex", "scripts.jhf"),
        hershey("hershey-greek", "Hershey Greek Simplex", "greeks.jhf"),
        FontPlan(
            FontMeta("simplex", "Simplex", "MIT", "showpy/geo/text.py"), None, "licenses/MIT.txt"
        ),
    ]


def _build(plan: FontPlan, cache: Path) -> FontDoc:
    if plan.source_key is None:
        return simplex_font(plan.meta)
    text = _fetch(SOURCES[plan.source_key], cache, plan.source_key).decode("utf-8")
    if plan.source_key.endswith(".svg"):
        return svg_font(plan.meta, text)
    glyphs = parse_jhf(text)
    if plan.source_key == "greeks.jhf":
        chars = {
            i: GREEK_BY_HERSHEY[g.number]
            for i, g in enumerate(glyphs)
            if g.number in GREEK_BY_HERSHEY
        }
    else:
        chars = {
            i: chr(HERSHEY_FIRST_CHAR + i) for i in range(min(HERSHEY_CHAR_COUNT, len(glyphs)))
        }
    return jhf_font(plan.meta, glyphs, chars)


def _notice_block(plan: FontPlan, output_sha: str) -> str:
    source_sha = SOURCES[plan.source_key].sha256 if plan.source_key else "built in"
    return (
        f"{plan.meta.name} ({plan.meta.family})\n"
        f"  source: {plan.meta.source}\n"
        f"  source sha256: {source_sha}\n"
        f"  license: {plan.meta.license} ({plan.license_file})\n"
        f"  output sha256: {output_sha}\n"
    )


def _sheet_label(char: str) -> str:
    name = unicodedata.name(char, "")[:SHEET_NAME_CHARS]
    return f"U+{ord(char):04X} {name}".strip()


def _draw_glyph(
    draw: ImageDraw.ImageDraw, strokes: Sequence[str], origin: Point, scale: float
) -> None:
    for d in strokes:
        for subpath in parse_path(d):
            ramp = np.linspace(0.0, 1.0, SHEET_CURVE_STEPS + 1)
            points: list[Point] = []
            for piece in subpath.pieces:
                for x, y in piece.evaluate(ramp):
                    points.append((origin[0] + x * scale, origin[1] - y * scale))
            if len(points) > 1:
                draw.line(points, fill="black", width=1)


def _write_sheet(font: FontDoc, path: Path) -> None:
    glyphs = font["glyphs"]
    assert isinstance(glyphs, dict)
    rows = math.ceil(len(glyphs) / SHEET_COLUMNS)
    image = Image.new("RGB", (SHEET_COLUMNS * SHEET_CELL_PX, rows * SHEET_CELL_PX), "white")
    draw = ImageDraw.Draw(image)
    units = float(str(font["units_per_em"]))
    scale = SHEET_GLYPH_PX / units
    for at, char in enumerate(sorted(glyphs)):
        col, row = at % SHEET_COLUMNS, at // SHEET_COLUMNS
        x0, y0 = col * SHEET_CELL_PX, row * SHEET_CELL_PX
        draw.text((x0 + 2, y0 + SHEET_CELL_PX - 12), _sheet_label(char), fill="gray")
        origin = (x0 + 8.0, y0 + SHEET_CELL_PX - 24.0)
        _draw_glyph(draw, glyphs[char]["strokes"], origin, scale)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Fetches, converts and writes every bundled font, licence text and notice.

    Args:
        argv: Command-line arguments; defaults to sys.argv.

    Returns:
        status: Process exit status, 0 on success.
    """
    parser = argparse.ArgumentParser(description="Build the bundled showpy fonts.")
    parser.add_argument("--cache", type=Path, required=True, help="download cache directory")
    default_out = Path(__file__).resolve().parents[1] / "showpy" / "fonts"
    parser.add_argument("--out", type=Path, default=default_out, help="output directory")
    parser.add_argument("--sheets", type=Path, default=None, help="contact sheet directory")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    notices: list[str] = []
    for plan in _plans():
        font = _build(plan, args.cache)
        data = serialize_font(font)
        target = args.out / f"{plan.meta.name}.json"
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        print(f"Wrote {target.relative_to(args.out.parent.parent)} sha256:{digest}")
        notices.append(_notice_block(plan, digest))
        if args.sheets is not None:
            _write_sheet(font, args.sheets / f"{plan.meta.name}.png")
    _write_licenses(args.out, args.cache)
    _write_notice(args.out, notices)
    return 0


def _write_notice(out: Path, notices: Sequence[str]) -> None:
    (out / "NOTICE.txt").write_bytes("\n".join(notices).encode("utf-8"))


def _write_licenses(out: Path, cache: Path) -> None:
    licenses = out / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for key, name in (("relief.ofl", "OFL-1.1.txt"), ("hershey.txt", "HERSHEY.txt")):
        (licenses / name).write_bytes(_fetch(SOURCES[key], cache, key))
    (licenses / "MIT.txt").write_bytes(REPO_LICENSE.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
