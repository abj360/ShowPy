"""
interchange.py --- JSON documents in and out of a show; re-exports the CSV positions import

Contains:
    SCHEMA_TAG: names the version 1 interchange format, `showpy.show.v1`, which import still reads
    FONTS_KEY: names the document key that holds the embedded fonts
    NOTES_KEY: names the document key that holds the notes document beside the show
    MAX_IMPORT_FONTS: bounds how many fonts one document may carry
    POSITION_FIELDS: re-exports the position fields a CSV column can map to
    export_json(): dumps a show to a format-tagged JSON-safe document, embedding the fonts its
        text forms use, writing the show's extensions as top-level `x-` keys and, when given, the
        notes document under `notes`
    import_json(): loads a show from a document of any readable format version, migrating it;
        refuses unknown top-level keys, keeps `x-` keys in `Show.extensions`
    import_fonts(): registers the fonts embedded in a document, checking each against its hash
    import_notes(): returns the notes document of a document, migrating a v1 show's notes
    import_positions(): re-exports the CSV positions import
"""

import base64
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any, Never

from showpy.choreo.form_models import TextRecipe
from showpy.core.deprecation import leaving_call
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import (
    ROOT_LOC,
    FontError,
    SchemaError,
    UnknownRefError,
    bounded_repr,
)
from showpy.core.json_types import JsonValue
from showpy.core.strict_json import strict_json_copy
from showpy.core.util import asset_uri, close_matches
from showpy.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from showpy.notes.models import NotesDocument, require_notes_of
from showpy.show.document_reading import decode_show_dump, migrate_dump
from showpy.show.interchange_positions import POSITION_FIELDS as POSITION_FIELDS
from showpy.show.interchange_positions import import_positions as import_positions
from showpy.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    FIRST_VERSION,
    FORMAT,
    FORMAT_KEY,
    VERSION_KEY,
    format_header,
    version_of,
)
from showpy.show.migrations.v1_to_v2_notes import NOTES_DOC
from showpy.show.models import Show, require_extension_size

