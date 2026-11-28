#!/usr/bin/env python3

"""
core_util.py --- parity cases for float comparison, noise ranks, count chunks, ID ranges and
natural sorting

Inputs come from `e8s.seeded_rng` on the side under test plus hand-picked hostile text, so both
sides see the same values. Floats are compared bit for bit as arrays; every other result and
every error, field by field through its `to_dict`, as JSON.

Contains:
    record(): runs one call and records its JSON-ready result, or the error it raised
    float_comparison_on_seeded_values(): approx_equal, noise_rank and chunk_counts on seeded,
        extreme and malformed inputs
    float_comparison_timed(): the timed share of the float helpers
    id_ranges_on_seeded_text(): split_id, parse_range, format_ranges, natural_key and
        natural_sort on seeded and hostile text
    id_ranges_timed(): the timed share of the ID helpers, on what a cast and a selection hold
    UTIL_CASES: the cases above
"""

import hashlib
import importlib
import math
from collections.abc import Callable, Mapping
from functools import cache
from types import ModuleType

import numpy as np
from pydantic import JsonValue

from tests.parity.cases import Case, Output
from tests.parity.draws import powers_of_ten, uniform

SEED = 20261127
PAIRS = 20_000
TIMED_PAIRS = 20_000
SHORT_LIST = 50
TOLERANCES = ((1e-6, 0.0), (0.0, 1e-9), (1e-9, 1e-6), (0.0, 0.0), (1e-3, 1e-3))
NOISE = (0.0, 1e-12, 1e-9, 1e-7, 1e-6, 2e-6, 1e-5, 1e-3)
SPECIALS = (0.0, -0.0, math.inf, -math.inf, math.nan, 1e308, -1e308, 5e-324, 1.7976931348623157e308)
DYADIC_DENOMINATOR = 1024
PREFIXES = ("T", "S", "flag-", "P1x", "set_", "", "\u00e9", "T\u0663", "a-b-")
SPACES = ("", " ", "\t", "\u00a0", "\u3000", "\n")
ID_COUNT = 3_000
TIMED_IDS = 3_000
FORMAT_LISTS = 300
FORMAT_SIZE = 30
HOSTILE_RANGES = (
    "",
    "-",
    "T1-",
    "-T1",
    "T1--T3",
    "T4-T1",
    "T1-B4",
    "T1-U3",
    "T1-T10001",
    "T1-T10000",
    "T08-T10",
    "T01-T10",
    "T1-T010",
    "T01-T1",
    "T008-T10",
    "T9-T010",
    "T0-T00",
    "T00-T0",
    "flag-1-flag-3",
    "P1x1-P1x3",
    "1-3",
    "007-009",
    "x1-y",
    "1-a2",
    "a-b",
    "  T1-T3 \u3000",
    "T\u0663-T\u0665",
    "T1-T" + "9" * 64,
    "T1-T" + "9" * 65,
    "T" + "9" * 70 + "-T1",
    "T99999999999999999999-T100000000000000000003",
    "T1-T100000000000000000000000",
    "T" + "0" * 64 + "-T" + "0" * 63 + "1",
    "\u00e9\u0301-1",
    "T\u00b2-T3",
)
HOSTILE_IDS = (
    "",
    "T",
    "007",
    "flag-12",
    "T\u0663",
    "T1\u00b2",
    "T" + "9" * 64,
    "T" + "9" * 65,
    "x" * 100 + "1" * 70,
    "T" + "1" * 30,
    "\u00e9" * 70 + "1" * 65,
)


def record(function: Callable[..., object], *args: object, **kwargs: object) -> JsonValue:
    """Runs one call and records its JSON-ready result, or the error it raised.

    Args:
        function: The function to call.
        args: Positional arguments of the call.
        kwargs: Keyword arguments of the call.

    Returns:
        outcome: `{"ok": result}` with tuples as lists and floats (numpy's too) as the repr of
            their value, the error's `to_dict` for an e8s error, or the class name of a foreign
            exception.
    """
    try:
        return {"ok": _plain(function(*args, **kwargs))}
    except Exception as error:
        to_dict = getattr(error, "to_dict", None)
        if callable(to_dict):
            return {"error": _plain(to_dict())}
        return {"raised": type(error).__name__}


