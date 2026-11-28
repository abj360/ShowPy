#!/usr/bin/env python3

"""
test_errors.py --- tests the E8sError family: fields, codes, hierarchy and rendering

Contains:
    test_font_errors_carry_their_codes: gives the font errors their codes and parents
    test_*(): one behavior of e8s.core.errors each
    test_regression_every_error_survives_pickling: round-trips every E8sError subclass through
        pickle
    test_regression_every_error_survives_deepcopy: deep-copies every E8sError subclass
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
    test_regression_huge_array_context_converts_only_kept_rows: converts 20 rows of a
        1,000,000-item array
    test_regression_huge_ordered_set_context_bounds_only_kept_items: selects a million-item int
        set or a 200,000-item text set by value, bounding only the 20 kept items
    test_regression_unorderable_set_at_the_cap_is_ranked: ranks a set of 1,000 frozensets
    test_regression_unorderable_set_past_the_cap_is_summarized: summarizes 1,001 frozensets
        without bounding or printing any item
    test_regression_set_context_order_ignores_the_hash_seed: bounds sets of frozensets and of
        NaN and text identically under hash seeds 0, 1 and 2
    test_regression_mixed_set_context_is_ordered_by_type_then_value: orders a mixed-type set
    test_regression_context_mappings_are_capped: keeps 20 entries of a 1,000-entry mapping
    test_regression_context_survives_a_broken_repr: names the type of a value whose repr raises
    test_regression_context_shows_a_numpy_scalar_with_no_python_twin_as_its_repr: bounds a
        long double or complex long double, alone, in an array or in a list, to its repr instead
        of recursing on `item()`
    test_regression_self_holding_zero_d_array_context_is_cut_to_text: bounds a 0-d object
        array that holds itself, unwrapping it only to the depth cap
"""

import copy
import json
import pickle
import sys
import time
from collections import Counter
from typing import Any, ClassVar

import numpy as np
import pytest
from pydantic import TypeAdapter, ValidationError

import e8s.core.error_context as context_module
from e8s.cast.models import Spot
from e8s.choreo.models import Placement
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    CUT_MARK,
    ECHO_HEAD_CHARS,
    MAX_CONTEXT_ITEMS,
    MAX_ECHOED_INPUT_CHARS,
    MAX_RANKED_SET_ITEMS,
    ConflictError,
    ContinuityError,
    E8sError,
    FontError,
    GeometryError,
    GlyphError,
    LegibilityWarning,
    LockedError,
    MissingFontError,
    PathError,
    SchemaError,
    StorageError,
    UnknownRefError,
    ZeroLengthError,
    bounded_repr,
    field_errors,
    schema_error_from,
)
from e8s.core.json_types import JsonValue
from e8s.core.types import Box2
from tests.helpers import outputs_under_hash_seeds

HUGE_INT = 10**5000
HUGE_INT_ECHO = f"<int of {HUGE_INT.bit_length()} bits>"
LOWEST_INT_DIGIT_LIMIT = 640
LONG_CONTEXT_ITEMS = 1_000
LISTED_SCHEMA_ERRORS = 5
BAD_LIST_ITEMS = 8
DEEP_NESTING = 50
HUGE_CONTEXT_ITEMS = 1_000_000
LARGE_TEXT_SET_ITEMS = 200_000
# A backstop only: capping takes about 10 ms, a full repr sort about 1 s.
HUGE_CONTEXT_BACKSTOP_S = 0.5
# One call for the outer value (or its context key) plus one per kept item.
CAPPED_BOUND_CALLS = MAX_CONTEXT_ITEMS + 1
SET_ORDER_SEEDS = ("0", "1", "2")
SET_ORDER_SCRIPT = (
    "import json\n"
    "from e8s.core.errors import SchemaError\n"
    "groups = {frozenset({f's{n}', f't{n}'}) for n in range(40)}\n"
    "mixed = {float('nan'), 3.0, 1.0, *(f'x{n}' for n in range(30))}\n"
    "error = SchemaError('x', hint='y', context={'groups': groups, 'mixed': mixed})\n"
    "print(json.dumps(error.to_dict()))\n"
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
    assert E8sError("x", hint="y", code=ErrorCode.CORRUPT).code == ErrorCode.CORRUPT


def test_context_defaults_to_empty_dict() -> None:
    assert E8sError("x", hint="y").context == {}


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (ZeroLengthError, GeometryError),
        (ContinuityError, PathError),
        (ConflictError, StorageError),
        (PathError, E8sError),
    ],
)
def test_hierarchy(child: type[E8sError], parent: type[E8sError]) -> None:
    assert issubclass(child, parent)


def _every_error_class() -> list[type[E8sError]]:
    found: list[type[E8sError]] = []
    pending = [E8sError]
    while pending:
        current = pending.pop()
        found.append(current)
        pending.extend(current.__subclasses__())
    return found


def _sample(error_class: type[E8sError]) -> E8sError:
    return error_class("boom", hint="fix it", context={"key": 1}, code=ErrorCode.CORRUPT)


def test_regression_every_error_survives_pickling() -> None:
    """Round-trips every E8sError subclass through pickle (audit 9.3)."""
    for error_class in _every_error_class():
        clone = pickle.loads(pickle.dumps(_sample(error_class)))
        assert type(clone) is error_class
        assert (clone.code, clone.message, clone.hint) == (ErrorCode.CORRUPT, "boom", "fix it")
        assert clone.context == {"key": 1}


def test_regression_every_error_survives_deepcopy() -> None:
    """Deep-copies every E8sError subclass (audit 9.3)."""
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
        Box2.from_json("not a box")
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


