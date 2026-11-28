#!/usr/bin/env python3

"""
core_text.py --- parity cases for close-name suggestions and CSV cells, and the record of the
utility answers that changed on purpose

Contains:
    close_matches_on_seeded_names(): close_matches on misspelled names, long names and unicode
    close_matches_timed(): the timed share of close_matches
    csv_cells_on_seeded_text(): neutralize_cell on formula-like and plain cells, timed
    util_inputs_v040_mishandled(): records the answers that changed on purpose: lone surrogates
        in error text, totals and part counts of other number types, non-text names, and float
        and Decimal sizes in range or NaN, and long double sizes
    TEXT_CASES: the cases above
"""

import hashlib
import importlib
from collections.abc import Callable, Mapping
from decimal import Decimal
from fractions import Fraction
from functools import cache
from types import ModuleType

import numpy as np
from pydantic import JsonValue

from tests.parity.cases import Case, Output
from tests.parity.core_util import SEED, record

NAME_COUNT = 400
TIMED_NAMES = 300
CANDIDATES = 60
LONG_NAME = 230
CAP = 1_000_000
IN_RANGE_SIZES = (3.0, 3.5, -0.0, 1e6, np.float64(3.0))
CHANGED_DECIMAL_SIZES = ("3", "3.5", "-0", "1000000", "NaN", "sNaN")
# v0.4.0 accepted the first and hit the recursion limit bounding the others' context.
LONG_DOUBLE_SIZES = (np.longdouble(3), np.longdouble(-1), np.longdouble(1_000_001))
CELL_COUNT = 20_000
CELL_ALPHABET = list("=+-@\t\r0123456789.eE xy\u0663\u2212")
HOSTILE_CELLS = (
    "=1+1",
    "-5",
    "+3.5e10",
    "-.5",
    "+.",
    "-",
    "@cue",
    "\tx",
    "\r",
    "-1e",
    "-1e+5",
    "-1.e5",
    "-\u0661",
    "cue",
    "",
    " =1",
    "\u22125",
    "+5\n",
    "-0005.000E-0003",
    "--5",
)


