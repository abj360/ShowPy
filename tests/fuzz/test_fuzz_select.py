#!/usr/bin/env python3

"""
test_fuzz_select.py --- feeds hostile text to the selection grammar and the ID parsers

Contains:
    fuzz_cast(): builds the small fixed cast the selection fuzz target runs against
    expressions(): draws selection expressions from words, ranges, operators, nesting and junk
    test_fuzz_select(): `select` rejects any expression with a ShowPyError or returns
    test_fuzz_parse_range(): `parse_range` rejects any text with a ShowPyError or returns
    test_fuzz_natural_sort(): `natural_sort` orders any list of text without raising
    test_fuzz_parse_ref(): `parse_ref` rejects any text with a ShowPyError or returns
    test_fuzz_parse_tid(): `parse_tid` rejects any text with a ShowPyError or returns
"""

from hypothesis import given
from hypothesis import strategies as st

from showpy.cast.models import Cast, Group, Spot
from showpy.cast.select import select
from showpy.core.ids import parse_ref, parse_tid
from showpy.core.util import natural_sort, parse_range
from tests.helpers import only_showpy_errors

GROUPS = {"trumpets": "T", "tubas": "TB", "flags": "F"}
TAGS = ("soloist", "leader")
SPOTS_PER_GROUP = 6
MAX_NESTING = 500
MAX_JUNK_CHARS = 40
OPERATORS = (" + ", " - ", " & ", "+", "-", "&", " ")
KIND_WORDS = ("form:", "group:", "tag:", "sel:", "set:", "all", "*", "-", ":")


def fuzz_cast() -> Cast:
    """Builds a three-group cast of tagged spots, the target of every selection fuzz input."""
    groups = {key: Group(key=key, label_prefix=prefix) for key, prefix in GROUPS.items()}
    spots = tuple(
        Spot(id=f"{prefix}{n}", group=key, tags=(TAGS[n % len(TAGS)],) if n % 3 else ())
        for key, prefix in GROUPS.items()
        for n in range(1, SPOTS_PER_GROUP + 1)
    )
    return Cast(groups=groups, spots=spots)


CAST = fuzz_cast()
DIGITS = st.text(alphabet="0123456789", min_size=1, max_size=12)
JUNK = st.text(max_size=MAX_JUNK_CHARS)
WORDS: st.SearchStrategy[str] = st.one_of(
    st.sampled_from(CAST.ids()),
    st.sampled_from([f"{prefix}*" for prefix in GROUPS.values()]),
    st.sampled_from([f"group:{key}" for key in GROUPS]),
    st.sampled_from([f"tag:{tag}" for tag in TAGS]),
    st.builds("{}{}".format, st.sampled_from(KIND_WORDS), JUNK),
    st.builds(
        "{}{}-{}{}".format,
        st.sampled_from(sorted(GROUPS.values())),
        DIGITS,
        st.sampled_from(sorted(GROUPS.values())),
        DIGITS,
    ),
    JUNK,
)


@st.composite
def expressions(draw: st.DrawFn) -> str:
    """Draws a selection expression: words joined by operators, possibly nested or truncated."""
    words = draw(st.lists(WORDS, min_size=1, max_size=6))
    expression = words[0]
    for word in words[1:]:
        expression += draw(st.sampled_from(OPERATORS)) + word
    nesting = draw(st.one_of(st.just(0), st.integers(0, 3), st.integers(0, MAX_NESTING)))
    expression = "(" * nesting + expression + ")" * draw(st.integers(max(nesting - 2, 0), nesting))
    if not draw(st.booleans()):
        return expression
    return expression[: draw(st.integers(0, len(expression)))]


@given(expressions())
def test_fuzz_select(expression: str) -> None:
    only_showpy_errors(lambda: select(CAST, expression), expression)


@given(st.one_of(st.text(), st.builds("{}-{}".format, DIGITS, DIGITS)))
def test_fuzz_parse_range(text: str) -> None:
    only_showpy_errors(lambda: parse_range(text), text)


@given(st.lists(st.one_of(st.text(), DIGITS), max_size=20))
def test_fuzz_natural_sort(ids: list[str]) -> None:
    only_showpy_errors(lambda: natural_sort(ids), ids)


@given(st.text())
def test_fuzz_parse_ref(text: str) -> None:
    only_showpy_errors(lambda: parse_ref(text), text)


@given(st.text())
def test_fuzz_parse_tid(text: str) -> None:
    only_showpy_errors(lambda: parse_tid(text), text)
