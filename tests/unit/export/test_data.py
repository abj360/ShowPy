#!/usr/bin/env python3

"""
test_data.py --- tests JSON interchange, frame CSV, position import, tables and format plugins

Also holds the audited data and CSV defects (UP-9.5-27 to UP-9.5-35) as strict xfails.

Contains:
    test_regression_chart_csv_neutralizes_formula_names(): quotes a formula-like performer name
    test_regression_csv_neutralizes_every_formula_trigger(): quotes cells led by + - @ tab or CR
    test_regression_csv_writes_large_floats_without_exponent(): writes 1234567.0 in full
    test_regression_csv_keeps_cue_seconds_above_one_thousand_exact(): keeps seconds past 1,000 exact
    test_regression_coordinate_sheet_csv_names_the_entity_and_performer(): adds entity and performer
    test_regression_to_text_drops_tabs_and_carriage_returns(): drops tab and CR from text cells
    test_regression_to_text_aligns_wide_cjk_characters(): pads wide characters by display width
    test_regression_export_json_include_accepts_a_single_field_name(): treats a string as one field
    test_regression_import_json_rejects_a_non_mapping_with_schema_error(): raises SchemaError
"""

import csv
import io
import json
import unicodedata

import pandas as pd
import pytest

from showpy.core.errors import SchemaError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.export.data import (
    DEFAULT_FORMATS,
    LEGACY_SCHEMA_TAGS,
    SCHEMA_TAG,
    as_table,
    export_frames_csv,
    export_json,
    formats,
    import_json,
    register_exporter,
    register_importer,
    to_csv,
    to_text,
)
from showpy.export.models import Chart, ChartRow, Claim, CoordinateSheet, SheetRow
from showpy.export.print_sheets import chart, coordinate_sheet, cue_sheet
from showpy.show.models import Show
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.export_shows import AT, guided_show, live_publication, performer
from tests.fixtures.shows import two_set_show

TOL = 1e-9
WIDE_CELL = 2
FORMULA_NAME = '=HYPERLINK("http://evil")'


def test_export_json_is_tagged_and_json_safe() -> None:
    exported = export_json(rich_show())
    assert exported["schema"] == SCHEMA_TAG == "showpy.show.v1"
    assert json.loads(json.dumps(exported)) == exported


def test_json_round_trip_restores_the_show() -> None:
    show = rich_show()
    assert import_json(json.loads(json.dumps(export_json(show)))) == show


def test_include_limits_top_level_fields_and_keeps_the_id() -> None:
    exported = export_json(two_set_show(), include=["sets", "title"])
    assert set(exported["show"]) == {"id", "sets", "title"}


def test_include_refuses_unknown_fields() -> None:
    with pytest.raises(UnknownRefError) as caught:
        export_json(two_set_show(), include=["setz"])
    assert "sets" in caught.value.hint


@pytest.mark.parametrize(
    "document",
    [
        {"show": {}},
        {"schema": "showpy.show/1", "show": {}},
        {"schema": "showpy.show.v1"},
        {"schema": "showpy.show.v1", "show": {"id": "x"}},
    ],
)
def test_import_json_refuses_wrong_or_missing_schema(document: dict[str, object]) -> None:
    with pytest.raises(SchemaError):
        import_json(document)


def test_frames_csv_samples_every_spot_per_count() -> None:
    rows = list(csv.DictReader(io.StringIO(export_frames_csv(two_set_show()))))
    assert list(rows[0]) == ["count", "seconds", "entity", "x", "y", "facing", "on_field"]
    assert len(rows) == 9 * 4
    mid = next(r for r in rows if r["count"] == "4" and r["entity"] == "T2")
    assert approx_equal(float(mid["y"]), 7.0, TOL)
    assert approx_equal(float(mid["seconds"]), 2.0, TOL)
    assert mid["on_field"] == "true"


def test_frames_csv_respects_per_count() -> None:
    text = export_frames_csv(two_set_show(), per_count=2)
    assert len(text.strip().splitlines()) == 1 + 17 * 4
    with pytest.raises(SchemaError):
        export_frames_csv(two_set_show(), per_count=0)


