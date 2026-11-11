#!/usr/bin/env python3

"""
test_errors.py --- tests the StageError family: fields, codes, hierarchy and rendering
"""

import pytest

from stage.core.errors import (
    ConflictError,
    ContinuityError,
    GeometryError,
    LockedError,
    PathError,
    StageError,
    StorageError,
    ZeroLengthError,
)


def test_error_carries_code_message_context_and_hint() -> None:
    error = LockedError("T4 is pinned", hint="pass force=True", context={"entity": "T4"})
    assert error.code == "LOCKED"
    assert error.message == "T4 is pinned"
    assert error.context == {"entity": "T4"}
    assert error.hint == "pass force=True"


def test_error_string_includes_code_and_hint() -> None:
    error = GeometryError("radius is zero", hint="use a radius above 0")
    assert str(error) == "GEOMETRY: radius is zero (hint: use a radius above 0)"


def test_custom_code_overrides_default() -> None:
    assert StageError("x", hint="y", code="CUSTOM").code == "CUSTOM"


def test_context_defaults_to_empty_dict() -> None:
    assert StageError("x", hint="y").context == {}


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (ZeroLengthError, GeometryError),
        (ContinuityError, PathError),
        (ConflictError, StorageError),
        (PathError, StageError),
    ],
)
def test_hierarchy(child: type[StageError], parent: type[StageError]) -> None:
    assert issubclass(child, parent)
