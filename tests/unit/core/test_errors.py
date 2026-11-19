#!/usr/bin/env python3

"""
test_errors.py --- tests the ShowPyError family: fields, codes, hierarchy and rendering

Contains:
    test_font_errors_carry_their_codes: gives the font errors their codes and parents
    test_*(): one behavior of showpy.core.errors each
    test_regression_every_error_survives_pickling: round-trips every ShowPyError subclass through
        pickle
    test_regression_every_error_survives_deepcopy: deep-copies every ShowPyError subclass
    test_regression_converted_schema_error_survives_pickling: round-trips a schema_error_from error
    test_regression_model_construction_raises_schema_error: rejects a bad Placement with SchemaError
    test_regression_schema_error_truncates_echoed_input: caps each echoed input at 80 characters
    test_regression_box_with_a_huge_int_corner_raises_a_printable_schema_error: echoes a
        5,000-digit coordinate by its bit count
    test_regression_spot_with_a_huge_int_id_raises_a_printable_schema_error: echoes a 5,000-digit
        spot ID by its bit count
    test_regression_root_level_schema_error_names_the_root: renders an empty loc as `(root)`
    test_regression_bounded_repr_prints_ints_below_the_digit_floor: echoes a 640-digit int under
        the lowest interpreter digit limit
    test_to_dict_round_trips_through_json: renders type, code, message, hint and context as JSON
    test_repr_shows_class_code_and_context: renders a debugging repr without the message
    test_regression_context_lists_are_capped: keeps 20 items of a 1,000-item list plus a count
    test_regression_capped_context_survives_a_re_raise: keeps the first count when re-bounded
    test_regression_context_text_is_bounded: cuts long text and huge ints in context
    test_regression_context_holds_only_json_values: turns tuples, sets, numpy values and objects
        into JSON values
    test_regression_deep_context_is_cut_to_text: renders nesting past the depth cap as text
    test_regression_unpicklable_context_value_pickles_as_its_repr: pickles a lambda in context
    test_regression_schema_error_message_counts_unlisted_errors: ends with "(+n more)" past five
    test_every_class_default_code_is_catalogued: makes every default code an ErrorCode member
    test_regression_huge_array_and_set_contexts_build_fast: caps a 5,000,000-item array and a
        1,000,000-item set without converting or sorting every item
    test_regression_mixed_set_context_is_ordered_by_repr: orders a set of unorderable items
    test_regression_context_mappings_are_capped: keeps 20 entries of a 1,000-entry mapping
    test_regression_context_survives_a_broken_repr: names the type of a value whose repr raises
"""

import copy
import json
import pickle
import sys
import time
from collections.abc import Collection

import numpy as np
import pytest
from pydantic import TypeAdapter, ValidationError

from showpy.cast.models import Spot
from showpy.choreo.models import Placement
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import (
    CUT_MARK,
    ECHO_HEAD_CHARS,
    MAX_CONTEXT_ITEMS,
    MAX_ECHOED_INPUT_CHARS,
    ConflictError,
    ContinuityError,
    FontError,
    GeometryError,
    GlyphError,
    LegibilityWarning,
    LockedError,
    MissingFontError,
    PathError,
    SchemaError,
    ShowPyError,
    StorageError,
    UnknownRefError,
    ZeroLengthError,
    bounded_repr,
    field_errors,
    schema_error_from,
)
from showpy.core.types import Box2

