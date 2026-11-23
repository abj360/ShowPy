"""
_data.py --- per-count frames, text tables and format plugins

Contains:
    export_frames_csv(): samples every spot through the engine, per count, as CSV
    as_table(): turns a coordinate sheet, chart or DataFrame into a TextTable
    to_csv(): writes a coordinate sheet, chart or DataFrame as CSV
    to_text(): writes a coordinate sheet, chart or DataFrame as aligned plain text
    FormatRegistry: holds named exporters and importers immutably
    register_exporter(): returns a registry extended with one exporter
    register_importer(): returns a registry extended with one importer
    formats(): lists the exporter and importer names of a registry
    DEFAULT_FORMATS: holds the built-in json and frames_csv formats
"""

import csv
import io
import math
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from showpy.choreo.engine import PathEngine
from showpy.core.csv_cells import neutralize_cell
from showpy.core.errors import (
    SchemaError,
    UnknownRefError,
)
from showpy.core.extras import is_dataframe
from showpy.core.numbers import format_number
from showpy.core.types import Point
from showpy.core.units import round_to
from showpy.core.util import close_matches
from showpy.export._models import Chart, CoordinateSheet, TextTable
from showpy.show import interchange
from showpy.show.models import Show
from showpy.time.clock import count_to_seconds
from showpy.time.models import MeasureLabel

if TYPE_CHECKING:
    import pandas as pd

FRAME_COLUMNS = ("count", "seconds", "entity", "x", "y", "facing", "on_field")
FRAME_PLACES = 6
POSITION_PLACES = 3
FACING_STEP = 0.1
FACING_PLACES = 1
FULL_TURN = 360.0
COLUMN_GAP = "  "
TITLE_SEPARATOR = " — "
LIST_SEPARATOR = ", "
TRUE_TEXT, FALSE_TEXT = "true", "false"
SHEET_HEADER = (
    "set", "counts", "cumulative", "measure", "position", "x", "y", "facing", "behavior",
    "crossings", "note",
)  # fmt: skip
CHART_HEADER = (
    "entity", "group", "performer", "position", "x", "y", "facing", "form", "on_field", "note",
)  # fmt: skip

type Tabular = CoordinateSheet | Chart | pd.DataFrame
type Exporter = Callable[..., str | bytes | Mapping[str, object]]
type Importer = Callable[..., Show]


def _number(value: float, places: int) -> str:
    text = f"{round(value, places) + 0.0:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _flag(*, value: bool) -> str:
    return TRUE_TEXT if value else FALSE_TEXT


def export_frames_csv(show: Show, per_count: int = 1) -> str:
    """Samples every spot through PathEngine from count 0 to the end, as CSV.

    Args:
        show: Show to sample.
        per_count: Samples per count, at least 1.

    Returns:
        text: Rows of count, seconds, entity, x, y, facing and on_field.

    >>> from showpy.examples import two_set_show
    >>> export_frames_csv(two_set_show()).splitlines()[1]
    '0,0,T1,0,5,0,true'
    """
    if per_count < 1:
        raise SchemaError(
            f"per_count must be at least 1, got {per_count}",
            hint="pass per_count >= 1",
            context={"per_count": per_count},
        )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FRAME_COLUMNS)
    total = sum(s.counts for s in show.sets[1:])
    frames = PathEngine(show).frames(0.0, total, per_count=per_count) if show.sets else iter(())
    for frame in frames:
        seconds = _number(count_to_seconds(show.timeline, frame.t), FRAME_PLACES)
        for state in frame.states:
            x, y = state.position
            writer.writerow(
                [
                    _number(frame.t, FRAME_PLACES),
                    seconds,
                    neutralize_cell(state.entity),
                    _number(x, FRAME_PLACES),
                    _number(y, FRAME_PLACES),
                    _number(state.facing, FRAME_PLACES),
                    _flag(value=state.on_field),
                ]
            )
    return out.getvalue()


def _facing_text(facing: float) -> str:
    return format_number(round_to(facing, FACING_STEP) % FULL_TURN, max_decimals=FACING_PLACES)


def _point_cells(xy: Point) -> list[str]:
    return [_number(xy[0], POSITION_PLACES), _number(xy[1], POSITION_PLACES)]


def _measure_text(label: MeasureLabel) -> str:
    return f"{label.measure}:{format_number(label.count_in_measure)}"


def _sheet_table(sheet: CoordinateSheet) -> TextTable:
    rows = []
    for r in sheet.rows:
        measure = "" if r.measure is None else _measure_text(r.measure)
        rows.append(
            (
                r.set,
                format_number(r.counts),
                format_number(r.cumulative),
                measure,
                r.position_text,
                *_point_cells(r.xy),
                _facing_text(r.facing),
                r.behavior,
                LIST_SEPARATOR.join(r.crossings),
                r.note or "",
            )
        )
    name = TITLE_SEPARATOR + sheet.performer_name if sheet.performer_name else ""
    return TextTable(title=sheet.entity + name, header=SHEET_HEADER, rows=tuple(rows))


def _chart_table(chart: Chart) -> TextTable:
    rows = tuple(
        (
            r.entity,
            r.group,
            r.performer_name or "",
            r.position_text,
            *_point_cells(r.xy),
            _facing_text(r.facing),
            r.form or "",
            _flag(value=r.on_field),
            r.note or "",
        )
        for r in chart.rows
    )
    return TextTable(title=f"set {chart.set_id}", header=CHART_HEADER, rows=rows)


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return _flag(value=value)
    if isinstance(value, float):
        return format_number(value) if math.isfinite(value) else str(value)
    return str(value)


