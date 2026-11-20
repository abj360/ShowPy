"""
migrate.py --- reads a stored show's format version and runs the steps that bring it up to date

A stored tree is versioned by its show.json alone: from version 2 on, `"format"` and `"version"`
are its first two keys, and a show.json without them is version 1. Each version has one step to
the next, made of named sub-steps that run in a fixed order: first on the parsed JSON documents
keyed by stored path, then on the show validated with any non-empty v1 ID accepted. Loading
migrates in memory; nothing stored is rewritten until the next save.

Contains:
    FORMAT: names the stored show format, `showpy.show`
    CURRENT_VERSION: the format version this release writes
    FIRST_VERSION: the format version of every show stored before the header existed
    FORMAT_KEY: names the key that holds the format in show.json and interchange documents
    VERSION_KEY: names the key that holds the version in show.json and interchange documents
    SHOW_DOC: names the document that holds show metadata, the set order and the format header
    LEGACY_CONTEXT: validation context that lets ID fields take any non-empty v1 ID
    CHECKED_CONTEXT: validation context of the check that follows the model sub-steps
    JSON_STEPS: maps each stored version to the JSON sub-steps that lift it one version
    MODEL_STEPS: maps each stored version to the model sub-steps that lift it one version
    MigrationReport: lists what a migration renamed and repaired
    ReportBuilder: collects the renames and repairs of one migration run
    read_version(): reads the format version of a stored document set from its show.json
    require_current_version(): refuses a parsed show.json of another format version
    require_current_show_doc(): refuses show.json bytes that are not in the current format
    version_of(): reads the format version from a parsed show.json or interchange document
    format_header(): returns the header fields written first in show.json
    without_header(): returns a parsed document without its header fields
    migrate_objects(): runs the JSON sub-steps from a stored version up to the current one
    migrate_model(): runs the model sub-steps on a show validated with legacy IDs accepted
"""

from collections.abc import Mapping
from typing import Annotated, Final

from pydantic import Field

from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, StorageError, VersionError
from showpy.core.ids import LEGACY_IDS_KEY
from showpy.core.json_types import JsonValue
from showpy.core.readonly import ReadOnly, ReadOnlyMap
from showpy.core.strict_json import parse_strict_json
from showpy.core.types import FrozenModel
from showpy.show.migrations import (
    V1_TO_V2_JSON_STEPS,
    V1_TO_V2_MODEL_STEPS,
    JsonStep,
    ModelStep,
)
from showpy.show.migrations.v1_to_v2_ids import KEEPS_LEGACY_IDS
from showpy.show.models import Show

FORMAT: Final = "showpy.show"
CURRENT_VERSION: Final = 2
FIRST_VERSION: Final = 1
FORMAT_KEY: Final = "format"
VERSION_KEY: Final = "version"
SHOW_DOC: Final = "/show/show.json"
LEGACY_CONTEXT: Final[Mapping[str, bool]] = ReadOnlyMap({LEGACY_IDS_KEY: True})
CHECKED_CONTEXT: Final[Mapping[str, bool] | None] = LEGACY_CONTEXT if KEEPS_LEGACY_IDS else None
JSON_STEPS: Final[Mapping[int, tuple[JsonStep, ...]]] = ReadOnlyMap(
    {FIRST_VERSION: V1_TO_V2_JSON_STEPS}
)
MODEL_STEPS: Final[Mapping[int, tuple[ModelStep, ...]]] = ReadOnlyMap(
    {FIRST_VERSION: V1_TO_V2_MODEL_STEPS}
)


class MigrationReport(FrozenModel):
    """Lists what one migration renamed and repaired, so a host can update what it keeps outside.

    Attributes:
        from_version: Format version the documents were stored in.
        renamed: Per ID kind (`spot`, `set`, `form`, ...), each (old, new) rename in show order.
        repairs: Each repair a sub-step made, in the order it was made.

    >>> MigrationReport(from_version=1).renamed
    ReadOnlyMap({})
    """

    from_version: Annotated[int, Field(ge=FIRST_VERSION)]
    renamed: Annotated[Mapping[str, tuple[tuple[str, str], ...]], ReadOnly()] = Field(
        default={}, validate_default=True
    )
    repairs: tuple[str, ...] = ()


