"""
interchange.py --- JSON documents in and out of a show; re-exports the CSV positions import

Contains:
    SCHEMA_TAG: names the version 1 interchange format, `showpy.show.v1`, which import still reads
    FONTS_KEY: names the document key that holds the embedded fonts
    MAX_IMPORT_FONTS: bounds how many fonts one document may carry
    POSITION_FIELDS: re-exports the position fields a CSV column can map to
    export_json(): dumps a show to a format-tagged JSON-safe document, embedding the fonts its
        text forms use and writing the show's extensions as top-level `x-` keys
    import_json(): loads a show from a document of any readable format version, migrating it;
        refuses unknown top-level keys, keeps `x-` keys in `Show.extensions`
    migrate_json(): loads a show as import_json() does, with the migration report and the notes
        of a version 1 document
    import_fonts(): registers the fonts embedded in a document, checking each against its hash
    import_positions(): re-exports the CSV positions import
"""

import base64
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any, Never, cast

from e8s.choreo.form_models import TextRecipe
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    ROOT_LOC,
    FontError,
    SchemaError,
    UnknownRefError,
    bounded_repr,
)
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import strict_json_copy
from e8s.core.util import asset_uri, close_matches
from e8s.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from e8s.show.document_reading import migrate_dump
from e8s.show.interchange_positions import POSITION_FIELDS as POSITION_FIELDS
from e8s.show.interchange_positions import import_positions as import_positions
from e8s.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    FIRST_VERSION,
    FORMAT,
    FORMAT_KEY,
    VERSION_KEY,
    MigrationReport,
    format_header,
    version_of,
)
from e8s.show.migrations.v1_to_v2_notes import NOTES_DOC
from e8s.show.models import Show, require_extension_size

SCHEMA_TAG = "showpy.show.v1"
SCHEMA_KEY = "schema"
SHOW_KEY = "show"
DOCUMENT_NAME = "document"
DOCUMENT_HINT = (
    f'documents are JSON objects: {{"{FORMAT_KEY}": "{FORMAT}", "{VERSION_KEY}": {CURRENT_VERSION},'
    f' "{SHOW_KEY}": {{...}}}}'
)
FONTS_KEY = "fonts"
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
KNOWN_KEYS = frozenset({SCHEMA_KEY, FORMAT_KEY, VERSION_KEY, SHOW_KEY, FONTS_KEY})


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(
    show: Show,
    include: str | Iterable[str] | None = None,
    *,
    font_registry: FontRegistry | None = None,
) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document, embedding the fonts its text forms use.

    Args:
        show: Show to export.
        include: Top-level show fields to keep, or one field name; None keeps all. The ID is
            always kept. The extensions are written only when `"extensions"` is kept.
        font_registry: Registry holding the fonts of the text forms; None means the bundled fonts.

    Returns:
        document: `{"format": "e8.show", "version": 2, "show": {...}}`, plus `"fonts"`
            mapping each font URI to its base64 bytes, sorted by URI, when an exported form is
            a text form. A font the registry lacks raises MissingFontError. Each of the show's
            extensions is a top-level key of the document, as imported.

    >>> from e8s.examples import two_set_show
    >>> sorted(export_json(two_set_show(), include=["title"])["show"])
    ['id', 'title']
    """
    dumped = _sorted_by_id(show.to_json())
    # Show.extensions is a JSON object, so its JSON dump is a dict.
    extensions = cast("dict[str, JsonValue]", dumped.pop(EXTENSIONS_FIELD, {}))
    if include is not None:
        names = [include] if isinstance(include, str) else include
        keep = {ALWAYS_EXPORTED, *names}
        for name in sorted(keep - set(dumped) - {EXTENSIONS_FIELD}):
            close = close_matches(name, [*dumped, EXTENSIONS_FIELD])
            raise UnknownRefError(
                f"show has no field {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(dumped)}",
                context={"field": name},
            )
        dumped = {key: value for key, value in dumped.items() if key in keep}
        extensions = extensions if EXTENSIONS_FIELD in keep else {}
    document: dict[str, Any] = {**format_header(), SHOW_KEY: dumped}
    registry = bundled_fonts() if font_registry is None else font_registry
    fonts = _embedded_fonts(show, dumped, registry)
    if fonts:
        document[FONTS_KEY] = fonts
    document.update(extensions)
    return document


def _by_key(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return dict(sorted(mapping.items()))


def _sorted_by_id(dumped: dict[str, Any]) -> dict[str, Any]:
    """Returns a show dump with every ID-keyed mapping in plain string order of its keys.

    Field order, list order and values stay as dumped, so equal shows dump equal bytes.
    """
    result = {**dumped}
    if FORMS_FIELD in result:
        result[FORMS_FIELD] = _by_key(result[FORMS_FIELD])
    if "dependencies" in result:
        result["dependencies"] = _by_key(result["dependencies"])
    if "cast" in result:
        result["cast"] = _sorted_cast(result["cast"])
    if "transitions" in result:
        result["transitions"] = [
            {**spec, "rows": _by_key(spec["rows"]), "routes": _by_key(spec["routes"])}
            for spec in result["transitions"]
        ]
    return result


def _sorted_cast(cast: dict[str, Any]) -> dict[str, Any]:
    groups = {
        key: {**group, "limits": _by_key(group["limits"])}
        for key, group in _by_key(cast["groups"]).items()
    }
    scheme = {**cast["label_scheme"], "prefixes": _by_key(cast["label_scheme"]["prefixes"])}
    return {
        **cast,
        "groups": groups,
        "selections": _by_key(cast["selections"]),
        "label_scheme": scheme,
    }


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
    `"version"`. The show is checked as strict JSON first, as stored documents are. Data that
    the current format no longer has, such as the notes of a version 1 show, is not part of the
    show; migrate_json() returns it with the migration report.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        show: The show in the current format. A wrong or missing tag raises SchemaError, a
            newer version VersionError, and a show that is not strict JSON or does not fit
            SchemaError. A top-level key other than the tag, header, show and fonts raises
            SchemaError naming it, unless it starts with `x-`; those are kept in `Show.extensions`,
            which must be strict JSON and at most `MAX_EXTENSION_BYTES` bytes.

    >>> from e8s.examples import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    >>> v1 = {"schema": "showpy.show.v1", "show": two_set_show().to_json()}
    >>> import_json(v1) == two_set_show()
    True
    >>> import_json({**export_json(two_set_show()), "x-host": {"n": 1}}).extensions["x-host"]
    ReadOnlyMap({'n': 1})
    """
    return migrate_json(d)[0]