def _plain(value: object) -> JsonValue:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, float):
        return repr(float(value))
    if value is None or isinstance(value, bool | int | str):
        return value
    return repr(value)


def _short(outcome: JsonValue) -> JsonValue:
    """Replaces a long `ok` list by its length and the SHA-256 of its items."""
    if isinstance(outcome, dict) and isinstance(outcome.get("ok"), list):
        items = outcome["ok"]
        if isinstance(items, list) and len(items) > SHORT_LIST:
            text = "\n".join(str(item) for item in items).encode()
            return {"count": len(items), "sha256": hashlib.sha256(text).hexdigest()}
    return outcome


@cache
def _float_pairs(e8s: ModuleType, count: int) -> tuple[np.ndarray, np.ndarray]:
    rng = e8s.seeded_rng(SEED)
    left = uniform(rng, -1e3, 1e3, size=count) * rng.choice([1.0, 1e-6, 1e6], size=count)
    noise = rng.choice(np.array(NOISE), size=count) * rng.choice([-1.0, 1.0], size=count)
    return left, left + noise


@cache
def _noise_floats(e8s: ModuleType, count: int) -> np.ndarray:
    rng = e8s.seeded_rng(SEED + 1)
    spread = uniform(rng, -1e4, 1e4, size=count) / powers_of_ten(rng.integers(0, 12, size=count))
    dyadic = (2 * rng.integers(-(2**20), 2**20, size=count) + 1) / DYADIC_DENOMINATOR
    return np.concatenate([np.array(SPECIALS), spread, dyadic])


def _comparisons(e8s: ModuleType) -> list[JsonValue]:
    left, right = _float_pairs(e8s, PAIRS)
    out: list[JsonValue] = []
    for index, (a, b) in enumerate(zip(left.tolist(), right.tolist(), strict=True)):
        abs_tol, rel_tol = TOLERANCES[index % len(TOLERANCES)]
        out.append(e8s.approx_equal(a, b, abs_tol=abs_tol, rel_tol=rel_tol))
    return out


def _odd_comparisons(e8s: ModuleType) -> list[JsonValue]:
    pairs: list[tuple[object, object]] = [(a, b) for a in SPECIALS for b in SPECIALS]
    pairs += [
        (np.array([1.0, 2.0]), [1.0, 2.0 + 1e-9]),
        (np.array([1.0, 2.0]), [1.0]),
        (np.array([[1.0, 2.0], [3.0, 4.0]]), [[1.0, 2.0], [3.0, 4.0 + 1e-3]]),
        (np.arange(6.0)[::2], [0.0, 2.0, 4.0]),
        (1.0, [1.0]),
        (np.float64(1.0), np.array(1.0)),
        ([], []),
        ([[]], []),
        (3, 3.0000001),
        (True, 1.0),
        (2**60 + 1, 2**60),
        (np.float32(0.1), 0.1),
        (np.int64(7), 7.0),
        ([1, [2]], [1, 2]),
        ("1.5", 1.5),
        ("abc", 1.0),
        (None, 1.0),
        (10**400, 1.0),
    ]
    out = [record(e8s.approx_equal, a, b) for a, b in pairs]
    tolerances: list[dict[str, object]] = [
        {"abs_tol": -1e-6},
        {"abs_tol": math.nan},
        {"rel_tol": -0.1},
        {"rel_tol": math.nan},
        {"abs_tol": np.float64(-1.0)},
        {"abs_tol": "x"},
        {"abs_tol": 0, "rel_tol": 1},
        {"abs_tol": -1.0, "rel_tol": -1.0},
        {"abs_tol": math.inf},
        {"abs_tol": True},
        {"tol": 1e-3},
    ]
    out += [record(e8s.approx_equal, 1.0, 1.5, **kw) for kw in tolerances]
    return out


