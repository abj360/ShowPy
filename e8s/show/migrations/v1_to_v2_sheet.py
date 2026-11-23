"""
v1_to_v2_sheet.py --- the sheet sub-step of the v1 to v2 format migration

Version 2 sheets refuse a non-positive size, an outline under three points, a reference that is
not `sheet:<name>@<version>`, repeated landmark, zone or guide IDs, landmarks answering to one
name, a guide with no points and a coordinate system whose precision is not above 0. A v1 sheet
or show that breaks one is refused with SchemaError naming the document, the field and the
value; nothing is repaired or guessed. A sheet that passes is returned unchanged.

Contains:
    migrate_sheet(): refuses a stored sheet or coordinate system that v2 cannot hold
"""

from typing import TYPE_CHECKING, Final

from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import LEGACY_IDS_KEY
from e8s.core.json_types import JsonValue
from e8s.sheet.models import CoordinateSystem, Sheet

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

SHEET_DOC: Final = "/show/sheet.json"
SHOW_DOC: Final = "/show/show.json"
SHEET_COORDINATES_FIELD: Final = "default_coordinates"
SHOW_COORDINATES_FIELD: Final = "coordinates"
PRECISION_FIELD: Final = "precision"
LEGACY_CONTEXT: Final = {LEGACY_IDS_KEY: True}


def _refused(document: str, field: str, value: object, error: E8sError) -> SchemaError:
    return SchemaError(
        f"{document} {field} is {value!r}: {error.message}",
        hint=error.hint,
        context={"document": document, "field": field, "value": repr(value)},
    )


def _check_precision(document: str, field: str, coordinates: JsonValue) -> None:
    if not isinstance(coordinates, dict):
        return
    value = coordinates.get(PRECISION_FIELD)
    if isinstance(value, bool) or not isinstance(value, int | float) or value > 0:
        return
    raise SchemaError(
        f"{document} {field}.{PRECISION_FIELD} is {value!r}; version 2 needs a step above 0",
        hint="set the precision to a rounding step such as 0.01, then load the show again",
        context={"document": document, "field": f"{field}.{PRECISION_FIELD}", "value": repr(value)},
    )


def _check_sheet(document: JsonValue) -> None:
    _check_precision(SHEET_DOC, SHEET_COORDINATES_FIELD, _member(document, SHEET_COORDINATES_FIELD))
    try:
        Sheet.model_validate(document, context=LEGACY_CONTEXT)
    except E8sError as error:
        raise _refused(SHEET_DOC, "sheet", _member(document, "ref"), error) from error


def _check_show_coordinates(document: JsonValue) -> None:
    coordinates = _member(document, SHOW_COORDINATES_FIELD)
    _check_precision(SHOW_DOC, SHOW_COORDINATES_FIELD, coordinates)
    if coordinates is None:
        return
    try:
        CoordinateSystem.model_validate(coordinates, context=LEGACY_CONTEXT)
    except E8sError as error:
        raise _refused(SHOW_DOC, SHOW_COORDINATES_FIELD, coordinates, error) from error


def _member(document: JsonValue, name: str) -> JsonValue:
    return document.get(name) if isinstance(document, dict) else None


def migrate_sheet(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Refuses a stored sheet or coordinate system that version 2 cannot hold.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the repairs the step makes; this step repairs nothing.

    Returns:
        docs: The documents as given.
    """
    del report
    if SHEET_DOC in docs:
        _check_sheet(docs[SHEET_DOC])
    if SHOW_DOC in docs:
        _check_show_coordinates(docs[SHOW_DOC])
    return docs
