"""
role_documents.py --- splits a set into its set and role documents and joins them back

A stored set is one set.json plus one document per placement role: drill.json holds the drill
and pin roles, facing.json the facing role and labels.json the placement notes. A role document
is a JSON object keyed by spot ID whose values hold that spot's role fields. set.json holds every
other set field and lists the spot IDs in placement order under `placement_order`, since
canonical JSON sorts object keys. A set.json holding `placements` is a layout-1 set and is read
as it is; one tree may hold sets of both forms, but never both forms of one set. The cast is
stored in its own document, out of show.json. A set.json of neither form, a partial role set,
role documents without set.json, a role row that names other spots than the placement order, a
spot ID the ID rule rejects and a cast stored in both places or, in a current tree, in neither
are refused.

Contains:
    PLACEMENTS_FIELD: names the layout-1 set.json field holding the placements
    PLACEMENT_ORDER_FIELD: names the set.json field listing spot IDs in placement order
    CAST_ROOT: names the root that holds only the cast document
    ROLE_FIELDS: maps each role document name to the placement fields it holds
    set_documents(): splits the JSON of a set into its set.json and role document objects
    read_set_object(): reads a set's documents of either layout as the JSON of one set
    with_cast(): adds the cast document's object to a show.json object, refusing both forms and
        a required cast stored in neither
    require_cast_beside_roles(): refuses a tree that stores role documents but no cast document
    require_known_cast_documents(): refuses a document under `/cast/` other than the cast document
    require_listed_role_folders(): refuses role documents of a set folder show.json does not list
    require_no_role_documents(): refuses role or cast documents in a tree of an older format
    holds_layout1_documents(): tells whether a tree still stores a layout-1 set or cast
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from types import MappingProxyType

from e8s.choreo.models import Set
from e8s.core.error_context import bounded_repr
from e8s.core.errors import SchemaError
from e8s.core.ids import check_id
from e8s.core.json_types import JsonValue
from e8s.show.document_paths import (
    CAST_DOC,
    CAST_FIELD,
    DRILL_NAME,
    FACING_NAME,
    LABELS_NAME,
    ROLE_NAMES,
    SET_DOC_NAME,
    is_set_document,
    role_docs,
    set_doc,
)
from e8s.show.document_sources import JsonObject, Source, load_object, shape_error
from e8s.show.item_table import DRILL, ENTITY_FIELD, FACING, ITEM_TABLE, LABELS, PIN
from e8s.show.migrate import SHOW_DOC

PLACEMENT_MODEL = "Placement"
PLACEMENTS_FIELD = "placements"
PLACEMENT_ORDER_FIELD = "placement_order"
SET_FOLDER_SEGMENTS = 4
FOLDER_INDEX = 2
SHOWN_ID_CHARS = 40
LISTED_SPOTS = 20
CAST_ROOT = "/cast/"
DOCUMENT_ROLES = {DRILL_NAME: (DRILL, PIN), FACING_NAME: (FACING,), LABELS_NAME: (LABELS,)}


def _role_fields(roles: tuple[str, ...]) -> frozenset[str]:
    """Returns the placement fields the item table files under any of `roles`."""
    return frozenset(
        field
        for (model, field), (_, role) in ITEM_TABLE.items()
        if model == PLACEMENT_MODEL and role in roles
    )


ROLE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {name: _role_fields(roles) for name, roles in DOCUMENT_ROLES.items()}
)


def set_documents(current: Set) -> dict[str, JsonObject]:
    """Splits the JSON of a set into its set.json object and one object per role document.

    Args:
        current: The set to split.

    Returns:
        documents: Document name to its JSON object: set.json holds every set field but the
            placements plus the placement order; each role document maps spot ID to role fields.

    >>> from e8s.examples import two_set_show
    >>> documents = set_documents(two_set_show().set("2"))
    >>> documents["set.json"]["placement_order"], documents["facing.json"]["T1"]
    (['T1', 'T2', 'T3', 'T4'], {'facing': 0.0})
    """
    dumped = current.to_json()
    stored = dumped.pop(PLACEMENTS_FIELD, [])
    rows = [row for row in stored if isinstance(row, dict)] if isinstance(stored, list) else []
    order: list[JsonValue] = [row[ENTITY_FIELD] for row in rows]
    documents: dict[str, JsonObject] = {SET_DOC_NAME: {**dumped, PLACEMENT_ORDER_FIELD: order}}
    for name, fields in ROLE_FIELDS.items():
        role_rows: JsonObject = {
            str(row[ENTITY_FIELD]): {field: row[field] for field in sorted(fields)} for row in rows
        }
        documents[name] = role_rows
    return documents


def read_set_object(source: Source, key: str) -> JsonObject:
    """Reads a set's documents of either layout as the JSON object of one set.

    Args:
        source: The stored tree's documents.
        key: The set's storage key, its folder under `/sets/`.

    Returns:
        raw: The set's JSON with its placements in placement order. A set.json that holds
            `placements` beside role documents or a placement order, or neither `placements` nor
            a placement order, role documents without set.json, a partial role set or role rows
            that do not match the placement order raise SchemaError.
    """
    path = set_doc(key)
    present = [p for p in role_docs(key) if source.has(p)]
    if present and not source.has(path):
        raise SchemaError(
            f"{', '.join(present)} have no {path}",
            hint=f"store {path} beside the role documents, as save_show() does",
            context={"path": path, "role_documents": present},
        )
    raw = load_object(source, path)
    if PLACEMENTS_FIELD in raw:
        if present or PLACEMENT_ORDER_FIELD in raw:
            raise _both_forms(path, present)
        return raw
    if PLACEMENT_ORDER_FIELD not in raw:
        raise SchemaError(
            f"{path} holds neither `{PLACEMENTS_FIELD}` nor `{PLACEMENT_ORDER_FIELD}`",
            hint=f"store the set's spots under `{PLACEMENT_ORDER_FIELD}`, `[]` for none, beside"
            f" {', '.join(ROLE_NAMES)}, as save_show() does",
            context={"path": path, "role_documents": present},
        )
    missing = [p for p in role_docs(key) if p not in present]
    if missing:
        raise SchemaError(
            f"{path} stores its placements in role documents, but {', '.join(missing)} is missing",
            hint=f"store {', '.join(ROLE_NAMES)} beside set.json, as save_show() does",
            context={"path": path, "missing": missing},
        )
    order = _placement_order(path, raw)
    rows = [_role_rows(source, p, order) for p in present]
    placements: list[JsonValue] = [
        {ENTITY_FIELD: spot, **{f: v for row in rows for f, v in row[spot].items()}}
        for spot in order
    ]
    rest = {field: value for field, value in raw.items() if field != PLACEMENT_ORDER_FIELD}
    return {**rest, PLACEMENTS_FIELD: placements}


def _both_forms(path: str, present: list[str]) -> SchemaError:
    return SchemaError(
        f"{path} holds `{PLACEMENTS_FIELD}`, the layout-1 form, beside the role-document form"
        f" (`{PLACEMENT_ORDER_FIELD}` or role documents)",
        hint=f"store the set one way: re-encode it with save_show(), which writes"
        f" `{PLACEMENT_ORDER_FIELD}` and {', '.join(ROLE_NAMES)}",
        context={"path": path, "role_documents": present},
    )


def _placement_order(path: str, raw: JsonObject) -> list[str]:
    """Returns set.json's placement order, refusing a value that is not distinct spot IDs."""
    order = raw.get(PLACEMENT_ORDER_FIELD)
    if not isinstance(order, list):
        raise shape_error(path, f"array under {PLACEMENT_ORDER_FIELD!r}", order)
    spots = [spot for spot in order if isinstance(spot, str)]
    if len(spots) != len(order):
        raise shape_error(path, f"array of spot IDs under {PLACEMENT_ORDER_FIELD!r}", order)
    for spot in spots:
        _require_stored_spot_id(path, spot)
    repeated = sorted(spot for spot, n in Counter(spots).items() if n > 1)
    if repeated:
        raise SchemaError(
            f"{path} lists {len(repeated)} spots more than once under {PLACEMENT_ORDER_FIELD}:"
            f" {_listed(repeated)}",
            hint=f"list each spot once under {PLACEMENT_ORDER_FIELD}",
            context={"path": path, "repeated": repeated, "repeated_count": len(repeated)},
        )
    return spots


