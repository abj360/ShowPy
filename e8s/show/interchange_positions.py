"""
interchange_positions.py --- merges CSV positions (set, counts, entity, x, y, facing) into a show

Contains:
    POSITION_FIELDS: names the position fields a CSV column can map to
    import_positions(): merges CSV rows into a show, creating missing sets and spots
"""

import csv
import io
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from e8s.cast.models import Cast, Spot, require_spot_count
from e8s.choreo.models import Placement, Set
from e8s.core.caps import MAX_POINTS, MAX_TEXT_CHARS, require_size
from e8s.core.errors import (
    SchemaError,
    ShowValidationError,
    UnknownRefError,
)
from e8s.core.ids import check_id
from e8s.core.numbers import normalize_angle
from e8s.core.types import Point
from e8s.core.util import close_matches
from e8s.show.manage import replace_sets
from e8s.show.models import Show, require_set_count
from e8s.show.set_keys import free_set_key, set_key

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


def _require_caps(base: Show, rows: list[_PositionRow]) -> None:
    """Refuses rows that would pass the placement, set or spot cap, before anything is built."""
    set_ids = {*base.set_ids(), *(r.set_id for r in rows)}
    spot_ids = {*base.cast.ids(), *(r.entity for r in rows)}
    require_size(len(set_ids) * len(spot_ids), MAX_POINTS, what="placements")
    require_set_count(len(set_ids))
    require_spot_count(len(spot_ids))


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

    >>> from e8s.examples import two_set_show
    >>> csv_text = "set,counts,entity,x,y\n3,4,T1,1,1\n"
    >>> import_positions(two_set_show(), csv_text, "trumpets").set("3").get("T2").point
    (2.0, 9.0)
    """
    require_size(len(text), MAX_TEXT_CHARS, what="positions CSV characters")
    rows = _read_rows(text, columns)
    _require_caps(base, rows)
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