HUGE_INT = 10**5000
HUGE_INT_ECHO = f"<int of {HUGE_INT.bit_length()} bits>"
LOWEST_INT_DIGIT_LIMIT = 640
LONG_CONTEXT_ITEMS = 1_000
LISTED_SCHEMA_ERRORS = 5
BAD_LIST_ITEMS = 8
DEEP_NESTING = 50
HUGE_CONTEXT_ITEMS = 1_000_000
# Converting every item of an array costs about 28 ms per million, so five million items
# make a full conversion (about 140 ms) fail the budget.
HUGE_ARRAY_ITEMS = 5_000_000
# About 5 times the measured cost of capping either value (array slice, scan of an int set),
# so a full conversion or a repr sort (about 1 s) fails while a slow CI runner still passes.
HUGE_CONTEXT_BUDGET_S = 0.05


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
    assert ShowPyError("x", hint="y", code=ErrorCode.CORRUPT).code == ErrorCode.CORRUPT


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
    return error_class("boom", hint="fix it", context={"key": 1}, code=ErrorCode.CORRUPT)


def test_regression_every_error_survives_pickling() -> None:
    """Round-trips every ShowPyError subclass through pickle (audit 9.3)."""
    for error_class in _every_error_class():
        clone = pickle.loads(pickle.dumps(_sample(error_class)))
        assert type(clone) is error_class
        assert (clone.code, clone.message, clone.hint) == (ErrorCode.CORRUPT, "boom", "fix it")
        assert clone.context == {"key": 1}


def test_regression_every_error_survives_deepcopy() -> None:
    """Deep-copies every ShowPyError subclass (audit 9.3)."""
    for error_class in _every_error_class():
        clone = copy.deepcopy(_sample(error_class))
        assert (clone.code, clone.message, clone.hint) == (ErrorCode.CORRUPT, "boom", "fix it")


def test_regression_converted_schema_error_survives_pickling() -> None:
    with pytest.raises(SchemaError) as info:
        Placement(entity="A", x="abc", y=0)  # pyright: ignore[reportArgumentType]
    clone = pickle.loads(pickle.dumps(info.value))
    assert type(clone) is SchemaError
    assert clone.context == info.value.context
    assert (clone.code, clone.message, clone.hint) == (
        info.value.code,
        info.value.message,
        info.value.hint,
    )
    assert clone.__cause__ is None


def test_regression_model_construction_raises_schema_error() -> None:
    with pytest.raises(SchemaError) as info:
        Placement(entity="A", x="abc", y=0)  # pyright: ignore[reportArgumentType]
    assert info.value.context["model"] == "Placement"
    assert field_errors(info.value)[0]["loc"] == "x"
    assert isinstance(info.value.__cause__, ValidationError)


def test_regression_schema_error_truncates_echoed_input() -> None:
    with pytest.raises(SchemaError) as info:
        Placement(entity="A", x="a" * 10_000, y=0)  # pyright: ignore[reportArgumentType]
    echoed = field_errors(info.value)[0]["input"]
    assert isinstance(echoed, str)
    assert len(echoed) <= MAX_ECHOED_INPUT_CHARS
    assert "a" * 81 not in str(info.value)


def test_regression_box_with_a_huge_int_corner_raises_a_printable_schema_error() -> None:
    with pytest.raises(SchemaError) as info:
        Box2(min=(HUGE_INT, 0), max=(1, 1))
    assert field_errors(info.value)[0]["input"] == HUGE_INT_ECHO
    assert HUGE_INT_ECHO in repr(info.value.context)


def test_regression_spot_with_a_huge_int_id_raises_a_printable_schema_error() -> None:
    with pytest.raises(SchemaError) as info:
        Spot(id=HUGE_INT, group="g")  # pyright: ignore[reportArgumentType]
    assert field_errors(info.value)[0]["input"] == HUGE_INT_ECHO
    assert HUGE_INT_ECHO in repr(info.value.context)


def test_regression_root_level_schema_error_names_the_root() -> None:
    with pytest.raises(SchemaError) as info:
        Box2.model_validate("not a box")
    assert field_errors(info.value)[0]["loc"] == "(root)"
    assert "(root):" in info.value.message


