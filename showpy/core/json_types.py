"""
json_types.py --- the JSON data model as a type, shared by canonical encoding and error context

Contains:
    JsonValue: the JSON data model every canonical value and error context value belongs to
"""

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