def _listed(spots: list[str]) -> str:
    """Joins the first LISTED_SPOTS keys, each cut to SHOWN_ID_CHARS and quoted; counts the rest.

    Keys come from stored documents, so each is quoted with repr and its control characters
    are escaped.
    """
    shown = [repr(spot[:SHOWN_ID_CHARS]) for spot in spots[:LISTED_SPOTS]]
    rest = len(spots) - len(shown)
    return ", ".join([*shown, f"(+{rest} more)"] if rest else shown)


def _more(items: list[str]) -> str:
    """Counts the items past the first LISTED_SPOTS, or returns nothing when none are left out."""
    rest = len(items) - LISTED_SPOTS
    return f" (+{rest} more)" if rest > 0 else ""


def _require_stored_spot_id(path: str, spot: str) -> None:
    """Refuses a spot ID the ID rule rejects, or one that is not stored in its NFC form."""
    try:
        is_stored_form = check_id(spot, kind="spot") == spot
    except SchemaError as exc:
        raise _bad_spot_id(path, spot, exc.message) from exc
    if not is_stored_form:
        raise _bad_spot_id(path, spot, "it is not in NFC form")


def _bad_spot_id(path: str, spot: str, reason: str) -> SchemaError:
    shown = spot[:SHOWN_ID_CHARS]
    return SchemaError(
        f"{path} lists the spot ID {shown!r} under {PLACEMENT_ORDER_FIELD}: {reason}",
        hint="store spot IDs as check_id() returns them: letters, digits, _ and . in NFC",
        context={"path": path, "spot": shown, "length": len(spot)},
    )


