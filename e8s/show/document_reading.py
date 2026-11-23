"""
document_reading.py --- reads stored show documents of any readable format version into a Show

Every document is parsed as strict JSON. A tree of the current format is assembled directly, reusing
cached per-document decodes, and validated with CHECKED_CONTEXT, which checks every ID: the ids step
renamed every v1 ID before a tree was saved in this format. A tree of an older version is parsed
whole, run through the migration sub-steps, validated with any non-empty v1 ID accepted, run
through the model sub-steps and validated again, then through the sub-steps that write the
documents beside the show, such as the notes; nothing stored changes. An interchange dump of an
older version is first split into the documents a stored tree of that version holds.

Contains:
    read_show(): assembles a show from stored documents, migrating an older format in memory
    read_migrated(): migrates stored documents of an older format and returns the report too
    decode_show_dump(): assembles a show from an interchange dump of an older format version
    migrate_dump(): migrates an older interchange dump to a show and the documents beside it
"""

import itertools
from collections.abc import Callable, Mapping
from functools import partial
from typing import NamedTuple

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError, StorageError, field_errors
from e8s.core.ids import SetId, TransitionId, check_id
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_paths import (
    FORMS_FIELD,
    LEGACY_NOTES_DOC,
    NOTES_FIELD,
    REVISION_FIELD,
    SET_ORDER_FIELD,
    SHEET_DOC,
    SHEET_FIELD,
    SPLIT_FIELDS,
    TIMELINE_DOC,
    TIMELINE_FIELD,
    TRANSITIONS_FIELD,
    docs_for_order,
    forms_doc,
    is_storable_folder,
    paths_doc,
    set_doc,
)
from e8s.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    LEGACY_CONTEXT,
    SHOW_DOC,
    ReportBuilder,
    migrate_beside,
    migrate_model,
    migrate_objects,
    version_of,
    without_header,
)
from e8s.show.models import Show

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


class _Source(NamedTuple):
    """Reads the show documents of one stored tree as parsed JSON, by stored path.

    Attributes:
        has: Tells whether the tree holds a document.
        load: Returns the parsed JSON of a document the tree holds.
    """

    has: Callable[[str], bool]
    load: Callable[[str], JsonValue]


def _raw_source(docs: Mapping[str, bytes]) -> _Source:
    """Reads stored bytes, parsing each document strictly when it is first asked for."""
    return _Source(docs.__contains__, lambda path: parse_strict_json(docs[path], document=path))


def _object_source(objects: Mapping[str, JsonValue]) -> _Source:
    """Reads documents that are already parsed, such as migrated ones."""
    return _Source(objects.__contains__, objects.__getitem__)


def _load_json(source: _Source, path: str) -> JsonValue:
    if not source.has(path):
        raise StorageError(
            f"document {path} is missing",
            hint="write the whole show with save_show() first",
            context={"path": path},
        )
    return source.load(path)


def _shape_error(path: str, name: str, value: object) -> SchemaError:
    return SchemaError(
        f"{path} must hold a JSON {name}, not {type(value).__name__}",
        hint=f"write a JSON {name}, as save_show() does",
        context={"path": path},
    )


def _load_object(source: _Source, path: str, *, is_optional: bool = False) -> JsonObject:
    """Parses a document that must hold a JSON object; an absent optional one reads as `{}`."""
    if is_optional and not source.has(path):
        return {}
    value = _load_json(source, path)
    if not isinstance(value, dict):
        raise _shape_error(path, "object", value)
    return value


def _load_array(source: _Source, path: str, *, is_optional: bool = False) -> JsonArray:
    """Parses a document that must hold a JSON array; an absent optional one reads as `[]`."""
    if is_optional and not source.has(path):
        return []
    value = _load_json(source, path)
    if not isinstance(value, list):
        raise _shape_error(path, "array", value)
    return value


