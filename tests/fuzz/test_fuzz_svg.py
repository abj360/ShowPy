#!/usr/bin/env python3

"""
test_fuzz_svg.py --- fuzzes parse_path with a grammar of SVG path data and junk

Contains:
    PATH_DATA: draws path data from commands, numbers, packed arc flags, separators and junk
    test_fuzz_parse_path_grammar: raises only E8sError for grammar-built path data
    test_fuzz_parse_path_notations_agree: checks packed or implicit notation equals plain text
    test_fuzz_parse_path_round_trips: re-parses the written path data into the same pieces
    test_fuzz_parse_path_raw_text: raises only E8sError for arbitrary text
"""

import itertools

from hypothesis import given
from hypothesis import strategies as st

from e8s.core.util import approx_equal
from e8s.geo.svg import Subpath, parse_path
from tests.helpers import only_e8s_errors

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
    """Raises only E8sError for path data built from the SVG grammar and junk."""
    only_e8s_errors(lambda: parse_path(d), d)


type Token = tuple[str, str]
type Segment = tuple[str, list[list[str]]]

ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
FLAG_SLOTS = (3, 4)
MAX_REPEATS = 3
MAX_SEGMENTS = 12
GAP_TEXTS = [" ", ",", " , ", "\n", "\t"]
GEOMETRY_ABS_TOL = 1e-6
GEOMETRY_REL_TOL = 1e-9

coordinate_texts = st.one_of(
    st.integers(-50, 50).map(str),
    st.from_regex(r"-?[0-9]{1,2}\.[0-9]{1,3}|-?\.[0-9]{1,3}|-?[0-9]{1,2}\.", fullmatch=True),
    st.sampled_from(["1e1", "-2E-1", "3e+0", ".5e1", "+4"]),
)
flag_texts = st.sampled_from(["0", "1"])


@st.composite
def segments(draw: st.DrawFn) -> Segment:
    """Draws one command letter with one or more argument groups of the right arity."""
    letter = draw(st.sampled_from(COMMANDS))
    arity = ARITY[letter.upper()]
    if arity == 0:
        return letter, []
    group = st.tuples(*(flag_texts if i in FLAG_SLOTS else coordinate_texts for i in range(arity)))
    groups = draw(st.lists(group.map(list), min_size=1, max_size=MAX_REPEATS))
    return letter, groups


path_segments = st.tuples(st.sampled_from("Mm"), st.lists(segments(), max_size=MAX_SEGMENTS)).map(
    lambda parts: [(parts[0], [["0", "0"]]), *parts[1]]
)


def _implicit_letter(letter: str) -> str:
    return {"M": "L", "m": "l"}.get(letter, letter)


def _canonical_text(path: list[Segment]) -> str:
    words: list[str] = []
    for letter, groups in path:
        if not groups:
            words.append(letter)
        for index, group in enumerate(groups):
            words.append(letter if index == 0 else _implicit_letter(letter))
            words.extend(group)
    return " ".join(words)


def _tokens(path: list[Segment]) -> list[Token]:
    tokens: list[Token] = []
    for letter, groups in path:
        tokens.append((letter, "letter"))
        for group in groups:
            for slot, text in enumerate(group):
                is_flag = letter in "Aa" and slot in FLAG_SLOTS
                tokens.append((text, "flag" if is_flag else "number"))
    return tokens


def _may_pack(previous: Token, following: Token) -> bool:
    (before, before_kind), (after, after_kind) = previous, following
    if "letter" in (before_kind, after_kind) or before_kind == "flag":
        return True
    if after[0] in "+-":
        return True
    return after[0] == "." and ("." in before or "e" in before.lower())


@st.composite
def varied_texts(draw: st.DrawFn, path: list[Segment]) -> str:
    """Writes the path with one letter per run, packed flags and glued numbers where legal."""
    tokens = _tokens(path)
    pieces = [tokens[0][0]]
    for previous, following in itertools.pairwise(tokens):
        gaps = [*GAP_TEXTS, ""] if _may_pack(previous, following) else GAP_TEXTS
        pieces.append(draw(st.sampled_from(gaps)) + following[0])
    return "".join(pieces)


def _flatten(piece: object) -> list[float]:
    values: list[float] = []
    for name in vars(piece):
        value = getattr(piece, name)
        values.extend(value if isinstance(value, tuple) else [value])
    return values


def _assert_same_geometry(first: list[Subpath], second: list[Subpath], sample: str) -> None:
    assert len(first) == len(second), sample
    for one, two in zip(first, second, strict=True):
        assert one.closed == two.closed, sample
        assert approx_equal(
            one.start, two.start, abs_tol=GEOMETRY_ABS_TOL, rel_tol=GEOMETRY_REL_TOL
        ), sample
        assert [type(p) for p in one.pieces] == [type(p) for p in two.pieces], sample
        for left, right in zip(one.pieces, two.pieces, strict=True):
            assert approx_equal(
                _flatten(left), _flatten(right), abs_tol=GEOMETRY_ABS_TOL, rel_tol=GEOMETRY_REL_TOL
            ), sample


@given(path_segments.flatmap(lambda path: st.tuples(st.just(path), varied_texts(path))))
def test_fuzz_parse_path_notations_agree(case: tuple[list[Segment], str]) -> None:
    """Parses packed flags, implicit repeats and glued numbers to the same pieces as plain text."""
    path, varied = case
    _assert_same_geometry(parse_path(_canonical_text(path)), parse_path(varied), varied)


@given(path_segments.flatmap(varied_texts))
def test_fuzz_parse_path_round_trips(d: str) -> None:
    """Re-parses the absolute path data of every accepted path into the same pieces."""
    first = parse_path(d)
    second = parse_path(" ".join(subpath.to_d() for subpath in first))
    _assert_same_geometry(first, second, d)


@given(st.text(max_size=200))
def test_fuzz_parse_path_raw_text(d: str) -> None:
    """Raises only E8sError for arbitrary text."""
    only_e8s_errors(lambda: parse_path(d), d)
