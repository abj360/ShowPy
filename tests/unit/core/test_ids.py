#!/usr/bin/env python3

"""
test_ids.py --- tests reference and transition ID parsing and formatting
"""

import pytest

from stage.core.errors import SchemaError
from stage.core.ids import format_ref, format_tid, parse_ref, parse_tid


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