@cache
def _names(e8s: ModuleType, count: int) -> list[tuple[str, list[str]]]:
    rng = e8s.seeded_rng(SEED + 5)
    alphabet = "abcdefghT0123456789-_\u00e9"
    cases = []
    for index in range(count):
        size = int(rng.integers(0, CANDIDATES))
        lengths = rng.integers(1, 12, size=size)
        candidates = ["".join(rng.choice(list(alphabet), size=int(n))) for n in lengths]
        if candidates and index % 2:
            name = list(candidates[int(rng.integers(0, len(candidates)))])
            name[int(rng.integers(0, len(name)))] = str(rng.choice(list(alphabet)))
            cases.append(("".join(name), candidates))
        else:
            cases.append(("".join(rng.choice(list(alphabet), size=4)), candidates))
    long_name = "ab" * (LONG_NAME // 2)
    cases.append((long_name, [long_name[:-3], "a" * LONG_NAME, "b" * 210 + "a" * 20, "xyz"]))
    cases.append(("", ["", "a", ""]))
    cases.append(("T1", ["T1", "T1", "1T", "T11", "T12", "T10"]))
    cases.append(("abcd", ["dcba", "bcda", "abdc", "acbd", "badc"]))
    return cases


def close_matches_on_seeded_names(e8s: ModuleType) -> Mapping[str, Output]:
    """Suggests close names for seeded misspellings, long names and repeated candidates.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The suggestions per name, and the outcome of odd inputs.
    """
    names = _names(e8s, NAME_COUNT)
    return {
        "suggestions": [e8s.close_matches(name, candidates) for name, candidates in names],
        "odd": [record(e8s.close_matches, "T1", [5]), record(e8s.close_matches, "T1", 5)],
    }


def close_matches_timed(e8s: ModuleType) -> Mapping[str, Output]:
    """Suggests close names for 300 seeded misspellings; the timed share of close_matches.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The digest of every suggestion.
    """
    names = _names(e8s, TIMED_NAMES)
    found = [e8s.close_matches(name, candidates) for name, candidates in names]
    return {"digest": hashlib.sha256(repr(found).encode()).hexdigest()}


@cache
def _cells(e8s: ModuleType, count: int) -> list[str]:
    rng = e8s.seeded_rng(SEED + 6)
    picks = rng.integers(0, len(CELL_ALPHABET), size=(count, 8))
    lengths = rng.integers(0, 9, size=count)
    texts = [
        "".join(CELL_ALPHABET[i] for i in row[:n]) for row, n in zip(picks, lengths, strict=True)
    ]
    return [*HOSTILE_CELLS, *texts]


def csv_cells_on_seeded_text(e8s: ModuleType) -> Mapping[str, Output]:
    """Neutralizes 20,000 seeded cells and the hostile ones; timed.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The guarded cells' count and digest, and the hostile cells' results.
    """
    cells_module = importlib.import_module(f"{e8s.__name__}.core.csv_cells")
    neutralize = cells_module.neutralize_cell
    cells = _cells(e8s, CELL_COUNT)
    safe = [neutralize(cell) for cell in cells]
    guarded = sum(1 for before, after in zip(cells, safe, strict=True) if before is not after)
    return {
        "hostile": safe[: len(HOSTILE_CELLS)],
        "guarded": guarded,
        "digest": hashlib.sha256("\n".join(safe).encode()).hexdigest(),
        "odd": [record(neutralize, 5), record(neutralize, b"=1")],
    }


def _answer(function: Callable[..., object], *args: object) -> JsonValue:
    try:
        result = function(*args)
    except Exception as error:
        to_dict = getattr(error, "to_dict", None)
        shown = ascii(to_dict()) if callable(to_dict) else ""
        return {"error": type(error).__name__, "detail": shown}
    items = result if isinstance(result, list) else [result]
    return {"types": [type(item).__name__ for item in items], "values": ascii(items)}


def util_inputs_v040_mishandled(e8s: ModuleType) -> Mapping[str, Output]:
    """Records the answers that changed on purpose against v0.4.0.

    A lone surrogate in an ID shows as U+FFFD in error text, as in the ID checks. A total that
    is neither a float nor an int splits as a float, and the parts are floats. A part count
    that is neither an int nor a float is refused with TypeError before the total is read.
    close_matches takes text only. require_size refuses a float or a Decimal in range with its
    own error class, asking for a whole-number int, where v0.4.0 let it through without
    truncating it, and refuses a Decimal NaN as over the cap, where v0.4.0 raised
    decimal.InvalidOperation. A long double size is refused like a float, where v0.4.0 accepted
    one in range and hit the recursion limit on the others.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Per call, the result's types and values, or the error and its fields.
    """
    surrogate = "\ud800"
    return {
        "surrogate_errors": [
            _answer(e8s.split_id, f"T{surrogate}{'9' * 65}"),
            _answer(e8s.parse_range, f"{surrogate}-"),
            _answer(e8s.format_ranges, [f"T{surrogate}{'1' * 70}", f"T{surrogate}2"]),
        ],
        "chunk_counts": [
            _answer(e8s.chunk_counts, Fraction(1), 3),
            _answer(e8s.chunk_counts, np.float32(1.5), 3),
            _answer(e8s.chunk_counts, np.float64(9.0), 4),
            _answer(e8s.chunk_counts, 8.0, np.int64(3)),
            _answer(e8s.chunk_counts, 8.0, Fraction(1, 2)),
        ],
        "require_size": [
            _answer(lambda size: e8s.require_size(size, CAP, what="points"), size)
            for size in (
                *IN_RANGE_SIZES,
                *(Decimal(text) for text in CHANGED_DECIMAL_SIZES),
                *LONG_DOUBLE_SIZES,
            )
        ],
        "close_matches": [
            _answer(e8s.close_matches, ("T", "1"), [("T", "1")]),
            _answer(e8s.close_matches, "T1", [("T", "1")]),
        ],
    }


TEXT_CASES = (
    Case(area="core", name="close_matches_on_seeded_names", run=close_matches_on_seeded_names),
    Case(area="core", name="close_matches_timed", run=close_matches_timed, timed=True),
    Case(area="core", name="csv_cells_on_seeded_text", run=csv_cells_on_seeded_text, timed=True),
    Case(area="core", name="util_inputs_v040_mishandled", run=util_inputs_v040_mishandled),
)
