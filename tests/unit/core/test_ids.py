#!/usr/bin/env python3

"""
test_ids.py --- tests reference and transition ID parsing and formatting

Contains:
    test_*(): one behavior of e8s.core.ids each
    test_regression_parse_ref_accepts_build_metadata: reads `1.0.0+build.5` as a version
    test_parse_ref_still_refuses_unsafe_versions: refuses `..`, `/`, `all` and bad edges in versions
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
    test_regression_check_id_reserves_props: rejects `props` ignoring case, the selection word
        that later selects every prop spot
    test_check_id_reserves_props_only_as_a_whole_id: accepts `props.1`, `props2` and `props_`
    test_check_id_returns_the_ascii_id_nfc_makes: turns the Kelvin sign into `K`, so callers store
        the returned ID
    test_find_case_clashes_*(): one behavior of the case-clash finder each
    test_is_device_name_matches_device_names_alone_or_before_a_dot: flags `con`, `NUL` and
        `aux.1`, ignoring case
    test_is_device_name_ignores_names_that_only_start_like_a_device: passes `cons`, `com10`,
        `all` and `show-1`
    test_id_fields_*(): one behavior of the annotated model ID field types each
    test_show_id_field_*(): show IDs keep an inner `-` and follow the rest of the sheet rule
    test_sheet_id_pattern_agrees_with_its_characters_and_edges: keeps `SHEET_ID_PATTERN` equal to
        its characters, length cap and edge rules over 2,000 strings
    test_bundled_*(): every bundled template ref and sheet item ID passes the sheet rule
    test_transition_id_*(): one behavior of the TransitionId named tuple each
    test_regression_check_id_reports_a_leading_dot_at_position_zero: reports `..a` at 0, the
        first character that breaks the pattern
    test_id_pattern_agrees_with_body_chars_and_length: keeps `ID_PATTERN` and the
        `ID_BODY_CHARS`/`ID_MAX_LENGTH` fast path equal over 10,000 strings
    test_check_sheet_id_*(): one rule of the sheet item and sheet reference ID check each
"""

import unicodedata
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter, ValidationError

from e8s.core.errors import SchemaError
from e8s.core.ids import (
    ID_BODY_CHARS,
    ID_MAX_LENGTH,
    ID_PATTERN,
    RESERVED_IDS,
    SHEET_ID_PATTERN,
    FormIdField,
    NoteIdField,
    RouteIdField,
    SelectionNameField,
    SetId,
    SetIdField,
    SetKeyField,
    ShowIdField,
    SpotIdField,
    TransitionId,
    check_id,
    check_sheet_id,
    find_case_clashes,
    format_ref,
    format_tid,
    is_device_name,
    make_tid,
    parse_ref,
    parse_tid,
)
from e8s.sheet.templates import sheet_templates

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


@pytest.mark.parametrize("text", ["sheet:..", "sheet:../etc", "sheet:a/b"])
def test_regression_parse_ref_rejects_path_separators(text: str) -> None:
    """Rejects `..` and `/` in reference IDs, which would escape a directory (audit 9.3)."""
    with pytest.raises(SchemaError):
        parse_ref(text)


def test_regression_parse_ref_accepts_build_metadata() -> None:
    """Reads `1.0.0+build.5` as a reference version instead of refusing the `+`."""
    assert parse_ref("sheet:gym@1.0.0+build.5") == ("sheet", "gym", "1.0.0+build.5")
    assert format_ref("sheet", "gym", "2-rc.1+x") == "sheet:gym@2-rc.1+x"


