"""
_pdf.py --- a small dependency-free PDF 1.4 writer for sheets, books, charts and cue sheets

Tables are laid out in Helvetica on US Letter landscape pages, one cell at a time, and spill
onto more pages when rows overflow. Text uses WinAnsiEncoding; characters outside it print
as `?`. Output is deterministic: no dates or IDs are embedded.

Contains:
    Layout: names the printable layouts
    escape_text(): encodes text as the body of a PDF literal string
    to_pdf(): prints a coordinate sheet, a book of sheets, chart pages or a cue sheet
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from e8s.core.errors import SchemaError
from e8s.core.extras import is_dataframe
from e8s.export._data import as_table
from e8s.export._models import Chart, CoordinateSheet, TextTable

if TYPE_CHECKING:
    import pandas as pd

type Layout = Literal["sheet", "book", "chart_pages", "cue_sheet"]
type Printable = (
    CoordinateSheet | Chart | pd.DataFrame | Sequence[CoordinateSheet] | Sequence[Chart]
)

SINGLE_LAYOUTS = ("sheet", "cue_sheet")
PAGE_WIDTH = 792.0
PAGE_HEIGHT = 612.0
MARGIN = 36.0
TITLE_SIZE = 12.0
BODY_SIZE = 8.0
MIN_BODY_SIZE = 5.0
FOOTER_SIZE = 7.0
LEADING_SHARE = 1.35
# Helvetica's widest common glyphs advance about 0.6 em; column widths assume it per character.
CHAR_WIDTH_EM = 0.6
COLUMN_GAP_EM = 1.5
MAX_CELL_CHARS = 60
ELLIPSIS = "..."
ENCODING = "cp1252"
PRINTABLE_ASCII = range(0x20, 0x7F)
ESCAPED_BYTES = {ord("("), ord(")"), ord("\\")}
REPLACEMENTS = {"→": "->", "\n": " ", "\r": " ", "\t": " "}
PDF_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
FONT_OBJECTS = {"F1": "Helvetica", "F2": "Helvetica-Bold"}
FIRST_PAGE_OBJECT = 3 + len(FONT_OBJECTS)
FREE_ENTRY = b"0000000000 65535 f \n"


def _is_sheet(item: object) -> bool:
    return isinstance(item, CoordinateSheet)


def _is_chart(item: object) -> bool:
    return isinstance(item, Chart)


LAYOUT_KINDS: dict[str, tuple[str, Callable[[object], bool]]] = {
    "sheet": ("CoordinateSheet", _is_sheet),
    "book": ("CoordinateSheet", _is_sheet),
    "chart_pages": ("Chart", _is_chart),
    "cue_sheet": ("DataFrame", is_dataframe),
}


@dataclass(frozen=True)
class _Text:
    x: float
    y: float
    size: float
    bold: bool
    text: str


def escape_text(text: str) -> bytes:
    r"""Encodes text as the body of a PDF literal string, escaping (, ) and backslash.

    >>> escape_text("T(1) 90°") == rb"T\(1\) 90\260"
    True
    """
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    out = bytearray()
    for byte in text.encode(ENCODING, errors="replace"):
        if byte in ESCAPED_BYTES:
            out += b"\\" + bytes([byte])
        elif byte in PRINTABLE_ASCII:
            out.append(byte)
        else:
            out += f"\\{byte:03o}".encode()
    return bytes(out)


def _items(obj: Printable, layout: Layout) -> list[object]:
    if layout in SINGLE_LAYOUTS or isinstance(obj, CoordinateSheet | Chart) or is_dataframe(obj):
        return [obj]
    return list(obj)


def _tables(obj: Printable, layout: Layout) -> list[TextTable]:
    if layout not in LAYOUT_KINDS:
        raise SchemaError(
            f"unknown layout {layout!r}",
            hint=f"use one of {', '.join(LAYOUT_KINDS)}",
            context={"layout": layout},
        )
    kind, is_kind = LAYOUT_KINDS[layout]
    items = _items(obj, layout)
    tables = [
        as_table(i) for i in items if isinstance(i, CoordinateSheet | Chart) or is_dataframe(i)
    ]
    if not items or not all(is_kind(item) for item in items):
        raise SchemaError(
            f"layout {layout} prints {kind} objects, got {type(obj).__name__}",
            hint="sheet: one CoordinateSheet; book: CoordinateSheets; chart_pages: Charts; "
            "cue_sheet: the cue_sheet() DataFrame",
            context={"layout": layout},
        )
    return tables


def _clip(cell: str) -> str:
    if len(cell) <= MAX_CELL_CHARS:
        return cell
    return cell[: MAX_CELL_CHARS - len(ELLIPSIS)] + ELLIPSIS


def _columns(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> tuple[float, list[float]]:
    """Picks the body font size that fits the page width and each column's left edge."""
    chars = [max(len(line[i]) for line in (header, *rows)) for i in range(len(header))]
    gaps = COLUMN_GAP_EM * max(len(chars) - 1, 0)
    width_em = (sum(chars) * CHAR_WIDTH_EM + gaps) or 1.0
    size = max(MIN_BODY_SIZE, min(BODY_SIZE, (PAGE_WIDTH - 2 * MARGIN) / width_em))
    edges, x = [], MARGIN
    for count in chars:
        edges.append(x)
        x += (count * CHAR_WIDTH_EM + COLUMN_GAP_EM) * size
    return size, edges


