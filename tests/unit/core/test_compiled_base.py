#!/usr/bin/env python3

"""
test_compiled_base.py --- tests the compiled core's handling of inputs the Python helpers took

Contains:
    test_format_number_writes_exact_integers: writes an integer of any size in full
    test_format_number_accepts_registered_real_numbers: writes numpy and Fraction values
    test_require_size_reports_a_size_beyond_64_bits: raises the caller's error for a huge size
    test_vector_facing_error_context_holds_the_vector: keeps the caller's vector in the context
    test_ids_with_non_ascii_text_follow_python_tables: normalizes, folds and shows text as Python
    test_regression_check_id_rejects_a_lone_surrogate: raises SchemaError, not UnicodeEncodeError
    test_parse_ref_reads_unicode_kinds_and_refuses_unicode_spaces: follows `\\w` and `\\s`
    test_format_tid_formats_any_pair_and_refuses_other_lengths: pairs of str-able parts only
    test_regression_a_failing_unicode_question_raises_its_own_error: propagates Python's error
        from a Unicode table instead of guessing an answer
    test_round_to_checks_the_value_before_the_step: raises SchemaError for a bad value and
        TypeError for a step that is not a number, as v0.4.0 did
    test_core_constants_match_the_model_layer: keeps the ID patterns and reserved names in step
    test_regression_require_size_refuses_out_of_range_floats_as_v040_did: raises v0.4.0's
        SchemaError, with the float in the message and context, for NaN, infinities and floats
        below 0 or over the cap
    test_regression_require_size_refuses_floats_v040_let_through: raises the size check's own
        error class, asking for a whole-number int, for a float in range, which v0.4.0 accepted
        without truncating
    test_regression_require_size_refuses_decimals_v040_let_through: refuses a Decimal in range
        as it refuses a float in range
    test_regression_require_size_refuses_out_of_range_decimals_as_v040_did: raises v0.4.0's
        negative and over-cap errors for a Decimal below 0, over the cap or infinite
    test_regression_require_size_refuses_a_decimal_nan_as_a_float_nan: raises the size check's
        error, not decimal.InvalidOperation
    test_regression_require_size_takes_bools_as_v040_did: accepts numpy bools as v0.4.0 did, and
        shows a bool over the cap as `True`
    test_regression_require_size_refuses_a_long_double_without_recursing: refuses a long double
        in range as a float in range, and one below 0 or over the cap with the size error and its
        repr in the context, where v0.4.0 hit the recursion limit
    test_regression_require_size_refuses_in_range_values_v040_let_through: refuses a Fraction,
        a half float, a one-item array and an object that only compares, in range, with the
        whole-number error, never numpy's or pyo3's TypeError
    test_regression_require_size_refuses_a_half_float_in_range: refuses np.float16(3) under a
        cap float16 holds; a larger cap overflows in numpy's comparison, as in v0.4.0
    test_regression_require_size_refuses_out_of_range_values_as_v040_did: gives v0.4.0's
        negative and over-cap errors for the same kinds below 0 or over the cap
    test_regression_require_size_refuses_an_array_of_other_than_one_item: refuses an empty or
        many-item array with the whole-number error, where v0.4.0 raised numpy's ValueError
    test_require_size_raises_the_comparison_type_error_for_text: raises the TypeError v0.4.0's
        `0 <= requested` raised for a value that does not compare with numbers
    test_regression_require_size_refuses_numpy_complex_sizes_in_range: refuses a numpy complex
        in range with the whole-number error, where v0.4.0 returned None
    test_regression_require_size_refuses_out_of_range_complex_sizes_as_v040_did: gives v0.4.0's
        negative and over-cap errors for numpy complex sizes, which numpy orders by real part
"""

import inspect
import math
import unicodedata
from decimal import Decimal
from fractions import Fraction
from functools import total_ordering
from typing import cast

import numpy as np
import pytest

from e8s._core import core
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, SpacingError, ZeroLengthError
from e8s.core.ids import ID_PATTERN, RESERVED_IDS, SHEET_ID_PATTERN, WINDOWS_DEVICE_NAMES
from e8s.core.units import vector_facing


def test_format_number_writes_exact_integers() -> None:
    assert core.format_number(2**70) == str(2**70)
    assert core.format_number(-7, max_decimals=0) == "-7"
    with pytest.raises(SchemaError):
        core.format_number(10**400)


def test_format_number_accepts_registered_real_numbers() -> None:
    assert core.format_number(cast("float", np.float32(0.5))) == "0.5"
    assert core.format_number(cast("float", np.int64(12))) == "12"
    assert core.format_number(cast("float", Fraction(1, 4))) == "0.25"