def test_regression_bounded_repr_prints_ints_below_the_digit_floor() -> None:
    largest_printed = 10**LOWEST_INT_DIGIT_LIMIT - 1
    previous_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(LOWEST_INT_DIGIT_LIMIT)
    try:
        assert bounded_repr(largest_printed) == "9" * ECHO_HEAD_CHARS + CUT_MARK + "9" * (
            MAX_ECHOED_INPUT_CHARS - ECHO_HEAD_CHARS - 1
        )
        assert bounded_repr(-largest_printed - 1).startswith("<int of ")
    finally:
        sys.set_int_max_str_digits(previous_limit)


def test_bounded_repr_survives_a_container_too_long_to_print() -> None:
    assert bounded_repr((HUGE_INT,)) == "<unprintable tuple>"


def test_bounded_repr_keeps_text_unquoted_and_cut() -> None:
    text = "a" * 100 + "z" * 100
    assert bounded_repr(text) == "a" * ECHO_HEAD_CHARS + CUT_MARK + "z" * (
        MAX_ECHOED_INPUT_CHARS - ECHO_HEAD_CHARS - 1
    )


def test_font_errors_carry_their_codes() -> None:
    assert GlyphError("m", hint="h").code == "MISSING_GLYPH"
    assert MissingFontError("m", hint="h").code == "MISSING_FONT"
    assert FontError("m", hint="h").code == "BAD_FONT"
    assert issubclass(GlyphError, UnknownRefError)
    assert issubclass(LegibilityWarning, UserWarning)
    assert issubclass(MissingFontError, UnknownRefError)


def test_to_dict_round_trips_through_json() -> None:
    """Renders the error as a JSON object that survives a dump and a load unchanged."""
    error = LockedError(
        "T4 is pinned",
        hint="pass force=True",
        context={"entity": "T4", "counts": (1, 2.5), "pinned": True, "set": None},
    )
    rendered = error.to_dict()
    assert json.loads(json.dumps(rendered)) == rendered
    assert rendered == {
        "type": "LockedError",
        "code": "LOCKED",
        "message": "T4 is pinned",
        "hint": "pass force=True",
        "context": {"entity": "T4", "counts": [1, 2.5], "pinned": True, "set": None},
    }


def test_repr_shows_class_code_and_context() -> None:
    error = ConflictError("late", hint="re-read", context={"head": "abc"})
    assert repr(error) == "ConflictError(code='CONFLICT', context={'head': 'abc'})"


def test_regression_context_lists_are_capped() -> None:
    """Keeps the first 20 items of a 1,000-item context list and counts the rest."""
    error = StorageError("x", hint="y", context={"paths": list(range(LONG_CONTEXT_ITEMS))})
    paths = error.context["paths"]
    assert paths == [*range(MAX_CONTEXT_ITEMS), "(+980 more)"]


def test_regression_capped_context_survives_a_re_raise() -> None:
    """Keeps the original count when a capped context is passed to a new error."""
    first = StorageError("x", hint="y", context={"paths": list(range(LONG_CONTEXT_ITEMS))})
    second = StorageError("x", hint="y", context=first.context)
    assert second.context == first.context


def test_regression_context_text_is_bounded() -> None:
    error = SchemaError("x", hint="y", context={"name": "n" * 10_000, "big": HUGE_INT})
    assert error.context["name"] == bounded_repr("n" * 10_000)
    assert len(bounded_repr("n" * 10_000)) == MAX_ECHOED_INPUT_CHARS
    assert error.context["big"] == HUGE_INT_ECHO


def test_regression_context_holds_only_json_values() -> None:
    """Turns tuples, sets, numpy values, odd keys and arbitrary objects into JSON values."""
    error = SchemaError(
        "x",
        hint="y",
        context={
            "point": (1.0, np.float64(2.0)),
            "ids": frozenset({"b", "a"}),
            "n": np.int64(3),
            "array": np.arange(3),
            "keys": {1: "one"},
            "nan": float("nan"),
            "obj": object,
        },
    )
    assert error.context == {
        "point": [1.0, 2.0],
        "ids": ["a", "b"],
        "n": 3,
        "array": [0, 1, 2],
        "keys": {"1": "one"},
        "nan": "nan",
        "obj": "<class 'object'>",
    }
    assert json.loads(json.dumps(error.to_dict(), allow_nan=False)) == error.to_dict()


