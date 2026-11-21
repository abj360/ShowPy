"""
v1_to_v2_paths.py --- the paths sub-step of the v1 to v2 format migration (lane B)

v2 bounds easings. For each path row, an easing strength below 1 is stored as the 1 that v1 used
in its place, recorded with `report.repair()`. A step count v2 cannot hold is refused with
SchemaError naming the document, the field and the value. Route curves keep their v1 shape until
lane B types them.

Contains:
    migrate_paths(): rewrites paths documents from the v1 shape to the v2 shape
"""

import re
from typing import TYPE_CHECKING

from showpy.choreo.path_models import MAX_EASING_STEPS, MIN_EASING_STRENGTH
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

# Matches stored paths documents; document_paths imports the migration chain, so not used here.
PATHS_DOC_PATTERN = re.compile(r"/transitions/[^/]+/paths\.json")


def migrate_paths(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites paths documents from the v1 shape to the v2 shape.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    migrated = dict(docs)
    for document, spec in docs.items():
        if not PATHS_DOC_PATTERN.fullmatch(document) or not isinstance(spec, dict):
            continue
        rows = spec.get("rows")
        if isinstance(rows, dict):
            new_rows: dict[str, JsonValue] = {
                key: _migrate_row(document, key, row, report) for key, row in rows.items()
            }
            migrated[document] = {**spec, "rows": new_rows}
    return migrated


def _migrate_row(document: str, key: str, row: JsonValue, report: "ReportBuilder") -> JsonValue:
    if not isinstance(row, dict):
        return row
    migrated = dict(row)
    easing = row.get("easing")
    if isinstance(easing, dict):
        migrated["easing"] = _migrate_easing(document, f"rows.{key}.easing", easing, report)
    return migrated


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


def _refusal(document: str, field: str, value: JsonValue, allowed: str) -> SchemaError:
    return SchemaError(
        f"{document} {field} holds {value!r}, which format v2 cannot hold",
        hint=f"edit {field} in the v1 show to a value v2 accepts: {allowed}",
        context={"path": document, "field": field, "value": repr(value)},
    )
