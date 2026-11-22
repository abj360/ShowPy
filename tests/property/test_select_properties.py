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
    test_format_ranges_round_trips_through_parse_range(): every run of format_ranges expands back
        to the sorted unique IDs it came from
    test_mirror_pairs_is_a_shortest_maximum_matching(): pairs as many spots as SciPy's maximum
        matching on the tolerance graph, each once, within tolerance, at the least total distance
"""

import math

import numpy as np
from hypothesis import example, given
from hypothesis import strategies as st
from scipy.sparse import csr_array
from scipy.sparse.csgraph import maximum_bipartite_matching

from showpy.cast.models import Cast, Group, Spot
from showpy.cast.select import mirror_pairs, select
from showpy.choreo.models import Placement, Set
from showpy.core.ids import SetId, SpotId
from showpy.core.util import format_ranges, natural_sort, parse_range

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


RANGE_PREFIXES = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,5}-?", fullmatch=True)
RANGE_NUMBERS = st.integers(min_value=0, max_value=120).flatmap(
    lambda n: st.sampled_from([str(n), f"{n:02d}", f"{n:03d}"])
)


@st.composite
def range_ids(draw: st.DrawFn) -> list[str]:
    """Draws unique IDs of one or more prefix-plus-number shapes, in natural order.

    The domain is IDs a prefix of letters, digits and underscores (optionally ending in `-`)
    followed by a padded or unpadded ASCII number; format_ranges is defined for these.
    """
    prefixes = draw(st.lists(RANGE_PREFIXES, min_size=1, max_size=3, unique=True))
    ids = draw(
        st.sets(
            st.builds(
                lambda prefix, number: prefix + number, st.sampled_from(prefixes), RANGE_NUMBERS
            ),
            max_size=40,
        )
    )
    return natural_sort(ids)


@given(range_ids())
def test_format_ranges_round_trips_through_parse_range(ids: list[str]) -> None:
    runs = format_ranges(ids).split(", ") if ids else []
    assert [spot_id for run in runs for spot_id in parse_range(run)] == ids


GRID_STEP_M = 0.04
GRID_CELLS = 4
MAX_SIDE_SPOTS = 5
MIRROR_TOLERANCES_M = (0.0, 0.04, 0.06, 0.09)
TOTAL_SLACK_M = 1e-8

grid_points = st.tuples(st.integers(1, GRID_CELLS), st.integers(0, GRID_CELLS - 1))
side_spots = st.lists(grid_points, max_size=MAX_SIDE_SPOTS)


def mirror_distance(left: Placement, right: Placement) -> float:
    return math.hypot(-left.x - right.x, left.y - right.y)


def shortest_maximum(
    lefts: list[Placement], rights: list[Placement], tol: float
) -> tuple[int, float]:
    """Finds the most pairs within tol and their least total distance by brute force."""
    best = (0, 0.0)

    def extend(index: int, used: frozenset[int], count: int, total: float) -> None:
        nonlocal best
        if index == len(lefts):
            best = min(best, (count, total), key=lambda option: (-option[0], option[1]))
            return
        extend(index + 1, used, count, total)
        for j, right in enumerate(rights):
            gap = mirror_distance(lefts[index], right)
            if j not in used and gap <= tol:
                extend(index + 1, used | {j}, count + 1, total + gap)

    extend(0, frozenset(), 0, 0.0)
    return best


def scipy_matching_size(lefts: list[Placement], rights: list[Placement], tol: float) -> int:
    edges = np.array(
        [[mirror_distance(a, b) <= tol for b in rights] for a in lefts], dtype=np.int8
    ).reshape(len(lefts), len(rights))
    matched = maximum_bipartite_matching(csr_array(edges), perm_type="column")
    return int(np.count_nonzero(matched >= 0))


@given(side_spots, side_spots, st.sampled_from(MIRROR_TOLERANCES_M))
@example(left_cells=[(1, 0), (1, 1)], right_cells=[(1, 1), (1, 2)], tol=0.0)
@example(left_cells=[(1, 0), (1, 1)], right_cells=[(1, 1), (1, 3)], tol=0.04)
def test_mirror_pairs_is_a_shortest_maximum_matching(
    left_cells: list[tuple[int, int]], right_cells: list[tuple[int, int]], tol: float
) -> None:
    lefts = [
        Placement(entity=SpotId(f"L{i}"), x=-cx * GRID_STEP_M, y=cy * GRID_STEP_M)
        for i, (cx, cy) in enumerate(left_cells)
    ]
    rights = [
        Placement(entity=SpotId(f"R{i}"), x=cx * GRID_STEP_M, y=cy * GRID_STEP_M)
        for i, (cx, cy) in enumerate(right_cells)
    ]
    set_ = Set(id=SetId("1"), counts=0.0, placements=(*lefts, *rights))
    pairs = mirror_pairs(set_, "all", tol=tol)
    by_id = {p.entity: p for p in (*lefts, *rights)}
    gaps = [mirror_distance(by_id[a], by_id[b]) for a, b in pairs]
    count, total = shortest_maximum(lefts, rights, tol)
    assert len(pairs) == count == scipy_matching_size(lefts, rights, tol)
    assert all(gap <= tol for gap in gaps)
    assert len({a for a, _ in pairs}) == len({b for _, b in pairs}) == len(pairs)
    assert all(a.startswith("L") and b.startswith("R") for a, b in pairs)
    assert [a for a, _ in pairs] == sorted((a for a, _ in pairs), key=lambda a: int(a[1:]))
    assert sum(gaps) <= total + TOTAL_SLACK_M
