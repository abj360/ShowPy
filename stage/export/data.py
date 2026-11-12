#!/usr/bin/env python3

"""
data.py --- native JSON interchange, per-count frames, position import, tables and format plugins

Contains:
    SCHEMA_TAG: names the interchange format, `stage.show.v1`
    export_json(): dumps a show to a tagged JSON-safe document
    import_json(): loads a show from a tagged document, refusing other schemas
    export_frames_csv(): samples every spot through the engine, per count, as CSV
    import_positions(): merges set, counts, entity, x, y rows from CSV into a show
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pydantic

from stage.cast.models import Cast, Spot
from stage.choreo.engine import PathEngine
from stage.choreo.models import Placement, Set
from stage.core.errors import SchemaError, UnknownRefError, ValidationError
from stage.core.ids import SetId, SpotId
from stage.core.types import Point
from stage.core.units import round_to
from stage.core.util import close_matches
from stage.export.models import Chart, CoordinateSheet, TextTable
from stage.show.models import Show
from stage.time.clock import count_to_seconds

SCHEMA_TAG = "stage.show.v1"
SCHEMA_KEY = "schema"
SHOW_KEY = "show"
ALWAYS_EXPORTED = "id"
FRAME_COLUMNS = ("count", "seconds", "entity", "x", "y", "facing", "on_field")
FRAME_PLACES = 6
POSITION_PLACES = 3
FACING_STEP = 0.1
FULL_TURN = 360.0
POSITION_FIELDS = ("set", "counts", "entity", "x", "y", "facing")
REQUIRED_FIELDS = POSITION_FIELDS[:5]
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


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(show: Show, include: Iterable[str] | None = None) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document.

    Args:
        show: Show to export.
        include: Top-level show fields to keep; None keeps all. The ID is always kept.

    Returns:
        document: `{"schema": "stage.show.v1", "show": {...}}`.

    >>> from tests.fixtures.shows import two_set_show
    >>> sorted(export_json(two_set_show(), include=["title"])["show"])
    ['id', 'title']
    """
    dumped = show.model_dump(mode="json")
    if include is not None:
        keep = {ALWAYS_EXPORTED, *include}
        for name in sorted(keep - set(dumped)):
            close = close_matches(name, dumped)
            raise UnknownRefError(
                f"show has no field {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(dumped)}",
                context={"field": name},
            )
        dumped = {key: value for key, value in dumped.items() if key in keep}
    return {SCHEMA_KEY: SCHEMA_TAG, SHOW_KEY: dumped}


# Any: an imported document holds arbitrarily nested JSON values.
def import_json(d: Mapping[str, Any]) -> Show:
    """Loads a show from a tagged document, refusing a wrong or missing schema tag.

    >>> from tests.fixtures.shows import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    """
    tag = d.get(SCHEMA_KEY)
    if tag != SCHEMA_TAG:
        raise SchemaError(
            f"document schema is {tag!r}, not {SCHEMA_TAG!r}",
            hint=f'export with export_json(); documents start {{"schema": "{SCHEMA_TAG}"}}',
            context={"schema": tag},
        )
    body = d.get(SHOW_KEY)
    if not isinstance(body, Mapping):
        raise SchemaError("document has no show object", hint=f"put the show under {SHOW_KEY!r}")
    try:
        return Show.model_validate(body)
    except pydantic.ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise SchemaError(
            f"show field {field or '(root)'} is invalid: {first['msg']}",
            hint=f"fix {field or 'the show'} to match {SCHEMA_TAG}",
            context={"field": field, "errors": error.error_count()},
        ) from error


def _number(value: float, places: int) -> str:
    text = f"{round(value, places) + 0.0:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _flag(value: bool) -> str:
    return TRUE_TEXT if value else FALSE_TEXT


