"""
v1_to_v2_paths.py --- the paths sub-step of the v1 to v2 format migration (lane B)

v2 types the motion parameters of the four built-in motions, bounds easings and tags facing
values. For each path row, a key the built-in motion never read in v1 is dropped, and an easing
strength below 1 is stored as the 1 that v1 used in its place; both are recorded with
`report.repair()`. A parameter or step count v2 cannot hold, or a `motion_fn` that is neither a
string nor null, is refused with SchemaError naming the document, the field and the value.
Parameters of registered custom motions pass through unchanged.

A route's v1 `curve` is a JSON construction recipe; v2 stores the curve model, so the step
validates the recipe and stores the curve's canonical shape. A recipe that is not a curve v2 can
hold is refused with SchemaError naming the document, the field and the value.

A bare v1 facing value of a path row or a show's facing override becomes the tagged value its
facing mode reads: a number becomes `fixed` with its degrees wrapped into [0, 360), a pair becomes
`point` and a string becomes `entity`; a value already tagged for its mode is validated and kept.
A wrapped heading and a value a mode never read
are repaired (the value is dropped); a value that does not fit its mode, or a mode that needs a
value and has none, is refused.

Contains:
    migrate_paths(): rewrites paths documents from the v1 shape to the v2 shape
"""

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from e8s.choreo.path_models import (
    BUILT_IN_MOTION_PARAMS,
    FACING_VALUE_KINDS,
    MAX_EASING_STEPS,
    MAX_MOTION_CYCLES,
    MAX_SPIRAL_TURNS,
    MIN_EASING_STRENGTH,
    EntityFacing,
    FacingMode,
    FixedFacing,
    PointFacing,
)
from e8s.core.errors import GeometryError, SchemaError
from e8s.core.json_types import JsonValue
from e8s.core.readonly import ReadOnlyMap
from e8s.geo.curves import curve_from_construction

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

ALLOWED_MOTION_FN = "a string or null"
# Matches stored paths documents; document_paths imports the migration chain, so not used here.
PATHS_DOC_PATTERN = re.compile(r"/transitions/[^/]+/paths\.json")
# Stored path of show.json, which holds the facing overrides; migrate imports this package.
SHOW_DOC_NAME = "/show/show.json"
FACING_MODELS: Final[Mapping[str, type[FixedFacing | PointFacing | EntityFacing]]] = ReadOnlyMap(
    {"fixed": FixedFacing, "point": PointFacing, "entity": EntityFacing}
)
FACING_FIELDS: Final[Mapping[str, str]] = ReadOnlyMap(
    {"fixed": "degrees", "point": "point", "entity": "entity"}
)
ALLOWED_ROUTE_CURVE = "a curve recipe: an object whose `kind` names a curve and whose fields fit it"
ALLOWED_FACING_VALUES: Final[Mapping[str, str]] = ReadOnlyMap(
    {
        "fixed": "a finite number of degrees",
        "point": "an [x, y] pair of finite meters",
        "entity": "a spot ID string",
    }
)
ALLOWED_PARAM_VALUES: Final[Mapping[str, str]] = ReadOnlyMap(
    {
        "amplitude": "a finite number of meters",
        "cycles": f"a multiple of 0.5 above 0, up to {MAX_MOTION_CYCLES}",
        "turns": f"a whole number from 0 to {MAX_SPIRAL_TURNS}",
        "clockwise": "true or false",
    }
)