class _Reading(NamedTuple):
    """Holds how one decode reads documents.

    Attributes:
        source: The tree's documents.
        context: Validation context of every model built from them.
        cache: Decoded documents to reuse, or None.
        digests: Path to blob digest, naming the cache entries.
    """

    source: _Source
    context: Mapping[str, bool] | None = None
    cache: DecodeCache | None = None
    digests: Mapping[str, str] = {}

    def cached[T](self, path: str, decode: Callable[[], T]) -> T:
        """Returns a cached decode of a document, or decodes it and caches the result."""
        digest = self.digests.get(path)
        if self.cache is None or digest is None:
            return decode()
        return self.cache.get_or_decode(path, digest, decode)


def _set(reading: _Reading, key: str) -> Set:
    raw = _load_object(reading.source, set_doc(key))
    stored_key = raw.get("key")
    if isinstance(stored_key, str):
        check_id(stored_key, kind="set key")
    if "key" not in raw:
        raw = {**raw, "key": key}
    loaded = Set._validated(raw, context=reading.context)
    if loaded.key != key:
        raise SchemaError(
            f"{set_doc(key)} holds the set keyed {loaded.key}",
            hint=f"store each set under its key; drop key or set it to {key!r}",
            context={"path": set_doc(key), "key": loaded.key},
        )
    return loaded


def _forms(reading: _Reading, key: str) -> tuple[Form, ...]:
    stored = _load_array(reading.source, forms_doc(key), is_optional=True)
    return tuple(Form._validated(f, context=reading.context) for f in stored)


def _require_forms_for(key: str, set_id: SetId, forms: tuple[Form, ...]) -> None:
    wrong = [f.id for f in forms if f.set != set_id]
    if wrong:
        raise SchemaError(
            f"{forms_doc(key)} holds forms of another set: {', '.join(wrong)}",
            hint=f"give every form in this file set={set_id!r}",
            context={"set": set_id, "forms": wrong},
        )


def _paths(reading: _Reading, folder: tuple[str, str]) -> tuple[TransitionSpec, ...]:
    raw = _load_object(reading.source, paths_doc(folder), is_optional=True)
    if not raw:
        return ()
    return (TransitionSpec._validated(raw, context=reading.context),)


def _require_paths_for(
    folder: tuple[str, str], tid: TransitionId, specs: tuple[TransitionSpec, ...]
) -> None:
    for spec in specs:
        if spec.id != tid:
            raise SchemaError(
                f"{paths_doc(folder)} holds paths for {spec.id}",
                hint=f"set id to {list(tid)}",
                context={"tid": tid, "found": spec.id},
            )


def _set_order(order: object) -> list[str]:
    """Checks the set order show.json lists: an array of storable set keys, never text."""
    if not isinstance(order, list):
        raise _shape_error(SHOW_DOC, "array under 'sets'", order)
    bad = [k for k in order if not is_storable_folder(k)]
    if bad:
        raise SchemaError(
            f"{SHOW_DOC} lists set keys that cannot be stored: {', '.join(map(str, bad))}",
            hint="use set keys of letters, digits, _ and ., with no doubled .",
            context={"bad_set_keys": bad},
        )
    return order


def _transition_specs(reading: _Reading, stored: list[tuple[str, Set]]) -> list[TransitionSpec]:
    specs: list[TransitionSpec] = []
    for (key_a, set_a), (key_b, set_b) in itertools.pairwise(stored):
        folder = (key_a, key_b)
        found = reading.cached(paths_doc(folder), partial(_paths, reading, folder))
        _require_paths_for(folder, TransitionId(set_a.id, set_b.id), found)
        specs.extend(found)
    return specs


def _with_counts(
    spec: TransitionSpec, counts: float, context: Mapping[str, bool] | None
) -> TransitionSpec:
    """Gives paths the destination set's counts, validated in the decode's ID context."""
    fields = {name: getattr(spec, name) for name in type(spec)._field_names()}
    return TransitionSpec._validated(fields | {"counts": counts}, context=context)


