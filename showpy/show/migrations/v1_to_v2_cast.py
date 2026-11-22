"""
v1_to_v2_cast.py --- the cast sub-step of the v1 to v2 format migration (lane D)

Lane D owns this module. A v2 cast refuses a group stored under another key, a selection naming a
spot the cast lacks and a label prefix naming no group. Those three are repaired, each recorded
with `report.repair()`: the group takes its own key (its spots and prefix follow it, and a clash
with an existing key is refused), and the dangling selection IDs and prefixes are dropped. A value
with no safe repair (a negative size, a color that is not `#rrggbb`, a pad outside its range, a
limit or default radius that is not a positive finite number) is refused with SchemaError naming
the document, the field and the value. ID renames belong to the ids step.

Contains:
    migrate_cast(): rewrites the cast from the v1 shape to the v2 shape
"""

import math
import re
from typing import TYPE_CHECKING, Final

from showpy.cast.models import HEX_COLOR_PATTERN, MAX_LABEL_PAD
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

# The same path as showpy.show.migrate.SHOW_DOC, which imports this package and so cannot be
# imported here.
SHOW_DOC: Final = "/show/show.json"
SHAPE_SIZE_FIELDS: Final = ("radius_m", "width_m", "depth_m")

type JsonObject = dict[str, JsonValue]


def _object(value: JsonValue | None) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _refuse(field: str, value: JsonValue, rule: str) -> SchemaError:
    return SchemaError(
        f"{SHOW_DOC} {field} is {value!r}: {rule}",
        hint=f"edit the stored show: {rule}",
        context={"document": SHOW_DOC, "field": field, "value": repr(value)},
    )


def _as_number(value: JsonValue | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _is_positive_finite(value: JsonValue) -> bool:
    return isinstance(value, int | float) and math.isfinite(value) and value > 0


def _check_color(field: str, obj: JsonObject) -> None:
    color = obj.get("color")
    if color is not None and not (
        isinstance(color, str) and re.fullmatch(HEX_COLOR_PATTERN, color) is not None
    ):
        raise _refuse(field + ".color", color, "colors are `#rrggbb`")


def _check_group(key: str, group: JsonObject) -> None:
    field = f"cast.groups.{key}"
    _check_color(field, group)
    radius = group.get("default_radius")
    if radius is not None and not _is_positive_finite(radius):
        raise _refuse(f"{field}.default_radius", radius, "radii are positive finite numbers")
    for name, limit in _object(group.get("limits")).items():
        if not _is_positive_finite(limit):
            raise _refuse(f"{field}.limits.{name}", limit, "limits are positive finite numbers")


def _check_spot(position: int, spot: JsonObject) -> None:
    field = f"cast.spots[{position}]"
    _check_color(field, spot)
    shape = _object(spot.get("shape"))
    for name in SHAPE_SIZE_FIELDS:
        size = _as_number(shape.get(name))
        if size is not None and not (math.isfinite(size) and size >= 0):
            raise _refuse(f"{field}.shape.{name}", size, "sizes are 0 or more")


def _check_pad(scheme: JsonObject) -> None:
    pad = _as_number(scheme.get("pad"))
    if pad is not None and not 0 <= pad <= MAX_LABEL_PAD:
        raise _refuse("cast.label_scheme.pad", pad, f"pads are 0 to {MAX_LABEL_PAD}")


def _check_values(cast: JsonObject) -> None:
    for key, group in _object(cast.get("groups")).items():
        _check_group(key, _object(group))
    for position, spot in enumerate(_list(cast.get("spots"))):
        _check_spot(position, _object(spot))
    _check_pad(_object(cast.get("label_scheme")))


def _own_key_of(stored_key: str, group: JsonValue) -> str:
    own = _object(group).get("key")
    return own if isinstance(own, str) else stored_key


def _rekey_groups(cast: JsonObject, report: "ReportBuilder") -> JsonObject:
    groups = _object(cast.get("groups"))
    moves = {key: _own_key_of(key, group) for key, group in groups.items()}
    if all(old == new for old, new in moves.items()):
        return cast
    if len(set(moves.values())) != len(moves):
        clash = next(old for old, new in moves.items() if old != new)
        raise _refuse(f"cast.groups.{clash}", clash, "its group key repeats another group's key")
    for old, new in moves.items():
        if old != new:
            report.repair(f"Stored group {old!r} under its own key {new!r}")
    spots = [_regroup_spot(spot, moves) for spot in _list(cast.get("spots"))]
    scheme = _object(cast.get("label_scheme"))
    prefixes = {moves.get(k, k): v for k, v in _object(scheme.get("prefixes")).items()}
    return {
        **cast,
        "groups": {moves[key]: group for key, group in groups.items()},
        "spots": spots,
        "label_scheme": {**scheme, "prefixes": prefixes},
    }


def _regroup_spot(spot: JsonValue, moves: dict[str, str]) -> JsonValue:
    group = _object(spot).get("group")
    if isinstance(group, str) and moves.get(group, group) != group:
        return {**_object(spot), "group": moves[group]}
    return spot


def _drop_dangling_selection_ids(cast: JsonObject, report: "ReportBuilder") -> JsonObject:
    known = {str(_object(spot).get("id")) for spot in _list(cast.get("spots"))}
    kept: JsonObject = {}
    for name, ids in _object(cast.get("selections")).items():
        alive: list[JsonValue] = [i for i in _list(ids) if isinstance(i, str) and i in known]
        if len(alive) != len(_list(ids)):
            report.repair(f"Dropped spots the cast lacks from selection {name!r}")
        kept[name] = alive
    return {**cast, "selections": kept} if "selections" in cast else cast


def _drop_unknown_prefixes(cast: JsonObject, report: "ReportBuilder") -> JsonObject:
    scheme = _object(cast.get("label_scheme"))
    groups = _object(cast.get("groups"))
    prefixes = _object(scheme.get("prefixes"))
    kept = {key: value for key, value in prefixes.items() if key in groups}
    for key in prefixes.keys() - kept.keys():
        report.repair(f"Dropped label prefix for unknown group {key!r}")
    if len(kept) == len(prefixes):
        return cast
    return {**cast, "label_scheme": {**scheme, "prefixes": kept}}


def migrate_cast(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites the cast from the v1 shape to the v2 shape.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents with the repaired cast; a value with no safe repair raises SchemaError.
    """
    show = _object(docs.get(SHOW_DOC))
    cast = show.get("cast")
    if not isinstance(cast, dict):
        return docs
    _check_values(cast)
    repaired = _rekey_groups(cast, report)
    repaired = _drop_dangling_selection_ids(repaired, report)
    repaired = _drop_unknown_prefixes(repaired, report)
    return {**docs, SHOW_DOC: {**show, "cast": repaired}}
