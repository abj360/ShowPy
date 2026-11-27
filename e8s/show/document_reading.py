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
from collections.abc import Mapping
from typing import NamedTuple

from e8s.cast.models import Cast
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError, field_errors
from e8s.core.ids import TransitionId
from e8s.core.json_types import JsonValue
from e8s.sheet.models import Sheet
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_parts import (
    OlderTreeError,
    PartReading,
    read_current_meta,
    read_forms,
    read_part,
    read_paths,
    read_set,
    set_order,
)
from e8s.show.document_paths import (
    CAST_DOC,
    CAST_FIELD,
    LEGACY_NOTES_DOC,
    REVISION_FIELD,
    SET_ORDER_FIELD,
    SHEET_DOC,
    TIMELINE_DOC,
    docs_for_order,
)
from e8s.show.document_sources import (
    JsonObject,
    Source,
    load_object,
    object_source,
    raw_source,
)
from e8s.show.dump_split import split_dump
from e8s.show.migrate import (
    CHECKED_CONTEXT,
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
from e8s.show.models import Show
from e8s.show.role_documents import (
    require_known_cast_documents,
    require_listed_role_folders,
    require_no_role_documents,
    with_cast,
)
from e8s.time.models import Timeline


def _transition_specs(reading: PartReading, stored: list[tuple[str, Set]]) -> list[TransitionSpec]:
    """Decodes the paths of each consecutive pair, each given its destination set's counts."""
    specs: list[TransitionSpec] = []
    for (key_a, set_a), (key_b, set_b) in itertools.pairwise(stored):
        tid = TransitionId(set_a.id, set_b.id)
        spec = read_paths(reading, (key_a, key_b), tid, set_b.counts)
        if spec is not None:
            specs.append(spec)
    return specs


def _show_fields(
    reading: PartReading, meta: JsonObject, *, is_current: bool
) -> Mapping[str, object]:
    """Returns show.json's fields with the cast; a stored cast document is decoded once."""
    if is_current and reading.source.has(CAST_DOC) and CAST_FIELD not in meta:
        return {**meta, CAST_FIELD: read_part(reading, CAST_DOC, Cast)}
    return with_cast(reading.source, meta, is_required=is_current)


def _assemble(reading: PartReading, meta: JsonObject, revision: str, *, is_current: bool) -> Show:
    """Builds the show from a header-free show.json object and the other documents.

    A tree of the current format (`is_current`) must store its cast; a migrated older tree may
    leave it to the Show default.
    """
    order = set_order(meta.get(SET_ORDER_FIELD, []))
    require_listed_role_folders(reading.source.paths(), order)
    require_known_cast_documents(reading.source.paths())
    kept = {k: v for k, v in meta.items() if k != SET_ORDER_FIELD}
    rest = _show_fields(reading, kept, is_current=is_current)
    stored = [(k, read_set(reading, k)) for k in order]
    forms: dict[str, Form] = {}
    for key, current in stored:
        forms.update((f.id, f) for f in read_forms(reading, key, current.id))
    return Show._validated(
        {
            **rest,
            "sheet": read_part(reading, SHEET_DOC, Sheet),
            "timeline": read_part(reading, TIMELINE_DOC, Timeline),
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
    order = set_order(meta.get(SET_ORDER_FIELD, []))
    require_no_role_documents(source.paths(), version)
    objects: dict[str, JsonValue] = {SHOW_DOC: meta}
    for path in sorted((docs_for_order(order) | {LEGACY_NOTES_DOC}) - {SHOW_DOC}):
        if source.has(path):
            objects[path] = source.load(path)
    report = ReportBuilder(version)
    migrated = migrate_objects(objects, version, report)
    reading = PartReading(object_source(migrated), LEGACY_CONTEXT)
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
    reading = PartReading(source, CHECKED_CONTEXT, cache, digests or {})
    try:
        try:
            meta = read_current_meta(reading)
        except OlderTreeError as older:
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
