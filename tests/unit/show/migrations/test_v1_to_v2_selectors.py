#!/usr/bin/env python3

"""
test_v1_to_v2_selectors.py --- tests the selectors sub-step of the v1 to v2 format migration

Contains:
    v1_select(): evaluates selector text the way v1 did: glued `+` and `&` split words, a glued
        `-` stays inside a range word, and every operator combines left to right
    test_migrate_selectors_rewrites_every_stored_field(): rewrites facing overrides, reporting each
        rewrite in document order, and leaves zones to the sheet sub-step, which drops them
    test_migrate_selectors_leaves_v2_text_and_id_lists_alone(): changes and reports nothing for
        text v2 reads the same way, ID lists and stored selections
    test_migrate_selectors_never_mutates_its_input(): leaves the documents it was given unchanged
    test_migrate_selectors_refuses_text_v1_never_parsed(): raises SchemaError naming the document,
        the field and the value
    test_normalized_text_selects_what_v1_selected(): for drawn v1 text, the rewrite selects the
        same spots in the same order as v1, and rewriting again changes nothing
"""

import copy
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from e8s.cast.models import Cast, Group, Spot
from e8s.cast.select import Selection, normalize_v1_selector, select
from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import SpotId
from e8s.core.json_types import JsonValue
from e8s.show.migrate import ReportBuilder
from e8s.show.migrations.v1_to_v2_selectors import migrate_selectors

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "repos"
V030_SHOW = "legacy-show"
SHEET_DOC = "/show/sheet.json"
SHOW_DOC = "/show/show.json"
V1_TOKENS = re.compile(r"[+\-&()]|[^\s+&()\-][^\s+&()]*")
V1_OPERATORS = ("+", "-", "&")
JOINS = (" + ", " - ", " & ", "+", "&", " -", "& ", " +")
GROUPS = {"trumpets": "T", "tubas": "TB"}
SPOTS_PER_GROUP = 4
MAX_WORDS = 6


def v1_select(source: Cast, text: str) -> tuple[str, ...]:
    """Evaluates selector text the way v1 did, resolving each word with today's `select`."""
    tokens = V1_TOKENS.findall(text)
    position = 0

    def term() -> Selection:
        nonlocal position
        token = tokens[position]
        position += 1
        if token != "(":
            return select(source, token)
        inner = expr()
        if tokens[position] != ")":
            raise SchemaError("expected )", hint="close the parenthesis")
        position += 1
        return inner

    def expr() -> Selection:
        nonlocal position
        result = term()
        while position < len(tokens) and tokens[position] in V1_OPERATORS:
            operator = tokens[position]
            position += 1
            right = term()
            result = {"+": result | right, "-": result - right, "&": result & right}[operator]
        return result

    picked = expr()
    if position != len(tokens):
        raise SchemaError("expected an operator", hint="join words with operators")
    return picked.ids


def _cast() -> Cast:
    groups = {key: Group(key=key, label_prefix=prefix) for key, prefix in GROUPS.items()}
    spots = tuple(
        Spot(id=SpotId(f"{prefix}{n}"), group=key)
        for key, prefix in GROUPS.items()
        for n in range(1, SPOTS_PER_GROUP + 1)
    )
    return Cast(groups=groups, spots=spots)


CAST = _cast()
WORDS = st.one_of(
    st.sampled_from(CAST.ids()),
    st.sampled_from([f"{prefix}*" for prefix in GROUPS.values()]),
    st.sampled_from([f"group:{key}" for key in GROUPS]),
    st.just("all"),
    st.just("T1-T3"),
)


def _documents() -> dict[str, JsonValue]:
    return {
        SHEET_DOC: {"zones": [{"id": "a", "applies_to": "T1+T2&T2"}, {"id": "b"}]},
        SHOW_DOC: {
            "id": "show-1",
            "zones": [{"id": "c", "applies_to": "all - T1 & T2"}],
            "facing_overrides": [
                {"entities": ["T1", "T2"], "mode": "fixed"},
                {"entities": "T1&T2+T3", "mode": "fixed"},
            ],
        },
    }


def test_migrate_selectors_rewrites_every_stored_field() -> None:
    report = ReportBuilder(1)
    migrated = migrate_selectors(_documents(), report)
    assert migrated[SHEET_DOC] == _documents()[SHEET_DOC]
    show_doc = migrated[SHOW_DOC]
    assert isinstance(show_doc, dict)
    assert show_doc["zones"] == [{"id": "c", "applies_to": "all - T1 & T2"}]
    assert show_doc["facing_overrides"] == [
        {"entities": ["T1", "T2"], "mode": "fixed"},
        {"entities": "(T1 & T2) + T3", "mode": "fixed"},
    ]
    assert report.build().repairs == (
        "/show/show.json facing_overrides[1].entities: rewrote 'T1&T2+T3' as '(T1 & T2) + T3'",
    )


def test_migrate_selectors_leaves_v2_text_and_id_lists_alone() -> None:
    documents: dict[str, JsonValue] = {
        SHEET_DOC: {"zones": [{"id": "a", "applies_to": "T1 + T2-T4"}]},
        SHOW_DOC: {
            "zones": [{"id": "b", "applies_to": {"ids": ["T1"], "description": "T1+T2&T2"}}],
            "facing_overrides": [{"entities": ["T1"]}, {"entities": "(T1 + T2) & T2"}],
        },
    }
    report = ReportBuilder(1)
    assert migrate_selectors(documents, report) == documents
    assert report.build().repairs == ()


def test_migrate_selectors_never_mutates_its_input() -> None:
    documents = _documents()
    snapshot = copy.deepcopy(documents)
    migrate_selectors(documents, ReportBuilder(1))
    assert documents == snapshot


@pytest.mark.parametrize("stored", ["T1 +", "(T1", "T1 T2", ""])
def test_migrate_selectors_refuses_text_v1_never_parsed(stored: str) -> None:
    documents: dict[str, JsonValue] = {SHOW_DOC: {"facing_overrides": [{"entities": stored}]}}
    with pytest.raises(SchemaError) as info:
        migrate_selectors(documents, ReportBuilder(1))
    assert info.value.context == {
        "path": SHOW_DOC,
        "field": "facing_overrides[0].entities",
        "value": stored,
    }


@given(
    st.lists(WORDS, min_size=1, max_size=MAX_WORDS),
    st.lists(st.sampled_from(JOINS), min_size=MAX_WORDS, max_size=MAX_WORDS),
    st.lists(st.booleans(), min_size=MAX_WORDS, max_size=MAX_WORDS),
)
def test_normalized_text_selects_what_v1_selected(
    words: list[str], joins: list[str], groups: list[bool]
) -> None:
    text = words[0]
    for word, join, is_grouped in zip(words[1:], joins, groups, strict=False):
        text = f"({text}){join}{word}" if is_grouped else f"{text}{join}{word}"
    try:
        expected = v1_select(CAST, text)
    except E8sError:
        return
    normalized = normalize_v1_selector(text)
    assert select(CAST, normalized).ids == expected, (text, normalized)
    assert normalize_v1_selector(normalized) == normalized
