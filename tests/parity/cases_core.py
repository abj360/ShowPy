#!/usr/bin/env python3

"""
cases_core.py --- parity cases for the core area: number text, unit helpers and ID parsing

Inputs come from `e8s.seeded_rng(SEED)` on the side under test, so the oracle and the working
tree build the same values. Each input list is built once per process, so a timed run measures
the functions under test and not the draw. Text outputs are compared byte for byte; `val:` and
`deg:` keys carry the results that go through the C math library.

Contains:
    describe(): runs one call and records its result, or the error it raised
    plain_decimal_floats(): builds the floats whose text the million-float case writes
    plain_decimal_text_on_a_million_floats(): writes each float with a seeded decimals count
    plain_decimal_text_on_20000_floats(): the timed share of the same writer
    unit_conversions_on_seeded_values(): angles, facings, rounding and size caps, with floats
        halfway between two shortest digit strings
    unit_conversions_timed(): the timed share of the unit helpers
    reference_and_transition_corpus(): the ID, reference and transition parsers on text
    reference_and_transition_corpus_timed(): the timed share of the parsers, on mostly valid text
    reference_and_transition_corpus_malformed_timed(): the timed share of the parsers' error path
    non_finite_inputs_raise_e8s_errors(): records the error class of inputs v0.4.0 mishandled
    CASES: the core area's cases, with the canonical JSON and strict JSON cases of core_json and
        the utility cases of core_util
"""

import hashlib
import math
from collections.abc import Callable, Mapping
from decimal import Decimal
from functools import cache
from types import ModuleType
from typing import cast

import numpy as np
from pydantic import JsonValue

from tests.parity.cases import Case, Output
from tests.parity.core_json import JSON_CASES
from tests.parity.core_util import UTIL_CASES

SEED = 20261124
MILLION = 1_000_000
CHUNK = 100_000
TIMED_FLOATS = 20_000
TIMED_VALUES = 20_000
CORPUS_STRINGS = 20_000
TIMED_STRINGS = 3_000
HOSTILE_SHARE = 10
PREFIXES = ("T", "S", "form", "route", "set_")
MAX_DECIMALS = 17
TIE_DRAWS = 40_000
TIE_MIN_BITS = 20
TIE_MAX_BITS = 54
TIE_MAX_SHIFT = 40
TIE_MIN_DIGITS = 16
TINY_STEP = 1e-300
DIGIT_TIES = ((165.035919189453125, 2e-14), (-649435869553220.25, 0.2))
SPECIAL_FLOATS = (0.0, -0.0, 1e308, -1e308, 1.7976931348623157e308, 5e-324, -5e-324, 1e-308)
STEPS = (0.01, 0.25, 0.5, 1.0, 5.0, 1e-3, 0.0625, 3.0, 0.1, 1e-7, 2.54, 1e5, 0.3, 7.0)
UNIT_SCALES = (1.0, 10.0, 360.0, 1e4, 1e8)
ALPHABET = [
    *"abcXYZ019_.-@:/ \t\n\x1c\u2192>*+",
    "\u00e9",
    "\u0663",
    "\u212a",
    "\u017f",
    "\u00a0",
    "\u2003",
    "\u01c6",
]
WORDS = (
    "sheet",
    "gym",
    "con",
    "COM1",
    "all",
    "props",
    "T1",
    "a",
    "x" * 64,
    "x" * 65,
    "1.0.0+build.5",
    "1..2",
    "..",
    "cons.5",
    "aux.txt",
    "hs-field",
    "-a",
    "a-",
    "prop\u017f",
    "\u01c5",
    "é",
)


