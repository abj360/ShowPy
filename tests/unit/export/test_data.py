#!/usr/bin/env python3

"""
test_data.py --- tests frame CSV, tables and format plugins

Also holds the audited data and CSV defects (UP-9.5-29 to UP-9.5-35). UP-9.5-29, -30, -34 and
-35 are fixed; UP-9.5-31 to -33 stay strict xfails.

Contains:
    test_regression_chart_csv_neutralizes_formula_names(): quotes a formula-like performer name
    test_regression_csv_neutralizes_every_formula_trigger(): quotes cells led by + - @ tab or CR
    test_regression_csv_neutralizes_header_cells(): quotes a formula-like column name
    test_regression_frames_csv_neutralizes_spot_ids(): quotes a formula-like spot ID in the frames
        CSV
    test_regression_csv_leaves_numeric_literals_untouched(): keeps -5, +3, 1.5e3 and -.5 as is
    test_regression_csv_prefixes_a_non_numeric_minus_cell(): quotes `-x` and `-1+1`
    test_regression_csv_opt_out_leaves_formula_cells_unchanged(): writes cells raw when asked
    test_regression_csv_writes_large_floats_without_exponent(): writes 1234567.0 in full; the `:g`
        format turns it into 1.23457e+06
    test_regression_csv_keeps_cue_seconds_above_one_thousand_exact(): keeps seconds past 1,000 exact
    test_regression_coordinate_sheet_csv_names_the_entity_and_performer(): adds entity and performer
    test_regression_to_text_drops_tabs_and_carriage_returns(): drops tab and CR from text cells
    test_regression_to_text_aligns_wide_cjk_characters(): pads wide characters by display width
    test_regression_csv_writes_every_float_in_plain_decimal(): never writes an exponent, keeps
        non-finite cells as text
    test_csv_neutralizes_exactly_the_non_numeric_formula_cells(): property over default cells
    test_frames_csv_is_byte_identical_across_runs_and_hash_seeds(): writes one CSV per show
"""

import csv
import io
import json
import unicodedata

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.core.csv_cells import FORMULA_TRIGGERS, PLAIN_DECIMAL
from showpy.core.errors import (
    SchemaError,
    UnknownRefError,
)
from showpy.core.ids import LEGACY_IDS_KEY, SetId, SpotId
from showpy.core.util import approx_equal
from showpy.export.data import (
    DEFAULT_FORMATS,
    as_table,
    export_frames_csv,
    formats,
    register_exporter,
    register_importer,
    to_csv,
    to_text,
)
from showpy.export.models import Chart, ChartRow, Claim, CoordinateSheet, SheetRow
from showpy.export.print_sheets import chart, coordinate_sheet, cue_sheet
from showpy.geo.fonts import FontRegistry
from showpy.show.models import Show
from tests.fixtures.export_shows import AT, guided_show, live_publication, performer
from tests.fixtures.shows import two_set_show
from tests.helpers import digests_under_hash_seeds

TOL = 1e-9
EMPTY_FONTS = FontRegistry(fonts={}, sources={}, names={})
WIDE_CELL = 2
FORMULA_NAME = '=HYPERLINK("http://evil")'


def test_frames_csv_samples_every_spot_per_count() -> None:
    rows = list(csv.DictReader(io.StringIO(export_frames_csv(two_set_show()))))
    assert list(rows[0]) == ["count", "seconds", "entity", "x", "y", "facing", "on_field"]
    assert len(rows) == 9 * 4
    mid = next(r for r in rows if r["count"] == "4" and r["entity"] == "T2")
    assert approx_equal(float(mid["y"]), 7.0, abs_tol=TOL)
    assert approx_equal(float(mid["seconds"]), 2.0, abs_tol=TOL)
    assert mid["on_field"] == "true"


def test_frames_csv_respects_per_count() -> None:
    text = export_frames_csv(two_set_show(), per_count=2)
    assert len(text.strip().splitlines()) == 1 + 17 * 4
    with pytest.raises(SchemaError):
        export_frames_csv(two_set_show(), per_count=0)


def test_csv_quotes_user_text() -> None:
    row = ChartRow(
        entity=SpotId("T1"),
        group="trumpets",
        position_text="a, b",
        xy=(0.0, 0.0),
        facing=0.0,
        note='say "hi"\nthen go',
    )
    text = to_csv(Chart(set_id=SetId("1"), rows=(row,)))
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[1][parsed[0].index("position")] == "a, b"
    assert parsed[1][parsed[0].index("note")] == 'say "hi"\nthen go'


