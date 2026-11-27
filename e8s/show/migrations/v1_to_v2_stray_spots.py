"""
v1_to_v2_stray_spots.py --- drops what a v1 tree says about spots outside the cast

A v2 show refers only to spots its cast holds, but a v1 tree can name a spot its cast lacks. The
cast sub-step calls this module after its own repairs. Each reference to such a spot is removed
and listed with `report.drop()`, the values as the earlier sub-steps left them, so a host can keep
them:

- a set placement goes whole (`placements[i]`, the spot as `stored_id`);
- a path row goes whole (`rows.<spot>`);
- a path row facing toward the spot takes the default `to_target` facing (`rows.<spot>`, its
  `facing_mode` and `facing_value`);
- a route loses the spot with its offset (`routes.<id>`, the `members` and `offsets` removed);
- a form slot the spot stood on becomes an open slot (`[i]` of the forms document, `members`),
  except in a text form, which has no open slots: its member stays for validation to report;
- a facing override loses the spot (`facing_overrides[i]`, `entities`), and goes whole when no
  spot is left or when it faces toward the spot;
- an entity pin on the spot goes whole (`pins[i]`).

A selector string names spots by the v2 grammar; it is left for validation to report.

Contains:
    drop_stray_spots(): removes and lists every reference to a spot outside the cast
"""

import re
from typing import TYPE_CHECKING, Final

from e8s.choreo.path_models import FacingMode
from e8s.core.json_types import JsonValue

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

# The same path as e8s.show.migrate.SHOW_DOC, which imports this package and so cannot be
# imported here.
SHOW_DOC: Final = "/show/show.json"
SET_DOC: Final = re.compile(r"^/sets/[^/]+/set\.json$")
FORMS_DOC: Final = re.compile(r"^/sets/[^/]+/forms\.json$")
PATHS_DOC: Final = re.compile(r"^/transitions/[^/]+/paths\.json$")
TOWARD_ENTITY: Final = FacingMode.TOWARD_ENTITY.value
DEFAULT_FACING_MODE: Final = FacingMode.TO_TARGET.value
TEXT_KIND: Final = "text"

type JsonObject = dict[str, JsonValue]


def _object(value: JsonValue | None) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _is_stray(value: JsonValue | None, known: frozenset[str]) -> bool:
    return isinstance(value, str) and value not in known


def _facing_target(mode: JsonValue | None, value: JsonValue | None) -> JsonValue | None:
    """Returns the spot a toward-entity facing aims at, tagged or bare, else None."""
    if mode != TOWARD_ENTITY:
        return None
    return _object(value).get("entity") if isinstance(value, dict) else value


