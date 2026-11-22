"""
interchange.py --- JSON documents and CSV positions in and out of a show

Contains:
    SCHEMA_TAG: names the version 1 interchange format, `showpy.show.v1`, which import still reads
    FONTS_KEY: names the document key that holds the embedded fonts
    MAX_IMPORT_FONTS: bounds how many fonts one document may carry
    POSITION_FIELDS: names the position fields a CSV column can map to
    export_json(): dumps a show to a format-tagged JSON-safe document, embedding the fonts its
        text forms use
    import_json(): loads a show from a document of any readable format version, migrating it
    import_fonts(): registers the fonts embedded in a document, checking each against its hash
    import_positions(): merges CSV rows into a show, creating missing sets and spots
"""

import base64
import csv
import io
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Never

from showpy.cast.models import Cast, Spot
from showpy.choreo.form_text import text_recipe
from showpy.choreo.models import Placement, Set
from showpy.core.caps import MAX_POINTS, MAX_TEXT_CHARS, require_size
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import (
    ROOT_LOC,
    FontError,
    SchemaError,
    ShowValidationError,
    UnknownRefError,
)
from showpy.core.ids import check_id
from showpy.core.numbers import normalize_angle
from showpy.core.strict_json import strict_json_copy
from showpy.core.types import Point
from showpy.core.util import asset_uri, close_matches
from showpy.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from showpy.show.document_reading import decode_show_dump
from showpy.show.manage import replace_sets
from showpy.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    FIRST_VERSION,
    FORMAT,
    FORMAT_KEY,
    VERSION_KEY,
    format_header,
    version_of,
)
from showpy.show.models import Show
from showpy.show.set_keys import free_set_key, set_key

SCHEMA_TAG = "showpy.show.v1"
SCHEMA_KEY = "schema"
SHOW_KEY = "show"
DOCUMENT_NAME = "document"
DOCUMENT_HINT = (
    f'documents are JSON objects: {{"{FORMAT_KEY}": "{FORMAT}", "{VERSION_KEY}": {CURRENT_VERSION},'
    f' "{SHOW_KEY}": {{...}}}}'
)
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
POSITION_FIELDS = ("set", "counts", "entity", "x", "y", "facing")
REQUIRED_FIELDS = POSITION_FIELDS[:5]


@dataclass(frozen=True)
class _PositionRow:
    line: int
    set_id: str
    counts: float
    entity: str
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
            hint="give set, counts, entity, x, y (and optional facing) columns, or use columns=",
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
        facing=normalize_angle(_cell_number(facing, "facing", line)) if facing else None,
    )


def _read_rows(text: str, columns: Mapping[str, str] | None) -> list[_PositionRow]:
    reader = csv.DictReader(io.StringIO(text))
    try:
        return _collect_rows(reader, _header_map(reader.fieldnames or (), columns))
    except csv.Error as error:
        raise SchemaError(
            f"line {reader.line_num}: CSV is malformed: {error}",
            hint="quote cells that hold line breaks and write one record per line",
            context={"line": reader.line_num},
        ) from error


def _collect_rows(reader: csv.DictReader[str], mapping: dict[str, str]) -> list[_PositionRow]:
    rows: list[_PositionRow] = []
    seen: set[tuple[str, str]] = set()
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


def _set_counts(base: Show, rows: list[_PositionRow]) -> dict[str, float]:
    counts: dict[str, float] = {}
    existing: dict[str, float] = {s.id: s.counts for s in base.sets}
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


def _require_placement_cap(base: Show, rows: list[_PositionRow]) -> None:
    set_ids = {*base.set_ids(), *(r.set_id for r in rows)}
    spot_ids = {*base.cast.ids(), *(r.entity for r in rows)}
    require_size(len(set_ids) * len(spot_ids), MAX_POINTS, what="placements")


def _chain(first: str, follower: dict[str, str]) -> list[str]:
    chain = [first]
    while chain[-1] in follower:
        chain.append(follower[chain[-1]])
    return chain


def _set_order(base: Show, file_ids: list[str]) -> list[str]:
    """Places each new set right after the set before it in the file, in linear time.

    A new set that opens the file goes before the first stored set the file names later, or
    last when the file names none.
    """
    stored = base.set_ids()
    known = set(stored)
    follower = {
        file_ids[i - 1]: set_id for i, set_id in enumerate(file_ids) if i and set_id not in known
    }
    leading = file_ids[0] if file_ids and file_ids[0] not in known else None
    before = next((s for s in file_ids if s in known), None) if leading else None
    order: list[str] = []
    for set_id in stored:
        if leading is not None and set_id == before:
            order += _chain(leading, follower)
        order += _chain(set_id, follower)
    if leading is not None and before is None:
        order += _chain(leading, follower)
    return order


