#!/usr/bin/env python3

"""
test_ids.py --- tests reference and transition ID parsing and formatting

Contains:
    test_*(): one behavior of showpy.core.ids each
    test_regression_parse_ref_rejects_path_separators: rejects `..` and `/` in reference IDs, which
        would escape a directory
    test_regression_format_tid_rejects_a_third_element: rejects a three-part transition ID instead
        of dropping the third
    test_check_id_*(): one rule of the ADR 0006 ID pattern each
    test_regression_check_id_error_names_the_pattern: puts the ID pattern in the context and `..`
        in the hint, as ADR 0006 decision 5 requires
    test_regression_check_id_rejects_a_trailing_newline: rejects `a\\n`, which `ID_PATTERN.match`
        accepts
    test_check_id_agrees_with_the_pattern_on_unreserved_text: accepts exactly what
        `ID_PATTERN.fullmatch` accepts, outside reserved names and `..`
    test_regression_check_id_rejects_a_device_name_before_an_extension: rejects `con.5`, which
        Windows opens as the console device
    test_check_id_accepts_names_that_only_start_like_a_device: accepts `cons.5`, `com10` and
        `all.1`
    test_check_id_returns_the_ascii_id_nfc_makes: turns the Kelvin sign into `K`, so callers store
        the returned ID
    test_find_case_clashes_*(): one behavior of the case-clash finder each
"""

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import SchemaError
from showpy.core.ids import (
    ID_MAX_LENGTH,
    ID_PATTERN,
    RESERVED_IDS,
    check_id,
    find_case_clashes,
    format_ref,
    format_tid,
    parse_ref,
    parse_tid,
)

ID_CANDIDATES = st.from_regex(ID_PATTERN, fullmatch=True) | st.text(
    alphabet=st.characters(max_codepoint=0x7F) | st.sampled_from("\u00e9\u0663\u212a"),
    max_size=ID_MAX_LENGTH + 2,
)


def test_parse_ref_with_version() -> None:
    assert parse_ref("sheet:field@2") == ("sheet", "field", "2")


def test_parse_ref_without_version() -> None:
    assert parse_ref("sheet:gym") == ("sheet", "gym", None)