def _assemble(reading: _Reading, meta: JsonObject, revision: str) -> Show:
    """Builds the show from a header-free show.json object and the other documents."""
    order = _set_order(meta.get(SET_ORDER_FIELD, []))
    rest = {key: value for key, value in meta.items() if key != SET_ORDER_FIELD}
    stored = [(k, reading.cached(set_doc(k), partial(_set, reading, k))) for k in order]
    forms: dict[str, Form] = {}
    for key, current in stored:
        found = reading.cached(forms_doc(key), partial(_forms, reading, key))
        _require_forms_for(key, current.id, found)
        forms.update((f.id, f) for f in found)
    sets = [current for _, current in stored]
    counts = {s.id: s.counts for s in sets}
    specs = [
        _with_counts(spec, counts[spec.id[1]], reading.context)
        for spec in _transition_specs(reading, stored)
    ]
    return Show._validated(
        {
            **rest,
            "sheet": _load_object(reading.source, SHEET_DOC),
            "timeline": _load_object(reading.source, TIMELINE_DOC),
            "sets": sets,
            "forms": forms,
            "transitions": specs,
            "revision": revision,
        },
        context=reading.context,
    )


class _Migrated(NamedTuple):
    """Holds what migrating an older tree produced.

    Attributes:
        show: The migrated show, validated with CHECKED_CONTEXT.
        report: The renames and repairs the sub-steps recorded.
        beside: Stored path to the parsed JSON of each document the migration writes beside
            the show, such as the notes document.
    """

    show: Show
    report: ReportBuilder
    beside: dict[str, JsonValue]


def _migrated_show(source: _Source, meta: JsonObject, version: int, revision: str) -> _Migrated:
    """Migrates a tree stored in an older format version to a show, in memory.

    Only the documents the set order names, and the v1 notes, are parsed, as for a current tree.
    The JSON sub-steps run first, then the show is validated with any non-empty v1 ID accepted,
    the model sub-steps run, the result is validated again with CHECKED_CONTEXT, and the
    sub-steps that write documents beside the show run on it and the migrated documents.
    """
    order = _set_order(meta.get(SET_ORDER_FIELD, []))
    objects: dict[str, JsonValue] = {SHOW_DOC: meta}
    for path in sorted((docs_for_order(order) | {LEGACY_NOTES_DOC}) - {SHOW_DOC}):
        if source.has(path):
            objects[path] = source.load(path)
    report = ReportBuilder(version)
    migrated = migrate_objects(objects, version, report)
    reading = _Reading(_object_source(migrated), LEGACY_CONTEXT)
    show = _assemble(reading, _load_object(reading.source, SHOW_DOC), revision)
    renamed = migrate_model(show, version, report)
    checked = Show._validated(renamed.to_json(), context=CHECKED_CONTEXT)
    return _Migrated(checked, report, migrate_beside(checked, migrated, version, report))


def _reraised_for_show(exc: SchemaError) -> SchemaError:
    """Turns a model validation error into one that names every failing field."""
    errors = field_errors(exc)
    return SchemaError(
        "show documents do not match the Show schema",
        hint="write documents produced by save_show(), or fix the fields named in context",
        context={"errors": errors},
    )


def decode_show_dump(dump: Mapping[str, JsonValue], version: int) -> Show:
    """Assembles a show from the interchange dump of an older format version, migrating it.

    The dump is split into the documents a stored tree of that version holds, so the same
    migration runs for interchange documents as for stored trees. `dump` is never mutated:
    the split builds new containers and migration steps never mutate what they are given.

    Args:
        dump: The `show` object of an interchange document, already strictly parsed.
        version: Format version of the interchange document.

    Returns:
        show: The migrated show; a dump that does not fit raises SchemaError.

    >>> from e8s.examples import two_set_show
    >>> dump = two_set_show().to_json()
    >>> decode_show_dump(dump, 1) == two_set_show()
    True
    """
    return migrate_dump(dump, version)[0]