class ReportBuilder:
    """Collects the renames and repairs of one migration run, then builds its report.

    Attributes:
        from_version: Format version the documents were stored in.
    """

    def __init__(self, from_version: int) -> None:
        self.from_version = from_version
        self._renamed: dict[str, list[tuple[str, str]]] = {}
        self._repairs: list[str] = []

    def rename(self, kind: str, old: str, new: str) -> None:
        """Records that an ID of one kind was renamed; call it in show order."""
        self._renamed.setdefault(kind, []).append((old, new))

    def repair(self, text: str) -> None:
        """Records one repair, worded for the designer."""
        self._repairs.append(text)

    def build(self) -> MigrationReport:
        """Returns the report of everything recorded so far."""
        return MigrationReport(
            from_version=self.from_version,
            renamed={kind: tuple(pairs) for kind, pairs in self._renamed.items()},
            repairs=tuple(self._repairs),
        )


def _format_error(document: str, message: str, value: object) -> SchemaError:
    return SchemaError(
        f"{document} {message}",
        hint=f'write "{FORMAT_KEY}": "{FORMAT}" and an integer "{VERSION_KEY}" from 2 to'
        f" {CURRENT_VERSION}, or neither for a version 1 document",
        context={"path": document, "value": repr(value)},
        code=ErrorCode.UNKNOWN_FORMAT,
    )


def version_of(document: Mapping[str, object], *, name: str) -> int:
    """Reads the format version from a parsed show.json or interchange document.

    Args:
        document: Parsed JSON object; a version 1 document has neither header key.
        name: Path or name of the document, used in errors.

    Returns:
        version: 1 without a header, else the stored version. A newer version than
            CURRENT_VERSION raises VersionError naming both; an unknown format, half a header
            or a version that is not an integer from 2 up raises SchemaError UNKNOWN_FORMAT.

    >>> version_of({"id": "show-1"}, name="/show/show.json")
    1
    >>> version_of(format_header(), name="/show/show.json") == CURRENT_VERSION
    True
    """
    if FORMAT_KEY not in document and VERSION_KEY not in document:
        return FIRST_VERSION
    stored_format = document.get(FORMAT_KEY)
    if stored_format != FORMAT:
        raise _format_error(name, f"names format {stored_format!r}, not {FORMAT!r}", stored_format)
    version = document.get(VERSION_KEY)
    if not isinstance(version, int) or isinstance(version, bool) or version <= FIRST_VERSION:
        raise _format_error(name, f"has format version {version!r}", version)
    if version > CURRENT_VERSION:
        raise VersionError(
            f"{name} is format version {version}; this release reads versions up to"
            f" {CURRENT_VERSION}",
            hint=f"upgrade showpy to a release that reads format version {version}",
            context={"path": name, "version": version, "newest": CURRENT_VERSION},
        )
    return version


def read_version(docs: Mapping[str, bytes]) -> int:
    """Reads the format version of a stored document set from its show.json.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.

    Returns:
        version: 1 when show.json has no header, else its version. A missing show.json raises
            StorageError; JSON that is not strict or not an object raises SchemaError; a
            version newer than CURRENT_VERSION raises VersionError.

    >>> read_version({SHOW_DOC: b'{"format": "showpy.show", "version": 2, "id": "show-1"}'})
    2
    >>> read_version({SHOW_DOC: b'{"id": "show-1"}'})
    1
    """
    raw = docs.get(SHOW_DOC)
    if raw is None:
        raise StorageError(
            f"document {SHOW_DOC} is missing",
            hint="write the whole show with save_show() first",
            context={"path": SHOW_DOC},
        )
    return version_of(_show_object(raw), name=SHOW_DOC)