@pytest.mark.parametrize("text", ["field", "sheet:", ":x", "sheet:a@", "sheet:a@b@c"])
def test_parse_ref_rejects_malformed(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_ref(text)


def test_format_ref_round_trips() -> None:
    assert parse_ref(format_ref("sheet", "hs-field", "3")) == ("sheet", "hs-field", "3")
    assert format_ref("sheet", "gym") == "sheet:gym"


@pytest.mark.parametrize("text", ["12→13", "12->13", " 12 -> 13 "])
def test_parse_tid_accepts_both_arrows(text: str) -> None:
    assert parse_tid(text) == ("12", "13")


@pytest.mark.parametrize("text", ["12", "12→", "→13", "1→2→3"])
def test_parse_tid_rejects_malformed(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_tid(text)


def test_format_tid_uses_arrow() -> None:
    assert format_tid(("12A", "13")) == "12A→13"


@pytest.mark.defect("UP-9.3-31")
@pytest.mark.xfail(strict=True, reason="UP-9.3-31")
@pytest.mark.parametrize("text", ["sheet:..", "sheet:../etc", "sheet:a/b"])
def test_regression_parse_ref_rejects_path_separators(text: str) -> None:
    """Rejects `..` and `/` in reference IDs, which would escape a directory (audit 9.3)."""
    with pytest.raises(SchemaError):
        parse_ref(text)


@pytest.mark.defect("UP-9.3-32")
@pytest.mark.xfail(strict=True, reason="UP-9.3-32")
def test_regression_format_tid_rejects_a_third_element() -> None:
    """Rejects a three-part transition ID instead of dropping the third (audit 9.3)."""
    with pytest.raises(SchemaError):
        format_tid(("1", "2", "3"))  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["1", "12A", "2.5", "T1", "x_y", "_a", "a" * ID_MAX_LENGTH])
def test_check_id_accepts_valid_ids(text: str) -> None:
    assert check_id(text, kind="spot") == text


@pytest.mark.parametrize(
    ("text", "position"),
    [
        ("", 0),
        ("-1", 0),
        ("a-b", 1),
        (".a", 0),
        ("a.", 1),
        ("a..b", 2),
        ("..", 1),
        ("ALL", 0),
        ("Con", 0),
        ("com9", 0),
        ("LPT1", 0),
        ("T*", 1),
        ("group:1", 5),
        ("T\u0663", 1),
        ("a" * (ID_MAX_LENGTH + 1), ID_MAX_LENGTH),
    ],
)
def test_check_id_rejects_with_position(text: str, position: int) -> None:
    with pytest.raises(SchemaError) as caught:
        check_id(text, kind="spot")
    assert caught.value.context["kind"] == "spot"
    assert caught.value.context["position"] == position


def test_check_id_normalizes_to_nfc_before_checking() -> None:
    with pytest.raises(SchemaError) as caught:
        check_id("e\u0301", kind="spot")
    assert caught.value.context["text"] == "\u00e9"


def test_check_id_truncates_text_in_context() -> None:
    with pytest.raises(SchemaError) as caught:
        check_id("-" * 500, kind="spot")
    assert caught.value.context["text"] == "-" * 80


def test_check_id_constants_agree() -> None:
    assert ID_PATTERN.pattern == r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$"
    assert "all" in RESERVED_IDS
    assert {"com1", "com9", "lpt1", "lpt9", "con", "prn", "aux", "nul"} <= RESERVED_IDS
    assert len(RESERVED_IDS) == 1 + 4 + 9 + 9


def test_find_case_clashes_pairs_ids_equal_ignoring_case() -> None:
    assert find_case_clashes(["T1", "t1", "B2"]) == [("T1", "t1")]


def test_find_case_clashes_is_sorted_and_empty_without_clashes() -> None:
    assert find_case_clashes(["b", "A", "B", "a"]) == [("A", "a"), ("B", "b")]
    assert find_case_clashes(["T1", "T2"]) == []


def test_regression_check_id_error_names_the_pattern() -> None:
    with pytest.raises(SchemaError) as caught:
        check_id("a b", kind="spot")
    assert caught.value.context["pattern"] == ID_PATTERN.pattern
    assert ".." in caught.value.hint


def test_regression_check_id_rejects_a_trailing_newline() -> None:
    assert ID_PATTERN.match("a\n") is not None
    with pytest.raises(SchemaError) as caught:
        check_id("a\n", kind="spot")
    assert caught.value.context["position"] == 1


@given(ID_CANDIDATES)
def test_check_id_agrees_with_the_pattern_on_unreserved_text(text: str) -> None:
    normalized = unicodedata.normalize("NFC", text)
    stem = normalized.casefold().partition(".")[0]
    if normalized.casefold() in RESERVED_IDS or stem in RESERVED_IDS or ".." in normalized:
        return
    try:
        check_id(text, kind="spot")
    except SchemaError:
        assert ID_PATTERN.fullmatch(normalized) is None
    else:
        assert ID_PATTERN.fullmatch(normalized) is not None


@pytest.mark.parametrize("text", ["con.5", "AUX.txt", "lpt1.2", "Nul.tar.gz", "COM9.x"])
def test_regression_check_id_rejects_a_device_name_before_an_extension(text: str) -> None:
    with pytest.raises(SchemaError) as caught:
        check_id(text, kind="spot")
    assert caught.value.context["position"] == 0


@pytest.mark.parametrize("text", ["cons.5", "com10", "com0.1", "all.1", "a.con", "auxiliary"])
def test_check_id_accepts_names_that_only_start_like_a_device(text: str) -> None:
    assert check_id(text, kind="spot") == text


def test_check_id_returns_the_ascii_id_nfc_makes() -> None:
    kelvin_one = "\u212a1"
    assert check_id(kelvin_one, kind="spot") == "K1"