def _checked_entity(entity: str, line: int) -> str:
    try:
        return check_id(entity, kind="spot")
    except SchemaError as error:
        raise SchemaError(
            f"line {line}: {error.message}",
            hint=error.hint,
            context={**error.context, "line": line},
        ) from error


def _extend_cast(base: Show, rows: list[_PositionRow], group: str) -> Cast:
    if group not in base.cast.groups:
        close = close_matches(group, base.cast.groups)
        raise UnknownRefError(
            f"unknown group {group!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "add the group first",
            context={"group": group},
        )
    known = set(base.cast.ids())
    first_line: dict[str, int] = {}
    for row in rows:
        if row.entity not in known:
            first_line.setdefault(row.entity, row.line)
    added = tuple(
        Spot.model_validate({"id": _checked_entity(entity, line), "group": group})
        for entity, line in first_line.items()
    )
    return base.cast.replace(spots=base.cast.spots + added)


@dataclass(frozen=True)
class _Merge:
    base: Show
    cast_ids: tuple[str, ...]
    rows: dict[tuple[str, str], _PositionRow]
    counts: dict[str, float]
    keys: dict[str, str]
    first_seen: dict[str, Point]
    force: bool


def _first_seen(base: Show, order: list[str], rows: list[_PositionRow]) -> dict[str, Point]:
    known: dict[str, dict[str, Point]] = {
        s.id: {p.entity: p.point for p in s.placements} for s in base.sets
    }
    for row in rows:
        known.setdefault(row.set_id, {})[row.entity] = row.point
    first: dict[str, Point] = {}
    for set_id in order:
        for entity, point in known.get(set_id, {}).items():
            first.setdefault(entity, point)
    return first


def _carried(merge: _Merge, entity: str, previous: dict[str, Placement]) -> Placement:
    if entity in previous:
        held = previous[entity]
        return held.replace(form=None, order=None, pinned=False, note=None)
    if entity not in merge.first_seen:
        raise ShowValidationError(
            f"{entity} has no position in any set",
            hint="add a row for the spot or place it in the show first",
            context={"entity": entity},
        )
    x, y = merge.first_seen[entity]
    return Placement.model_validate({"entity": entity, "x": x, "y": y, "on_field": False})


def _new_keys(base: Show, order: list[str]) -> dict[str, str]:
    stored = set(base.set_ids())
    taken = [set_key(s) for s in base.sets]
    keys: dict[str, str] = {}
    for set_id in (s for s in order if s not in stored):
        keys[set_id] = free_set_key(taken, set_id)
        taken.append(keys[set_id])
    return keys


def _merged_set(merge: _Merge, set_id: str, previous: dict[str, Placement]) -> Set:
    stored = set_id in merge.base.set_ids()
    current = (
        merge.base.set(set_id)
        if stored
        else Set.model_validate(
            {"id": set_id, "key": merge.keys[set_id], "counts": merge.counts[set_id]}
        )
    )
    kept: dict[str, Placement] = {p.entity: p for p in current.placements}
    placements = tuple(kept.get(e) or _carried(merge, e, previous) for e in merge.cast_ids)
    filled = current.replace(placements=placements)
    updates = {}
    for entity in merge.cast_ids:
        row = merge.rows.get((set_id, entity))
        if row is None:
            continue
        old = filled.get(entity)
        facing = old.facing if row.facing is None else row.facing
        x, y = row.point
        new = old.replace(x=x, y=y, facing=facing, on_field=True)
        if new != old:
            updates[entity] = new
    return filled.with_placements(updates, force=merge.force)