def test_regression_deep_context_is_cut_to_text() -> None:
    """Renders nesting past the depth cap, a reference cycle included, as bounded text."""
    deep: list[object] = []
    current = deep
    for _ in range(DEEP_NESTING):
        inner: list[object] = []
        current.append(inner)
        current = inner
    cycle: list[object] = []
    cycle.append(cycle)
    error = SchemaError("x", hint="y", context={"deep": deep, "cycle": cycle})
    assert json.loads(json.dumps(error.to_dict())) == error.to_dict()


def test_regression_unpicklable_context_value_pickles_as_its_repr() -> None:
    """Pickles an error whose context got a lambda: the context holds only its bounded repr."""
    error = SchemaError("x", hint="y", context={"call": lambda: None})
    clone = pickle.loads(pickle.dumps(error))
    assert isinstance(clone.context["call"], str)
    assert clone.context == error.context


def test_regression_schema_error_message_counts_unlisted_errors() -> None:
    """Lists five failing fields in the message and counts the rest."""
    with pytest.raises(ValidationError) as info:
        TypeAdapter(list[int]).validate_python(["a"] * BAD_LIST_ITEMS)
    error = schema_error_from(info.value, model="Ints")
    assert error.message.count(": Input should be") == LISTED_SCHEMA_ERRORS
    assert error.message.endswith(f"(+{BAD_LIST_ITEMS - LISTED_SCHEMA_ERRORS} more)")


def test_every_class_default_code_is_catalogued() -> None:
    for error_class in _every_error_class():
        assert isinstance(error_class.default_code, ErrorCode), error_class.__name__


def _capped_build_seconds(value: object) -> tuple[float, object]:
    start = time.perf_counter()
    error = SchemaError("x", hint="y", context={"value": value})
    return time.perf_counter() - start, error.context["value"]


@pytest.mark.parametrize(
    "value",
    [np.arange(HUGE_ARRAY_ITEMS), set(range(HUGE_CONTEXT_ITEMS))],
    ids=["array", "set"],
)
def test_regression_huge_array_and_set_contexts_build_fast(value: Collection[int]) -> None:
    """Caps a huge array and set by their first 20 items without converting or sorting all."""
    seconds, bounded = _capped_build_seconds(value)
    assert bounded == [*range(MAX_CONTEXT_ITEMS), f"(+{len(value) - MAX_CONTEXT_ITEMS} more)"]
    assert seconds < HUGE_CONTEXT_BUDGET_S, f"{type(value).__name__} took {seconds:.3f} s"


def test_regression_mixed_set_context_is_ordered_by_repr() -> None:
    error = SchemaError("x", hint="y", context={"ids": {2, "a", 1.5}})
    assert error.context["ids"] == [1.5, 2, "a"]


def test_regression_context_mappings_are_capped() -> None:
    """Keeps 20 entries of a 1,000-entry mapping, counts the rest and survives a re-raise."""
    first = SchemaError("x", hint="y", context={"by_id": {str(n): n for n in range(1_000)}})
    assert first.context["by_id"] == {
        **{str(n): n for n in range(MAX_CONTEXT_ITEMS)},
        "(+980 more)": None,
    }
    assert SchemaError("x", hint="y", context=first.context).context == first.context


class _BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")


def test_regression_context_survives_a_broken_repr() -> None:
    """Names the type of a context value or key whose repr raises instead of failing to build."""
    error = SchemaError("x", hint="y", context={"obj": _BrokenRepr(), "keys": {_BrokenRepr(): 1}})
    assert error.context == {
        "obj": "<unprintable _BrokenRepr>",
        "keys": {"<unprintable _BrokenRepr>": 1},
    }