def _role_rows(source: Source, path: str, order: list[str]) -> dict[str, JsonObject]:
    """Reads one role document, refusing other spots than `order` or fields of other roles."""
    document = load_object(source, path)
    missing = sorted(set(order) - document.keys())
    extra = sorted(document.keys() - set(order))
    if missing or extra:
        raise SchemaError(
            f"{path} names other spots than its set's {PLACEMENT_ORDER_FIELD}: {len(missing)}"
            f" missing ({_listed(missing)}), {len(extra)} extra ({_listed(extra)})",
            hint=f"key {path} by exactly the spots {PLACEMENT_ORDER_FIELD} lists",
            context={
                "path": path,
                "missing": missing,
                "missing_count": len(missing),
                "extra": extra,
                "extra_count": len(extra),
            },
        )
    fields = ROLE_FIELDS[path.rsplit("/", 1)[1]]
    rows: dict[str, JsonObject] = {}
    for spot, row in document.items():
        if not isinstance(row, dict):
            raise shape_error(path, f"object under spot {spot!r}", row)
        stray = sorted(row.keys() - fields)
        if stray:
            raise SchemaError(
                f"{path} holds {_listed(stray)} for spot {spot!r}, which it does not store",
                hint=f"store only {', '.join(sorted(fields))} in {path}",
                context={"path": path, "spot": spot, "fields": stray},
            )
        rows[spot] = row
    return rows


