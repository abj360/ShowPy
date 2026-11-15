"""
util.py --- small shared helpers: ID ranges, float comparison, seeded randomness, hashing

Contains:
    natural_sort(): sorts IDs so T2 comes before T10
    split_id(): splits an ID into its prefix and trailing number
    parse_range(): expands `T1-T8` into IDs
    format_ranges(): compacts IDs into `T1–T4, T7`
    approx_equal(): compares floats or arrays with an absolute tolerance
    seeded_rng(): returns the only allowed random generator
    as_points(): normalizes point-like inputs to an (N, 2) array
    content_hash(): hashes bytes, text or canonical JSON under a domain tag
    chunk_counts(): splits counts into equal parts that sum exactly
    close_matches(): suggests known names close to a misspelled one
    log(): emits a structured log line on the `showpy` logger
    timer(): measures elapsed time of a block
    Timer: holds the elapsed milliseconds measured by timer()
"""

import difflib
import hashlib
import logging
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import shapely

from showpy.core.canonical import canonical_value, jcs
from showpy.core.caps import MAX_RANGE, require_size
from showpy.core.errors import MAX_ECHOED_INPUT_CHARS, SchemaError
from showpy.core.ids import ID_MAX_LENGTH
from showpy.core.types import Points

ASCII_DIGITS = "0123456789"
NATURAL_CHUNK = re.compile(r"([0-9]+)")
RANGE_PATTERN = re.compile(r"^([A-Za-z_]+)([0-9]+)-([A-Za-z_]+)([0-9]+)$")
RANGE_DASH = "–"
LOGGER = logging.getLogger("showpy")
HASH_PREFIX = "showpy-hash/1"
# The domain is one line of the hash header, so it may not hold a newline or forge a kind line.
HASH_DOMAIN_PATTERN = re.compile(r"[a-z0-9_.]+")
CLOSE_MATCH_COUNT = 3
CLOSE_MATCH_CUTOFF = 0.4