def _frame_table(frame: "pd.DataFrame") -> TextTable:
    title = frame.attrs.get("title", "")
    rows = tuple(tuple(_cell(v) for v in record) for record in frame.itertuples(index=False))
    header = tuple(str(c) for c in frame.columns)
    return TextTable(title=title if isinstance(title, str) else "", header=header, rows=rows)


def as_table(obj: Tabular) -> TextTable:
    """Turns a coordinate sheet, chart or DataFrame into a titled table of display strings.

    >>> from showpy.examples import two_set_show
    >>> from showpy.export._print_sheets import chart
    >>> as_table(chart(two_set_show(), "1")).rows[0][:2]
    ('T1', 'trumpets')
    """
    if isinstance(obj, CoordinateSheet):
        return _sheet_table(obj)
    if isinstance(obj, Chart):
        return _chart_table(obj)
    if is_dataframe(obj):
        return _frame_table(obj)
    raise SchemaError(
        f"cannot tabulate a {type(obj).__name__}",
        hint="pass a CoordinateSheet, a Chart or a DataFrame",
        context={"type": type(obj).__name__},
    )


def to_csv(obj: Tabular, *, neutralize: bool = True) -> str:
    """Writes a coordinate sheet, chart or DataFrame as CSV; the csv module quotes every cell.

    Neutralizes formula cells per OWASP: a header or body cell that starts with `=`, `+`, `-`, `@`,
    tab or carriage return and is not a plain decimal literal gets a leading `'`, so spreadsheets
    show it as text. Pass `neutralize=False` for machine pipelines that read the cells back as-is.

    Args:
        obj: Table source to write.
        neutralize: Whether to prefix formula cells with a quote.

    Returns:
        text: The CSV document.

    >>> import pandas as pd
    >>> to_csv(pd.DataFrame({"a": ["x, y", "=1+1", "-5"]})).splitlines()
    ['a', '"x, y"', "'=1+1", '-5']
    """
    table = as_table(obj)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    guard = neutralize_cell if neutralize else str
    writer.writerow(guard(cell) for cell in table.header)
    writer.writerows([guard(cell) for cell in row] for row in table.rows)
    return out.getvalue()


def to_text(obj: Tabular) -> str:
    """Writes a coordinate sheet, chart or DataFrame as terse, left-aligned plain text.

    >>> import pandas as pd
    >>> print(to_text(pd.DataFrame({"set": ["1", "12"], "x": [0.5, 2.0]})))
    set  x
    1    0.5
    12   2
    """
    table = as_table(obj)
    lines = [[cell.replace("\n", " ") for cell in row] for row in (table.header, *table.rows)]
    widths = [max(len(line[i]) for line in lines) for i in range(len(table.header))]
    body = [
        COLUMN_GAP.join(c.ljust(w) for c, w in zip(line, widths, strict=True)) for line in lines
    ]
    return "\n".join(([table.title] if table.title else []) + [b.rstrip() for b in body])


class FormatRegistry:
    """Holds named exporters and importers; registration returns a new registry."""

    def __init__(self, exporters: Mapping[str, Exporter], importers: Mapping[str, Importer]):
        self._exporters = dict(exporters)
        self._importers = dict(importers)

    def exporter(self, name: str) -> Exporter:
        """Returns an exporter by name, raising UnknownRefError with suggestions."""
        return _lookup(self._exporters, name, "exporter")

    def importer(self, name: str) -> Importer:
        """Returns an importer by name, raising UnknownRefError with suggestions."""
        return _lookup(self._importers, name, "importer")

    def names(self) -> dict[str, list[str]]:
        """Lists exporter and importer names, sorted."""
        return {"export": sorted(self._exporters), "import": sorted(self._importers)}

    def with_exporter(self, name: str, fn: Exporter) -> "FormatRegistry":
        """Returns a copy that also holds `fn` as an exporter."""
        return FormatRegistry({**self._exporters, name: fn}, self._importers)

    def with_importer(self, name: str, fn: Importer) -> "FormatRegistry":
        """Returns a copy that also holds `fn` as an importer."""
        return FormatRegistry(self._exporters, {**self._importers, name: fn})


def _lookup[T](table: dict[str, T], name: str, kind: str) -> T:
    if name not in table:
        close = close_matches(name, table)
        raise UnknownRefError(
            f"unknown {kind} {name!r}",
            hint=f"did you mean {', '.join(close)}?" if close else f"use one of {sorted(table)}",
            context={kind: name},
        )
    return table[name]


def _require_name(name: str) -> None:
    if not name.strip():
        raise SchemaError("format name is blank", hint="name formats like 'json' or 'csv'")


def register_exporter(registry: FormatRegistry, name: str, fn: Exporter) -> FormatRegistry:
    """Returns a registry extended with an exporter that takes a show.

    >>> "text" in formats(register_exporter(DEFAULT_FORMATS, "text", lambda s: s.title))["export"]
    True
    """
    _require_name(name)
    return registry.with_exporter(name, fn)


def register_importer(registry: FormatRegistry, name: str, fn: Importer) -> FormatRegistry:
    """Returns a registry extended with an importer that returns a show.

    >>> from showpy.show.interchange import import_json
    >>> "copy" in formats(register_importer(DEFAULT_FORMATS, "copy", import_json))["import"]
    True
    """
    _require_name(name)
    return registry.with_importer(name, fn)


def formats(registry: FormatRegistry) -> dict[str, list[str]]:
    """Lists the exporter and importer names of a registry.

    >>> formats(DEFAULT_FORMATS)["export"]
    ['frames_csv', 'json']
    """
    return registry.names()


DEFAULT_FORMATS = FormatRegistry(
    exporters={"json": interchange.export_json, "frames_csv": export_frames_csv},
    importers={"json": interchange.import_json},
)
