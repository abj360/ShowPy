"""
json_types.py --- the JSON data model as a type, shared by canonical encoding and error context

Contains:
    JsonValue: the JSON data model every canonical value and error context value belongs to
    FrozenJsonValue: the read-only form of a JSON value, with tuples and mappings for containers
"""

from collections.abc import Mapping

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type FrozenJsonValue = (
    bool | int | float | str | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue] | None
)
