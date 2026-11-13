#!/usr/bin/env python3

"""
test_pdf.py --- tests the dependency-free PDF writer and its four layouts

Also holds the audited print defects (UP-9.5-36 to UP-9.5-38) as strict xfails.
"""

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from showpy.core.errors import SchemaError
from showpy.export.models import CoordinateSheet, SheetRow
from showpy.export.pdf import escape_text, to_pdf
from showpy.export.print_sheets import chart, coordinate_sheet, coordinate_sheets, cue_sheet
from tests.fixtures.export_shows import guided_show

REPO_ROOT = Path(__file__).resolve().parents[3]
XREF_ENTRY = re.compile(rb"^(\d{10}) (\d{5}) ([nf]) ?\r?$")
TEXT_RUN = re.compile(rb"/F\d ([\d.]+) Tf\n1 0 0 1 ([\d.]+) [\d.]+ Tm\n\((.*)\) Tj")
PAGE_WIDTH = 792.0
EM_WIDTH = 0.6
WIDE_COLUMNS = 40
LONG_TITLE_REPEATS = 40


def _long_sheet(rows: int) -> CoordinateSheet:
    row = SheetRow(
        set="1",
        counts=8.0,
        cumulative=0.0,
        position_text="here",
        xy=(0.0, 0.0),
        facing=0.0,
        behavior="move 8",
    )
    return CoordinateSheet(
        entity="T1",
        rows=tuple(row.model_copy(update={"set": str(i)}) for i in range(rows)),
    )


def _xref(pdf: bytes) -> list[tuple[int, int, str]]:
    start = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    lines = pdf[start:].splitlines()
    assert lines[0] == b"xref"
    first, count = (int(v) for v in lines[1].split())
    entries = []
    for number, line in enumerate(lines[2 : 2 + count], start=first):
        match = XREF_ENTRY.match(line)
        assert match is not None, line
        entries.append((number, int(match[1]), match[3].decode()))
    return entries


def _page_count(pdf: bytes) -> int:
    match = re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", pdf)
    assert match is not None
    return int(match[1])


def test_pdf_has_header_trailer_and_correct_xref_offsets() -> None:
    pdf = to_pdf(coordinate_sheet(guided_show(), "T2"))
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    entries = _xref(pdf)
    assert entries[0] == (0, 0, "f")
    for number, offset, kind in entries[1:]:
        assert kind == "n"
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())


def test_stream_lengths_match_their_content() -> None:
    pdf = to_pdf(chart(guided_show(), "2"), layout="chart_pages")
    for match in re.finditer(rb"<< /Length (\d+) >>\nstream\n", pdf):
        length = int(match[1])
        body = pdf[match.end() : match.end() + length]
        assert pdf[match.end() + length :].startswith(b"\nendstream")
        assert body.startswith(b"BT")


def test_text_is_in_the_page_content() -> None:
    pdf = to_pdf(coordinate_sheet(guided_show(), "T2"))
    assert b"(delay 2, move 6) Tj" in pdf
    assert b"/BaseFont /Helvetica" in pdf


def test_long_tables_overflow_onto_more_pages() -> None:
    assert _page_count(to_pdf(_long_sheet(5))) == 1
    assert _page_count(to_pdf(_long_sheet(200))) > 1


def test_book_starts_each_sheet_on_a_new_page() -> None:
    sheets = coordinate_sheets(guided_show())
    assert _page_count(to_pdf(sheets, layout="book")) == len(sheets)


def test_chart_pages_and_cue_sheet_layouts() -> None:
    show = guided_show()
    charts = [chart(show, "1"), chart(show, "2")]
    assert _page_count(to_pdf(charts, layout="chart_pages")) == 2
    assert to_pdf(cue_sheet(show), layout="cue_sheet").startswith(b"%PDF-1.4")


def test_output_is_deterministic() -> None:
    sheet = coordinate_sheet(guided_show(), "T4")
    assert to_pdf(sheet) == to_pdf(sheet)


def test_layout_and_object_must_match() -> None:
    with pytest.raises(SchemaError):
        to_pdf(chart(guided_show(), "1"), layout="sheet")
    with pytest.raises(SchemaError):
        to_pdf(coordinate_sheet(guided_show(), "T1"), layout="poster")  # type: ignore[arg-type]
    with pytest.raises(SchemaError):
        to_pdf([], layout="book")


def test_strings_are_escaped() -> None:
    assert escape_text("a(b)c\\") == b"a\\(b\\)c\\\\"
    assert escape_text("90°") == b"90\\260"
    assert escape_text("1→2") == b"1->2"
    assert escape_text("line\nbreak") == b"line break"


def test_pillow_is_not_needed() -> None:
    script = (
        "import sys\n"
        "sys.modules['PIL'] = None\n"
        "from showpy.export.models import CoordinateSheet\n"
        "from showpy.export.pdf import to_pdf\n"
        "sys.stdout.write(to_pdf(CoordinateSheet(entity='T1'))[:8].decode())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "%PDF-1.4"


def _placed_text(pdf: bytes) -> list[tuple[float, float, bytes]]:
    """Lists (x, font size, string) for every text run in the page content."""
    runs = []
    for match in TEXT_RUN.finditer(pdf):
        runs.append((float(match[2]), float(match[1]), match[3]))
    return runs


def _wide_cue_table(columns: int) -> pd.DataFrame:
    frame = pd.DataFrame({f"column {i}": [f"value {i}"] for i in range(columns)})
    frame.attrs["title"] = "wide cue sheet"
    return frame


def _right_edges(pdf: bytes) -> list[float]:
    return [x + len(text) * EM_WIDTH * size for x, size, text in _placed_text(pdf)]


@pytest.mark.defect("UP-9.5-36")
@pytest.mark.xfail(strict=True, reason="UP-9.5-36")
def test_regression_wide_table_stays_on_the_page() -> None:
    """Keeps every text run of a 40-column cue sheet inside the 792 pt page width.

    Today the font stops shrinking at 5 pt and text is placed at x = 3,070 pt (audit 9.5).
    """
    pdf = to_pdf(_wide_cue_table(WIDE_COLUMNS), layout="cue_sheet")
    assert max(x for x, _, _ in _placed_text(pdf)) < PAGE_WIDTH
    assert max(_right_edges(pdf)) <= PAGE_WIDTH


@pytest.mark.defect("UP-9.5-37")
@pytest.mark.xfail(strict=True, reason="UP-9.5-37")
def test_regression_long_title_is_clipped_to_the_page() -> None:
    """Clips or wraps a title wider than the page; titles are never clipped today."""
    frame = _wide_cue_table(2)
    frame.attrs["title"] = "very long title " * LONG_TITLE_REPEATS
    assert max(_right_edges(to_pdf(frame, layout="cue_sheet"))) <= PAGE_WIDTH


@pytest.mark.defect("UP-9.5-38")
@pytest.mark.xfail(strict=True, reason="UP-9.5-38")
def test_regression_non_latin_names_are_not_replaced_by_question_marks() -> None:
    """Prints Cyrillic, CJK, Arabic, Vietnamese and emoji names without losing them (audit 9.5).

    The writer encodes cp1252 with replacement, so every such character prints as `?`.
    """
    names = ["Иван", "李雷", "محمد", "Nguyễn", "😀"]
    pdf = to_pdf(pd.DataFrame({"name": names}), layout="cue_sheet")
    assert b"?" not in b"".join(text for _, _, text in _placed_text(pdf))
