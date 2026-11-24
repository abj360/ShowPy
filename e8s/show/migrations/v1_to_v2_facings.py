"""
v1_to_v2_facings.py --- the facings sub-step of the v1 to v2 format migration

A v1 placement facing and fixed form facing could hold any number of degrees; v2 stores them as
headings wrapped into [0, 360). This step wraps each stored value the same way and records one
repair per value it changes. The offsets of the `along`, `normal` and `outward` form facings and
the facings of path rows are not headings and pass through. A facing that is not a finite
number cannot be held by v2 and is refused with SchemaError naming the document, the field and
the value; nothing is guessed.

Contains:
    migrate_facings(): rewrites stored facings from the v1 shape to the v2 shape
"""

import math
import re
from typing import TYPE_CHECKING, Final

from e8s.core.errors import SchemaError, bounded_repr
from e8s.core.json_types import JsonValue
from e8s.core.numbers import TURN_DEG
from e8s.core.units import normalize_angle

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

SET_DOC: Final = re.compile(r"^/sets/[^/]+/set\.json$")
FORMS_DOC: Final = re.compile(r"^/sets/[^/]+/forms\.json$")
FIXED_MODE: Final = "fixed"


def _refusal(document: str, field: str, value: object) -> SchemaError:
    return SchemaError(
        f"{document}: facing field {field} is not a finite number of degrees",
        hint="store the facing as a finite number of degrees before migrating",
        context={"path": document, "field": field, "value": bounded_repr(value)},
    )


def _wrapped(document: str, field: str, value: JsonValue) -> float | int:
    """Returns the heading of a stored facing, refusing a value that is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _refusal(document, field, value)
    return value if 0 <= value < TURN_DEG else normalize_angle(value)


def _migrated_placements(document: str, set_doc: JsonValue, report: "ReportBuilder") -> JsonValue:
    rows = set_doc.get("placements") if isinstance(set_doc, dict) else None
    if not isinstance(set_doc, dict) or not isinstance(rows, list):
        return set_doc
    migrated: list[JsonValue] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "facing" not in row:
            migrated.append(row)
            continue
        facing = _wrapped(document, f"placements[{index}].facing", row["facing"])
        if facing != row["facing"]:
            wrapped_from = row["facing"]
            report.repair(
                f"{document}: placement {row.get('entity')} faced {wrapped_from}; stored {facing}"
            )
        migrated.append({**row, "facing": facing})
    return {**set_doc, "placements": migrated}


def _migrated_form(
    document: str, index: int, form: JsonValue, report: "ReportBuilder"
) -> JsonValue:
    spec = form.get("facing") if isinstance(form, dict) else None
    if not isinstance(form, dict) or not isinstance(spec, dict) or spec.get("mode") != FIXED_MODE:
        return form
    field = f"[{index}].facing.value"
    value = _wrapped(document, field, spec.get("value"))
    if value != spec["value"]:
        report.repair(f"{document}: form {form.get('id')} faced {spec['value']}; stored {value}")
    return {**form, "facing": {**spec, "value": value}}


def _migrated_forms(document: str, forms: JsonValue, report: "ReportBuilder") -> JsonValue:
    if not isinstance(forms, list):
        return forms
    return [_migrated_form(document, i, form, report) for i, form in enumerate(forms)]


def migrate_facings(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites stored facings from the v1 shape to the v2 shape, wrapped into [0, 360).

    Wraps every placement facing in a set document and every fixed form facing in a forms
    document, recording one repair per value it changes. Every other document and field passes
    through as it is, and the input is never mutated.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes, one per wrapped facing.

    Returns:
        docs: The documents in the v2 shape.

    Raises:
        SchemaError: A facing is not a finite number; the context names the document (`path`),
            the field and the value.

    >>> from e8s.show.migrate import ReportBuilder
    >>> docs = {"/sets/1/set.json": {"placements": [{"entity": "A", "facing": 450.0}]}}
    >>> migrate_facings(docs, ReportBuilder(1))["/sets/1/set.json"]["placements"][0]["facing"]
    90.0
    """
    migrated: dict[str, JsonValue] = {}
    for path, doc in docs.items():
        if SET_DOC.match(path):
            migrated[path] = _migrated_placements(path, doc, report)
        elif FORMS_DOC.match(path):
            migrated[path] = _migrated_forms(path, doc, report)
        else:
            migrated[path] = doc
    return migrated
