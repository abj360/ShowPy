#!/usr/bin/env python3

"""
test_fuzz_select.py --- feeds hostile text to the selection grammar and the ID parsers

Contains:
    fuzz_cast(): builds the small fixed cast the selection fuzz target runs against
    expressions(): draws selection expressions from words, ranges, operators, nesting and junk
    test_fuzz_select(): `select` rejects any expression with an E8sError or returns
    reference_select(): evaluates an expression with the recursive left-to-right parser that
        `select` used before it became iterative; the oracle of the iterative evaluator
    trees(): draws expression trees of known words joined by +, - and &
    test_fuzz_iterative_select_matches_the_recursive_reference(): every accepted expression,
        hostile or drawn from a tree, gives the reference's result
    test_fuzz_redundant_parentheses_never_change_a_result(): wrapping parts of an accepted
        expression in extra parentheses keeps its result
    test_fuzz_parse_range(): `parse_range` rejects any text with an E8sError or returns
    test_fuzz_natural_sort(): `natural_sort` orders any list of text without raising
    test_fuzz_parse_ref(): `parse_ref` rejects any text with an E8sError or returns
    test_fuzz_parse_tid(): `parse_tid` rejects any text with an E8sError or returns
"""

import re
from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

from e8s.cast.models import Cast, Group, Spot
from e8s.cast.select import Selection, select
from e8s.core.errors import E8sError
from e8s.core.ids import SpotId, parse_ref, parse_tid
from e8s.core.util import natural_sort, parse_range
from tests.helpers import only_e8s_errors

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
        Spot(id=SpotId(f"{prefix}{n}"), group=key, tags=(TAGS[n % len(TAGS)],) if n % 3 else ())
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
    only_e8s_errors(lambda: select(CAST, expression), expression)


REFERENCE_TOKENS = re.compile(r"[+\-&()]|[^\s+&()\-][^\s+&()]*")
TREE_OPERATORS = ("+", "-", "&")
MAX_TREE_LEAVES = 12

type Tree = str | tuple["Tree", str, "Tree"]
type Draw = Callable[[st.SearchStrategy[bool]], bool]


def reference_select(expression: str) -> tuple[str, ...]:
    """Evaluates an expression the way the recursive parser did: words via `select`, operators
    left to right, a parenthesized group as one term."""
    tokens = REFERENCE_TOKENS.findall(expression)
    position = 0

    def term() -> Selection:
        nonlocal position
        token = tokens[position]
        position += 1
        if token != "(":
            return select(CAST, token)
        inner = expr()
        position += 1
        return inner

    def expr() -> Selection:
        nonlocal position
        result = term()
        while position < len(tokens) and tokens[position] in TREE_OPERATORS:
            operator = tokens[position]
            position += 1
            right = term()
            result = {"+": result | right, "-": result - right, "&": result & right}[operator]
        return result

    return expr().ids


def _kind(operator: str) -> str:
    return "&" if operator == "&" else "+-"


def _render(tree: Tree, wraps: Draw | None, parent: str | None = None) -> str:
    """Prints a tree as text the grammar accepts; with `wraps`, adds drawn redundant parentheses."""
    if isinstance(tree, str):
        text = tree
    else:
        left, operator, right = tree
        text = f"{_render(left, wraps, operator)} {operator} {_render(right, wraps, '(')}"
        if parent is not None and (parent == "(" or _kind(parent) != _kind(operator)):
            text = f"({text})"
    if wraps is not None and wraps(st.booleans()):
        return f"({text})"
    return text


ATOMS = st.one_of(
    st.sampled_from(CAST.ids()),
    st.sampled_from([f"{prefix}*" for prefix in GROUPS.values()]),
    st.sampled_from([f"group:{key}" for key in GROUPS]),
    st.sampled_from([f"tag:{tag}" for tag in TAGS]),
    st.just("all"),
)


def trees() -> st.SearchStrategy[Tree]:
    """Draws expression trees whose leaves are words the fuzz cast knows."""
    return st.recursive(
        ATOMS,
        lambda children: st.tuples(children, st.sampled_from(TREE_OPERATORS), children),
        max_leaves=MAX_TREE_LEAVES,
    )


@given(st.one_of(expressions(), trees().map(lambda tree: _render(tree, None))))
def test_fuzz_iterative_select_matches_the_recursive_reference(expression: str) -> None:
    try:
        picked = select(CAST, expression)
    except E8sError:
        return
    assert picked.ids == reference_select(expression), expression


@given(trees(), st.data())
def test_fuzz_redundant_parentheses_never_change_a_result(tree: Tree, data: st.DataObject) -> None:
    plain = _render(tree, None)
    wrapped = _render(tree, data.draw)
    assert select(CAST, wrapped).ids == select(CAST, plain).ids, (plain, wrapped)


@given(st.one_of(st.text(), st.builds("{}-{}".format, DIGITS, DIGITS)))
def test_fuzz_parse_range(text: str) -> None:
    only_e8s_errors(lambda: parse_range(text), text)


@given(st.lists(st.one_of(st.text(), DIGITS), max_size=20))
def test_fuzz_natural_sort(ids: list[str]) -> None:
    only_e8s_errors(lambda: natural_sort(ids), ids)


@given(st.text())
def test_fuzz_parse_ref(text: str) -> None:
    only_e8s_errors(lambda: parse_ref(text), text)


@given(st.text())
def test_fuzz_parse_tid(text: str) -> None:
    only_e8s_errors(lambda: parse_tid(text), text)