def _natural_key(text: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    chunks = tuple(
        (0, len(stripped), stripped) if index % 2 else (1, 0, chunk)
        for index, chunk in enumerate(NATURAL_CHUNK.split(text))
        if chunk
        for stripped in (chunk.lstrip("0"),)
    )
    return chunks, text


def natural_sort(ids: Iterable[str]) -> list[str]:
    """Sorts IDs with embedded numbers compared numerically; ties fall back to the text.

    >>> natural_sort(["T10", "T2"])
    ['T2', 'T10']
    """
    return sorted(ids, key=_natural_key)


def split_id(text: str) -> tuple[str, int | None]:
    """Splits an ID into its prefix and trailing ASCII number, in time linear in its length.

    Args:
        text: The ID to split.

    Returns:
        parts: The text before the trailing digits and their value, or `(text, None)` without
            trailing digits.

    >>> split_id("flag-12"), split_id("lead")
    (('flag-', 12), ('lead', None))
    """
    prefix = text.rstrip(ASCII_DIGITS)
    digits = text[len(prefix) :]
    if not digits:
        return text, None
    _require_digit_run(digits, text)
    return prefix, int(digits)


def _require_digit_run(digits: str, text: str) -> None:
    if len(digits) > ID_MAX_LENGTH:
        raise SchemaError(
            f"number in {text[:ID_MAX_LENGTH]!r}... has {len(digits)} digits",
            hint=f"IDs hold at most {ID_MAX_LENGTH} characters, so no number is longer",
            context={"digits": len(digits), "max": ID_MAX_LENGTH},
        )


def parse_range(text: str) -> list[str]:
    """Expands an ID range such as `T1-T8`; a single ID returns itself.

    >>> parse_range("T1-T3")
    ['T1', 'T2', 'T3']
    """
    stripped = text.strip()
    match = RANGE_PATTERN.match(stripped)
    if match is None:
        if not stripped or stripped.startswith("-") or stripped.endswith("-"):
            raise SchemaError(
                f"bad range {text!r}", hint="write ranges as T1-T8", context={"text": text}
            )
        return [stripped]
    prefix, first, last_prefix, last = match.groups()
    _require_digit_run(first, text)
    _require_digit_run(last, text)
    low, high = int(first), int(last)
    if prefix != last_prefix or low > high:
        raise SchemaError(
            f"bad range {text!r}",
            hint="use one prefix and an ascending range, e.g. T1-T8",
            context={"text": text},
        )
    require_size(high - low + 1, MAX_RANGE, what="range IDs")
    width = len(first) if first.startswith("0") else 0
    return [f"{prefix}{n:0{width}d}" for n in range(low, high + 1)]


def format_ranges(ids: Iterable[str]) -> str:
    """Compacts IDs into runs for display.

    >>> format_ranges(["T1", "T2", "T3", "T7"])
    'T1–T3, T7'
    """
    runs: list[list[str]] = []
    for spot_id in natural_sort(set(ids)):
        if runs and _continues(runs[-1][-1], spot_id):
            runs[-1].append(spot_id)
        else:
            runs.append([spot_id])
    return ", ".join(run[0] if len(run) == 1 else f"{run[0]}{RANGE_DASH}{run[-1]}" for run in runs)


def _continues(previous: str, current: str) -> bool:
    (left, left_number), (right, right_number) = split_id(previous), split_id(current)
    return (
        left_number is not None
        and right_number is not None
        and left == right
        and right_number == left_number + 1
    )


def approx_equal(a: npt.ArrayLike, b: npt.ArrayLike, tol: float = 1e-6) -> bool:
    """Compares numbers or arrays with an absolute tolerance; shape mismatch is unequal.

    >>> approx_equal(0.1 + 0.2, 0.3)
    True
    """
    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return left.shape == right.shape and bool(np.all(np.abs(left - right) <= tol))


def seeded_rng(seed: int) -> np.random.Generator:
    """Returns a deterministic generator; the only randomness allowed in the library.

    Args:
        seed: Non-negative integer seed; None, booleans and negatives are rejected.

    Returns:
        rng: A PCG64 generator that yields the same stream for the same seed.

    >>> float(seeded_rng(1).integers(0, 10)) >= 0
    True
    """
    if isinstance(seed, bool) or not isinstance(seed, int | np.integer) or seed < 0:
        raise SchemaError(
            f"seed must be a non-negative integer, got {seed!r}",
            hint="pass an int such as 0 or 42; None and negative seeds are not allowed",
            context={"seed": repr(seed)},
        )
    return np.random.Generator(np.random.PCG64(int(seed)))


def as_points(obj: object) -> Points:
    """Normalizes tuples, lists, arrays, Shapely geometry or objects with x/y to (N, 2).

    >>> as_points((1, 2)).tolist()
    [[1.0, 2.0]]
    """
    if isinstance(obj, shapely.Polygon):
        obj = obj.exterior
    if isinstance(obj, shapely.geometry.base.BaseGeometry):
        return np.asarray(obj.coords, dtype=float).reshape(-1, 2)[:, :2]
    if isinstance(obj, Sequence) and obj and hasattr(obj[0], "x") and hasattr(obj[0], "y"):
        return np.array([[float(item.x), float(item.y)] for item in obj], dtype=float)
    array = np.asarray(obj, dtype=float)
    if array.size == 0:
        return np.zeros((0, 2))
    if array.ndim == 1 and array.shape == (2,):
        return array.reshape(1, 2)
    if array.ndim != 2 or array.shape[1] != 2:
        raise SchemaError(
            f"points must have shape (N, 2), got {array.shape}",
            hint="pass (x, y) pairs",
            context={"shape": array.shape},
        )
    return array


def content_hash(obj: object, *, domain: str = "showpy.content") -> str:
    """Hashes a value as SHA-256 over domain-tagged canonical JSON (ADR 0014).

    Bytes and text hash as themselves under their own kind tag, so the string `'{"a":1}'` and
    the object `{"a": 1}` never collide. Everything else hashes as RFC 8785 JSON.

    Args:
        obj: Bytes, text or any value `canonical_value` accepts.
        domain: Name of what the hash identifies, so equal content in two domains differs;
            only lowercase ASCII letters, digits, `_` and `.`.

    Returns:
        digest: 64 lowercase hex characters.

    >>> content_hash({"a": 1}) == content_hash({"a": 1.0})
    True
    >>> content_hash({"a": 1}) == content_hash({"a": 1}, domain="showpy.revision")
    False
    """
    if HASH_DOMAIN_PATTERN.fullmatch(domain) is None:
        raise SchemaError(
            f"hash domain {domain[:MAX_ECHOED_INPUT_CHARS]!r} is not allowed",
            hint="use lowercase ASCII letters, digits, _ and . only, as in showpy.revision",
            context={"domain": domain[:MAX_ECHOED_INPUT_CHARS]},
        )
    if isinstance(obj, bytes):
        kind, payload = "bytes", obj
    elif isinstance(obj, str):
        kind, payload = "str", str(canonical_value(obj)).encode()
    else:
        kind, payload = "json", jcs(obj)
    head = f"{HASH_PREFIX}\n{domain}\n{kind}\n".encode()
    return hashlib.sha256(head + payload).hexdigest()


def chunk_counts(total: float, parts: int) -> list[float]:
    """Splits counts into equal parts whose sum is exactly `total`.

    >>> chunk_counts(8.0, 4)
    [2.0, 2.0, 2.0, 2.0]
    """
    if parts < 1:
        raise SchemaError(
            "parts must be at least 1", hint="pass parts >= 1", context={"parts": parts}
        )
    size = total / parts
    chunks = [size] * (parts - 1)
    return [*chunks, total - sum(chunks)]


def close_matches(name: str, candidates: Iterable[str]) -> list[str]:
    """Suggests up to three known names close to `name`, best first, for error hints.

    >>> close_matches("T3", ["T1", "B7"])
    ['T1']
    """
    return difflib.get_close_matches(
        name, list(candidates), n=CLOSE_MATCH_COUNT, cutoff=CLOSE_MATCH_CUTOFF
    )


def log(msg: str, **fields: object) -> None:
    """Emits `msg key=value ...` at INFO on the `showpy` logger, where builds capture it."""
    suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    LOGGER.info(f"{msg} {suffix}" if suffix else msg)


@dataclass
class Timer:
    """Holds the elapsed time measured by timer().

    Attributes:
        elapsed_ms: Milliseconds between entering and leaving the block.
    """

    elapsed_ms: float = 0.0


@contextmanager
def timer() -> Iterator[Timer]:
    """Measures how long a block takes, for build reports; never feeds library logic."""
    clock = Timer()
    start = time.perf_counter()
    try:
        yield clock
    finally:
        clock.elapsed_ms = (time.perf_counter() - start) * 1000.0