@pytest.mark.parametrize(
    "text",
    [
        "sheet:a@1..2",
        "sheet:a@1/2",
        "sheet:a@all",
        "sheet:a@con",
        "sheet:a@+1",
        "sheet:a@1+",
        "sheet:a@1_2",
        "sheet:a@" + "1" * 65,
    ],
)
def test_parse_ref_still_refuses_unsafe_versions(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_ref(text)


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
        ("..", 0),
        ("..a", 0),
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
    assert {"all", "props"} <= RESERVED_IDS
    assert {"com1", "com9", "lpt1", "lpt9", "con", "prn", "aux", "nul"} <= RESERVED_IDS
    assert len(RESERVED_IDS) == 2 + 4 + 9 + 9


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


@pytest.mark.parametrize("text", ["props", "Props", "PROPS"])
def test_regression_check_id_reserves_props(text: str) -> None:
    with pytest.raises(SchemaError) as caught:
        check_id(text, kind="spot")
    assert caught.value.context["position"] == 0
    assert "props" in caught.value.hint


@pytest.mark.parametrize("text", ["props.1", "props2", "props_", "Props.A", "my_props"])
def test_check_id_reserves_props_only_as_a_whole_id(text: str) -> None:
    assert check_id(text, kind="spot") == text


def test_check_id_returns_the_ascii_id_nfc_makes() -> None:
    kelvin_one = "\u212a1"
    assert check_id(kelvin_one, kind="spot") == "K1"


@pytest.mark.parametrize("text", ["con", "CON", "nul", "Aux", "aux.1", "com1", "LPT9.txt"])
def test_is_device_name_matches_device_names_alone_or_before_a_dot(text: str) -> None:
    assert is_device_name(text)


@pytest.mark.parametrize("text", ["cons", "com10", "com0", "all", "show-1", "x.con"])
def test_is_device_name_ignores_names_that_only_start_like_a_device(text: str) -> None:
    assert not is_device_name(text)


AGREEMENT_EXAMPLES = 10_000
AGREEMENT_ALPHABET = st.sampled_from(sorted(ID_BODY_CHARS)) | st.sampled_from(".-*: \n\u00e9\u0663")
NEAR_CAP_IDS = st.text(
    alphabet=st.sampled_from(sorted(ID_BODY_CHARS)),
    min_size=ID_MAX_LENGTH - 4,
    max_size=ID_MAX_LENGTH + 2,
)
AGREEMENT_CANDIDATES = (
    st.text(alphabet=AGREEMENT_ALPHABET, max_size=ID_MAX_LENGTH + 2) | NEAR_CAP_IDS
)
SHEET_AGREEMENT_EXAMPLES = 2_000
SHEET_AGREEMENT_CANDIDATES = (
    st.text(alphabet=AGREEMENT_ALPHABET | st.just("-"), max_size=ID_MAX_LENGTH + 2) | NEAR_CAP_IDS
)
BUNDLED_TEMPLATES = 5
BUNDLED_SHEET_ITEMS = 91
TOY_SPOT_ID = "T1"
# Any: annotated field types are type forms, which mypy cannot hold as plain values.
SPOT_ID_FIELDS: list[Any] = [
    SpotIdField,
    SetIdField,
    SetKeyField,
    FormIdField,
    RouteIdField,
    NoteIdField,
    SelectionNameField,
]


class _ToySpot(BaseModel):
    id: SpotIdField


class _Outer(BaseModel):
    inner: _ToySpot


class _ToyTransition(BaseModel):
    tid: TransitionId


@pytest.mark.parametrize("text", ["a b", "", "all", "a-b", "a\n"])
def test_id_fields_reject_bad_ids(text: str) -> None:
    with pytest.raises(ValidationError, match="spot id"):
        _ToySpot.model_validate({"id": text})


def test_id_fields_accept_legacy_ids_in_context() -> None:
    toy = _ToySpot.model_validate({"id": "a-b"}, context={"legacy_ids": True})
    assert toy.id == "a-b"


@pytest.mark.parametrize("context", [{"legacy_ids": True}, {"legacy_ids": False}, None])
def test_id_fields_reject_an_empty_id_in_every_context(context: dict[str, bool] | None) -> None:
    with pytest.raises(ValidationError):
        _ToySpot.model_validate({"id": ""}, context=context)


@pytest.mark.parametrize("context", [{"legacy_ids": False}, {"legacy_ids": "yes"}, {}])
def test_id_fields_check_unless_the_context_says_legacy_ids_is_true(
    context: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _ToySpot.model_validate({"id": "a-b"}, context=context)


def test_id_fields_store_the_nfc_id() -> None:
    assert _ToySpot.model_validate({"id": "\u212a1"}).id == "K1"


@pytest.mark.parametrize("field", SPOT_ID_FIELDS)
def test_id_fields_of_every_kind_run_the_id_check(field: Any) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(field)
    assert adapter.validate_python(TOY_SPOT_ID) == TOY_SPOT_ID
    with pytest.raises(ValidationError):
        adapter.validate_python("a-b")


def test_show_id_field_keeps_a_hyphenated_show_id() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(ShowIdField)
    assert adapter.validate_python("my-show") == "my-show"


@pytest.mark.parametrize("text", ["a b", "..", "all", "-show", "con"])
def test_show_id_field_refuses_ids_outside_the_sheet_rule(text: str) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(ShowIdField)
    with pytest.raises(ValidationError, match="show id"):
        adapter.validate_python(text)


def test_transition_id_is_a_named_tuple() -> None:
    tid = TransitionId(SetId("1"), SetId("2"))
    assert isinstance(tid, tuple)
    assert (tid.from_set, tid.to_set) == ("1", "2")
    assert tid == ("1", "2")
    assert hash(tid) == hash(("1", "2"))
    assert str(tid) == "1→2"
    assert format_tid(tid) == "1→2"


def test_transition_id_comes_from_parse_tid() -> None:
    tid = parse_tid("12->13")
    assert isinstance(tid, TransitionId)
    assert tid.from_set == "12"


def test_transition_id_round_trips_through_json() -> None:
    toy = _ToyTransition.model_validate_json('{"tid": ["1", "2"]}')
    assert isinstance(toy.tid, TransitionId)
    assert toy.model_dump_json() == '{"tid":["1","2"]}'


def test_make_tid_checks_both_set_ids() -> None:
    assert make_tid("1", "2A") == TransitionId(SetId("1"), SetId("2A"))
    with pytest.raises(SchemaError, match="set id"):
        make_tid("1", "a-b")


@pytest.mark.parametrize(("text", "parts"), [("con->1", ("con", "1")), ("1.→a-b", ("1.", "a-b"))])
def test_parse_tid_reads_stored_set_ids_outside_the_id_rule(
    text: str, parts: tuple[str, str]
) -> None:
    assert parse_tid(text) == parts


def test_regression_check_id_reports_a_leading_dot_at_position_zero() -> None:
    with pytest.raises(SchemaError) as caught:
        check_id("..a", kind="spot")
    assert caught.value.context["position"] == 0


def test_regression_check_id_hint_names_the_pattern() -> None:
    with pytest.raises(SchemaError) as caught:
        check_id("a b", kind="spot")
    assert ID_PATTERN.pattern in caught.value.hint


def _fast_path_accepts(text: str) -> bool:
    return (
        0 < len(text) <= ID_MAX_LENGTH
        and all(char in ID_BODY_CHARS for char in text)
        and not text.startswith(".")
        and not text.endswith(".")
    )


@settings(max_examples=AGREEMENT_EXAMPLES)
@given(AGREEMENT_CANDIDATES)
def test_id_pattern_agrees_with_body_chars_and_length(text: str) -> None:
    assert (ID_PATTERN.fullmatch(text) is not None) == _fast_path_accepts(text)


def _sheet_rule_accepts(text: str) -> bool:
    return (
        0 < len(text) <= ID_MAX_LENGTH
        and all(char in ID_BODY_CHARS or char == "-" for char in text)
        and text[0] not in ".-"
        and text[-1] not in ".-"
    )


@settings(max_examples=SHEET_AGREEMENT_EXAMPLES)
@given(SHEET_AGREEMENT_CANDIDATES)
def test_sheet_id_pattern_agrees_with_its_characters_and_edges(text: str) -> None:
    assert (SHEET_ID_PATTERN.fullmatch(text) is not None) == _sheet_rule_accepts(text)


def test_id_fields_take_the_legacy_context_into_nested_models() -> None:
    nested = {"inner": {"id": "a-b"}}
    assert _Outer.model_validate(nested, context={"legacy_ids": True}).inner.id == "a-b"
    with pytest.raises(ValidationError, match="spot id"):
        _Outer.model_validate(nested)


def test_bundled_template_refs_pass_parse_ref() -> None:
    refs = [sheet.ref for sheet in sheet_templates()]
    assert len(refs) == BUNDLED_TEMPLATES
    assert [parse_ref(ref)[1] for ref in refs] == [
        check_sheet_id(ref.partition(":")[2].partition("@")[0], kind="reference") for ref in refs
    ]


def test_bundled_sheet_items_pass_check_sheet_id() -> None:
    ids: list[str] = []
    for sheet in sheet_templates():
        ids += [guide.id for guide in sheet.guides]
    assert len(ids) == BUNDLED_SHEET_ITEMS
    assert [check_sheet_id(item_id, kind="sheet item") for item_id in ids] == ids


@pytest.mark.parametrize("text", ["hs-football", "end-zone-left", "a--b", "x.1-2", "T1"])
def test_check_sheet_id_accepts_an_inner_hyphen(text: str) -> None:
    assert check_sheet_id(text, kind="landmark") == text


@pytest.mark.parametrize(
    ("text", "position"),
    [("-a", 0), ("a-", 1), ("a b", 1), ("..", 0), ("a..b", 2), ("con", 0), ("all", 0), ("", 0)],
)
def test_check_sheet_id_rejects_with_position(text: str, position: int) -> None:
    with pytest.raises(SchemaError) as caught:
        check_sheet_id(text, kind="landmark")
    assert caught.value.context["position"] == position
    assert caught.value.context["pattern"] == SHEET_ID_PATTERN.pattern


@pytest.mark.parametrize("text", ["sheet:-a", "sheet:a b@1", "sheet:con", "sheet:a@1/2"])
def test_parse_ref_rejects_ids_and_versions_that_fail_the_check(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_ref(text)


def test_parse_ref_keeps_a_hyphenated_sheet_ref() -> None:
    assert parse_ref("sheet:hs-football@1") == ("sheet", "hs-football", "1")
