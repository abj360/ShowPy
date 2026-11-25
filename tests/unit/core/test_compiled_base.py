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
"""

import inspect
import math
import unicodedata
from fractions import Fraction
from typing import cast

import numpy as np
import pytest

from e8s._core import core
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
