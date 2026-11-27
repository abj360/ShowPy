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
    migrate_dump(): migrates an older interchange dump to a show, its report and the documents
        beside it
"""

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import NamedTuple

from e8s.cast.models import Cast
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError, field_errors
from e8s.core.ids import SetId, TransitionId, check_id
from e8s.core.json_types import JsonValue
from e8s.core.types import FrozenModel
from e8s.sheet.models import Sheet
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_paths import (
    CAST_DOC,
    CAST_FIELD,
    LEGACY_NOTES_DOC,
    REVISION_FIELD,
    SET_ORDER_FIELD,
    SHEET_DOC,
    TIMELINE_DOC,
    docs_for_order,
    forms_doc,
    is_storable_folder,
    paths_doc,
    role_docs,
    set_doc,
)
from e8s.show.document_sources import (
    JsonObject,
    Source,
    load_array,
    load_object,
    object_source,
    raw_source,
    shape_error,
)
from e8s.show.dump_split import split_dump
from e8s.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    LEGACY_CONTEXT,
    SHOW_DOC,
    MigrationReport,
    ReportBuilder,
    migrate_beside,
    migrate_model,
    migrate_objects,
    version_of,
    without_header,
)
from e8s.show.models import Show, require_set_count
from e8s.show.role_documents import (
    read_set_object,
    require_known_cast_documents,
    require_listed_role_folders,
    require_no_role_documents,
    with_cast,
)
from e8s.time.models import Timeline


class _Reading(NamedTuple):
    """Holds how one decode reads documents.

    Attributes:
        source: The tree's documents.
        context: Validation context of every model built from them.
        cache: Decoded documents to reuse, or None.
        digests: Path to blob digest, naming the cache entries.
    """

    source: Source
    context: Mapping[str, bool] | None = None
    cache: DecodeCache | None = None
    digests: Mapping[str, str] = {}

    def cached[T](
        self, path: str, decode: Callable[[], T], also: Sequence[str] = (), variant: str = ""
    ) -> T:
        """Returns a cached decode of a document and the `also` documents it reads, or decodes it.

        The cache key joins the digests of the documents present, so a change to any of them,
        or one appearing or going, misses the cache. `variant` names any other input the decode
        reads, such as the counts given to paths, so each input value gets its own entry.
        """
        digest = self.digests.get(path)
        present = [p for p in also if self.source.has(p)]
        if self.cache is None or digest is None or any(p not in self.digests for p in present):
            return decode()
        parts = [
            digest,
            *(f"{p}={self.digests[p]}" for p in present),
            *([variant] if variant else []),
        ]
        return self.cache.get_or_decode(path, " ".join(parts), decode)


def _set(reading: _Reading, key: str) -> Set:
    raw = read_set_object(reading.source, key)
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
    stored = load_array(reading.source, forms_doc(key), is_optional=True)
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
    raw = load_object(reading.source, paths_doc(folder), is_optional=True)
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
    """Checks the set order show.json lists: an array of at most MAX_SETS storable set keys."""
    if not isinstance(order, list):
        raise shape_error(SHOW_DOC, "array under 'sets'", order)
    require_set_count(len(order))
    bad = [k for k in order if not is_storable_folder(k)]
    if bad:
        raise SchemaError(
            f"{SHOW_DOC} lists set keys that cannot be stored: {', '.join(map(str, bad))}",
            hint="use set keys of letters, digits, _ and ., with no doubled .",
            context={"bad_set_keys": bad},
        )
    return order


def _transition_specs(reading: _Reading, stored: list[tuple[str, Set]]) -> list[TransitionSpec]:
    """Decodes the paths of each consecutive pair, each given its destination set's counts."""
    specs: list[TransitionSpec] = []
    for (key_a, set_a), (key_b, set_b) in itertools.pairwise(stored):
        folder = (key_a, key_b)
        found = reading.cached(paths_doc(folder), partial(_paths, reading, folder))
        _require_paths_for(folder, TransitionId(set_a.id, set_b.id), found)
        specs.extend(_counted(reading, folder, spec, set_b.counts) for spec in found)
    return specs


def _is_same_float(a: float, b: float) -> bool:
    """Tells whether two floats are one value, signed zeros told apart."""
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


def _counted(
    reading: _Reading, folder: tuple[str, str], spec: TransitionSpec, counts: float
) -> TransitionSpec:
    """Returns paths with the destination set's counts; paths already holding them are reused."""
    if _is_same_float(spec.counts, counts):
        return spec
    rebuild = partial(_with_counts, spec, counts, reading.context)
    return reading.cached(paths_doc(folder), rebuild, variant=f"counts={counts!r}")


def _with_counts(
    spec: TransitionSpec, counts: float, context: Mapping[str, bool] | None
) -> TransitionSpec:
    """Gives paths the destination set's counts, validated in the decode's ID context."""
    fields = {name: getattr(spec, name) for name in type(spec)._field_names()}
    return TransitionSpec._validated(fields | {"counts": counts}, context=context)


def _validated_part[M: FrozenModel](reading: _Reading, path: str, model: type[M]) -> M:
    return model._validated(load_object(reading.source, path), context=reading.context)


def _part[M: FrozenModel](reading: _Reading, path: str, model: type[M]) -> M | JsonObject:
    """Returns a show-level document decoded once per digest, or its object when it is invalid.

    An invalid document goes into the Show as parsed, so the Show's own validation refuses it
    and names the failing fields under the Show's field, as a whole-show decode does.
    """
    try:
        return reading.cached(path, partial(_validated_part, reading, path, model))
    except SchemaError:
        return load_object(reading.source, path)