def _show_object(raw: bytes) -> dict[str, JsonValue]:
    """Parses show.json bytes strictly, refusing anything but a JSON object."""
    document = parse_strict_json(raw, document=SHOW_DOC)
    if not isinstance(document, dict):
        raise SchemaError(
            f"{SHOW_DOC} must hold a JSON object, not {type(document).__name__}",
            hint="write a JSON object, as save_show() does",
            context={"path": SHOW_DOC},
        )
    return document


def require_current_version(document: Mapping[str, object], *, name: str) -> None:
    """Refuses a parsed show.json of another format version, so a write never stores one.

    A tree is read in the version of its show.json, so writing an older show.json beside
    current documents would make the whole tree read as old.

    Args:
        document: Parsed show.json object about to be written.
        name: Path or name of the document, used in errors.

    >>> require_current_version(format_header(), name=SHOW_DOC)
    >>> try:
    ...     require_current_version({"id": "show-1"}, name=SHOW_DOC)
    ... except SchemaError as exc:
    ...     print(exc.code)
    OLD_FORMAT
    """
    version = version_of(document, name=name)
    if version != CURRENT_VERSION:
        raise SchemaError(
            f"{name} is format version {version}; writes store version {CURRENT_VERSION}",
            hint=f'read {SHOW_DOC} first and keep its "{FORMAT_KEY}" and "{VERSION_KEY}" keys, or'
            " save the show with save_show()",
            context={"path": name, "version": version, "current": CURRENT_VERSION},
            code=ErrorCode.OLD_FORMAT,
        )


def require_current_show_doc(raw: bytes) -> None:
    """Refuses show.json bytes that are not strict JSON objects in the current format version.

    >>> require_current_show_doc(b'{"format": "showpy.show", "version": 2, "id": "show-1"}')
    """
    require_current_version(_show_object(raw), name=SHOW_DOC)


def format_header() -> dict[str, JsonValue]:
    """Returns the header fields written first in show.json and in interchange documents.

    >>> format_header()
    {'format': 'showpy.show', 'version': 2}
    """
    return {FORMAT_KEY: FORMAT, VERSION_KEY: CURRENT_VERSION}


def without_header(document: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Returns a copy of a parsed document without its header fields.

    >>> without_header({"format": "showpy.show", "version": 2, "id": "show-1"})
    {'id': 'show-1'}
    """
    return {key: value for key, value in document.items() if key not in (FORMAT_KEY, VERSION_KEY)}


def migrate_objects(
    objects: dict[str, JsonValue], version: int, report: ReportBuilder
) -> dict[str, JsonValue]:
    """Runs the JSON sub-steps that bring parsed documents from `version` to CURRENT_VERSION.

    Args:
        objects: Stored path to parsed document, without a format header; never mutated.
        version: Format version the documents were stored in.
        report: Collects what the sub-steps rename and repair.

    Returns:
        migrated: The documents in the current version's shape, still without a header.

    >>> migrate_objects({"/show/show.json": {"id": "x"}}, 1, ReportBuilder(1))
    {'/show/show.json': {'id': 'x'}}
    """
    for stored_version in range(version, CURRENT_VERSION):
        for step in JSON_STEPS[stored_version]:
            objects = step(objects, report)
    return objects


def migrate_model(show: Show, version: int, report: ReportBuilder) -> Show:
    """Runs the model sub-steps on a show that was validated with LEGACY_CONTEXT.

    Args:
        show: Show assembled from migrated documents, any non-empty v1 ID accepted.
        version: Format version the documents were stored in.
        report: Collects what the sub-steps rename.

    Returns:
        show: The show the sub-steps return; validate it again with CHECKED_CONTEXT.

    >>> from showpy.examples import two_set_show
    >>> migrate_model(two_set_show(), 1, ReportBuilder(1)) == two_set_show()
    True
    """
    for stored_version in range(version, CURRENT_VERSION):
        for step in MODEL_STEPS[stored_version]:
            show = step(show, report)
    return show