def describe(function: Callable[..., object], *args: object, **kwargs: object) -> JsonValue:
    """Runs one call and records its JSON-ready result, or the error it raised.

    Args:
        function: The function to call.
        args: Positional arguments of the call.
        kwargs: Keyword arguments of the call.

    Returns:
        outcome: `{"ok": result}`, or the error's class, code, message, hint and context; a
            foreign exception records only its class name.
    """
    try:
        return {"ok": cast("JsonValue", function(*args, **kwargs))}
    except Exception as error:
        to_dict = getattr(error, "to_dict", None)
        if callable(to_dict):
            return {"error": cast("JsonValue", to_dict())}
        return {"raised": type(error).__name__}


@cache
def plain_decimal_floats(e8s: ModuleType, count: int) -> np.ndarray:
    """Draws floats from every magnitude, with ties, subnormals and the extremes.

    Args:
        e8s: The e8s module under test.
        count: How many floats to draw, at least 1,000.

    Returns:
        floats: The floats, finite only, in a fixed order.
    """
    rng = e8s.seeded_rng(SEED)
    share = count // 10
    bits = rng.integers(0, 2**64, size=share * 3, dtype=np.uint64).view(np.float64)
    uniform = rng.uniform(-1e6, 1e6, size=share * 3)
    places = rng.integers(0, 9, size=share)
    short = np.round(rng.uniform(-1e4, 1e4, size=share), 3) / 10.0**places
    ties = (rng.integers(-(2**20), 2**20, size=share) + 0.5) / 2.0 ** rng.integers(
        0, 12, size=share
    )
    tiny = 10.0 ** rng.uniform(-324.0, -290.0, size=share)
    pool = np.concatenate([bits, uniform, short, ties, tiny])
    shuffled = rng.permutation(pool[np.isfinite(pool)])
    return np.concatenate([np.array(SPECIAL_FLOATS), shuffled])


def _write_all(e8s: ModuleType, floats: np.ndarray, decimals: np.ndarray) -> list[str]:
    format_number = e8s.format_number
    return [
        format_number(float(value), max_decimals=int(places))
        for value, places in zip(floats, decimals, strict=True)
    ]


