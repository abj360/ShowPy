"""
interchange.py --- JSON documents in and out of a show; re-exports the CSV positions import

Contains:
    SCHEMA_TAG: names the version 1 interchange format, `showpy.show.v1`, which import still reads
    FONTS_KEY: names the document key that holds the embedded fonts
    MAX_IMPORT_FONTS: bounds how many fonts one document may carry
    POSITION_FIELDS: re-exports the position fields a CSV column can map to
    export_json(): dumps a show to a format-tagged JSON-safe document, embedding the fonts its
        text forms use
    import_json(): loads a show from a document of any readable format version, migrating it;
        refuses unknown top-level keys, keeps `x-` keys
    import_fonts(): registers the fonts embedded in a document, checking each against its hash
    import_positions(): re-exports the CSV positions import
"""

import base64
import math
from collections.abc import Iterable, Mapping
from typing import Any, Never

from showpy.choreo.form_text import text_recipe
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import (
    ROOT_LOC,
    FontError,
    SchemaError,
    UnknownRefError,
)
from showpy.core.strict_json import strict_json_copy
from showpy.core.util import asset_uri, close_matches
from showpy.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from showpy.show.document_reading import decode_show_dump
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
from showpy.show.models import Show

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
TEXT_KIND = "text"
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
KNOWN_KEYS = frozenset({SCHEMA_KEY, FORMAT_KEY, VERSION_KEY, SHOW_KEY, FONTS_KEY})


# Any: the exported document holds arbitrarily nested JSON values.
def export_json(
    show: Show,
    include: str | Iterable[str] | None = None,
    *,
    font_registry: FontRegistry | None = None,
    extensions: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Dumps a show to a tagged JSON-safe document, embedding the fonts its text forms use.

    Args:
        show: Show to export.
        include: Top-level show fields to keep, or one field name; None keeps all. The ID is
            always kept.
        font_registry: Registry holding the fonts of the text forms; None means the bundled fonts.
        extensions: Host-owned top-level keys to write back unchanged. Every key must start with
            `x-` and every value must be strict JSON; anything else raises SchemaError.

    Returns:
        document: `{"format": "showpy.show", "version": 2, "show": {...}}`, plus `"fonts"`
            mapping each font URI to its base64 bytes, sorted by URI, when an exported form is
            a text form, plus the extensions. A font the registry lacks raises MissingFontError.

    >>> from showpy.examples import two_set_show
    >>> sorted(export_json(two_set_show(), include=["title"])["show"])
    ['id', 'title']
    """
    dumped = show.model_dump(mode="json")
    if include is not None:
        names = [include] if isinstance(include, str) else include
        keep = {ALWAYS_EXPORTED, *names}
        for name in sorted(keep - set(dumped)):
            close = close_matches(name, dumped)
            raise UnknownRefError(
                f"show has no field {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(dumped)}",
                context={"field": name},
            )
        dumped = {key: value for key, value in dumped.items() if key in keep}
    document: dict[str, Any] = {**format_header(), SHOW_KEY: dumped}
    registry = bundled_fonts() if font_registry is None else font_registry
    fonts = _embedded_fonts(show, dumped, registry)
    if fonts:
        document[FONTS_KEY] = fonts
    return {**document, **_checked_extensions(extensions)}


def _checked_extensions(extensions: Mapping[str, object] | None) -> dict[str, Any]:
    """Returns the extension keys as strict JSON, refusing a key without the `x-` prefix."""
    if not extensions:
        return {}
    for key in extensions:
        if not isinstance(key, str) or not key.startswith(EXTENSION_PREFIX):
            raise SchemaError(
                f"extension key {key!r} does not start with {EXTENSION_PREFIX!r}",
                hint=f"name each extension key {EXTENSION_PREFIX}<name>",
                context={"key": key},
            )
    copied = strict_json_copy(dict(extensions), document="extensions")
    return copied if isinstance(copied, dict) else {}


def _embedded_fonts(
    show: Show, dumped: Mapping[str, Any], registry: FontRegistry
) -> dict[str, str]:
    if FORMS_FIELD not in dumped:
        return {}
    uris = {
        uri
        for form in show.forms.values()
        if form.kind == TEXT_KIND
        for uri in text_recipe(form.geometry).fonts
    }
    return {uri: base64.b64encode(registry.source(uri)).decode("ascii") for uri in sorted(uris)}


# Any: an imported document holds arbitrarily nested JSON values.
def import_json(d: Mapping[str, Any]) -> Show:
    """Loads a show from a document of any readable format version, migrating older ones.

    Version 1 documents carry `"schema": "showpy.show.v1"`; later ones carry `"format"` and
    `"version"`. The show is checked as strict JSON first, as stored documents are.

    Args:
        d: A document from `export_json`, of this or an earlier release.

    Returns:
        show: The show in the current format. A wrong or missing tag raises SchemaError, a
            newer version VersionError, and a show that is not strict JSON or does not fit
            SchemaError. A top-level key other than the tag, header, show and fonts raises
            SchemaError naming it, unless it starts with `x-`; the show does not keep x- keys,
            so a host reads them from the document and passes them to `export_json`.

    >>> from showpy.examples import two_set_show
    >>> import_json(export_json(two_set_show())).id
    'show-1'
    >>> v1 = {"schema": "showpy.show.v1", "show": two_set_show().model_dump(mode="json")}
    >>> import_json(v1) == two_set_show()
    True
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
    try:
        if version < CURRENT_VERSION:
            return decode_show_dump(show, version)
        return Show.model_validate(show, context=CHECKED_CONTEXT)
    except SchemaError as error:
        friendly = _first_field_error(error)
        if friendly is error:
            raise
        raise friendly from error


def _refuse_unknown_keys(d: Mapping[str, object]) -> None:
    """Raises SchemaError naming the first top-level key that is neither known nor `x-`."""
    for key in d:
        if key in KNOWN_KEYS or (isinstance(key, str) and key.startswith(EXTENSION_PREFIX)):
            continue
        close = close_matches(str(key), sorted(KNOWN_KEYS))
        raise SchemaError(
            f"document has an unknown top-level key {key!r}",
            hint=(
                f"did you mean {', '.join(close)}? "
                if close
                else f"use {', '.join(sorted(KNOWN_KEYS))}; "
            )
            + f"host keys must start with {EXTENSION_PREFIX!r}",
            context={"key": str(key)},
        )


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