def with_cast(source: Source, meta: JsonObject, *, is_required: bool) -> JsonObject:
    """Adds the cast document's object to a show.json object; layout-1 show.json holds its own.

    Args:
        source: The stored tree's documents.
        meta: The header-free show.json object.
        is_required: Whether the tree must store a cast, as every format-2 tree does.

    Returns:
        meta: `meta` with the cast of the cast document, or `meta` itself when show.json holds
            the cast. A show.json that holds a cast beside the cast document, or a required cast
            stored in neither, raises SchemaError.
    """
    if not source.has(CAST_DOC):
        if is_required and CAST_FIELD not in meta:
            raise SchemaError(
                f"the tree stores no cast: {CAST_DOC} is missing and {SHOW_DOC} holds none",
                hint=f"store the cast in {CAST_DOC}, as save_show() does",
                context={"path": CAST_DOC},
            )
        return meta
    if CAST_FIELD in meta:
        raise SchemaError(
            f"{SHOW_DOC} holds the cast beside {CAST_DOC}",
            hint=f"store the cast in {CAST_DOC} only, as save_show() does",
            context={"path": SHOW_DOC, "cast_document": CAST_DOC},
        )
    return {**meta, CAST_FIELD: load_object(source, CAST_DOC)}


def _folder(path: str) -> str:
    return path.split("/")[FOLDER_INDEX]


def require_cast_beside_roles(paths: Collection[str]) -> None:
    """Refuses a tree that stores role documents but no cast document.

    >>> require_cast_beside_roles(["/show/show.json", "/sets/1/set.json"])
    """
    if CAST_DOC in paths or not any(_is_role(p) for p in paths):
        return
    raise SchemaError(
        f"the tree stores role documents but no {CAST_DOC}",
        hint=f"keep {CAST_DOC} beside the role documents; save_show() writes both",
        context={"path": CAST_DOC},
    )


def require_known_cast_documents(paths: Iterable[str]) -> None:
    """Refuses a document under `/cast/` other than the cast document, which no format defines.

    >>> require_known_cast_documents(["/cast/cast.json", "/show/show.json"])
    """
    unknown = sorted(p for p in paths if p.startswith(CAST_ROOT) and p != CAST_DOC)
    if unknown:
        raise SchemaError(
            f"{len(unknown)} documents lie under {CAST_ROOT}, which holds only {CAST_DOC}:"
            f" {', '.join(bounded_repr(p) for p in unknown[:LISTED_SPOTS])}{_more(unknown)}",
            hint=f"delete them; the format stores only {CAST_DOC} under {CAST_ROOT}",
            context={"documents": unknown, "count": len(unknown)},
        )


def require_listed_role_folders(paths: Iterable[str], order: Collection[str]) -> None:
    """Refuses role documents of a set folder that show.json does not list.

    >>> require_listed_role_folders(["/sets/1/drill.json", "/sets/9/set.py"], ["1"])
    """
    listed = set(order)
    stray = sorted(p for p in paths if _is_role(p) and _folder(p) not in listed)
    if stray:
        raise SchemaError(
            f"{', '.join(stray)} belong to no set show.json lists, so they have no set.json",
            hint="delete them, or list their set in show.json with its set.json beside them",
            context={"role_documents": stray},
        )


def require_no_role_documents(paths: Iterable[str], version: int) -> None:
    """Refuses role or cast documents in a tree whose format version predates them.

    >>> require_no_role_documents(["/show/show.json", "/sets/1/set.json"], 1)
    """
    found = sorted(p for p in paths if p == CAST_DOC or _is_role(p))
    if found:
        raise SchemaError(
            f"a format {version} tree holds {', '.join(found)}",
            hint=f"format {version} stores placements in set.json and the cast in show.json;"
            " save the show with this release to store role documents",
            context={"version": version, "documents": found},
        )


def _is_role(path: str) -> bool:
    return is_set_document(path) and path.rsplit("/", 1)[1] in ROLE_NAMES


def holds_layout1_documents(paths: Collection[str]) -> bool:
    """Tells whether a tree stores a show.json without the cast document or a set without roles.

    >>> holds_layout1_documents(["/show/show.json", "/sets/1/set.json"])
    True
    >>> holds_layout1_documents([])
    False
    """
    if SHOW_DOC not in paths:
        return False
    if CAST_DOC not in paths:
        return True
    return any(
        len(p.split("/")) == SET_FOLDER_SEGMENTS
        and p == set_doc(_folder(p))
        and role_docs(_folder(p))[0] not in paths
        for p in paths
        if p.startswith("/sets/")
    )