def plain_decimal_text_on_a_million_floats(e8s: ModuleType) -> Mapping[str, Output]:
    """Writes about a million floats, each with a seeded decimals count, and hashes the text.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: `count` and the SHA-256 of each chunk of 100,000 texts.
    """
    floats = plain_decimal_floats(e8s, MILLION + MILLION // 5)[:MILLION]
    decimals = e8s.seeded_rng(SEED + 1).integers(0, MAX_DECIMALS + 1, size=len(floats))
    digests: list[JsonValue] = []
    for start in range(0, len(floats), CHUNK):
        texts = _write_all(e8s, floats[start : start + CHUNK], decimals[start : start + CHUNK])
        digests.append(hashlib.sha256("\n".join(texts).encode()).hexdigest())
    return {"count": len(floats), "chunks": digests}


@cache
def _timed_floats(e8s: ModuleType) -> list[float]:
    meters = e8s.seeded_rng(SEED + 6).uniform(-500.0, 500.0, size=TIMED_FLOATS)
    half = TIMED_FLOATS // 2
    return [float(value) for value in meters[:half]] + [
        float(np.round(value, 3)) for value in meters[half:]
    ]


def plain_decimal_text_on_20000_floats(e8s: ModuleType) -> Mapping[str, Output]:
    """Writes 20,000 stage coordinates with the default decimals; the timed share of the writer.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The SHA-256 of the texts.
    """
    texts = [e8s.format_number(value) for value in _timed_floats(e8s)]
    return {"digest": hashlib.sha256("\n".join(texts).encode()).hexdigest()}


@cache
def _seeded_angles(e8s: ModuleType, count: int) -> np.ndarray:
    rng = e8s.seeded_rng(SEED + 2)
    scale = rng.choice(UNIT_SCALES, size=count)
    return np.concatenate(
        [rng.uniform(-1.0, 1.0, size=count) * scale, np.array([0.0, -0.0, 360.0])]
    )


def _rounded(e8s: ModuleType, values: np.ndarray, steps: np.ndarray) -> list[float]:
    return [e8s.round_to(float(v), float(s)) for v, s in zip(values, steps, strict=True)]


def unit_conversions_on_seeded_values(e8s: ModuleType) -> Mapping[str, Output]:
    """Runs the angle, facing, rounding and size helpers on seeded values and odd inputs.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: `val:` and `deg:` arrays for the helpers that use the math library, exact
            arrays for the rest, and the recorded outcome of each odd input.
    """
    angles = _seeded_angles(e8s, TIMED_VALUES)
    others = np.roll(angles, 7)
    rng = e8s.seeded_rng(SEED + 3)
    values = rng.uniform(-1e4, 1e4, size=TIMED_VALUES) * rng.choice(UNIT_SCALES, size=TIMED_VALUES)
    steps = rng.choice(np.array(STEPS), size=TIMED_VALUES)
    vectors = rng.uniform(-3.0, 3.0, size=(TIMED_VALUES, 2))
    return {
        "normalized": np.array([e8s.normalize_angle(float(a)) for a in angles]),
        "diff": np.array(
            [e8s.angle_diff(float(a), float(b)) for a, b in zip(angles, others, strict=True)]
        ),
        "val:facing": np.array([e8s.facing_vector(float(a)) for a in angles]),
        "deg:vector_facing": np.array(
            [e8s.vector_facing((float(x), float(y))) for x, y in vectors]
        ),
        "rounded": np.array(_rounded(e8s, values, steps)),
        "rounded_ties": np.array([e8s.round_to(v, s) for v, s in _digit_ties(e8s)]),
        "tie_errors": [describe(e8s.round_to, v, TINY_STEP) for v, _ in _digit_ties(e8s)[:200]],
        "odd": odd_inputs(e8s),
    }


def _significant(text: str) -> str:
    return text.replace("-", "").split("e")[0].replace(".", "").strip("0")


@cache
def _digit_ties(e8s: ModuleType) -> list[tuple[float, float]]:
    """Finds floats k * 2**-n that lie exactly halfway between two shortest digit strings, each
    with a step at its last repr digit and one a digit before.

    Args:
        e8s: The e8s module under test.

    Returns:
        ties: (value, step) pairs, the hand-picked ones first.
    """
    rng = e8s.seeded_rng(SEED + 7)
    widths = rng.integers(TIE_MIN_BITS, TIE_MAX_BITS, size=TIE_DRAWS)
    numerators = [int(rng.integers(2 ** (w - 1), 2**w)) | 1 for w in widths]
    shifts = rng.integers(1, TIE_MAX_SHIFT, size=TIE_DRAWS)
    ties: list[tuple[float, float]] = list(DIGIT_TIES)
    for numerator, shift in zip(numerators, shifts, strict=True):
        value = math.ldexp(float(numerator), -int(shift))
        exact = _significant(format(Decimal(value), "f"))
        shown = _significant(repr(value))
        if len(shown) < TIE_MIN_DIGITS or len(exact) != len(shown) + 1 or exact[-1] != "5":
            continue
        place = math.floor(math.log10(value)) - len(shown) + 1
        for step in (10.0**place, 2.0 * 10.0**place, 10.0 ** (place + 1)):
            ties += [(value, step), (-value, step)]
    return ties


def odd_inputs(e8s: ModuleType) -> list[JsonValue]:
    """Records how the helpers answer bad numbers, wrong types, huge sizes and zero vectors.

    Args:
        e8s: The e8s module under test.

    Returns:
        outcomes: One `describe` record per call, in a fixed order.
    """
    bad = [math.nan, math.inf, -math.inf, True, None, "1.0", 5, np.float64(2.5), np.float32(0.5)]
    out: list[JsonValue] = []
    for value in bad:
        out += [
            describe(e8s.format_number, value),
            describe(e8s.format_number, value, max_decimals=2),
            describe(e8s.normalize_angle, value),
            describe(lambda v: float(e8s.round_to(v)), value),
        ]
    out += [describe(e8s.format_number, 1.5, max_decimals=n) for n in (-1, 0, 17, 18)]
    out += [describe(e8s.round_to, 1.0, s) for s in (0.0, -0.01, math.nan, math.inf, 1e-300)]
    out += [describe(e8s.round_to, v, 0.01) for v in (1e300, 1e30, 1e27, 1e26, 9.99e25, -1e300)]
    out += [describe(e8s.round_to, v, 1e-300) for v in (1e-299, 1.0, 7e-300, 1e300)]
    out += [describe(e8s.angle_diff, math.nan, 1.0), describe(e8s.angle_diff, 1e308, -1e308)]
    zero_vectors = ((0.0, 0.0), (1e-13, 0.0), (1e-11, 0.0), (math.nan, 1.0), (math.inf, 0.0))
    out += [describe(e8s.vector_facing, v) for v in zero_vectors]
    sizes = (0, 5, 1_000_000, 1_000_001, -1, -(10**30), 10**30)
    out += [describe(e8s.require_size, r, 1_000_000, what="points") for r in sizes]
    out += [describe(e8s.require_size, 5, 1, what="ids", error=e8s.SpacingError)]
    return out


def unit_conversions_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Runs each unit helper on 20,000 values; the timed share of the helpers.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: One checksum per helper.
    """
    angles = _seeded_angles(e8s, TIMED_VALUES)
    return {
        "val:normalized": np.array([sum(map(e8s.normalize_angle, map(float, angles)))]),
        "val:rounded": np.array([sum(e8s.round_to(float(a) * 3.7) for a in angles)]),
        "val:facing": np.array([sum(e8s.facing_vector(float(a))[0] for a in angles)]),
    }


@cache
def _corpus(e8s: ModuleType, count: int) -> list[str]:
    rng = e8s.seeded_rng(SEED + 4)
    picks = rng.integers(0, len(ALPHABET), size=(count, 12))
    lengths = rng.integers(0, 13, size=count)
    texts = ["".join(ALPHABET[i] for i in row[:n]) for row, n in zip(picks, lengths, strict=True)]
    joined = [
        f"{kind}:{word}@{other}"
        for kind in ("sheet", "\u00e9")
        for word in WORDS
        for other in WORDS
    ]
    return [*WORDS, *joined, *texts]


def _parse_all(e8s: ModuleType, texts: list[str]) -> dict[str, Output]:
    return {
        "parse_ref": [describe(e8s.parse_ref, t) for t in texts],
        "parse_tid": [describe(e8s.parse_tid, t) for t in texts],
        "check_id": [describe(e8s.check_id, t, kind="spot") for t in texts],
        "check_sheet_id": [describe(e8s.check_sheet_id, t, kind="guide") for t in texts],
        "is_device_name": [e8s.is_device_name(t) for t in texts],
    }


def reference_and_transition_corpus(e8s: ModuleType) -> Mapping[str, Output]:
    """Runs the ID, reference and transition parsers on seeded text and hand-picked words.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Each parser's recorded outcome for every text, and the case clashes.
    """
    texts = _corpus(e8s, CORPUS_STRINGS)
    outputs = _parse_all(e8s, texts)
    pairs = [(k, w, v) for k in ("sheet", "x y") for w in WORDS for v in (None, "1", "a b", "..")]
    outputs["format_ref"] = [describe(e8s.format_ref, *p) for p in pairs]
    tids: tuple[object, ...] = (("1", "2"), ("a",), ("1", "2", "3"), [])
    outputs["format_tid"] = [describe(e8s.format_tid, t) for t in tids]
    outputs["clashes"] = [e8s.find_case_clashes(texts[i : i + 40]) for i in range(0, 4000, 40)]
    return outputs


@cache
def _realistic_texts(e8s: ModuleType, count: int) -> dict[str, list[str]]:
    rng = e8s.seeded_rng(SEED + 5)
    numbers = [int(n) for n in rng.integers(0, 5000, size=count)]
    prefixes = [PREFIXES[int(w)] for w in rng.integers(0, len(PREFIXES), size=count)]
    hostile = _corpus(e8s, count)[len(WORDS) :][: count // HOSTILE_SHARE]
    ids = [f"{prefix}{n}" for prefix, n in zip(prefixes, numbers, strict=True)]
    refs = [
        f"sheet:{prefix}-{n % 9}@{n % 4}.{n % 7}"
        for prefix, n in zip(prefixes, numbers, strict=True)
    ]
    tids = [f"{n}->{n + 1}" for n in numbers]
    return {"ids": [*ids, *hostile], "refs": [*refs, *hostile], "tids": [*tids, *hostile]}


def reference_and_transition_corpus_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Parses what a show load parses: 3,000 mostly valid IDs, references and transitions.

    A show load checks every ID and reference it reads and almost none is malformed, so a tenth
    of each list is the hostile corpus's text and the rest is valid. Building an error object is
    a Python class call of about 2 microseconds in both trees, so a list of only malformed text
    would mostly time that call; the parity case above checks every error's fields instead.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Each parser's recorded outcome for every text.
    """
    texts = _realistic_texts(e8s, TIMED_STRINGS)
    return {
        "check_id": [describe(e8s.check_id, t, kind="spot") for t in texts["ids"]],
        "check_sheet_id": [describe(e8s.check_sheet_id, t, kind="guide") for t in texts["ids"]],
        "is_device_name": [e8s.is_device_name(t) for t in texts["ids"]],
        "parse_ref": [describe(e8s.parse_ref, t) for t in texts["refs"]],
        "parse_tid": [describe(e8s.parse_tid, t) for t in texts["tids"]],
    }


def reference_and_transition_corpus_malformed_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Parses 3,000 malformed texts; the timed share of the parsers' error path.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Each parser's recorded outcome for every text.
    """
    return _parse_all(e8s, _corpus(e8s, TIMED_STRINGS)[len(WORDS) :][:TIMED_STRINGS])


def non_finite_inputs_raise_e8s_errors(e8s: ModuleType) -> Mapping[str, Output]:
    """Records the error class and code of facing_vector on non-finite input and of format_number
    on an int too large for a float.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Per call, the exception class name and its code, or the foreign class name.
    """

    def outcome(function: Callable[..., object], *args: object) -> JsonValue:
        try:
            function(*args)
        except Exception as error:
            code = getattr(error, "code", None)
            return {"error": type(error).__name__, "code": None if code is None else str(code)}
        return {"error": None, "code": None}

    return {
        "facing_vector": [outcome(e8s.facing_vector, v) for v in (math.inf, -math.inf, math.nan)],
        "format_number": [outcome(e8s.format_number, 10**400)],
    }


CASES = (
    Case(
        area="core",
        name="plain_decimal_text_on_a_million_floats",
        run=plain_decimal_text_on_a_million_floats,
    ),
    Case(
        area="core",
        name="plain_decimal_text_on_20000_floats",
        run=plain_decimal_text_on_20000_floats,
        timed=True,
    ),
    Case(
        area="core",
        name="unit_conversions_on_seeded_values",
        run=unit_conversions_on_seeded_values,
    ),
    Case(area="core", name="unit_conversions_timed", run=unit_conversions_timed, timed=True),
    Case(
        area="core",
        name="reference_and_transition_corpus",
        run=reference_and_transition_corpus,
    ),
    Case(
        area="core",
        name="reference_and_transition_corpus_timed",
        run=reference_and_transition_corpus_timed,
        timed=True,
    ),
    Case(
        area="core",
        name="reference_and_transition_corpus_malformed_timed",
        run=reference_and_transition_corpus_malformed_timed,
        timed=True,
    ),
    Case(
        area="core",
        name="non_finite_inputs_raise_e8s_errors",
        run=non_finite_inputs_raise_e8s_errors,
    ),
    *JSON_CASES,
    *UTIL_CASES,
)
