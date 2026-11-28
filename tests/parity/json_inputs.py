#!/usr/bin/env python3

"""
json_inputs.py --- seeded and hand-picked inputs for the canonical JSON and strict JSON cases

Contains:
    FLOAT_SPECIALS, INT_SPECIALS: numbers at the edges of the ECMAScript text and int caps
    TEXT_ALPHABET, RARE_TEXTS: characters that need escapes, and texts the writer refuses
    MUTATIONS, RAW_MUTATIONS: JSON tokens, escapes and bytes that are not UTF-8, for mutants
    HAND_PICKED: texts for every refusal of the strict reader and its order
    Label, Count, Ratio: subclasses of str, int and float
    generated_value(): builds one seeded Python value from every kind canonical_value takes
    nested(): wraps a value in a given number of containers
    mutants(): mutates seeded picks of seed texts
"""

from collections.abc import Callable, Iterator, Sequence

import numpy as np

from tests.parity.draws import power_of_ten, uniform

MAX_DEPTH = 4
INT_CAP = 10**640
FLOAT_SPECIALS = (
    0.0,
    -0.0,
    1e21,
    9.999999999999999e20,
    1e-7,
    9.99e-8,
    5e-324,
    2.0**53,
    1.7976931348623157e308,
    0.1,
    -1.5e-300,
    123456789.125,
    float("nan"),
    float("inf"),
)
INT_SPECIALS = (
    0,
    -1,
    2**53,
    2**63,
    -(2**63) - 1,
    2**64 + 1,
    INT_CAP - 1,
    INT_CAP,
    -INT_CAP,
    10**700,
)
TEXT_ALPHABET = [
    *"abcXYZ019 _-.:/$",
    '"',
    "\\",
    "\x00",
    "\x08",
    "\x1f",
    "\x7f",
    "\n",
    "\u00e9",
    "\u20ac",
    "\u2028",
    "\ufb33",
    "\ufeff",
    "\U0001f600",
]
RARE_TEXTS = ("\ud800", "a\udfffb", "$showpy:map", "$showpy:x", "\ud83d\ude00")
MUTATIONS = [
    *'[]{},:"\\ 0-.eE+\n\t',
    "\\u",
    "\\ud800",
    "\\udc00",
    "\\ud83d\\ude00",
    "\\uZZZZ",
    "\\x",
    "NaN",
    "Infinity",
    "-Infinity",
    "01",
    "1e400",
    "-0",
    "true",
    "nul",
    "\x00",
    "\x1f",
    "\u00e9",
    "\U0001f600",
    "\ufeff",
]
RAW_MUTATIONS = (b"\xff", b"\xc3", b"\xed\xa0\x80", b"\xef\xbb\xbf")
HAND_PICKED = (
    b"",
    b" ",
    b"null",
    b" [1, 2.5, -0, -0.0, 1E5, 1e-400, 1e400] ",
    b'{"a": 1, "a": 2}',
    b'{"x": [{"b": 1, "c": 2, "b": 3}], "x": 1}',
    b'{"a": 1, "b": 2, "b": 3, "a": 4}',
    b'{"a": NaN}',
    b"[Infinity]",
    b"[-Infinity, NaN]",
    b"[-Inf]",
    b"[NaNa]",
    b"[1, 2,]",
    b'{"a": 1,}',
    b'{"a" 1}',
    b"{1: 2}",
    b'["a\\x"]',
    b'["\\u12"]',
    b'["\x01"]',
    b'"unterminated',
    b"[01]",
    b"[1.]",
    b"[1e]",
    b"[1e+]",
    b"[.5]",
    b"[+1]",
    b"[-]",
    b"1 2",
    b'{"a": "\\ud800"}',
    b'{"\\udfff": 1}',
    b'["x\\ud83d"]',
    b'["\\ud83d\\ude00", "\\ud83d\\u0041"]',
    b'{"\\ud800": 1, "\\ud800": 2}',
    b'{"\\ud800": 1, "\\ud801": 2}',
    b'["\\ud800", NaN]',
    b'["\\ud800", 1,]',
    b'\xef\xbb\xbf{"a": 1}',
    b'{"a": "\xff"}',
    b'{"a": 1, "a": "\xff"}',
    '{"a": 1}'.encode("utf-16"),
    b'["\\\\"]',
    b'["\\\\\\"[[["]',
    b'{"a": [1, {"b": []}]}',
    b"[" + b"1" * 4300 + b"]",
    b"[-" + b"1" * 4301 + b"]",
    b"[" + b"1" * 4301 + b", NaN]",
    b"[NaN, " + b"1" * 4301 + b"]",
    b"[" + b"1" * 4301 + b".5]",
    b"[" * 100 + b"]" * 100,
    b"[" * 101 + b"]" * 101,
    b'["' + b"[" * 200 + b'"]',
    b"[" * 5_000 + b"]" * 5_000,
    b"{" * 3,
    b'{"a":' * 101 + b"1" + b"}" * 101,
)


class Label(str):
    """Marks a text as a str subclass, which canonical_value turns into plain text."""

    __slots__ = ()


class Count(int):
    """Marks an int as an int subclass, which canonical_value turns into a plain int."""