def _chunks(e8s: ModuleType) -> tuple[list[float], list[JsonValue]]:
    rng = e8s.seeded_rng(SEED + 2)
    totals = uniform(rng, -1e6, 1e6, size=2_000) * rng.choice([1.0, 1e-9, 1e300], size=2_000)
    parts = rng.integers(1, 200, size=2_000)
    values = [
        piece
        for total, count in zip(totals.tolist(), parts.tolist(), strict=True)
        for piece in e8s.chunk_counts(total, count)
    ]
    odd: list[tuple[object, object]] = [
        (8, 3),
        (2**60 + 1, 3),
        (10**20 + 7, 7),
        (-(2**55) - 3, 9),
        (8.0, 0),
        (8.0, -1),
        (math.nan, 4),
        (math.inf, 3),
        (-math.inf, 0),
        (1.7e308, 3),
        (-0.0, 2),
        (8.0, np.int64(3)),
        (np.float64(9.0), 4),
        (8.0, True),
        (8.0, False),
        (8.0, 1_000_001),
        (8.0, 10**30),
        (8.0, -(10**30)),
        (10**400, 2),
        (math.nan, 1_000_001),
        ("8", 2),
        (None, 2),
        (np.float64(math.nan), 2),
    ]
    return values, [record(e8s.chunk_counts, total, count) for total, count in odd]


def float_comparison_on_seeded_values(e8s: ModuleType) -> Mapping[str, Output]:
    """Runs approx_equal, noise_rank and chunk_counts on seeded, extreme and malformed inputs.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The comparisons, the noise ranks and the chunks bit for bit, and the outcome
            of each odd input.
    """
    util = importlib.import_module(f"{e8s.__name__}.core.util")
    floats = _noise_floats(e8s, PAIRS)
    chunks, odd_chunks = _chunks(e8s)
    ranks = [util.noise_rank(value) for value in floats.tolist()]
    odd_ranks: list[object] = [np.float32(0.1), 3, -(10**20), True, np.float64(2.5e-10)]
    return {
        "approx_equal": _comparisons(e8s),
        "approx_equal_odd": _odd_comparisons(e8s),
        "noise_rank": np.array(ranks),
        "noise_rank_odd": [record(util.noise_rank, value) for value in odd_ranks],
        "chunk_counts": np.array(chunks),
        "chunk_counts_odd": odd_chunks,
    }