SCHEMA_TAG = "showpy.show.v1"
SCHEMA_KEY = "schema"
SHOW_KEY = "show"
DOCUMENT_NAME = "document"
DOCUMENT_HINT = (
    f'documents are JSON objects: {{"{FORMAT_KEY}": "{FORMAT}", "{VERSION_KEY}": {CURRENT_VERSION},'
    f' "{SHOW_KEY}": {{...}}}}'
)
FONTS_KEY = "fonts"
NOTES_KEY = "notes"
FORMS_FIELD = "forms"
# A document with more fonts than the five bundled plus a generous allowance of user fonts is
# hostile; each entry is also bounded by MAX_FONT_BYTES.
MAX_IMPORT_FONTS = 64
BASE64_BLOCK_CHARS = 4
BASE64_BYTES_PER_BLOCK = 3
BASE64_SLACK_CHARS = 4
MAX_ENCODED_FONT_CHARS = (
    BASE64_BLOCK_CHARS * math.ceil(MAX_FONT_BYTES / BASE64_BYTES_PER_BLOCK) + BASE64_SLACK_CHARS
)
ALWAYS_EXPORTED = "id"
EXTENSION_PREFIX = "x-"
EXTENSIONS_FIELD = "extensions"
EXTENSIONS_KEY = "extensions"
KNOWN_KEYS = frozenset({SCHEMA_KEY, FORMAT_KEY, VERSION_KEY, SHOW_KEY, FONTS_KEY, NOTES_KEY})


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(
    show: Show,
    include: str | Iterable[str] | None = None,
    *,
    font_registry: FontRegistry | None = None,
    notes: NotesDocument | None = None,
) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document, embedding the fonts its text forms use.

    Args:
        show: Show to export.
        include: Top-level show fields to keep, or one field name; None keeps all. The ID is
            always kept. The extensions are written only when `"extensions"` is kept.
        font_registry: Registry holding the fonts of the text forms; None means the bundled fonts.
        notes: The show's notes document, written beside the show; None writes none.

    Returns:
        document: `{"format": "showpy.show", "version": 2, "show": {...}}`, plus `"fonts"`
            mapping each font URI to its base64 bytes, sorted by URI, when an exported form is
            a text form. A font the registry lacks raises MissingFontError. Each of the show's
            extensions is a top-level key of the document, as imported. With `notes`, the key
            `"notes"` holds the notes document, header first; one of another show raises
            SchemaError.

    >>> from showpy.examples import two_set_show
    >>> sorted(export_json(two_set_show(), include=["title"])["show"])
    ['id', 'title']
    """
    dumped = show.model_dump(mode="json")
    extensions = dumped.pop(EXTENSIONS_FIELD, {})
    if include is not None:
        names = [include] if isinstance(include, str) else include
        keep = {ALWAYS_EXPORTED, *names}
        for name in sorted(keep - set(dumped) - {EXTENSIONS_FIELD}):
            close = close_matches(name, dumped)
            raise UnknownRefError(
                f"show has no field {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(dumped)}",
                context={"field": name},
            )
        dumped = {key: value for key, value in dumped.items() if key in keep}
        extensions = extensions if EXTENSIONS_FIELD in keep else {}
    if notes is not None:
        require_notes_of(notes, show.id)
    document: dict[str, Any] = {**format_header(), SHOW_KEY: dumped}
    if notes is not None:
        document[NOTES_KEY] = notes.model_dump(mode="json")
    registry = bundled_fonts() if font_registry is None else font_registry
    fonts = _embedded_fonts(show, dumped, registry)
    if fonts:
        document[FONTS_KEY] = fonts
    document.update(extensions)
    return document


def _embedded_fonts(
    show: Show, dumped: Mapping[str, Any], registry: FontRegistry
) -> dict[str, str]:
    if FORMS_FIELD not in dumped:
        return {}
    uris = {
        uri
        for form in show.forms.values()
        if isinstance(form.recipe, TextRecipe)
        for uri in form.recipe.fonts
    }
    return {uri: base64.b64encode(registry.source(uri)).decode("ascii") for uri in sorted(uris)}


# Any: an imported document holds arbitrarily nested JSON values.
def import_json(d: Mapping[str, Any]) -> Show:
    """Loads a show from a document of any readable format version, migrating older ones.

    Version 1 documents carry `"schema": "showpy.show.v1"`; later ones carry `"format"` and
    `"version"`. The show is checked as strict JSON first, as stored documents are. The value
    under the top-level `notes` key is not read here: import_notes() parses and checks it, and
    returns the notes of a version 1 show.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        show: The show in the current format. A wrong or missing tag raises SchemaError, a
            newer version VersionError, and a show that is not strict JSON or does not fit
            SchemaError. A top-level key other than the tag, header, show, fonts and notes raises
            SchemaError naming it, unless it starts with `x-`; those are kept in `Show.extensions`,
            which must be strict JSON and at most `MAX_EXTENSION_BYTES` bytes.

    >>> from showpy.examples import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    >>> v1 = {"schema": "showpy.show.v1", "show": two_set_show().model_dump(mode="json")}
    >>> import_json(v1) == two_set_show()
    True
    >>> import_json({**export_json(two_set_show()), "x-host": {"n": 1}}).extensions["x-host"]
    ReadOnlyMap({'n': 1})
    """
    if not isinstance(d, Mapping):
        raise SchemaError(
            f"document is a {type(d).__name__}, not an object",
            hint=DOCUMENT_HINT,
            context={"type": type(d).__name__},
        )
    version = _document_version(d)
    _refuse_unknown_keys(d)
    body = d.get(SHOW_KEY)
    show = strict_json_copy(dict(body), document=SHOW_KEY) if isinstance(body, Mapping) else None
    if not isinstance(show, dict):
        raise SchemaError("document has no show object", hint=f"put the show under {SHOW_KEY!r}")
    if EXTENSIONS_FIELD in show:
        raise SchemaError(
            f"show object holds {EXTENSIONS_FIELD!r}, which documents keep at the top level",
            hint=f"write host keys at the top level of the document as {EXTENSION_PREFIX}<name>",
            context={"field": EXTENSIONS_FIELD},
        )
    hosted = {key: value for key, value in d.items() if _is_extension_key(key)}
    _require_cap_before_copy(hosted)
    extensions = strict_json_copy(hosted, document=EXTENSIONS_KEY)
    if extensions:
        show[EXTENSIONS_FIELD] = extensions
    try:
        if version < CURRENT_VERSION:
            return decode_show_dump(show, version)
        return Show.model_validate(show, context=CHECKED_CONTEXT)
    except SchemaError as error:
        friendly = _first_field_error(error)
        if friendly is error:
            raise
        raise friendly from error


def _require_cap_before_copy(hosted: Mapping[str, object]) -> None:
    """Refuses oversized extension values before they are copied; non-JSON values pass on."""
    try:
        size = len(json.dumps(hosted, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return
    require_extension_size(size)


def _refuse_unknown_keys(d: Mapping[str, object]) -> None:
    """Raises SchemaError naming the first top-level key that is neither known nor `x-`."""
    for key in d:
        if key in KNOWN_KEYS or _is_extension_key(key):
            continue
        close = close_matches(str(key), sorted(KNOWN_KEYS))
        raise SchemaError(
            f"document has an unknown top-level key {bounded_repr(key)}",
            hint=(
                f"did you mean {', '.join(close)}? "
                if close
                else f"use {', '.join(sorted(KNOWN_KEYS))}; "
            )
            + f"host keys must start with {EXTENSION_PREFIX!r}",
            context={"key": str(key)},
        )


def _is_extension_key(key: object) -> bool:
    return isinstance(key, str) and key.startswith(EXTENSION_PREFIX)


def _document_version(d: Mapping[str, object]) -> int:
    """Reads the format version of an interchange document from its tag or header."""
    if SCHEMA_KEY not in d:
        return version_of(d, name=DOCUMENT_NAME) if FORMAT_KEY in d else _unknown_tag(None)
    if d[SCHEMA_KEY] != SCHEMA_TAG or FORMAT_KEY in d or VERSION_KEY in d:
        _unknown_tag(d[SCHEMA_KEY])
    return FIRST_VERSION


def _unknown_tag(tag: object) -> Never:
    raise SchemaError(
        f"document schema is {tag!r}, not {SCHEMA_TAG!r} or format {FORMAT!r}",
        hint=f"export with export_json(); {DOCUMENT_HINT}, or version 1 documents"
        f' {{"{SCHEMA_KEY}": "{SCHEMA_TAG}", "{SHOW_KEY}": {{...}}}}',
        context={"schema": repr(tag)},
        code=ErrorCode.UNKNOWN_FORMAT,
    )


def _first_field_error(error: SchemaError) -> SchemaError:
    """Names the first failing show field of a validation error and counts the rest.

    An error without per-field entries already teaches and is returned unchanged.
    """
    errors = error.context.get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return error
    field = errors[0].get("loc")
    return SchemaError(
        f"show field {field} is invalid: {errors[0].get('msg')}",
        hint=f"fix {'the show' if field == ROOT_LOC else field} to match {FORMAT}",
        context={"field": field, "error_count": len(errors)},
    )


def _decode_font(uri: object, encoded: object) -> bytes:
    if not isinstance(uri, str) or not isinstance(encoded, str):
        raise FontError(
            "embedded fonts must map URI strings to base64 strings",
            hint=f'write {{"{FONTS_KEY}": {{"asset:sha256:<hex>": "<base64>"}}}}',
            context={"uri": repr(uri)[:80]},
        )
    if len(encoded) > MAX_ENCODED_FONT_CHARS:
        raise FontError(
            f"embedded font {uri} is {len(encoded)} characters, over {MAX_ENCODED_FONT_CHARS}",
            hint=f"embed fonts of at most {MAX_FONT_BYTES} bytes",
            context={"uri": uri, "chars": len(encoded)},
        )
    try:
        data = base64.b64decode(encoded, validate=True)
    # b64decode raises binascii.Error for bad padding and plain ValueError for non-ASCII text.
    except ValueError as exc:
        raise FontError(
            f"embedded font {uri} is not valid base64",
            hint="embed each font with export_json()",
            context={"uri": uri},
        ) from exc
    if asset_uri(data) != uri:
        raise FontError(
            f"embedded font bytes do not hash to {uri}",
            hint="re-export the document; the font data was changed",
            context={"uri": uri, "actual": asset_uri(data)},
            code=ErrorCode.FONT_HASH_MISMATCH,
        )
    return data


def import_fonts(d: Mapping[str, Any], font_registry: FontRegistry | None = None) -> FontRegistry:
    """Registers the fonts embedded in a document, checking each against its URI.

    Recipes name fonts by URI, so an embedded font whose name is bound to other bytes, such as
    an older release of a bundled font, is registered by URI and the binding is left alone.

    Args:
        d: A document from `export_json`; one without a `fonts` key changes nothing.
        font_registry: Registry to extend; None means the bundled fonts. It is not changed.

    Returns:
        registry: `font_registry` plus every embedded font. A malformed entry, a hash mismatch or
            more than `MAX_IMPORT_FONTS` fonts raises FontError.

    >>> from showpy.examples import two_set_show
    >>> import_fonts(export_json(two_set_show())) is bundled_fonts()
    True
    """
    registry = bundled_fonts() if font_registry is None else font_registry
    if not isinstance(d, Mapping):
        raise FontError(
            f"document is a {type(d).__name__}, not a mapping",
            hint="pass the dict that export_json() returned",
            context={"type": type(d).__name__},
        )
    if FONTS_KEY not in d:
        return registry
    fonts = d[FONTS_KEY]
    if not isinstance(fonts, Mapping) or len(fonts) > MAX_IMPORT_FONTS:
        raise FontError(
            f"{FONTS_KEY!r} must be a mapping of at most {MAX_IMPORT_FONTS} fonts",
            hint=f'write {{"{FONTS_KEY}": {{"asset:sha256:<hex>": "<base64>"}}}}',
            context={"type": type(fonts).__name__},
        )
    for uri, encoded in fonts.items():
        registry, _ = register_font(registry, _decode_font(uri, encoded), if_name_taken="keep")
    return registry


def _show_object(d: Mapping[str, object]) -> dict[str, JsonValue]:
    """Returns a strict JSON copy of a document's show object, refusing a document without one."""
    body = d.get(SHOW_KEY)
    show = strict_json_copy(dict(body), document=SHOW_KEY) if isinstance(body, Mapping) else None
    if not isinstance(show, dict):
        raise SchemaError("document has no show object", hint=f"put the show under {SHOW_KEY!r}")
    return show


