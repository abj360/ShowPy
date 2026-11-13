#!/usr/bin/env python3

"""
positions.py --- merges positions from CSV (set, counts, entity, x, y, facing) into a show

Contains:
    POSITION_FIELDS: names the position fields a CSV column can map to
    import_positions(): merges CSV rows into a show, creating missing sets and spots
"""

import csv
import io
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from showpy.cast.models import Cast, Spot
from showpy.choreo.models import Placement, Set
from showpy.core.errors import SchemaError, UnknownRefError, ValidationError
from showpy.core.ids import SetId, SpotId
from showpy.core.types import Point
from showpy.core.util import close_matches
from showpy.show.models import Show

POSITION_FIELDS = ("set", "counts", "entity", "x", "y", "facing")
REQUIRED_FIELDS = POSITION_FIELDS[:5]


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

    >>> from showpy.examples import two_set_show
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
