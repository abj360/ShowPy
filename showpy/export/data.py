"""
data.py --- native JSON interchange, per-count frames, text tables and format plugins

Contains:
    SCHEMA_TAG: names the interchange format, `showpy.show.v1`
    FONTS_KEY: names the document key that holds the embedded fonts
    MAX_IMPORT_FONTS: bounds how many fonts one document may carry
    export_json(): dumps a show to a tagged JSON-safe document, embedding the fonts its text
        forms use
    import_json(): loads a show from a tagged document, refusing unknown schemas
    import_fonts(): registers the fonts embedded in a document, checking each against its hash
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

import base64
import csv
import io
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import pandas as pd

from showpy.choreo.engine import PathEngine
from showpy.choreo.form_text import text_recipe
from showpy.core.csv_cells import neutralize_cell
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import (
    ROOT_LOC,
    FontError,
    SchemaError,
    UnknownRefError,
    field_errors,
)
from showpy.core.types import Point
from showpy.core.units import round_to
from showpy.core.util import asset_uri, close_matches
from showpy.export.models import Chart, CoordinateSheet, TextTable
from showpy.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from showpy.show.models import Show
from showpy.time.clock import count_to_seconds

SCHEMA_TAG = "showpy.show.v1"
SCHEMA_KEY = "schema"
SHOW_KEY = "show"
FONTS_KEY = "fonts"
FORMS_FIELD = "forms"
TEXT_KIND = "text"
# A document with more fonts than the five bundled plus a generous allowance of user fonts is
# hostile; each entry is also bounded by MAX_FONT_BYTES.
MAX_IMPORT_FONTS = 64
BASE64_BLOCK_CHARS = 4
BASE64_BYTES_PER_BLOCK = 3
BASE64_SLACK_CHARS = 4
MAX_ENCODED_FONT_CHARS = (
    BASE64_BLOCK_CHARS * math.ceil(MAX_FONT_BYTES / BASE64_BYTES_PER_BLOCK) + BASE64_SLACK_CHARS
)
ALWAYS_EXPORTED = "id"
FRAME_COLUMNS = ("count", "seconds", "entity", "x", "y", "facing", "on_field")
FRAME_PLACES = 6
POSITION_PLACES = 3
FACING_STEP = 0.1
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


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(
    show: Show,
    include: Iterable[str] | None = None,
    *,
    font_registry: FontRegistry | None = None,
) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document, embedding the fonts its text forms use.

    Args:
        show: Show to export.
        include: Top-level show fields to keep; None keeps all. The ID is always kept.
        font_registry: Registry holding the fonts of the text forms; None means the bundled fonts.

    Returns:
        document: `{"schema": "showpy.show.v1", "show": {...}}`, plus `"fonts"` mapping each
            font URI to its base64 bytes, sorted by URI, when an exported form is a text form.
            A font the registry lacks raises MissingFontError.

    >>> from showpy.examples import two_set_show
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
    document: dict[str, Any] = {SCHEMA_KEY: SCHEMA_TAG, SHOW_KEY: dumped}
    registry = bundled_fonts() if font_registry is None else font_registry
    fonts = _embedded_fonts(show, dumped, registry)
    if fonts:
        document[FONTS_KEY] = fonts
    return document


def _embedded_fonts(
    show: Show, dumped: Mapping[str, Any], registry: FontRegistry
) -> dict[str, str]:
    if FORMS_FIELD not in dumped:
        return {}
    uris = {
        uri
        for form in show.forms.values()
        if form.kind == TEXT_KIND
        for uri in text_recipe(form.geometry).fonts
    }
    return {uri: base64.b64encode(registry.source(uri)).decode("ascii") for uri in sorted(uris)}


# Any: an imported document holds arbitrarily nested JSON values.
def import_json(d: Mapping[str, Any]) -> Show:
    """Loads a show from a tagged document, refusing a wrong or missing schema tag.

    >>> from showpy.examples import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    """
    if not isinstance(d, Mapping):
        raise SchemaError(
            f"document is a {type(d).__name__}, not an object",
            hint=f'documents are JSON objects: {{"schema": "{SCHEMA_TAG}", "show": {{...}}}}',
            context={"type": type(d).__name__},
        )
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
    except SchemaError as error:
        errors = field_errors(error)
        first = errors[0]
        field = first["loc"]
        raise SchemaError(
            f"show field {field} is invalid: {first['msg']}",
            hint=f"fix {'the show' if field == ROOT_LOC else field} to match {SCHEMA_TAG}",
            context={"field": field, "error_count": len(errors)},
        ) from error


def _decode_font(uri: object, encoded: object) -> bytes:
    if not isinstance(uri, str) or not isinstance(encoded, str):
        raise FontError(
            "embedded fonts must map URI strings to base64 strings",
            hint=f'write {{"{FONTS_KEY}": {{"asset:sha256:<hex>": "<base64>"}}}}',
            context={"uri": repr(uri)[:80]},
        )
    if len(encoded) > MAX_ENCODED_FONT_CHARS:
        raise FontError(
            f"embedded font {uri} is {len(encoded)} characters, over {MAX_ENCODED_FONT_CHARS}",
            hint=f"embed fonts of at most {MAX_FONT_BYTES} bytes",
            context={"uri": uri, "chars": len(encoded)},
        )
    try:
        data = base64.b64decode(encoded, validate=True)
    # b64decode raises binascii.Error for bad padding and plain ValueError for non-ASCII text.
    except ValueError as exc:
        raise FontError(
            f"embedded font {uri} is not valid base64",
            hint="embed each font with export_json()",
            context={"uri": uri},
        ) from exc
    if asset_uri(data) != uri:
        raise FontError(
            f"embedded font bytes do not hash to {uri}",
            hint="re-export the document; the font data was changed",
            context={"uri": uri, "actual": asset_uri(data)},
            code=ErrorCode.FONT_HASH_MISMATCH,
        )
    return data


def import_fonts(d: Mapping[str, Any], font_registry: FontRegistry | None = None) -> FontRegistry:
    """Registers the fonts embedded in a document, checking each against its URI.

    Recipes name fonts by URI, so an embedded font whose name is bound to other bytes, such as
    an older release of a bundled font, is registered by URI and the binding is left alone.

    Args:
        d: A document from `export_json`; one without a `fonts` key changes nothing.
        font_registry: Registry to extend; None means the bundled fonts. It is not changed.

    Returns:
        registry: `font_registry` plus every embedded font. A malformed entry, a hash mismatch or
            more than `MAX_IMPORT_FONTS` fonts raises FontError.

    >>> from showpy.examples import two_set_show
    >>> import_fonts(export_json(two_set_show())) is bundled_fonts()
    True
    """
    registry = bundled_fonts() if font_registry is None else font_registry
    if not isinstance(d, Mapping):
        raise FontError(
            f"document is a {type(d).__name__}, not a mapping",
            hint="pass the dict that export_json() returned",
            context={"type": type(d).__name__},
        )
    if FONTS_KEY not in d:
        return registry
    fonts = d[FONTS_KEY]
    if not isinstance(fonts, Mapping) or len(fonts) > MAX_IMPORT_FONTS:
        raise FontError(
            f"{FONTS_KEY!r} must be a mapping of at most {MAX_IMPORT_FONTS} fonts",
            hint=f'write {{"{FONTS_KEY}": {{"asset:sha256:<hex>": "<base64>"}}}}',
            context={"type": type(fonts).__name__},
        )
    for uri, encoded in fonts.items():
        registry, _ = register_font(registry, _decode_font(uri, encoded), if_name_taken="keep")
    return registry


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
        return f"{value:g}"
    return str(value)


def _frame_table(frame: pd.DataFrame) -> TextTable:
    title = frame.attrs.get("title", "")
    rows = tuple(tuple(_cell(v) for v in record) for record in frame.itertuples(index=False))
    header = tuple(str(c) for c in frame.columns)
    return TextTable(title=title if isinstance(title, str) else "", header=header, rows=rows)


def as_table(obj: Tabular) -> TextTable:
    """Turns a coordinate sheet, chart or DataFrame into a titled table of display strings.

    >>> from showpy.examples import two_set_show
    >>> from showpy.export.print_sheets import chart
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
