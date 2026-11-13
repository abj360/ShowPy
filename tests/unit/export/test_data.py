#!/usr/bin/env python3

"""
test_data.py --- tests JSON interchange, frame CSV, position import, tables and format plugins
"""

import csv
import io
import json

import pandas as pd
import pytest

from stage.core.errors import SchemaError, UnknownRefError
from stage.core.util import approx_equal
from stage.export.data import (
    DEFAULT_FORMATS,
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
from stage.export.models import Chart, ChartRow, CoordinateSheet, SheetRow
from stage.export.print_sheets import chart, coordinate_sheet, cue_sheet
from stage.show.models import Show
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.export_shows import guided_show
from tests.fixtures.shows import two_set_show

TOL = 1e-9


def test_export_json_is_tagged_and_json_safe() -> None:
    exported = export_json(rich_show())
    assert exported["schema"] == SCHEMA_TAG == "stage.show.v1"
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
        {"schema": "stage.show/1", "show": {}},
        {"schema": "stage.show.v1"},
        {"schema": "stage.show.v1", "show": {"id": "x"}},
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