def _line(
    edges: list[float], y: float, size: float, cells: tuple[str, ...], *, bold: bool
) -> list[_Text]:
    return [_Text(x, y, size, bold, cell) for x, cell in zip(edges, cells, strict=True) if cell]


def _table_pages(table: TextTable) -> list[list[_Text]]:
    """Lays one table out over as many pages as its rows need, repeating title and header."""
    rows = [tuple(_clip(c) for c in row) for row in table.rows]
    size, edges = _columns(table.header, rows)
    leading = size * LEADING_SHARE
    title_y = PAGE_HEIGHT - MARGIN - TITLE_SIZE
    header_y = title_y - TITLE_SIZE * LEADING_SHARE
    per_page = max(1, math.floor((header_y - MARGIN) / leading))
    pages = []
    for first in range(0, max(len(rows), 1), per_page):
        page = (
            [_Text(MARGIN, title_y, TITLE_SIZE, bold=True, text=table.title)] if table.title else []
        )
        page += _line(edges, header_y, size, table.header, bold=True)
        for offset, row in enumerate(rows[first : first + per_page], start=1):
            page += _line(edges, header_y - offset * leading, size, row, bold=False)
        pages.append(page)
    return pages


def _with_footers(pages: list[list[_Text]]) -> list[list[_Text]]:
    total = len(pages)
    footer_y = MARGIN / 2
    return [
        [*page, _Text(MARGIN, footer_y, FOOTER_SIZE, bold=False, text=f"page {i} of {total}")]
        for i, page in enumerate(pages, start=1)
    ]


def _content(page: list[_Text]) -> bytes:
    out = bytearray(b"BT\n")
    for item in page:
        font = "F2" if item.bold else "F1"
        out += f"/{font} {item.size:.2f} Tf\n1 0 0 1 {item.x:.2f} {item.y:.2f} Tm\n".encode()
        out += b"(" + escape_text(item.text) + b") Tj\n"
    out += b"ET"
    return bytes(out)


def _objects(contents: list[bytes]) -> list[bytes]:
    page_ids = [FIRST_PAGE_OBJECT + 2 * i for i in range(len(contents))]
    kids = " ".join(f"{n} 0 R" for n in page_ids)
    font_refs = " ".join(f"/{name} {3 + i} 0 R" for i, name in enumerate(FONT_OBJECTS))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(),
    ]
    objects += [
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode()
        for base in FONT_OBJECTS.values()
    ]
    for page_id, stream in zip(page_ids, contents, strict=True):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:g} {PAGE_HEIGHT:g}] "
            f"/Resources << /Font << {font_refs} >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    return objects


def _document(objects: list[bytes]) -> bytes:
    out = bytearray(PDF_HEADER)
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + FREE_ENTRY
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def to_pdf(obj: Printable, layout: Layout = "sheet") -> bytes:
    """Prints export tables as a PDF 1.4 document, without Pillow or any other dependency.

    Args:
        obj: A CoordinateSheet (`sheet`), CoordinateSheets (`book`, one per page run),
            a Chart or Charts (`chart_pages`), or the cue_sheet() DataFrame (`cue_sheet`).
        layout: How to print.

    Returns:
        pdf: The document; rows that overflow a page continue on the next.

    >>> from e8s.examples import two_set_show
    >>> from e8s.export._print_sheets import coordinate_sheet
    >>> to_pdf(coordinate_sheet(two_set_show(), "T1"))[:8]
    b'%PDF-1.4'
    """
    pages = [page for table in _tables(obj, layout) for page in _table_pages(table)]
    contents = [_content(page) for page in _with_footers(pages)]
    return _document(_objects(contents))
