"""
document_sources.py --- reads parsed JSON documents from a stored tree, refusing wrong shapes

Contains:
    JsonObject, JsonArray: name the parsed JSON object and array shapes documents hold
    Source: reads the show documents of one stored tree as parsed JSON, by stored path
    raw_source(): reads stored bytes, parsing each document strictly when first asked for
    object_source(): reads documents that are already parsed, such as migrated ones
    shape_error(): builds the SchemaError for a document of the wrong JSON shape
    load_object(): parses a document that must hold a JSON object
    load_array(): parses a document that must hold a JSON array
"""

from collections.abc import Callable, Iterable, Mapping
from typing import NamedTuple

from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


class Source(NamedTuple):
    """Reads the show documents of one stored tree as parsed JSON, by stored path.

    Attributes:
        has: Tells whether the tree holds a document.
        load: Returns the parsed JSON of a document the tree holds.
        paths: Lists every path the tree holds.
    """

    has: Callable[[str], bool]
    load: Callable[[str], JsonValue]
    paths: Callable[[], Iterable[str]]


def raw_source(docs: Mapping[str, bytes]) -> Source:
    """Reads stored bytes, parsing each document strictly when it is first asked for."""
    return Source(
        docs.__contains__, lambda path: parse_strict_json(docs[path], document=path), docs.__iter__
    )


def object_source(objects: Mapping[str, JsonValue]) -> Source:
    """Reads documents that are already parsed, such as migrated ones."""
    return Source(objects.__contains__, objects.__getitem__, objects.__iter__)


def _load_json(source: Source, path: str) -> JsonValue:
    if not source.has(path):
        raise StorageError(
            f"document {path} is missing",
            hint="write the whole show with save_show() first",
            context={"path": path},
        )
    return source.load(path)


def shape_error(path: str, name: str, value: object) -> SchemaError:
    """Builds the SchemaError for a document, or part of one, that is not the JSON `name`."""
    return SchemaError(
        f"{path} must hold a JSON {name}, not {type(value).__name__}",
        hint=f"write a JSON {name}, as save_show() does",
        context={"path": path},
    )


def load_object(source: Source, path: str, *, is_optional: bool = False) -> JsonObject:
    """Parses a document that must hold a JSON object; an absent optional one reads as `{}`."""
    if is_optional and not source.has(path):
        return {}
    value = _load_json(source, path)
    if not isinstance(value, dict):
        raise shape_error(path, "object", value)
    return value


def load_array(source: Source, path: str, *, is_optional: bool = False) -> JsonArray:
    """Parses a document that must hold a JSON array; an absent optional one reads as `[]`."""
    if is_optional and not source.has(path):
        return []
    value = _load_json(source, path)
    if not isinstance(value, list):
        raise shape_error(path, "array", value)
    return value