def test_csv_quotes_user_text() -> None:
    row = ChartRow(
        entity="T1",
        group="trumpets",
        position_text="a, b",
        xy=(0.0, 0.0),
        facing=0.0,
        note='say "hi"\nthen go',
    )
    text = to_csv(Chart(set_id="1", rows=(row,)))
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
        entity="T1",
        performer_name="Ada",
        rows=(
            SheetRow(
                set="1",
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

    def blank(text: str) -> Show:
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


def test_export_json_writes_showpy_tag() -> None:
    assert export_json(two_set_show())["schema"] == "showpy.show.v1"
    assert SCHEMA_TAG == "showpy.show.v1"


def test_regression_import_json_reads_legacy_stage_tag() -> None:
    show = rich_show()
    current = json.loads(json.dumps(export_json(show)))
    legacy = {**current, "schema": "stage.show.v1"}
    assert LEGACY_SCHEMA_TAGS == ("stage.show.v1",)
    assert import_json(legacy) == import_json(current) == show


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def _display_width(text: str) -> int:
    return sum(WIDE_CELL if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


@pytest.mark.defect("UP-9.5-27")
@pytest.mark.xfail(strict=True, reason="UP-9.5-27")
def test_regression_chart_csv_neutralizes_formula_names() -> None:
    """Prefixes a quote to a performer name that spreadsheets would run as a formula.

    A name like `=HYPERLINK("http://evil")` lands in the chart CSV unchanged, and Excel or
    Sheets then evaluates it (OWASP CSV injection; audit 9.5 security; P0 #10).
    """
    show = guided_show()
    pub = live_publication(show)
    claim = Claim(spot="T1", performer=performer(FORMULA_NAME), claimed_at=AT)
    pub = pub.model_copy(update={"claims": (claim,)})
    rows = _csv_rows(to_csv(chart(show, "1", publication=pub)))
    cells = [cell for row in rows[1:] for cell in row]
    assert f"'{FORMULA_NAME}" in cells
    assert FORMULA_NAME not in cells


@pytest.mark.defect("UP-9.5-28")
@pytest.mark.xfail(strict=True, reason="UP-9.5-28")
@pytest.mark.parametrize("lead", ["+", "-", "@", "\t", "\r"])
def test_regression_csv_neutralizes_every_formula_trigger(lead: str) -> None:
    """Prefixes a quote to cells starting with + - @ tab or carriage return (audit 9.5)."""
    cell = f"{lead}1+1"
    rows = _csv_rows(to_csv(pd.DataFrame({"name": [cell], "offset": ["-5"]})))
    assert rows[1] == [f"'{cell}", "-5"]


@pytest.mark.defect("UP-9.5-29")
@pytest.mark.xfail(strict=True, reason="UP-9.5-29")
def test_regression_csv_writes_large_floats_without_exponent() -> None:
    """Writes 1234567.0 in full; the `:g` format turns it into 1.23457e+06 (audit 9.5)."""
    rows = _csv_rows(to_csv(pd.DataFrame({"count": [1234567.0]})))
    assert float(rows[1][0]) == 1234567.0
    assert "e" not in rows[1][0].lower()


@pytest.mark.defect("UP-9.5-30")
@pytest.mark.xfail(strict=True, reason="UP-9.5-30")
def test_regression_csv_keeps_cue_seconds_above_one_thousand_exact() -> None:
    """Keeps the precision of cue seconds past 1,000 s; `:g` keeps six significant digits."""
    seconds = 1234.5678
    rows = _csv_rows(to_csv(pd.DataFrame({"seconds": [seconds]})))
    assert approx_equal(float(rows[1][0]), seconds, TOL)


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


@pytest.mark.defect("UP-9.5-34")
@pytest.mark.xfail(strict=True, reason="UP-9.5-34")
def test_regression_export_json_include_accepts_a_single_field_name() -> None:
    """Treats `include="title"` as one field; today it iterates the characters of the string."""
    document = export_json(two_set_show(), include="title")
    assert sorted(document["show"]) == ["id", "title"]


@pytest.mark.defect("UP-9.5-35")
@pytest.mark.xfail(strict=True, reason="UP-9.5-35")
def test_regression_import_json_rejects_a_non_mapping_with_schema_error() -> None:
    """Raises SchemaError for `[1]`; today an AttributeError leaks from `.get`."""
    with pytest.raises(SchemaError):
        import_json([1])  # type: ignore[arg-type]
