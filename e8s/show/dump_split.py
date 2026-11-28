"""
dump_split.py --- splits an interchange show dump into the documents a stored tree holds

Contains:
    split_dump(): splits a show dump into the documents encode_show() writes, as parsed JSON
"""

import itertools
from collections.abc import Mapping

from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.show.document_paths import (
    FORMS_FIELD,
    LEGACY_NOTES_DOC,
    NOTES_FIELD,
    SET_ORDER_FIELD,
    SHEET_DOC,
    SHEET_FIELD,
    SPLIT_FIELDS,
    TIMELINE_DOC,
    TIMELINE_FIELD,
    TRANSITIONS_FIELD,
    forms_doc,
    paths_doc,
    set_doc,
)
from e8s.show.document_sources import JsonObject, shape_error
from e8s.show.migrate import SHOW_DOC


def _dump_list(dump: Mapping[str, JsonValue], field: str) -> list[JsonObject]:
    """Returns a list field of a show dump whose items must be objects; absent reads as `[]`."""
    value = dump.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise shape_error(f"show field {field}", "array of objects", value)
    return [item for item in value if isinstance(item, dict)]


def split_dump(dump: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Splits a show dump into the documents encode_show() writes, as parsed JSON.

    Args:
        dump: The `show` object of an interchange document, already strictly parsed.

    Returns:
        objects: Stored path to the parsed JSON of each document; `dump` is never mutated.

    >>> from e8s.examples import two_set_show
    >>> sorted(split_dump(two_set_show().to_json()))[:2]
    ['/sets/1/set.json', '/sets/2/set.json']
    """
    sets = _dump_list(dump, SET_ORDER_FIELD)
    keys = {str(s.get("id")): str(s.get("key") or s.get("id")) for s in sets}
    if len(set(keys.values())) != len(sets):
        raise shape_error(f"show field {SET_ORDER_FIELD}", "array of distinct sets", sets)
    meta = {key: value for key, value in dump.items() if key not in SPLIT_FIELDS}
    objects: dict[str, JsonValue] = {SHOW_DOC: {**meta, SET_ORDER_FIELD: list(keys.values())}}
    missing = [field for field in (SHEET_FIELD, TIMELINE_FIELD) if field not in dump]
    if missing:
        raise SchemaError(
            f"show field {missing[0]} is missing",
            hint="export the whole show with export_json(), without include=",
            context={"field": missing[0], "error_count": len(missing)},
        )
    objects[SHEET_DOC] = dump[SHEET_FIELD]
    objects[TIMELINE_DOC] = dump[TIMELINE_FIELD]
    objects[LEGACY_NOTES_DOC] = dump.get(NOTES_FIELD, [])
    objects.update((set_doc(keys[str(s.get("id"))]), s) for s in sets)
    for set_id, forms in _forms_by_set(dump, keys).items():
        objects[forms_doc(keys[set_id])] = forms
    objects.update(_paths_by_pair(dump, keys))
    return objects


def _paths_by_pair(dump: Mapping[str, JsonValue], keys: Mapping[str, str]) -> JsonObject:
    """Files each transition of a show dump under its paths document by set keys.

    Every v1 release stored paths only between consecutive sets and read only those, so a
    transition between any other pair has no document to go to and is refused, never dropped.
    """
    consecutive = set(itertools.pairwise(keys))
    found: JsonObject = {}
    for spec in _dump_list(dump, TRANSITIONS_FIELD):
        pair = spec.get("id")
        ids = [part for part in pair if isinstance(part, str)] if isinstance(pair, list) else []
        if not isinstance(pair, list) or len(ids) != len(pair) or tuple(ids) not in consecutive:
            raise SchemaError(
                f"show field {TRANSITIONS_FIELD} holds paths for {pair!r}, which are not two"
                " consecutive sets",
                hint="keep paths only between consecutive sets, as save_show() stores them;"
                " remove the entry or point it at a consecutive pair",
                context={"field": TRANSITIONS_FIELD, "pair": repr(pair)},
            )
        first, second = ids
        path = paths_doc((keys[first], keys[second]))
        if path in found:
            raise shape_error(f"show field {TRANSITIONS_FIELD}", "one entry per pair", pair)
        found[path] = spec
    return found


def _forms_by_set(
    dump: Mapping[str, JsonValue], keys: Mapping[str, str]
) -> dict[str, list[JsonValue]]:
    """Groups the forms of a show dump by set ID, refusing a form of no set."""
    forms = dump.get(FORMS_FIELD, {})
    if not isinstance(forms, dict):
        raise shape_error(f"show field {FORMS_FIELD}", "object", forms)
    grouped: dict[str, list[JsonValue]] = {}
    for form in forms.values():
        set_id = form.get("set") if isinstance(form, dict) else None
        if not isinstance(set_id, str) or set_id not in keys:
            raise shape_error(f"show field {FORMS_FIELD}", "form of an existing set", form)
        grouped.setdefault(set_id, []).append(form)
    return grouped