def test_sheet_table_and_text_are_aligned() -> None:
    sheet = coordinate_sheet(guided_show(), "T2")
    table = as_table(sheet)
    assert table.header[:3] == ("set", "counts", "cumulative")
    assert table.rows[1][table.header.index("behavior")] == "delay 2, move 6"
    lines = to_text(sheet).splitlines()
    assert lines[0] == "T2"
    starts = {line.index("delay") for line in lines if "delay" in line}
    header_start = lines[1].index("behavior")
    assert starts == {header_start}
    assert all(line == line.rstrip() for line in lines)


def test_text_and_csv_accept_dataframes() -> None:
    table = cue_sheet(two_set_show())
    assert to_csv(table).splitlines()[0] == "count,seconds,measure,kind,label,detail"
    frame = pd.DataFrame({"a": [1.5, float("nan")], "b": ["x", "y"]})
    assert to_text(frame).splitlines() == ["a    b", "1.5  x", "     y"]


def test_tables_name_the_performer() -> None:
    sheet = CoordinateSheet(
        entity=SpotId("T1"),
        performer_name="Ada",
        rows=(
            SheetRow(
                set=SetId("1"),
                counts=0.0,
                cumulative=0.0,
                position_text="here",
                xy=(0.0, 0.0),
                facing=0.0,
                behavior="start",
            ),
        ),
    )
    assert as_table(sheet).title == "T1 — Ada"
    assert as_table(chart(two_set_show(), "1")).title == "set 1"


def test_tables_refuse_other_objects() -> None:
    with pytest.raises(SchemaError):
        to_csv(two_set_show())  # type: ignore[arg-type]  # deliberately the wrong type