class Ratio(float):
    """Marks a float as a float subclass, which canonical_value turns into a plain float."""


def _pick[T](rng: np.random.Generator, options: Sequence[T]) -> T:
    return options[int(rng.integers(0, len(options)))]


def _text(rng: np.random.Generator) -> str:
    if int(rng.integers(0, 40)) == 0:
        return _pick(rng, RARE_TEXTS)
    length = int(rng.integers(0, 9))
    return "".join(_pick(rng, TEXT_ALPHABET) for _ in range(length))


def _float(rng: np.random.Generator) -> float:
    choice = int(rng.integers(0, 4))
    if choice == 0:
        return _pick(rng, FLOAT_SPECIALS)
    if choice == 1:
        bits = rng.integers(0, 2**63, dtype=np.int64)
        return float(np.array([bits], dtype=np.int64).view(np.float64)[0])
    if choice == 2:
        return float(np.round(uniform(rng, -1e4, 1e4), int(rng.integers(0, 8))))
    scale = power_of_ten(int(rng.integers(-30, 30)))
    return float(uniform(rng, -1.0, 1.0)) * scale


def _int(rng: np.random.Generator) -> int:
    choice = int(rng.integers(0, 3))
    if choice == 0:
        return _pick(rng, INT_SPECIALS)
    if choice == 1:
        return int(rng.integers(-(2**62), 2**62))
    digits = "".join(str(int(d)) for d in rng.integers(0, 10, size=int(rng.integers(1, 700))))
    return int(digits) * (-1 if int(rng.integers(0, 2)) else 1)


def _scalar(rng: np.random.Generator) -> object:
    makers: list[Callable[[], object]] = [
        lambda: None,
        lambda: bool(rng.integers(0, 2)),
        lambda: _int(rng),
        lambda: _float(rng),
        lambda: _text(rng),
        lambda: Label(_text(rng)),
        lambda: Count(int(rng.integers(-5, 5))),
        lambda: Ratio(_float(rng)),
        lambda: np.float64(_float(rng)),
        lambda: np.int64(int(rng.integers(-(2**62), 2**62))),
        lambda: np.float32(uniform(rng, -1e4, 1e4)),
        lambda: np.bool_(bool(rng.integers(0, 2))),
    ]
    return makers[int(rng.integers(0, len(makers)))]()


def _key(rng: np.random.Generator) -> object:
    makers: list[Callable[[], object]] = [
        lambda: _text(rng),
        lambda: int(rng.integers(-3, 3)),
        lambda: float(rng.integers(-3, 3)) / 2.0,
        lambda: bool(rng.integers(0, 2)),
        lambda: None,
        lambda: (int(rng.integers(0, 3)), _text(rng)),
        lambda: frozenset({int(rng.integers(0, 3)), _text(rng)}),
    ]
    return makers[int(rng.integers(0, len(makers)))]()


def generated_value(rng: np.random.Generator, depth: int = 0) -> object:
    """Builds one seeded value from every kind canonical_value takes, and a few it refuses.

    Args:
        rng: The seeded generator of the side under test.
        depth: The container nesting so far; containers stop at MAX_DEPTH.

    Returns:
        value: A scalar, numpy value, list, tuple, set, text-keyed or mixed-keyed dict.
    """
    kinds = 8 if depth < MAX_DEPTH else 1
    kind = int(rng.integers(0, kinds + 1))
    if kind == 0 or depth >= MAX_DEPTH:
        return _scalar(rng)
    size = int(rng.integers(0, 5))
    children = [generated_value(rng, depth + 1) for _ in range(size)]
    if kind == 1:
        return children
    if kind == 2:
        return tuple(children)
    if kind == 3:
        return {_text(rng): child for child in children}
    if kind == 4:
        return {_key(rng): child for child in children}
    if kind == 5:
        hashable = {_key(rng) for _ in range(size)} | {_float(rng) for _ in range(size % 2)}
        return frozenset(hashable) if size % 2 else set(hashable)
    if kind == 6:
        return np.array([_float(rng) for _ in range(size)])
    return _scalar(rng)


def nested(depth: int, build: Callable[[object], object]) -> object:
    """Wraps 0 in `depth` containers that `build` makes."""
    value: object = 0
    for _ in range(depth):
        value = build(value)
    return value


def mutants(rng: np.random.Generator, seeds: list[bytes], count: int) -> Iterator[bytes]:
    """Mutates seeded picks of the seed texts one to three times each.

    Args:
        rng: The seeded generator of the side under test.
        seeds: Texts to start from.
        count: How many mutants to yield.

    Returns:
        mutants: Texts with JSON tokens, escapes or bytes that are not UTF-8 inserted or
            swapped in.
    """
    for _ in range(count):
        text = bytearray(seeds[int(rng.integers(0, len(seeds)))])
        for _ in range(int(rng.integers(1, 4))):
            at = int(rng.integers(0, len(text) + 1))
            if int(rng.integers(0, 8)) == 0:
                piece = RAW_MUTATIONS[int(rng.integers(0, len(RAW_MUTATIONS)))]
            else:
                piece = _pick(rng, MUTATIONS).encode("utf-8", "surrogatepass")
            cut = int(rng.integers(0, 3))
            text[at : at + cut] = piece
        yield bytes(text)