# Any: an imported document holds arbitrarily nested JSON values.
def migrate_json(
    d: Mapping[str, Any],
) -> tuple[Show, MigrationReport, dict[str, JsonValue] | None]:
    """Loads a show as import_json() does, and returns what migrating an older document changed.

    Use it to keep what a version 1 document holds that the current format does not: the report
    lists each renamed ID, each repair and each dropped stored field with its stored value, and
    the notes of a version 1 show come back as a notes document of plain JSON.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        show: The show in the current format; errors are those of import_json().
        report: What the migration renamed, repaired and dropped; empty for a current document.
        notes: The version 1 show's notes as a notes document (`{"format": "e8.notes", ...}`),
            or None when the document is current or held no notes.

    >>> from e8s.examples import two_set_show
    >>> v1 = {"schema": "showpy.show.v1", "show": two_set_show().to_json()}
    >>> show, report, notes = migrate_json(v1)
    >>> show == two_set_show(), report.from_version, report.dropped, notes
    (True, 1, (), None)
    """
    version, show = _show_of(d)
    try:
        if version < CURRENT_VERSION:
            migrated, report, beside = migrate_dump(show, version)
            notes = beside.get(NOTES_DOC)
            return migrated, report, notes if isinstance(notes, dict) else None
        current = Show._validated(show, context=CHECKED_CONTEXT)
        return current, MigrationReport(from_version=CURRENT_VERSION), None
    except SchemaError as error:
        friendly = _first_field_error(error)
        if friendly is error:
            raise
        raise friendly from error


def _show_of(d: Mapping[str, object]) -> tuple[int, dict[str, JsonValue]]:
    """Checks a document's shape and returns its version and its show, extensions included."""
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
    return version, show


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

    >>> from e8s.examples import two_set_show
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