class _CountingArray(np.ndarray):
    """Records how many items each `tolist` call converts."""

    converted: ClassVar[list[int]] = []

    # Any: an override must keep the return type of numpy's own tolist stub.
    def tolist(self) -> Any:
        type(self).converted.append(int(self.size))
        return super().tolist()


def _count_bounding_calls(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> tuple[Counter[str], JsonValue]:
    """Bounds `value` as context, counting calls of `_bounded_value` and `bounded_repr`."""
    calls: Counter[str] = Counter()
    for name in ("_bounded_value", "bounded_repr"):
        original = getattr(context_module, name)

        def counted(*args: object, _name: str = name, _original: object = original) -> object:
            calls[_name] += 1
            assert callable(_original)
            return _original(*args)

        monkeypatch.setattr(context_module, name, counted)
    start = time.perf_counter()
    bounded = SchemaError("x", hint="y", context={"value": value}).context["value"]
    assert time.perf_counter() - start < HUGE_CONTEXT_BACKSTOP_S
    return calls, bounded


def test_regression_huge_array_context_converts_only_kept_rows() -> None:
    """Converts the 20 kept rows of a million-item array, never the whole array."""
    _CountingArray.converted.clear()
    array = np.arange(HUGE_CONTEXT_ITEMS).view(_CountingArray)
    bounded = SchemaError("x", hint="y", context={"value": array}).context["value"]
    assert bounded == [
        *range(MAX_CONTEXT_ITEMS),
        f"(+{HUGE_CONTEXT_ITEMS - MAX_CONTEXT_ITEMS} more)",
    ]
    assert _CountingArray.converted == [MAX_CONTEXT_ITEMS]


@pytest.mark.timing
@pytest.mark.parametrize(
    ("value", "smallest"),
    [
        (set(range(HUGE_CONTEXT_ITEMS)), list(range(MAX_CONTEXT_ITEMS))),
        ({f"{n:06d}" for n in range(LARGE_TEXT_SET_ITEMS)}, [f"{n:06d}" for n in range(20)]),
    ],
    ids=["ints", "strs"],
)
def test_regression_huge_ordered_set_context_bounds_only_kept_items(
    monkeypatch: pytest.MonkeyPatch, value: set[object], smallest: list[object]
) -> None:
    """Selects the 20 smallest items of a large int or text set by value, never by repr."""
    calls, bounded = _count_bounding_calls(monkeypatch, value)
    assert bounded == [*smallest, f"(+{len(value) - MAX_CONTEXT_ITEMS} more)"]
    assert calls["_bounded_value"] == CAPPED_BOUND_CALLS
    assert calls["bounded_repr"] <= CAPPED_BOUND_CALLS


def test_regression_set_context_order_ignores_the_hash_seed() -> None:
    """Gives the same bounded sets of frozensets and of NaN, floats and text under every seed."""
    outputs = outputs_under_hash_seeds(SET_ORDER_SCRIPT, SET_ORDER_SEEDS)
    assert len(set(outputs)) == 1, outputs


def test_regression_mixed_set_context_is_ordered_by_type_then_value() -> None:
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


def _frozenset_items(count: int) -> set[frozenset[int]]:
    return {frozenset({n}) for n in range(count)}


@pytest.mark.timing
def test_regression_unorderable_set_at_the_cap_is_ranked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranks a set of exactly 1,000 frozensets by bounded JSON text and keeps the 20 first."""
    calls, bounded = _count_bounding_calls(monkeypatch, _frozenset_items(MAX_RANKED_SET_ITEMS))
    ranked = sorted(range(MAX_RANKED_SET_ITEMS), key=lambda n: json.dumps([n]))
    assert bounded == [
        *([n] for n in ranked[:MAX_CONTEXT_ITEMS]),
        f"(+{MAX_RANKED_SET_ITEMS - MAX_CONTEXT_ITEMS} more)",
    ]
    assert calls["_bounded_value"] > MAX_RANKED_SET_ITEMS


@pytest.mark.timing
def test_regression_unorderable_set_past_the_cap_is_summarized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports 1,001 frozensets by type and count, never bounding or printing an item."""
    count = MAX_RANKED_SET_ITEMS + 1
    calls, bounded = _count_bounding_calls(monkeypatch, _frozenset_items(count))
    assert bounded == [f"<set of {count} items>", f"(not shown: {count} unorderable items)"]
    assert calls["_bounded_value"] == 1
    assert calls["bounded_repr"] == 1


def test_regression_context_shows_a_numpy_scalar_with_no_python_twin_as_its_repr() -> None:
    """Shows a scalar whose `item()` returns a numpy scalar as its bounded repr, never recursing."""
    error = SchemaError(
        "x",
        hint="y",
        context={
            "long": np.longdouble(11),
            "complex": np.clongdouble(1 + 2j),
            "zero_d": np.array(np.longdouble(-1)),
            "row": np.array([np.longdouble(3)]),
            "items": [np.longdouble(2), np.float64(2.5)],
        },
    )
    assert error.context == {
        "long": repr(np.longdouble(11)),
        "complex": repr(np.clongdouble(1 + 2j)),
        "zero_d": repr(np.longdouble(-1)),
        "row": [repr(np.longdouble(3))],
        "items": [repr(np.longdouble(2)), 2.5],
    }
    assert json.loads(json.dumps(error.to_dict(), allow_nan=False)) == error.to_dict()


def test_regression_self_holding_zero_d_array_context_is_cut_to_text() -> None:
    """Counts each 0-d array unwrap as a level, so an array that holds itself ends as text."""
    looped = np.empty((), dtype=object)
    looped[()] = looped
    error = SchemaError("x", hint="y", context={"looped": looped})
    assert error.context["looped"] == repr(looped)
    assert json.loads(json.dumps(error.to_dict())) == error.to_dict()
