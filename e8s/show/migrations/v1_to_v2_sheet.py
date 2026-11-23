"""
v1_to_v2_sheet.py --- the sheet sub-step of the v1 to v2 format migration

Version 2 has no landmarks, zones or coordinate systems. The step removes a v1 sheet's
`landmarks`, `zones` and `default_coordinates` and a v1 show's `coordinates` and `zones`, and
lists every one that held more than its v1 default with `report.drop()`, so a host can keep it.

Version 2 sheets refuse a non-positive size, an outline under three points, a reference that is
not `sheet:<name>@<version>`, repeated guide IDs and a guide with no points. A v1 sheet that
breaks one is refused with SchemaError naming the document, the field and the value; nothing is
repaired or guessed.

Contains:
    SHEET_DROPPED_FIELDS: names the v1 sheet fields version 2 drops, with their v1 defaults
    SHOW_DROPPED_FIELDS: names the v1 show fields version 2 drops, with their v1 defaults
    migrate_sheet(): drops the sheet and show fields v2 lacks and refuses a sheet v2 cannot hold
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import LEGACY_IDS_KEY
from e8s.core.json_types import JsonValue
from e8s.sheet.models import Sheet

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

SHEET_DOC: Final = "/show/sheet.json"
SHOW_DOC: Final = "/show/show.json"
LEGACY_CONTEXT: Final = {LEGACY_IDS_KEY: True}
V1_DEFAULT_COORDINATES: Final[JsonValue] = {
    "kind": "grid",
    "origin": [0.0, 0.0],
    "audience_side": "front",
    "units": "m",
    "precision": 0.01,
    "labels": {},
}
SHEET_DROPPED_FIELDS: Final[Mapping[str, JsonValue]] = {
    "landmarks": [],
    "zones": [],
    "default_coordinates": V1_DEFAULT_COORDINATES,
}
SHOW_DROPPED_FIELDS: Final[Mapping[str, JsonValue]] = {
    "coordinates": V1_DEFAULT_COORDINATES,
    "zones": [],
}

type JsonObject = dict[str, JsonValue]


def _refused(document: str, field: str, value: object, error: E8sError) -> SchemaError:
    return SchemaError(
        f"{document} {field} is {value!r}: {error.message}",
        hint=error.hint,
        context={"document": document, "field": field, "value": repr(value)},
    )


def _without_dropped(
    document: JsonObject,
    name: str,
    defaults: Mapping[str, JsonValue],
    report: "ReportBuilder",
) -> JsonObject:
    """Removes the fields v2 lacks from a document, listing each that held more than its default."""
    dropped = {key: document[key] for key in defaults if key in document}
    kept = {key: value for key, value in document.items() if key not in defaults}
    listed = {key: value for key, value in dropped.items() if value != defaults[key]}
    if listed:
        report.drop(name, "", listed)
    return kept


def _check_sheet(document: JsonValue) -> None:
    try:
        Sheet.model_validate(document, context=LEGACY_CONTEXT)
    except E8sError as error:
        ref = document.get("ref") if isinstance(document, dict) else None
        raise _refused(SHEET_DOC, "sheet", ref, error) from error


def migrate_sheet(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Drops the sheet and show fields version 2 lacks, then refuses a sheet v2 cannot hold.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the fields the step drops.

    Returns:
        docs: The documents without the dropped fields.

    >>> from e8s.show.migrate import ReportBuilder
    >>> report = ReportBuilder(1)
    >>> docs = migrate_sheet({SHOW_DOC: {"id": "s", "zones": [{"id": "pit"}]}}, report)
    >>> docs[SHOW_DOC], report.build().dropped[0].model_dump(mode="json")["fields"]
    ({'id': 's'}, {'zones': [{'id': 'pit'}]})
    """
    migrated = dict(docs)
    sheet = docs.get(SHEET_DOC)
    if isinstance(sheet, dict):
        migrated[SHEET_DOC] = _without_dropped(sheet, SHEET_DOC, SHEET_DROPPED_FIELDS, report)
    if SHEET_DOC in migrated:
        _check_sheet(migrated[SHEET_DOC])
    show = docs.get(SHOW_DOC)
    if isinstance(show, dict):
        migrated[SHOW_DOC] = _without_dropped(show, SHOW_DOC, SHOW_DROPPED_FIELDS, report)
    return migrated