def migrate_paths(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites paths documents from the v1 shape to the v2 shape.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    migrated = dict(docs)
    show = docs.get(SHOW_DOC_NAME)
    if isinstance(show, dict):
        migrated[SHOW_DOC_NAME] = _migrate_overrides(show, report)
    for document, spec in docs.items():
        if not PATHS_DOC_PATTERN.fullmatch(document) or not isinstance(spec, dict):
            continue
        rows = spec.get("rows")
        if isinstance(rows, dict):
            new_rows: dict[str, JsonValue] = {
                key: _migrate_row(document, key, row, report) for key, row in rows.items()
            }
            spec = {**spec, "rows": new_rows}
            migrated[document] = spec
        routes = spec.get("routes")
        if isinstance(routes, dict):
            new_routes: dict[str, JsonValue] = {
                key: _migrate_route(document, key, route) for key, route in routes.items()
            }
            migrated[document] = {**spec, "routes": new_routes}
    return migrated


def _migrate_row(document: str, key: str, row: JsonValue, report: "ReportBuilder") -> JsonValue:
    if not isinstance(row, dict):
        return row
    migrated = dict(row)
    if "facing_value" in row:
        field = f"rows.{key}.facing_value"
        value = _migrate_facing(
            document, field, row.get("facing_mode"), row["facing_value"], report
        )
        migrated["facing_value"] = value
    easing = row.get("easing")
    if isinstance(easing, dict):
        migrated["easing"] = _migrate_easing(document, f"rows.{key}.easing", easing, report)
    motion_fn = row.get("motion_fn")
    if motion_fn is not None and not isinstance(motion_fn, str):
        raise _refusal(document, f"rows.{key}.motion_fn", motion_fn, ALLOWED_MOTION_FN)
    params = row.get("motion_params", {})
    if (
        isinstance(motion_fn, str)
        and motion_fn in BUILT_IN_MOTION_PARAMS
        and isinstance(params, dict)
    ):
        field = f"rows.{key}.motion_params"
        migrated["motion_params"] = _migrate_params(document, field, motion_fn, params, report)
    return migrated


def _migrate_route(document: str, key: str, route: JsonValue) -> JsonValue:
    if not isinstance(route, dict) or "curve" not in route:
        return route
    recipe = route["curve"]
    field = f"routes.{key}.curve"
    if not isinstance(recipe, dict):
        raise _refusal(document, field, recipe, ALLOWED_ROUTE_CURVE)
    try:
        curve = curve_from_construction(recipe).construction()
    except (SchemaError, GeometryError) as exc:
        raise _refusal(document, field, recipe, ALLOWED_ROUTE_CURVE) from exc
    return {**route, "curve": curve}


def _migrate_overrides(show: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    overrides = show.get("facing_overrides")
    if not isinstance(overrides, list):
        return show
    migrated: list[JsonValue] = []
    for index, override in enumerate(overrides):
        if isinstance(override, dict) and "value" in override:
            field = f"facing_overrides[{index}].value"
            value = _migrate_facing(
                SHOW_DOC_NAME, field, override.get("mode"), override["value"], report
            )
            override = {**override, "value": value}
        migrated.append(override)
    return {**show, "facing_overrides": migrated}


def _migrate_facing(
    document: str, field: str, mode: JsonValue, value: JsonValue, report: "ReportBuilder"
) -> JsonValue:
    mode = FacingMode.TO_TARGET.value if mode is None else mode
    if not isinstance(mode, str) or mode not in FacingMode:
        return value
    kind = FACING_VALUE_KINDS.get(FacingMode(mode))
    if kind is None:
        if value is not None:
            report.repair(f"{document}: dropped {field} {value!r}, which v1's {mode} never read")
        return None
    return _tag_facing(document, field, kind, value, report)


def _tag_facing(
    document: str, field: str, kind: str, value: JsonValue, report: "ReportBuilder"
) -> JsonValue:
    allowed = ALLOWED_FACING_VALUES[kind]
    if value is None or isinstance(value, bool) or _holds_bool(value):
        raise _refusal(document, field, value, allowed)
    model = FACING_MODELS[kind]
    candidate = value if _is_tagged(kind, value) else {"kind": kind, FACING_FIELDS[kind]: value}
    try:
        tagged = model._validated(candidate).to_json()
    except SchemaError as exc:
        raise _refusal(document, field, value, allowed) from exc
    if kind == "fixed" and not isinstance(value, dict) and tagged["degrees"] != value:
        report.repair(f"{document}: {field} {value!r} wrapped to {tagged['degrees']!r} degrees")
    return tagged


def _is_tagged(kind: str, value: JsonValue) -> bool:
    return isinstance(value, dict) and value.get("kind") == kind


def _holds_bool(value: JsonValue) -> bool:
    return isinstance(value, list) and any(isinstance(item, bool) for item in value)


def _migrate_easing(
    document: str, field: str, easing: dict[str, JsonValue], report: "ReportBuilder"
) -> dict[str, JsonValue]:
    strength = easing.get("strength")
    if not isinstance(strength, int | float) or isinstance(strength, bool):
        return easing
    if easing.get("kind") == "steps" and round(strength) > MAX_EASING_STEPS:
        raise _refusal(document, f"{field}.strength", strength, f"at most {MAX_EASING_STEPS} steps")
    if strength >= MIN_EASING_STRENGTH:
        return easing
    report.repair(
        f"{document}: {field}.strength {strength} stored as {MIN_EASING_STRENGTH:g}, the strength"
        " v1 used for any value below it"
    )
    return {**easing, "strength": MIN_EASING_STRENGTH}


def _migrate_params(
    document: str,
    field: str,
    motion_fn: str,
    params: dict[str, JsonValue],
    report: "ReportBuilder",
) -> dict[str, JsonValue]:
    model = BUILT_IN_MOTION_PARAMS[motion_fn]
    kept: dict[str, JsonValue] = {}
    for name, value in params.items():
        if name not in model._field_names():
            report.repair(
                f"{document}: dropped key {name!r} from {field}, which v1's {motion_fn} never read"
            )
            continue
        try:
            model._validated({name: value})
        except SchemaError as exc:
            allowed = ALLOWED_PARAM_VALUES[name]
            raise _refusal(document, f"{field}.{name}", value, allowed) from exc
        kept[name] = value
    return kept


def _refusal(document: str, field: str, value: JsonValue, allowed: str) -> SchemaError:
    return SchemaError(
        f"{document} {field} holds {value!r}, which format v2 cannot hold",
        hint=f"edit {field} in the v1 show to a value v2 accepts: {allowed}",
        context={"path": document, "field": field, "value": repr(value)},
    )