def _placements(
    path: str, set_doc: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    rows = _list(set_doc.get("placements"))
    kept: list[JsonValue] = []
    for position, row in enumerate(rows):
        spot = _object(row).get("entity")
        if _is_stray(spot, known):
            report.drop(path, f"placements[{position}]", _object(row), stored_id=str(spot))
        else:
            kept.append(row)
    return set_doc if len(kept) == len(rows) else {**set_doc, "placements": kept}


def _row_facing(
    path: str, spot: str, row: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    mode, value = row.get("facing_mode"), row.get("facing_value")
    if not _is_stray(_facing_target(mode, value), known):
        return row
    report.drop(path, f"rows.{spot}", {"facing_mode": mode, "facing_value": value}, stored_id=spot)
    return {**row, "facing_mode": DEFAULT_FACING_MODE, "facing_value": None}


def _rows(
    path: str, rows: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    kept: JsonObject = {}
    for spot, row in rows.items():
        if _is_stray(spot, known):
            report.drop(path, f"rows.{spot}", _object(row), stored_id=spot)
        elif isinstance(row, dict):
            kept[spot] = _row_facing(path, spot, row, known, report)
        else:
            kept[spot] = row
    return kept


def _route(
    path: str, route_id: str, route: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    members, offsets = _list(route.get("members")), _list(route.get("offsets"))
    stray = [i for i, member in enumerate(members) if _is_stray(member, known)]
    if not stray:
        return route
    dropped: JsonObject = {
        "members": [members[i] for i in stray],
        "offsets": [offsets[i] for i in stray if i < len(offsets)],
    }
    report.drop(path, f"routes.{route_id}", dropped, stored_id=route_id)
    return {
        **route,
        "members": [m for i, m in enumerate(members) if i not in stray],
        "offsets": [o for i, o in enumerate(offsets) if i not in stray],
    }


def _paths(
    path: str, paths_doc: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    rows = _rows(path, _object(paths_doc.get("rows")), known, report)
    routes = {
        route_id: _route(path, route_id, route, known, report) if isinstance(route, dict) else route
        for route_id, route in _object(paths_doc.get("routes")).items()
    }
    migrated = {**paths_doc}
    if "rows" in paths_doc:
        migrated["rows"] = rows
    if "routes" in paths_doc:
        migrated["routes"] = routes
    return migrated


def _form(
    path: str, position: int, form: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject:
    members = _list(form.get("members"))
    stray = [member for member in members if _is_stray(member, known)]
    if not stray or _object(form.get("recipe")).get("kind") == TEXT_KIND:
        return form
    form_id = form.get("id")
    report.drop(
        path,
        f"[{position}]",
        {"members": stray},
        stored_id=form_id if isinstance(form_id, str) else None,
    )
    return {**form, "members": [None if m in stray else m for m in members]}


def _forms(
    path: str, forms_doc: list[JsonValue], known: frozenset[str], report: "ReportBuilder"
) -> list[JsonValue]:
    return [
        _form(path, position, form, known, report) if isinstance(form, dict) else form
        for position, form in enumerate(forms_doc)
    ]


def _override(
    position: int, override: JsonObject, known: frozenset[str], report: "ReportBuilder"
) -> JsonObject | None:
    location = f"facing_overrides[{position}]"
    if _is_stray(_facing_target(override.get("mode"), override.get("value")), known):
        report.drop(SHOW_DOC, location, override)
        return None
    entities = override.get("entities")
    selection = _object(entities)
    ids = _list(selection.get("ids") if isinstance(entities, dict) else entities)
    stray = [spot for spot in ids if _is_stray(spot, known)]
    if not stray:
        return override
    kept = [spot for spot in ids if spot not in stray]
    if not kept:
        report.drop(SHOW_DOC, location, override)
        return None
    report.drop(SHOW_DOC, location, {"entities": stray})
    narrowed: JsonValue = {**selection, "ids": kept} if isinstance(entities, dict) else kept
    return {**override, "entities": narrowed}


def _show(show: JsonObject, known: frozenset[str], report: "ReportBuilder") -> JsonObject:
    migrated = {**show}
    if "facing_overrides" in show:
        overrides = [
            _override(position, override, known, report) if isinstance(override, dict) else override
            for position, override in enumerate(_list(show.get("facing_overrides")))
        ]
        migrated["facing_overrides"] = [o for o in overrides if o is not None]
    if "pins" in show:
        migrated["pins"] = [
            record
            for position, record in enumerate(_list(show.get("pins")))
            if not _is_stray_pin(position, record, known, report)
        ]
    return migrated


def _is_stray_pin(
    position: int, record: JsonValue, known: frozenset[str], report: "ReportBuilder"
) -> bool:
    fields = _object(record)
    pinned = fields.get("id")
    if fields.get("target") != "entity" or not _is_stray(pinned, known):
        return False
    report.drop(SHOW_DOC, f"pins[{position}]", fields, stored_id=str(pinned))
    return True


def _migrated(
    path: str, doc: JsonValue, known: frozenset[str], report: "ReportBuilder"
) -> JsonValue:
    if isinstance(doc, dict) and SET_DOC.match(path):
        return _placements(path, doc, known, report)
    if isinstance(doc, dict) and PATHS_DOC.match(path):
        return _paths(path, doc, known, report)
    if isinstance(doc, list) and FORMS_DOC.match(path):
        return _forms(path, doc, known, report)
    if isinstance(doc, dict) and path == SHOW_DOC:
        return _show(doc, known, report)
    return doc


def drop_stray_spots(
    docs: dict[str, JsonValue], known: frozenset[str], report: "ReportBuilder"
) -> dict[str, JsonValue]:
    """Removes every reference to a spot outside the cast and lists each with `report.drop()`.

    Args:
        docs: Stored path to parsed document after the earlier sub-steps; never mutated.
        known: IDs of the spots the cast holds, as stored.
        report: Collects what is dropped.

    Returns:
        docs: The documents with no reference to a spot outside the cast.

    >>> from e8s.show.migrate import ReportBuilder
    >>> docs = {"/sets/1/set.json": {"placements": [{"entity": "A"}, {"entity": "Z"}]}}
    >>> report = ReportBuilder(1)
    >>> drop_stray_spots(docs, frozenset({"A"}), report)["/sets/1/set.json"]
    {'placements': [{'entity': 'A'}]}
    >>> report.build().dropped[0].location
    'placements[1]'
    """
    return {path: _migrated(path, doc, known, report) for path, doc in docs.items()}
