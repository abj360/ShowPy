#!/usr/bin/env python3

"""
test_fuzz_svg.py --- fuzzes parse_path with a grammar of SVG path data and junk

Contains:
    PATH_DATA: draws path data from commands, numbers, packed arc flags, separators and junk
    test_fuzz_parse_path_grammar: raises only ShowPyError for grammar-built path data
    test_fuzz_parse_path_raw_text: raises only ShowPyError for arbitrary text
"""

from hypothesis import given
from hypothesis import strategies as st

from showpy.geo.svg import parse_path
from tests.helpers import only_showpy_errors

COMMANDS = "MmLlHhVvCcSsQqTtAaZz"
JUNK = "xX#%;()[]{}<>é٣ \t\n"
MAX_TOKENS = 40

signs = st.sampled_from(["", "-", "+"])
plain_numbers = st.from_regex(r"\d{1,4}(\.\d{1,4})?|\.\d{1,4}|\d{1,3}\.", fullmatch=True)
exponents = st.sampled_from(["", "e2", "E-3", "e+1", "e400", "e-400", "e300", "e154", "e"])
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


@given(st.text(max_size=200))
def test_fuzz_parse_path_raw_text(d: str) -> None:
    """Raises only ShowPyError for arbitrary text."""
    only_showpy_errors(lambda: parse_path(d), d)