def float_comparison_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Compares 20,000 float pairs, ranks 20,000 floats and splits 2,000 totals; the timed share.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The count of close pairs and checksums of the ranks and chunks.
    """
    util = importlib.import_module(f"{e8s.__name__}.core.util")
    left, right = _float_pairs(e8s, TIMED_PAIRS)
    approx_equal = e8s.approx_equal
    close = sum(approx_equal(a, b) for a, b in zip(left.tolist(), right.tolist(), strict=True))
    arrays = sum(approx_equal(left[i : i + 8], right[i : i + 8]) for i in range(0, 4_000, 8))
    noise_rank = util.noise_rank
    ranks = math.fsum(noise_rank(value) for value in left.tolist())
    chunk_counts = e8s.chunk_counts
    chunks = math.fsum(sum(chunk_counts(t, 1 + i % 16)) for i, t in enumerate(left[:2_000]))
    return {"close": [close, arrays], "ranks": np.array([ranks]), "chunks": np.array([chunks])}


@cache
def _range_texts(e8s: ModuleType, count: int) -> list[str]:
    rng = e8s.seeded_rng(SEED + 3)
    texts: list[str] = list(HOSTILE_RANGES)
    for _ in range(count):
        prefix = PREFIXES[int(rng.integers(0, len(PREFIXES)))]
        other = PREFIXES[int(rng.integers(0, len(PREFIXES)))] if rng.random() < 0.1 else prefix
        low = int(rng.integers(0, 120))
        high = low + int(rng.integers(-3, 40))
        width = int(rng.integers(0, 4))
        low_text, high_text = f"{low:0{width}d}", f"{high:0{width}d}" if high >= 0 else "x"
        if rng.random() < 0.1:
            high_text = f"{high:0{width + 1}d}"
        space = SPACES[int(rng.integers(0, len(SPACES)))]
        texts.append(f"{space}{prefix}{low_text}-{other}{high_text}{space}")
    return texts


@cache
def _id_lists(e8s: ModuleType, lists: int, size: int) -> list[list[str]]:
    rng = e8s.seeded_rng(SEED + 4)
    out = []
    for _ in range(lists):
        prefix = PREFIXES[int(rng.integers(0, 3))]
        numbers = rng.integers(0, 3 * size, size=size)
        width = int(rng.integers(0, 3))
        out.append([f"{prefix}{int(n):0{width}d}" for n in numbers])
    out.append(["T1", "T2", "T" + "9" * 70, "T3"])
    out.append(["T09", "T10", "T9", "T010", "T011", "T1", "T01", "T001", "T02", "T2", "T3"])
    out.append(["\u00e91", "\u00e92", "\u00e93", "T\u00b2", "T\u0663", "", "x", "x"])
    out.append(["T99999999999999999998", "T99999999999999999999", "T100000000000000000000"])
    return out


def id_ranges_on_seeded_text(e8s: ModuleType) -> Mapping[str, Output]:
    """Runs split_id, parse_range, format_ranges, natural_key and natural_sort on seeded text.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Each helper's recorded outcome per text or per list.
    """
    ranges = _range_texts(e8s, ID_COUNT)
    words = [*HOSTILE_IDS, *(text.strip() for text in ranges)]
    lists = _id_lists(e8s, FORMAT_LISTS, FORMAT_SIZE)
    return {
        "parse_range": [_short(record(e8s.parse_range, text)) for text in ranges],
        "split_id": [record(e8s.split_id, word) for word in words],
        "natural_key": [record(e8s.natural_key, word) for word in words],
        "natural_sort": [record(e8s.natural_sort, items) for items in [*lists, words]],
        "format_ranges": [record(e8s.format_ranges, items) for items in lists],
        "odd": [
            record(e8s.natural_sort, [1, "T1"]),
            record(e8s.format_ranges, ["T1", 2]),
            record(e8s.split_id, 5),
            record(e8s.parse_range, None),
            record(e8s.natural_key, b"T1"),
        ],
    }


def id_ranges_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Expands ranges, splits and sorts IDs and compacts lists as selections and casts do.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Counts and digests of the results.
    """
    texts = [text.strip() for text in _range_texts(e8s, TIMED_IDS)[len(HOSTILE_RANGES) :]]
    parse_range, split_id = e8s.parse_range, e8s.split_id
    expanded = 0
    for text in texts:
        try:
            expanded += len(parse_range(text))
        except e8s.E8sError:
            expanded -= 1
    lists = _id_lists(e8s, FORMAT_LISTS, FORMAT_SIZE)[:FORMAT_LISTS]
    ids = [item for items in lists for item in items]
    split = sum(1 for item in ids if split_id(item)[1] is not None)
    ordered = e8s.natural_sort(ids)
    keys = sum(len(e8s.natural_key(item)[0]) for item in ids)
    text = "\n".join(e8s.format_ranges(items) for items in lists)
    digest = hashlib.sha256("\n".join([*ordered, text]).encode()).hexdigest()
    return {"counts": [expanded, split, keys], "digest": digest}


UTIL_CASES = (
    Case(
        area="core",
        name="float_comparison_on_seeded_values",
        run=float_comparison_on_seeded_values,
    ),
    Case(area="core", name="float_comparison_timed", run=float_comparison_timed, timed=True),
    Case(area="core", name="id_ranges_on_seeded_text", run=id_ranges_on_seeded_text),
    Case(area="core", name="id_ranges_timed", run=id_ranges_timed, timed=True),
)