def test_require_size_reports_a_size_beyond_64_bits() -> None:
    with pytest.raises(SpacingError) as caught:
        core.require_size(10**30, 5, what="ids", error=SpacingError)
    assert caught.value.message == f"ids would need {10**30}, over the cap of 5"
    with pytest.raises(SchemaError) as negative:
        core.require_size(-(10**30), 5, what="ids")
    assert negative.value.message == f"ids would need -{10**30}, which is negative"


def test_vector_facing_error_context_holds_the_vector() -> None:
    with pytest.raises(ZeroLengthError) as caught:
        vector_facing((0.0, 0.0))
    assert caught.value.context == {"vector": [0.0, 0.0]}


def test_ids_with_non_ascii_text_follow_python_tables() -> None:
    assert core.check_id("\N{KELVIN SIGN}1", kind="spot") == "K1"
    with pytest.raises(SchemaError) as caught:
        core.check_id("a\N{LATIN SMALL LETTER E WITH ACUTE}", kind="spot")
    assert caught.value.message == "bad spot id 'a\N{LATIN SMALL LETTER E WITH ACUTE}'"
    assert caught.value.context["position"] == 1
    assert core.is_device_name("CON.txt")
    assert core.find_case_clashes(["stra\N{LATIN SMALL LETTER SHARP S}e", "STRASSE"]) == [
        ("STRASSE", "stra\N{LATIN SMALL LETTER SHARP S}e")
    ]


def test_regression_check_id_rejects_a_lone_surrogate() -> None:
    with pytest.raises(SchemaError):
        core.check_id("a\ud800", kind="spot")
    with pytest.raises(SchemaError):
        core.parse_ref("sheet:a\ud800")


def test_parse_ref_reads_unicode_kinds_and_refuses_unicode_spaces() -> None:
    assert core.parse_ref("x\N{LATIN SMALL LETTER E WITH ACUTE}t:gym") == (
        "x\N{LATIN SMALL LETTER E WITH ACUTE}t",
        "gym",
        None,
    )
    assert core.parse_ref("\N{EM SPACE}sheet:gym\N{NO-BREAK SPACE}") == ("sheet", "gym", None)
    for text in (
        "sheet:a\N{EM SPACE}b",
        "sheet:a@1\N{NO-BREAK SPACE}2",
        "\N{ARABIC-INDIC DIGIT THREE}x:a",
    ):
        with pytest.raises(SchemaError):
            core.parse_ref(text)


def test_format_tid_formats_any_pair_and_refuses_other_lengths() -> None:
    assert core.format_tid((1, 2)) == "1→2"  # type: ignore[arg-type]
    with pytest.raises(SchemaError) as caught:
        core.format_tid(("1", "2", "3"))  # type: ignore[arg-type]
    assert caught.value.context == {"parts": 3}


def test_core_constants_match_the_model_layer() -> None:
    format_default = inspect.signature(core.format_number).parameters["max_decimals"].default
    round_default = inspect.signature(core.round_to).parameters["step"].default
    assert format_default == core.DEFAULT_DECIMALS
    assert round_default == core.DEFAULT_STEP
    assert ID_PATTERN.pattern == core.ID_PATTERN_SOURCE
    assert SHEET_ID_PATTERN.pattern == core.SHEET_ID_PATTERN_SOURCE
    assert core.RESERVED_IDS == RESERVED_IDS == WINDOWS_DEVICE_NAMES | {"all", "props"}


