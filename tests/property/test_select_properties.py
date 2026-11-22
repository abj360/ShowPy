#!/usr/bin/env python3

"""
test_select_properties.py --- checks the selection grammar against the Selection set algebra

Contains:
    test_union_matches_parts(): `A + B` equals the union of A and B
    test_difference_matches_parts(): `A - B` equals A minus B
    test_intersection_matches_parts(): `A & B` equals A intersect B
    test_self_difference_is_empty(): `X - X` selects nothing
    test_parentheses_group_right_operand(): `A - (B + C)` equals A minus (B union C)
    test_results_are_unique_known_ids(): every result is duplicate-free and in the cast
    test_natural_sort_is_a_permutation(): sorting keeps every ID, duplicates included
    test_natural_sort_is_idempotent(): sorting a sorted list changes nothing
"""

from hypothesis import given
from hypothesis import strategies as st

from showpy.cast.models import Cast, Group, Spot
from showpy.cast.select import select
from showpy.core.ids import SpotId
from showpy.core.util import natural_sort

GROUPS = {"trumpets": "T", "tubas": "TB", "flags": "F"}
TAGS = ("soloist", "leader")
SPOTS_PER_GROUP = 6
MAX_SORTED_IDS = 20
ID_LIKE_PATTERN = r"[A-Za-z_-]{0,3}[0-9]{1,12}([a-z][0-9]{1,3})?"


def property_cast() -> Cast:
    groups = {key: Group(key=key, label_prefix=prefix) for key, prefix in GROUPS.items()}
    spots = tuple(
        Spot(
            id=SpotId(f"{prefix}{n}"),
            group=key,
            tags=(TAGS[n % len(TAGS)],) if n % 3 else (),
        )
        for key, prefix in GROUPS.items()
        for n in range(1, SPOTS_PER_GROUP + 1)
    )
    return Cast(groups=groups, spots=spots)


CAST = property_cast()


@st.composite
def ranges(draw: st.DrawFn) -> str:
    prefix = draw(st.sampled_from(sorted(GROUPS.values())))
    first = draw(st.integers(1, SPOTS_PER_GROUP))
    last = draw(st.integers(first, SPOTS_PER_GROUP))
    return f"{prefix}{first}-{prefix}{last}"


atoms = st.one_of(
    st.sampled_from(CAST.ids()),
    st.sampled_from([f"{p}*" for p in GROUPS.values()]),
    st.sampled_from([f"group:{key}" for key in GROUPS]),
    st.sampled_from([f"tag:{tag}" for tag in TAGS]),
    st.just("all"),
    ranges(),
)


@given(atoms, atoms)
def test_union_matches_parts(a: str, b: str) -> None:
    assert select(CAST, f"{a} + {b}").ids == (select(CAST, a) | select(CAST, b)).ids


@given(atoms, atoms)
def test_difference_matches_parts(a: str, b: str) -> None:
    assert select(CAST, f"{a} - {b}").ids == (select(CAST, a) - select(CAST, b)).ids


@given(atoms, atoms)
def test_intersection_matches_parts(a: str, b: str) -> None:
    assert select(CAST, f"{a} & {b}").ids == (select(CAST, a) & select(CAST, b)).ids


@given(atoms)
def test_self_difference_is_empty(x: str) -> None:
    assert select(CAST, f"{x} - {x}").ids == ()


@given(atoms, atoms, atoms)
def test_parentheses_group_right_operand(a: str, b: str, c: str) -> None:
    expected = select(CAST, a) - (select(CAST, b) | select(CAST, c))
    assert set(select(CAST, f"{a} - ({b} + {c})").ids) == set(expected.ids)


@given(st.lists(atoms, min_size=1, max_size=6), st.lists(st.sampled_from("+-&"), max_size=5))
def test_results_are_unique_known_ids(words: list[str], operators: list[str]) -> None:
    expr = words[0]
    for operator, word in zip(operators, words[1:], strict=False):
        expr = f"({expr}) {operator} {word}"
    result = select(CAST, expr).ids
    assert len(result) == len(set(result))
    assert set(result) <= set(CAST.ids())


SORTABLE_IDS = st.lists(
    st.one_of(st.text(), st.from_regex(ID_LIKE_PATTERN, fullmatch=True)),
    max_size=MAX_SORTED_IDS,
)


@given(SORTABLE_IDS)
def test_natural_sort_is_a_permutation(ids: list[str]) -> None:
    assert sorted(natural_sort(ids)) == sorted(ids)


@given(SORTABLE_IDS)
def test_natural_sort_is_idempotent(ids: list[str]) -> None:
    once = natural_sort(ids)
    assert natural_sort(once) == once