def test_format_registry_is_immutable() -> None:
    def shout(show: Show) -> str:
        return show.title.upper()

    def blank(_text: str) -> Show:
        return two_set_show()

    extended = register_importer(register_exporter(DEFAULT_FORMATS, "shout", shout), "x", blank)
    assert "shout" in formats(extended)["export"]
    assert "shout" not in formats(DEFAULT_FORMATS)["export"]
    assert formats(DEFAULT_FORMATS) == {"export": ["frames_csv", "json"], "import": ["json"]}
    assert extended.exporter("shout")(two_set_show()) == "TEST SHOW"
    with pytest.raises(UnknownRefError):
        DEFAULT_FORMATS.exporter("shout")
    with pytest.raises(SchemaError):
        register_exporter(DEFAULT_FORMATS, " ", shout)


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def _display_width(text: str) -> int:
    return sum(WIDE_CELL if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def test_regression_chart_csv_neutralizes_formula_names() -> None:
    """Prefixes a quote to a performer name that spreadsheets would run as a formula.

    A name like `=HYPERLINK("http://evil")` lands in the chart CSV unchanged, and Excel or
    Sheets then evaluates it (OWASP CSV injection; audit 9.5 security; P0 #10).
    """
    show = guided_show()
    pub = live_publication(show)
    claim = Claim(spot=SpotId("T1"), performer=performer(FORMULA_NAME), claimed_at=AT)
    pub = pub.model_copy(update={"claims": (claim,)})
    rows = _csv_rows(to_csv(chart(show, "1", publication=pub)))
    cells = [cell for row in rows[1:] for cell in row]
    assert f"'{FORMULA_NAME}" in cells
    assert FORMULA_NAME not in cells


@pytest.mark.parametrize("lead", ["+", "-", "@", "\t", "\r"])
def test_regression_csv_neutralizes_every_formula_trigger(lead: str) -> None:
    """Prefixes a quote to cells starting with + - @ tab or carriage return (audit 9.5)."""
    cell = f"{lead}1+1"
    rows = _csv_rows(to_csv(pd.DataFrame({"name": [cell], "offset": ["-5"]})))
    assert rows[1] == [f"'{cell}", "-5"]


def test_regression_csv_neutralizes_header_cells() -> None:
    rows = _csv_rows(to_csv(pd.DataFrame({"=SUM(A1)": ["ok"]})))
    assert rows[0] == ["'=SUM(A1)"]


def test_regression_frames_csv_neutralizes_spot_ids() -> None:
    """Prefixes a quote to a formula-like spot ID in the frames CSV."""
    payload = '=HYPERLINK("http://evil","x")'
    renamed = two_set_show().model_dump_json().replace('"T1"', json.dumps(payload))
    show = Show.model_validate_json(renamed, context={LEGACY_IDS_KEY: True})
    rows = _csv_rows(export_frames_csv(show))
    entities = {row[2] for row in rows[1:]}
    assert f"'{payload}" in entities
    assert payload not in entities


@pytest.mark.parametrize("literal", ["-5", "+3", "1.5e3", "-.5"])
def test_regression_csv_leaves_numeric_literals_untouched(literal: str) -> None:
    rows = _csv_rows(to_csv(pd.DataFrame({"n": [literal]})))
    assert rows[1] == [literal]


def test_regression_csv_prefixes_a_non_numeric_minus_cell() -> None:
    rows = _csv_rows(to_csv(pd.DataFrame({"n": ["-x", "-1+1"]})))
    assert [row[0] for row in rows[1:]] == ["'-x", "'-1+1"]


def test_regression_csv_opt_out_leaves_formula_cells_unchanged() -> None:
    rows = _csv_rows(to_csv(pd.DataFrame({"n": [FORMULA_NAME]}), neutralize=False))
    assert rows[1] == [FORMULA_NAME]


def test_regression_csv_writes_large_floats_without_exponent() -> None:
    """Writes 1234567.0 in full, not as 1.23457e+06 (audit 9.5)."""
    rows = _csv_rows(to_csv(pd.DataFrame({"count": [1234567.0]})))
    assert float(rows[1][0]) == 1234567.0
    assert "e" not in rows[1][0].lower()


def test_regression_csv_keeps_cue_seconds_above_one_thousand_exact() -> None:
    """Keeps the precision of cue seconds past 1,000 s."""
    seconds = 1234.5678
    rows = _csv_rows(to_csv(pd.DataFrame({"seconds": [seconds]})))
    assert approx_equal(float(rows[1][0]), seconds, abs_tol=TOL)


@pytest.mark.defect("UP-9.5-31")
@pytest.mark.xfail(strict=True, reason="UP-9.5-31")
def test_regression_coordinate_sheet_csv_names_the_entity_and_performer() -> None:
    """Carries the entity and performer in every row so concatenated sheets stay apart."""
    header = _csv_rows(to_csv(coordinate_sheet(guided_show(), "T1")))[0]
    assert {"entity", "performer"} <= set(header)


@pytest.mark.defect("UP-9.5-32")
@pytest.mark.xfail(strict=True, reason="UP-9.5-32")
def test_regression_to_text_drops_tabs_and_carriage_returns() -> None:
    """Replaces tab and carriage return in cells so the aligned text keeps its columns."""
    text = to_text(pd.DataFrame({"a": ["x\ty\rz"], "b": ["w"]}))
    assert "\t" not in text
    assert "\r" not in text


@pytest.mark.defect("UP-9.5-33")
@pytest.mark.xfail(strict=True, reason="UP-9.5-33")
def test_regression_to_text_aligns_wide_cjk_characters() -> None:
    """Pads by display width so the next column lines up under double-width characters."""
    lines = to_text(pd.DataFrame({"name": ["李雷", "ab"], "x": ["1", "2"]})).splitlines()
    starts = [
        _display_width(line[: line.rindex(value)])
        for line, value in zip(lines[1:], "12", strict=True)
    ]
    assert starts[0] == starts[1]


@pytest.mark.parametrize(
    ("value", "text"),
    [(1e16, "10000000000000000"), (1e-7, "0"), (-0.0, "0"), (0.5, "0.5"), (float("inf"), "inf")],
)
def test_regression_csv_writes_every_float_in_plain_decimal(value: float, text: str) -> None:
    """Writes floats without an exponent; infinity stays text instead of raising."""
    assert _csv_rows(to_csv(pd.DataFrame({"v": [value]})))[1] == [text]


CELL_TEXT = st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=12)
TRIGGER_LED_TEXT = st.builds(
    lambda trigger, rest: trigger + rest, st.sampled_from(FORMULA_TRIGGERS), CELL_TEXT
)
DECIMAL_TEXT = st.from_regex(PLAIN_DECIMAL, fullmatch=True)


@settings(max_examples=200, deadline=None)
@given(cells=st.lists(st.one_of(CELL_TEXT, TRIGGER_LED_TEXT, DECIMAL_TEXT), min_size=1, max_size=8))
def test_csv_neutralizes_exactly_the_non_numeric_formula_cells(cells: list[str]) -> None:
    """Prefixes `'` on a cell that starts with a trigger and is no plain decimal; keeps the rest."""
    text = to_csv(pd.DataFrame({"cells": cells}))
    written = [row[0] for row in csv.reader(io.StringIO(text, newline=""))][1:]
    expected = [
        f"'{cell}"
        if cell.startswith(FORMULA_TRIGGERS) and PLAIN_DECIMAL.fullmatch(cell) is None
        else cell
        for cell in cells
    ]
    assert written == expected


def test_frames_csv_is_byte_identical_across_runs_and_hash_seeds() -> None:
    """Writes the same frames CSV twice in one run and under two hash seeds (audit 4.3)."""
    digests = digests_under_hash_seeds(
        "from showpy.export.data import export_frames_csv", "export_frames_csv(show).encode()"
    )
    assert all(first == second for first, second in digests), digests
    assert len({first for first, _ in digests}) == 1, digests