@pytest.mark.parametrize("error", [MemoryError, KeyboardInterrupt])
def test_regression_a_failing_unicode_question_raises_its_own_error(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    def broken(*_arguments: str) -> str:
        raise error("unicode table")

    with monkeypatch.context() as patch:
        patch.setattr(unicodedata, "normalize", broken)
        raised: list[BaseException] = []
        for call in (
            lambda: core.check_id("caf\N{LATIN SMALL LETTER E WITH ACUTE}", kind="spot"),
            lambda: core.parse_ref("sheet:caf\N{LATIN SMALL LETTER E WITH ACUTE}"),
        ):
            try:
                call()
            except BaseException as caught:
                raised.append(caught)
    assert [type(caught) for caught in raised] == [error, error]


def test_round_to_checks_the_value_before_the_step() -> None:
    with pytest.raises(SchemaError):
        core.round_to(math.nan, cast("float", "x"))
    with pytest.raises(TypeError):
        core.round_to(1.0, cast("float", "x"))


SIZE_CAP = 1_000_000


@pytest.mark.parametrize(
    ("requested", "shown", "is_negative"),
    [
        (math.nan, "nan", False),
        (math.inf, "inf", False),
        (-math.inf, "-inf", True),
        (2e6, "2000000.0", False),
        (1_000_000.5, "1000000.5", False),
        (-1.5, "-1.5", True),
        (np.float64(-2.0), "-2.0", True),
    ],
)
def test_regression_require_size_refuses_out_of_range_floats_as_v040_did(
    requested: float, shown: str, is_negative: bool
) -> None:
    with pytest.raises(SpacingError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points", error=SpacingError)
    tail = "which is negative" if is_negative else f"over the cap of {SIZE_CAP}"
    assert caught.value.message == f"points would need {shown}, {tail}"
    assert caught.value.context["what"] == "points"
    assert caught.value.context["cap"] == SIZE_CAP


@pytest.mark.parametrize(
    ("requested", "shown"),
    [(3.0, "3.0"), (3.5, "3.5"), (-0.0, "-0.0"), (1e6, "1000000.0"), (np.float64(2.0), "2.0")],
)
def test_regression_require_size_refuses_floats_v040_let_through(
    requested: float, shown: str
) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.code == ErrorCode.SCHEMA
    assert caught.value.message == f"points would need {shown}, which is not a whole-number count"
    assert caught.value.hint == "pass the number of points as a whole-number int"
    assert caught.value.context["requested"] == requested
    with pytest.raises(SpacingError) as spacing:
        core.require_size(cast("int", requested), SIZE_CAP, what="spots", error=SpacingError)
    assert spacing.value.hint == "pass the number of spots as a whole-number int"


@pytest.mark.parametrize(
    "requested", [Decimal(3), Decimal("3.5"), Decimal("-0"), Decimal(SIZE_CAP)]
)
def test_regression_require_size_refuses_decimals_v040_let_through(requested: Decimal) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.code == ErrorCode.SCHEMA
    assert (
        caught.value.message == f"points would need {requested}, which is not a whole-number count"
    )
    assert caught.value.hint == "pass the number of points as a whole-number int"
    assert caught.value.context["requested"] == repr(requested)
    with pytest.raises(SpacingError) as spacing:
        core.require_size(cast("int", requested), SIZE_CAP, what="spots", error=SpacingError)
    assert spacing.value.code == ErrorCode.SPACING
    assert spacing.value.hint == "pass the number of spots as a whole-number int"


@pytest.mark.parametrize(
    ("requested", "is_negative"),
    [
        (Decimal(-1), True),
        (Decimal("-0.5"), True),
        (Decimal("-Infinity"), True),
        (Decimal(SIZE_CAP + 1), False),
        (Decimal("1000000.5"), False),
        (Decimal("Infinity"), False),
        (Decimal("1E+1000"), False),
    ],
)
def test_regression_require_size_refuses_out_of_range_decimals_as_v040_did(
    requested: Decimal, is_negative: bool
) -> None:
    with pytest.raises(SpacingError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points", error=SpacingError)
    tail = "which is negative" if is_negative else f"over the cap of {SIZE_CAP}"
    assert caught.value.message == f"points would need {requested}, {tail}"
    assert caught.value.context == {"what": "points", "requested": repr(requested), "cap": SIZE_CAP}


@pytest.mark.parametrize("requested", [Decimal("NaN"), Decimal("-NaN"), Decimal("sNaN")])
def test_regression_require_size_refuses_a_decimal_nan_as_a_float_nan(requested: Decimal) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.message == f"points would need {requested}, over the cap of {SIZE_CAP}"
    assert caught.value.hint == "ask for fewer points or use a coarser setting"


@pytest.mark.parametrize("requested", [True, False, np.True_, np.False_])
def test_regression_require_size_takes_bools_as_v040_did(requested: bool) -> None:
    core.require_size(requested, 1, what="points")
    if not requested:
        core.require_size(requested, 0, what="points")
        return
    with pytest.raises(SchemaError) as caught:
        core.require_size(requested, 0, what="points")
    assert caught.value.message == "points would need True, over the cap of 0"
    assert caught.value.context == {"what": "points", "requested": True, "cap": 0}


@pytest.mark.parametrize(
    ("requested", "tail"),
    [
        (np.longdouble(3), "which is not a whole-number count"),
        (np.longdouble(-1), "which is negative"),
        (np.longdouble(SIZE_CAP + 1), f"over the cap of {SIZE_CAP}"),
    ],
)
def test_regression_require_size_refuses_a_long_double_without_recursing(
    requested: np.longdouble, tail: str
) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.message == f"points would need {requested}, {tail}"
    assert caught.value.context == {"what": "points", "requested": repr(requested), "cap": SIZE_CAP}


@total_ordering
class _Compares:
    """Stands for a size type that only defines comparisons, as v0.4.0's check took any."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, int | float) and self.value == other

    def __lt__(self, other: float) -> bool:
        return self.value < other

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"Compares({self.value})"


@pytest.mark.parametrize(
    ("requested", "shown"),
    [
        (Fraction(3), "3"),
        (Fraction(7, 2), "7/2"),
        (np.array(3.0), "3.0"),
        (np.array([3]), "[3]"),
        (np.array(np.True_), "True"),
        (_Compares(3), "Compares(3)"),
    ],
)
def test_regression_require_size_refuses_in_range_values_v040_let_through(
    requested: object, shown: str
) -> None:
    with pytest.raises(SpacingError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points", error=SpacingError)
    assert caught.value.message == f"points would need {shown}, which is not a whole-number count"
    assert caught.value.hint == "pass the number of points as a whole-number int"


HALF_FLOAT_CAP = 1_000


def test_regression_require_size_refuses_a_half_float_in_range() -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", np.float16(3)), HALF_FLOAT_CAP, what="points")
    assert caught.value.message == "points would need 3.0, which is not a whole-number count"


@pytest.mark.parametrize(
    ("requested", "shown", "tail", "context"),
    [
        (Fraction(-1), "-1", "which is negative", "Fraction(-1, 1)"),
        (np.float16(-2), "-2.0", "which is negative", -2.0),
        (np.array(1e7), "10000000.0", f"over the cap of {SIZE_CAP}", 1e7),
        (np.array([-1.5]), "[-1.5]", "which is negative", [-1.5]),
        (_Compares(-1), "Compares(-1)", "which is negative", "Compares(-1)"),
        (_Compares(SIZE_CAP + 1), "Compares(1000001)", f"over the cap of {SIZE_CAP}", None),
    ],
)
def test_regression_require_size_refuses_out_of_range_values_as_v040_did(
    requested: object, shown: str, tail: str, context: object
) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.message == f"points would need {shown}, {tail}"
    assert caught.value.context["requested"] == (repr(requested) if context is None else context)


@pytest.mark.parametrize("requested", [np.array([]), np.array([3, 4]), np.zeros((2, 2))])
def test_regression_require_size_refuses_an_array_of_other_than_one_item(
    requested: object,
) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.message == (
        f"points would need {requested}, which is not a whole-number count"
    )


@pytest.mark.parametrize("requested", ["3", None, b"3", [3], complex(3)])
def test_require_size_raises_the_comparison_type_error_for_text(requested: object) -> None:
    with pytest.raises(TypeError, match="'<=' not supported between instances of 'int' and"):
        core.require_size(cast("int", requested), SIZE_CAP, what="points")


@pytest.mark.parametrize(
    ("requested", "shown", "context"),
    [
        (np.complex128(3), "(3+0j)", "(3+0j)"),
        (np.complex64(3), "(3+0j)", "(3+0j)"),
        (np.complex128(3 + 1j), "(3+1j)", "(3+1j)"),
        (np.clongdouble(3), "(3+0j)", repr(np.clongdouble(3))),
    ],
)
def test_regression_require_size_refuses_numpy_complex_sizes_in_range(
    requested: object, shown: str, context: str
) -> None:
    with pytest.raises(SpacingError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points", error=SpacingError)
    assert caught.value.message == f"points would need {shown}, which is not a whole-number count"
    assert caught.value.hint == "pass the number of points as a whole-number int"
    assert caught.value.context["requested"] == context


@pytest.mark.parametrize(
    ("requested", "shown", "tail"),
    [
        (np.complex128(-1), "(-1+0j)", "which is negative"),
        (np.complex128(-1 + 1j), "(-1+1j)", "which is negative"),
        (np.complex128(SIZE_CAP + 1), "(1000001+0j)", f"over the cap of {SIZE_CAP}"),
        (np.complex128(complex("nan")), "(nan+0j)", f"over the cap of {SIZE_CAP}"),
    ],
)
def test_regression_require_size_refuses_out_of_range_complex_sizes_as_v040_did(
    requested: object, shown: str, tail: str
) -> None:
    with pytest.raises(SchemaError) as caught:
        core.require_size(cast("int", requested), SIZE_CAP, what="points")
    assert caught.value.message == f"points would need {shown}, {tail}"
    assert caught.value.context["requested"] == shown