def import_positions(
    base: Show,
    text: str,
    group: str,
    columns: Mapping[str, str] | None = None,
    *,
    force: bool = False,
) -> Show:
    r"""Merges CSV rows of set, counts, entity, x, y and optional facing into a show.

    Missing sets are created where they fall in file order, keyed like add_set(), and missing
    spots join `group`; a new entity ID must be a valid spot ID, and a facing wraps into [0, 360).
    Every spot ends up placed in every set: a spot without a row holds its previous
    placement, and sits off the field at its first position before it first appears. A set inserted
    between two stored sets drops the stored paths of the transition it splits; a locked one
    raises LockedError unless `force` is set.

    Args:
        base: Show to merge into.
        text: CSV with a header row; a SchemaError names the line of the first bad row.
        group: Group that new spots join.
        columns: Position field (`set`, `counts`, `entity`, `x`, `y`, `facing`) to the CSV
            column holding it, for files with other headers.
        force: Move pinned placements and edit locked transitions anyway, as the designer.

    Returns:
        show: The merged show.

    >>> from showpy.examples import two_set_show
    >>> csv_text = "set,counts,entity,x,y\n3,4,T1,1,1\n"
    >>> import_positions(two_set_show(), csv_text, "trumpets").set("3").get("T2").point
    (2.0, 9.0)
    """
    require_size(len(text), MAX_TEXT_CHARS, what="positions CSV characters")
    rows = _read_rows(text, columns)
    _require_placement_cap(base, rows)
    counts = _set_counts(base, rows)
    cast = _extend_cast(base, rows, group)
    order = _set_order(base, list(counts))
    merge = _Merge(
        base=base,
        cast_ids=cast.ids(),
        rows={(r.set_id, r.entity): r for r in rows},
        counts=counts,
        keys=_new_keys(base, order),
        first_seen=_first_seen(base, order, rows),
        force=force,
    )
    sets: list[Set] = []
    for set_id in order:
        previous: dict[str, Placement] = {p.entity: p for p in sets[-1].placements} if sets else {}
        sets.append(_merged_set(merge, set_id, previous))
    return replace_sets(base, sets, force=force, cast=cast)


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(
    show: Show,
    include: str | Iterable[str] | None = None,
    *,
    font_registry: FontRegistry | None = None,
) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document, embedding the fonts its text forms use.

    Args:
        show: Show to export.
        include: Top-level show fields to keep, or one field name; None keeps all. The ID is
            always kept.
        font_registry: Registry holding the fonts of the text forms; None means the bundled fonts.

    Returns:
        document: `{"format": "showpy.show", "version": 2, "show": {...}}`, plus `"fonts"`
            mapping each font URI to its base64 bytes, sorted by URI, when an exported form is
            a text form. A font the registry lacks raises MissingFontError.

    >>> from showpy.examples import two_set_show
    >>> sorted(export_json(two_set_show(), include=["title"])["show"])
    ['id', 'title']
    """
    dumped = show.model_dump(mode="json")
    if include is not None:
        names = [include] if isinstance(include, str) else include
        keep = {ALWAYS_EXPORTED, *names}
        for name in sorted(keep - set(dumped)):
            close = close_matches(name, dumped)
            raise UnknownRefError(
                f"show has no field {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(dumped)}",
                context={"field": name},
            )
        dumped = {key: value for key, value in dumped.items() if key in keep}
    document: dict[str, Any] = {**format_header(), SHOW_KEY: dumped}
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
    """Loads a show from a document of any readable format version, migrating older ones.

    Version 1 documents carry `"schema": "showpy.show.v1"`; later ones carry `"format"` and
    `"version"`. The show is checked as strict JSON first, as stored documents are.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        show: The show in the current format. A wrong or missing tag raises SchemaError, a
            newer version VersionError, and a show that is not strict JSON or does not fit
            SchemaError.

    >>> from showpy.examples import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    >>> v1 = {"schema": "showpy.show.v1", "show": two_set_show().model_dump(mode="json")}
    >>> import_json(v1) == two_set_show()
    True
    """
    if not isinstance(d, Mapping):
        raise SchemaError(
            f"document is a {type(d).__name__}, not an object",
            hint=DOCUMENT_HINT,
            context={"type": type(d).__name__},
        )
    version = _document_version(d)
    body = d.get(SHOW_KEY)
    show = strict_json_copy(dict(body), document=SHOW_KEY) if isinstance(body, Mapping) else None
    if not isinstance(show, dict):
        raise SchemaError("document has no show object", hint=f"put the show under {SHOW_KEY!r}")
    try:
        if version < CURRENT_VERSION:
            return decode_show_dump(show, version)
        return Show.model_validate(show, context=CHECKED_CONTEXT)
    except SchemaError as error:
        friendly = _first_field_error(error)
        if friendly is error:
            raise
        raise friendly from error


def _document_version(d: Mapping[str, object]) -> int:
    """Reads the format version of an interchange document from its tag or header."""
    if SCHEMA_KEY not in d:
        return version_of(d, name=DOCUMENT_NAME) if FORMAT_KEY in d else _unknown_tag(None)
    if d[SCHEMA_KEY] != SCHEMA_TAG or FORMAT_KEY in d or VERSION_KEY in d:
        _unknown_tag(d[SCHEMA_KEY])
    return FIRST_VERSION


def _unknown_tag(tag: object) -> Never:
    raise SchemaError(
        f"document schema is {tag!r}, not {SCHEMA_TAG!r} or format {FORMAT!r}",
        hint=f"export with export_json(); {DOCUMENT_HINT}, or version 1 documents"
        f' {{"{SCHEMA_KEY}": "{SCHEMA_TAG}", "{SHOW_KEY}": {{...}}}}',
        context={"schema": repr(tag)},
        code=ErrorCode.UNKNOWN_FORMAT,
    )


def _first_field_error(error: SchemaError) -> SchemaError:
    """Names the first failing show field of a validation error and counts the rest.

    An error without per-field entries already teaches and is returned unchanged.
    """
    errors = error.context.get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return error
    field = errors[0].get("loc")
    return SchemaError(
        f"show field {field} is invalid: {errors[0].get('msg')}",
        hint=f"fix {'the show' if field == ROOT_LOC else field} to match {FORMAT}",
        context={"field": field, "error_count": len(errors)},
    )


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