def export_frames_csv(show: Show, per_count: int = 1) -> str:
    """Samples every spot through PathEngine from count 0 to the end, as CSV.

    Args:
        show: Show to sample.
        per_count: Samples per count, at least 1.

    Returns:
        text: Rows of count, seconds, entity, x, y, facing and on_field.

    >>> from tests.fixtures.shows import two_set_show
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
                    state.entity,
                    _number(x, FRAME_PLACES),
                    _number(y, FRAME_PLACES),
                    _number(state.facing, FRAME_PLACES),
                    _flag(state.on_field),
                ]
            )
    return out.getvalue()


@dataclass(frozen=True)
class _PositionRow:
    line: int
    set_id: SetId
    counts: float
    entity: SpotId
    point: Point
    facing: float | None


def _header_map(fieldnames: Iterable[str], columns: Mapping[str, str] | None) -> dict[str, str]:
    given = dict(columns or {})
    for name in given:
        if name not in POSITION_FIELDS:
            raise UnknownRefError(
                f"unknown position field {name!r}",
                hint=f"map only {', '.join(POSITION_FIELDS)}",
                context={"field": name},
            )
    present = set(fieldnames)
    mapping = {field: given.get(field, field) for field in POSITION_FIELDS}
    missing = [mapping[f] for f in REQUIRED_FIELDS if mapping[f] not in present]
    if missing:
        raise SchemaError(
            f"CSV lacks columns {', '.join(missing)}",
            hint="give set, counts, entity, x and y columns, or map them with columns=",
            context={"missing": missing, "header": sorted(present)},
        )
    return {field: name for field, name in mapping.items() if name in present}


def _cell_number(text: str, field: str, line: int) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise SchemaError(
            f"line {line}: {field} {text!r} is not a finite number",
            hint=f"write {field} as a plain number",
            context={"line": line, "field": field, "text": text},
        )
    return value


def _parse_row(
    record: Mapping[str, str | None], mapping: dict[str, str], line: int
) -> _PositionRow:
    def cell(field: str) -> str:
        return (record.get(mapping[field]) or "").strip() if field in mapping else ""

    facing = cell("facing")
    return _PositionRow(
        line=line,
        set_id=cell("set"),
        counts=_cell_number(cell("counts"), "counts", line),
        entity=cell("entity"),
        point=(_cell_number(cell("x"), "x", line), _cell_number(cell("y"), "y", line)),
        facing=_cell_number(facing, "facing", line) if facing else None,
    )


def _read_rows(text: str, columns: Mapping[str, str] | None) -> list[_PositionRow]:
    reader = csv.DictReader(io.StringIO(text))
    mapping = _header_map(reader.fieldnames or (), columns)
    rows: list[_PositionRow] = []
    seen: set[tuple[SetId, SpotId]] = set()
    for record in reader:
        row = _parse_row(record, mapping, reader.line_num)
        key = (row.set_id, row.entity)
        if not all(key) or key in seen:
            raise SchemaError(
                f"line {row.line}: set {row.set_id!r} entity {row.entity!r} is blank or repeated",
                hint="give each set and entity pair once",
                context={"line": row.line},
            )
        seen.add(key)
        rows.append(row)
    return rows


def _set_counts(base: Show, rows: list[_PositionRow]) -> dict[SetId, float]:
    counts: dict[SetId, float] = {}
    existing = {s.id: s.counts for s in base.sets}
    for row in rows:
        expected = counts.get(row.set_id, existing.get(row.set_id, row.counts))
        if expected != row.counts:
            raise SchemaError(
                f"line {row.line}: set {row.set_id} has {expected:g} counts, row says "
                f"{row.counts:g}",
                hint="give one counts value per set; retime existing sets before importing",
                context={"line": row.line, "set": row.set_id},
            )
        counts[row.set_id] = row.counts
    return counts


def _insert_at(order: list[SetId], file_ids: list[SetId], position: int) -> int:
    previous = file_ids[position - 1] if position > 0 else None
    if previous is not None:
        return order.index(previous) + 1
    later = [s for s in file_ids[position + 1 :] if s in order]
    return order.index(later[0]) if later else len(order)


def _set_order(base: Show, file_ids: list[SetId]) -> list[SetId]:
    order = base.set_ids()
    for position, set_id in enumerate(file_ids):
        if set_id not in order:
            order.insert(_insert_at(order, file_ids, position), set_id)
    return order


def _extend_cast(base: Show, rows: list[_PositionRow], group: str) -> Cast:
    if group not in base.cast.groups:
        close = close_matches(group, base.cast.groups)
        raise UnknownRefError(
            f"unknown group {group!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "add the group first",
            context={"group": group},
        )
    known = set(base.cast.ids())
    new_ids = [r.entity for r in rows if r.entity not in known]
    added = tuple(Spot(id=e, group=group) for e in dict.fromkeys(new_ids))
    return base.cast.model_copy(update={"spots": base.cast.spots + added})


@dataclass(frozen=True)
class _Merge:
    base: Show
    cast_ids: tuple[SpotId, ...]
    rows: dict[tuple[SetId, SpotId], _PositionRow]
    counts: dict[SetId, float]
    first_seen: dict[SpotId, Point]
    force: bool


def _first_seen(base: Show, order: list[SetId], rows: list[_PositionRow]) -> dict[SpotId, Point]:
    known: dict[SetId, dict[SpotId, Point]] = {
        s.id: {p.entity: p.point for p in s.placements} for s in base.sets
    }
    for row in rows:
        known.setdefault(row.set_id, {})[row.entity] = row.point
    first: dict[SpotId, Point] = {}
    for set_id in order:
        for entity, point in known.get(set_id, {}).items():
            first.setdefault(entity, point)
    return first


def _carried(merge: _Merge, entity: SpotId, previous: dict[SpotId, Placement]) -> Placement:
    if entity in previous:
        held = previous[entity]
        return Placement(
            entity=entity, x=held.x, y=held.y, facing=held.facing, on_field=held.on_field
        )
    if entity not in merge.first_seen:
        raise ValidationError(
            f"{entity} has no position in any set",
            hint="add a row for the spot or place it in the show first",
            context={"entity": entity},
        )
    x, y = merge.first_seen[entity]
    return Placement(entity=entity, x=x, y=y, on_field=False)


def _merged_set(merge: _Merge, set_id: SetId, previous: dict[SpotId, Placement]) -> Set:
    stored = set_id in merge.base.set_ids()
    current = merge.base.set(set_id) if stored else Set(id=set_id, counts=merge.counts[set_id])
    kept = {p.entity: p for p in current.placements}
    placements = tuple(kept.get(e) or _carried(merge, e, previous) for e in merge.cast_ids)
    filled = current.model_copy(update={"placements": placements})
    updates = {}
    for entity in merge.cast_ids:
        row = merge.rows.get((set_id, entity))
        if row is None:
            continue
        old = filled.get(entity)
        facing = old.facing if row.facing is None else row.facing
        x, y = row.point
        new = old.model_copy(update={"x": x, "y": y, "facing": facing, "on_field": True})
        if new != old:
            updates[entity] = new
    return filled.with_placements(updates, force=merge.force)


def import_positions(
    base: Show,
    text: str,
    group: str,
    columns: Mapping[str, str] | None = None,
    force: bool = False,
) -> Show:
    r"""Merges CSV rows of set, counts, entity, x, y and optional facing into a show.

    Missing sets are created where they fall in file order and missing spots join `group`.
    Every spot ends up placed in every set: a spot without a row holds its previous
    placement, and sits off the field at its first position before it first appears.

    Args:
        base: Show to merge into.
        text: CSV with a header row.
        group: Group that new spots join.
        columns: Position field (`set`, `counts`, `entity`, `x`, `y`, `facing`) to the CSV
            column holding it, for files with other headers.
        force: Move pinned placements anyway, as the designer.

    Returns:
        show: The merged show.

    >>> from tests.fixtures.shows import two_set_show
    >>> csv_text = "set,counts,entity,x,y\n3,4,T1,1,1\n"
    >>> import_positions(two_set_show(), csv_text, "trumpets").set("3").get("T2").point
    (2.0, 9.0)
    """
    rows = _read_rows(text, columns)
    counts = _set_counts(base, rows)
    cast = _extend_cast(base, rows, group)
    order = _set_order(base, list(counts))
    merge = _Merge(
        base=base,
        cast_ids=cast.ids(),
        rows={(r.set_id, r.entity): r for r in rows},
        counts=counts,
        first_seen=_first_seen(base, order, rows),
        force=force,
    )
    sets: list[Set] = []
    for set_id in order:
        previous = {p.entity: p for p in sets[-1].placements} if sets else {}
        sets.append(_merged_set(merge, set_id, previous))
    return base.model_copy(update={"cast": cast, "sets": tuple(sets)})


def _facing_text(facing: float) -> str:
    return f"{round_to(facing, FACING_STEP) % FULL_TURN:g}"


def _point_cells(xy: Point) -> list[str]:
    return [_number(xy[0], POSITION_PLACES), _number(xy[1], POSITION_PLACES)]


def _sheet_table(sheet: CoordinateSheet) -> TextTable:
    rows = []
    for r in sheet.rows:
        measure = "" if r.measure is None else f"{r.measure.measure}:{r.measure.count_in_measure:g}"
        rows.append(
            (
                r.set,
                f"{r.counts:g}",
                f"{r.cumulative:g}",
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
            _flag(r.on_field),
            r.note or "",
        )
        for r in chart.rows
    )
    return TextTable(title=f"set {chart.set_id}", header=CHART_HEADER, rows=rows)


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return _flag(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _frame_table(frame: pd.DataFrame) -> TextTable:
    title = frame.attrs.get("title", "")
    rows = tuple(tuple(_cell(v) for v in record) for record in frame.itertuples(index=False))
    header = tuple(str(c) for c in frame.columns)
    return TextTable(title=title if isinstance(title, str) else "", header=header, rows=rows)


def as_table(obj: Tabular) -> TextTable:
    """Turns a coordinate sheet, chart or DataFrame into a titled table of display strings.

    >>> from tests.fixtures.shows import two_set_show
    >>> from stage.export.print_sheets import chart
    >>> as_table(chart(two_set_show(), "1")).rows[0][:2]
    ('T1', 'trumpets')
    """
    if isinstance(obj, CoordinateSheet):
        return _sheet_table(obj)
    if isinstance(obj, Chart):
        return _chart_table(obj)
    if isinstance(obj, pd.DataFrame):
        return _frame_table(obj)
    raise SchemaError(
        f"cannot tabulate a {type(obj).__name__}",
        hint="pass a CoordinateSheet, a Chart or a DataFrame",
        context={"type": type(obj).__name__},
    )


def to_csv(obj: Tabular) -> str:
    """Writes a coordinate sheet, chart or DataFrame as CSV; the csv module quotes every cell.

    >>> import pandas as pd
    >>> to_csv(pd.DataFrame({"a": ["x, y"]})).splitlines()
    ['a', '"x, y"']
    """
    table = as_table(obj)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(table.header)
    writer.writerows(table.rows)
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
    exporters={"json": export_json, "frames_csv": export_frames_csv},
    importers={"json": import_json},
)
