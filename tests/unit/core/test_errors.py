#!/usr/bin/env python3

"""
test_errors.py --- tests the ShowPyError family: fields, codes, hierarchy and rendering
"""

import copy
import pickle

import pytest

from showpy.core.errors import (
    ConflictError,
    ContinuityError,
    GeometryError,
    LockedError,
    PathError,
    ShowPyError,
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
    assert ShowPyError("x", hint="y", code="CUSTOM").code == "CUSTOM"


def test_context_defaults_to_empty_dict() -> None:
    assert ShowPyError("x", hint="y").context == {}


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (ZeroLengthError, GeometryError),
        (ContinuityError, PathError),
        (ConflictError, StorageError),
        (PathError, ShowPyError),
    ],
)
def test_hierarchy(child: type[ShowPyError], parent: type[ShowPyError]) -> None:
    assert issubclass(child, parent)


def _every_error_class() -> list[type[ShowPyError]]:
    found: list[type[ShowPyError]] = []
    pending = [ShowPyError]
    while pending:
        current = pending.pop()
        found.append(current)
        pending.extend(current.__subclasses__())
    return found


def _sample(error_class: type[ShowPyError]) -> ShowPyError:
    return error_class("boom", hint="fix it", context={"key": 1}, code="X")


@pytest.mark.defect("UP-9.3-29")
@pytest.mark.xfail(strict=True, reason="UP-9.3-29")
def test_regression_every_error_survives_pickling() -> None:
    """Round-trips every ShowPyError subclass through pickle (audit 9.3)."""
    for error_class in _every_error_class():
        clone = pickle.loads(pickle.dumps(_sample(error_class)))
        assert type(clone) is error_class
        assert (clone.code, clone.message, clone.hint) == ("X", "boom", "fix it")
        assert clone.context == {"key": 1}


@pytest.mark.defect("UP-9.3-30")
@pytest.mark.xfail(strict=True, reason="UP-9.3-30")
def test_regression_every_error_survives_deepcopy() -> None:
    """Deep-copies every ShowPyError subclass (audit 9.3)."""
    for error_class in _every_error_class():
        clone = copy.deepcopy(_sample(error_class))
        assert (clone.code, clone.message, clone.hint) == ("X", "boom", "fix it")