def migrate_dump(dump: Mapping[str, JsonValue], version: int) -> tuple[Show, dict[str, JsonValue]]:
    """Migrates the interchange dump of an older format version to a show and its side documents.

    Args:
        dump: The `show` object of an interchange document, already strictly parsed.
        version: Format version of the interchange document.

    Returns:
        show: The migrated show; a dump that does not fit raises SchemaError.
        beside: Stored path to the parsed JSON of each document beside the show, such as the
            notes document at `/notes/notes.json`; empty when the dump held no notes.

    >>> from e8s.examples import two_set_show
    >>> migrate_dump(two_set_show().to_json(), 1)[1]
    {}
    """
    revision = dump.get(REVISION_FIELD, "")
    source = _object_source(_split_dump(dump))
    try:
        meta = _load_object(source, SHOW_DOC)
        migrated = _migrated_show(
            source, meta, version, revision if isinstance(revision, str) else ""
        )
    except SchemaError as exc:
        raise _reraised_for_show(exc) from exc
    return migrated.show, migrated.beside


def _dump_list(dump: Mapping[str, JsonValue], field: str) -> list[JsonObject]:
    """Returns a list field of a show dump whose items must be objects; absent reads as `[]`."""
    value = dump.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _shape_error(f"show field {field}", "array of objects", value)
    return [item for item in value if isinstance(item, dict)]


def _split_dump(dump: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Splits a show dump into the documents encode_show() writes, as parsed JSON."""
    sets = _dump_list(dump, SET_ORDER_FIELD)
    keys = {str(s.get("id")): str(s.get("key") or s.get("id")) for s in sets}
    if len(set(keys.values())) != len(sets):
        raise _shape_error(f"show field {SET_ORDER_FIELD}", "array of distinct sets", sets)
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
            raise _shape_error(f"show field {TRANSITIONS_FIELD}", "one entry per pair", pair)
        found[path] = spec
    return found


def _forms_by_set(
    dump: Mapping[str, JsonValue], keys: Mapping[str, str]
) -> dict[str, list[JsonValue]]:
    """Groups the forms of a show dump by set ID, refusing a form of no set."""
    forms = dump.get(FORMS_FIELD, {})
    if not isinstance(forms, dict):
        raise _shape_error(f"show field {FORMS_FIELD}", "object", forms)
    grouped: dict[str, list[JsonValue]] = {}
    for form in forms.values():
        set_id = form.get("set") if isinstance(form, dict) else None
        if not isinstance(set_id, str) or set_id not in keys:
            raise _shape_error(f"show field {FORMS_FIELD}", "form of an existing set", form)
        grouped.setdefault(set_id, []).append(form)
    return grouped


def read_show(
    docs: Mapping[str, bytes],
    revision: str = "",
    *,
    cache: DecodeCache | None = None,
    digests: Mapping[str, str] | None = None,
) -> Show:
    """Assembles a show from stored documents of any readable format version.

    documents.decode_show() documents the arguments; this is its implementation.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.documents import encode_show
    >>> read_show(encode_show(two_set_show())) == two_set_show()
    True
    """
    source = _raw_source(docs)
    try:
        meta = _load_object(source, SHOW_DOC)
        version = version_of(meta, name=SHOW_DOC)
        if version < CURRENT_VERSION:
            return _migrated_show(source, meta, version, revision).show
        reading = _Reading(source, CHECKED_CONTEXT, cache, digests or {})
        return _assemble(reading, without_header(meta), revision)
    except SchemaError as exc:
        raise _reraised_for_show(exc) from exc


def read_migrated(
    docs: Mapping[str, bytes],
) -> tuple[Show, ReportBuilder, dict[str, JsonValue]]:
    """Migrates stored documents of an older format version to a show, collecting the report.

    Args:
        docs: Stored path to document bytes; show.json must have an older version.

    Returns:
        show: The migrated show.
        report: What the migration renamed and repaired.
        beside: Stored path to the parsed JSON of each document the migration writes beside the
            show, such as the notes document.
    """
    source = _raw_source(docs)
    try:
        meta = _load_object(source, SHOW_DOC)
        migrated = _migrated_show(source, meta, version_of(meta, name=SHOW_DOC), "")
        return migrated.show, migrated.report, migrated.beside
    except SchemaError as exc:
        raise _reraised_for_show(exc) from exc