def _import_notes(d: Mapping[str, object]) -> NotesDocument:
    """Returns the notes document of an interchange document; see import_notes().

    >>> from showpy.examples import two_set_show
    >>> _import_notes(export_json(two_set_show())).notes
    ()
    """
    if not isinstance(d, Mapping):
        raise SchemaError(
            f"document is a {type(d).__name__}, not an object",
            hint=DOCUMENT_HINT,
            context={"type": type(d).__name__},
        )
    version = _document_version(d)
    _refuse_unknown_keys(d)
    show = _show_object(d)
    if version < CURRENT_VERSION:
        if NOTES_KEY in d:
            raise SchemaError(
                f"a version {version} document holds {NOTES_KEY!r} beside the show",
                hint="version 1 documents keep their notes in the show object",
                context={"version": version},
            )
        migrated, beside = migrate_dump(show, version)
        moved = beside.get(NOTES_DOC)
        if moved is None:
            return NotesDocument(show=migrated.id)
        return NotesDocument.model_validate(moved)
    show_id = show.get("id")
    if not isinstance(show_id, str):
        raise SchemaError("show object has no string id", hint="export with export_json()")
    if NOTES_KEY not in d:
        return NotesDocument(show=show_id)
    notes = NotesDocument.model_validate(strict_json_copy(d[NOTES_KEY], document=NOTES_KEY))
    require_notes_of(notes, show_id)
    return notes


@leaving_call(since="0.4.0", remove="1.0.0")
def import_notes(d: Mapping[str, object]) -> NotesDocument:
    """Returns the notes document of an interchange document, migrating a version 1 show's notes.

    Deprecated: this name leaves the library in 1.0.0.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        notes: The document's `notes`, checked against its show ID; for a version 1 document,
            the show's notes with IDs and anchors renamed as import_json() renames the show; an
            empty notes document when there are none. A document that import_json() refuses,
            or notes that do not fit, raise SchemaError.

    >>> import warnings
    >>> from showpy.examples import two_set_show
    >>> from showpy.notes.models import NotesDocument
    >>> show = two_set_show()
    >>> document = export_json(show, notes=NotesDocument(show=show.id))
    >>> with warnings.catch_warnings(action="ignore"):
    ...     notes = import_notes(document)
    >>> notes.show, notes.notes
    ('show-1', ())
    """
    return _import_notes(d)
