#!/usr/bin/env python3

"""
test_fuzz_svg.py --- fuzzes parse_path with a grammar of SVG path data and junk

Contains:
    PATH_DATA: draws path data from commands, numbers, packed arc flags, separators and junk
    test_fuzz_parse_path_grammar: raises only ShowPyError for grammar-built path data
    test_fuzz_parse_path_round_trips: re-parses the written path data into the same subpaths
    test_fuzz_parse_path_raw_text: raises only ShowPyError for arbitrary text
"""

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import ShowPyError
from showpy.geo.svg import Subpath, parse_path
from tests.helpers import only_showpy_errors

COMMANDS = "MmLlHhVvCcSsQqTtAaZz"
JUNK = "xX#%;()[]{}<>é٣ \t\n"
MAX_TOKENS = 40

signs = st.sampled_from(["", "-", "+"])
plain_numbers = st.from_regex(r"\d{1,4}(\.\d{1,4})?|\.\d{1,4}|\d{1,3}\.", fullmatch=True)
exponents = st.sampled_from(
    ["", "e2", "E-3", "e+1", "e400", "e-400", "e300", "e154", "e80", "e100", "e"]
)
numbers = st.builds(lambda s, n, e: s + n + e, signs, plain_numbers, exponents)
packed_numbers = st.builds(lambda a, b: a + b, numbers, st.from_regex(r"\.\d{1,3}", fullmatch=True))
flags = st.sampled_from(["0", "1", "2", "01", "10", "11", "00", "011", "01.5", "0.5", "1e1"])
separators = st.sampled_from(["", " ", ",", " , ", "  ", "\n"])
junk = st.text(alphabet=JUNK, min_size=1, max_size=3)
commands = st.sampled_from(list(COMMANDS))
arc_arguments = st.lists(st.one_of(numbers, flags), min_size=0, max_size=9)
tokens = st.one_of(
    commands,
    numbers,
    packed_numbers,
    flags,
    junk,
    st.builds(lambda c, a: c + " ".join(a), st.sampled_from("Aa"), arc_arguments),
)
PATH_DATA = st.lists(st.tuples(tokens, separators), max_size=MAX_TOKENS).map(
    lambda parts: "".join(token + separator for token, separator in parts)
)


@given(PATH_DATA)
def test_fuzz_parse_path_grammar(d: str) -> None:
    """Raises only ShowPyError for path data built from the SVG grammar and junk."""
    only_showpy_errors(lambda: parse_path(d), d)


coordinates = st.integers(-50, 50).map(str)
arc_flags = st.sampled_from(["0", "1"])


def _arguments(command: str) -> st.SearchStrategy[str]:
    if command in "Aa":
        parts = [coordinates, coordinates, coordinates, arc_flags, arc_flags, coordinates]
        return st.tuples(*parts, coordinates).map(" ".join)
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "Z": 0}[
        command.upper()
    ]
    return st.lists(coordinates, min_size=arity, max_size=arity).map(" ".join)


VALID_SEGMENT = st.sampled_from(list(COMMANDS)).flatmap(
    lambda command: _arguments(command).map(lambda args: f"{command}{args}")
)
VALID_PATH_DATA = st.builds(
    lambda start, rest: "M" + start + " " + " ".join(rest),
    _arguments("M"),
    st.lists(VALID_SEGMENT, max_size=MAX_TOKENS),
)


def _reparse(d: str) -> tuple[list[Subpath], list[Subpath]] | None:
    try:
        first = parse_path(d)
    except ShowPyError:
        return None
    return first, parse_path(" ".join(subpath.to_d() for subpath in first))


@given(VALID_PATH_DATA)
def test_fuzz_parse_path_round_trips(d: str) -> None:
    """Re-parses the absolute path data of every accepted path into the same subpaths."""
    result = _reparse(d)
    assert result is not None, d
    first, second = result
    assert len(second) == len(first), d
    for one, two in zip(first, second, strict=True):
        assert one.closed == two.closed, d
        assert [type(p) for p in one.pieces] == [type(p) for p in two.pieces], d
        assert np.allclose(_end_points(one), _end_points(two), rtol=1e-9, atol=1e-9), d


def _end_points(subpath: Subpath) -> list[tuple[float, float]]:
    return [subpath.start, *(piece.end for piece in subpath.pieces)]


@given(st.text(max_size=200))
def test_fuzz_parse_path_raw_text(d: str) -> None:
    """Raises only ShowPyError for arbitrary text."""
    only_showpy_errors(lambda: parse_path(d), d)