def _show_fields(reading: _Reading, meta: JsonObject, *, is_current: bool) -> Mapping[str, object]:
    """Returns show.json's fields with the cast; a stored cast document is decoded once."""
    if is_current and reading.source.has(CAST_DOC) and CAST_FIELD not in meta:
        return {**meta, CAST_FIELD: _part(reading, CAST_DOC, Cast)}
    return with_cast(reading.source, meta, is_required=is_current)


def _assemble(reading: _Reading, meta: JsonObject, revision: str, *, is_current: bool) -> Show:
    """Builds the show from a header-free show.json object and the other documents.

    A tree of the current format (`is_current`) must store its cast; a migrated older tree may
    leave it to the Show default.
    """
    order = _set_order(meta.get(SET_ORDER_FIELD, []))
    require_listed_role_folders(reading.source.paths(), order)
    require_known_cast_documents(reading.source.paths())
    kept = {k: v for k, v in meta.items() if k != SET_ORDER_FIELD}
    rest = _show_fields(reading, kept, is_current=is_current)
    stored = [
        (k, reading.cached(set_doc(k), partial(_set, reading, k), also=role_docs(k))) for k in order
    ]
    forms: dict[str, Form] = {}
    for key, current in stored:
        found = reading.cached(forms_doc(key), partial(_forms, reading, key))
        _require_forms_for(key, current.id, found)
        forms.update((f.id, f) for f in found)
    return Show._validated(
        {
            **rest,
            "sheet": _part(reading, SHEET_DOC, Sheet),
            "timeline": _part(reading, TIMELINE_DOC, Timeline),
            "sets": [current for _, current in stored],
            "forms": forms,
            "transitions": _transition_specs(reading, stored),
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


def _migrated_show(source: Source, meta: JsonObject, version: int, revision: str) -> _Migrated:
    """Migrates a tree stored in an older format version to a show, in memory.

    Only the documents the set order names, and the v1 notes, are parsed, as for a current tree.
    The JSON sub-steps run first, then the show is validated with any non-empty v1 ID accepted,
    the model sub-steps run, the result is validated again with CHECKED_CONTEXT, and the
    sub-steps that write documents beside the show run on it and the migrated documents.
    """
    order = _set_order(meta.get(SET_ORDER_FIELD, []))
    require_no_role_documents(source.paths(), version)
    objects: dict[str, JsonValue] = {SHOW_DOC: meta}
    for path in sorted((docs_for_order(order) | {LEGACY_NOTES_DOC}) - {SHOW_DOC}):
        if source.has(path):
            objects[path] = source.load(path)
    report = ReportBuilder(version)
    migrated = migrate_objects(objects, version, report)
    reading = _Reading(object_source(migrated), LEGACY_CONTEXT)
    show = _assemble(reading, load_object(reading.source, SHOW_DOC), revision, is_current=False)
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


def migrate_dump(
    dump: Mapping[str, JsonValue], version: int
) -> tuple[Show, MigrationReport, dict[str, JsonValue]]:
    """Migrates an older interchange dump to a show, its migration report and its side documents.

    Args:
        dump: The `show` object of an interchange document, already strictly parsed.
        version: Format version of the interchange document.

    Returns:
        show: The migrated show; a dump that does not fit raises SchemaError.
        report: What the migration renamed, repaired and dropped.
        beside: Stored path to the parsed JSON of each document beside the show, such as the
            notes document at `/notes/notes.json`; empty when the dump held no notes.

    >>> from e8s.examples import two_set_show
    >>> migrate_dump(two_set_show().to_json(), 1)[2]
    {}
    """
    revision = dump.get(REVISION_FIELD, "")
    source = object_source(split_dump(dump))
    try:
        meta = load_object(source, SHOW_DOC)
        migrated = _migrated_show(
            source, meta, version, revision if isinstance(revision, str) else ""
        )
    except SchemaError as exc:
        raise _reraised_for_show(exc) from exc
    return migrated.show, migrated.report.build(), migrated.beside


class _OlderTreeError(Exception):
    """Carries the show.json of an older format out of a cached decode, so none is cached.

    Attributes:
        meta: The parsed show.json.
        version: Its format version.
    """

    def __init__(self, meta: JsonObject, version: int) -> None:
        super().__init__(version)
        self.meta = meta
        self.version = version


def _current_meta(source: Source) -> JsonObject:
    """Parses show.json; an older format raises _OlderTreeError, so the decode cache keeps none."""
    meta = load_object(source, SHOW_DOC)
    version = version_of(meta, name=SHOW_DOC)
    if version < CURRENT_VERSION:
        raise _OlderTreeError(meta, version)
    return meta


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
    source = raw_source(docs)
    reading = _Reading(source, CHECKED_CONTEXT, cache, digests or {})
    try:
        try:
            meta = reading.cached(SHOW_DOC, partial(_current_meta, source))
        except _OlderTreeError as older:
            return _migrated_show(source, older.meta, older.version, revision).show
        return _assemble(reading, without_header(meta), revision, is_current=True)
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
    source = raw_source(docs)
    try:
        meta = load_object(source, SHOW_DOC)
        migrated = _migrated_show(source, meta, version_of(meta, name=SHOW_DOC), "")
        return migrated.show, migrated.report, migrated.beside
    except SchemaError as exc:
        raise _reraised_for_show(exc) from exc
