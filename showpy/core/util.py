"""
util.py --- small shared helpers: ID ranges, float comparison, seeded randomness, hashing

Contains:
    natural_sort(): sorts IDs so T2 comes before T10
    split_id(): splits an ID into its prefix and trailing number
    parse_range(): expands `T1-T8` into IDs
    format_ranges(): compacts IDs into `T1–T4, T7`
    approx_equal(): compares floats or arrays within absolute and relative tolerances
    seeded_rng(): returns the only allowed random generator
    as_points(): copies point-like inputs into a new (N, 2) float64 array
    content_hash(): hashes bytes, text or canonical JSON under a domain tag
    asset_uri(): names bytes by the `asset:sha256:<hex>` URI an asset store returns
    chunk_counts(): splits counts into equal parts that sum exactly
    close_matches(): suggests known names close to a misspelled one
    log(): emits a structured log line on the `showpy` logger
    timer(): measures elapsed time of a block
    Timer: holds the elapsed milliseconds measured by timer()
"""

import difflib
import hashlib
import itertools
import logging
import math
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import shapely

from showpy.core.canonical import canonical_value, jcs
from showpy.core.caps import MAX_POINTS, MAX_RANGE, require_size
from showpy.core.deprecation import renamed_keyword
from showpy.core.errors import MAX_ECHOED_INPUT_CHARS, GeometryError, SchemaError
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
ASSET_PREFIX = "asset:sha256:"
CLOSE_MATCH_COUNT = 3
CLOSE_MATCH_CUTOFF = 0.4
DEFAULT_ABS_TOL = 1e-6
RENAME_REMOVAL = "0.6.0"
SINGLE_PART_GEOMETRY = frozenset({"Point", "LineString", "LinearRing", "Polygon"})


def _natural_key(text: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    chunks = tuple(
        (0, len(stripped), stripped) if index % 2 else (1, 0, chunk)
        for index, chunk in enumerate(NATURAL_CHUNK.split(text))
        if chunk
        for stripped in (chunk.lstrip("0"),)
    )
    return chunks, text


def natural_sort[T: str](ids: Iterable[T]) -> list[T]:
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


def approx_equal(
    a: npt.ArrayLike,
    b: npt.ArrayLike,
    *,
    abs_tol: float | None = None,
    rel_tol: float = 0.0,
    tol: float | None = None,
) -> bool:
    """Compares numbers or arrays within `abs_tol` plus `rel_tol` of `b`; shapes must match.

    Equal infinities are equal and NaN equals nothing, without any warning. A negative or NaN
    tolerance raises SchemaError.

    Args:
        a: First number or array.
        b: Second number or array.
        abs_tol: Absolute tolerance; 1e-6 when omitted.
        rel_tol: Tolerance relative to the magnitude of `b`.
        tol: Deprecated name of `abs_tol`.

    Returns:
        is_close: Whether every element of `a` is within tolerance of its element of `b`.

    >>> approx_equal(0.1 + 0.2, 0.3)
    True
    """
    limit = renamed_keyword(
        "tol", tol, "abs_tol", abs_tol, default=DEFAULT_ABS_TOL, remove=RENAME_REMOVAL
    )
    _require_tolerance("abs_tol", limit)
    _require_tolerance("rel_tol", rel_tol)
    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if left.shape != right.shape:
        return False
    with np.errstate(invalid="ignore", over="ignore"):
        close = np.abs(left - right) <= limit + rel_tol * np.abs(right)
    return bool(np.all(close | (left == right)))


def _require_tolerance(name: str, value: float) -> None:
    if not value >= 0:
        raise SchemaError(
            f"{name} must be 0 or more, got {value!r}",
            hint=f"pass {name} as a number 0 or more, such as 1e-6",
            context={name: repr(value)},
        )


def seeded_rng(seed: int) -> np.random.Generator:
    """Returns a deterministic generator; the only randomness allowed in the library.

    Args:
        seed: Non-negative `int` or `np.integer` seed; None, booleans and negatives are rejected.

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

    The result is always a new C-contiguous float64 array. Shapely coordinates keep x and y and
    drop z; a polygon gives its exterior; multi-part geometry raises GeometryError.

    >>> as_points((1, 2)).tolist()
    [[1.0, 2.0]]
    """
    if isinstance(obj, shapely.Polygon):
        obj = obj.exterior
    if isinstance(obj, shapely.geometry.base.BaseGeometry):
        return _geometry_points(obj)
    if isinstance(obj, Sequence) and obj and hasattr(obj[0], "x") and hasattr(obj[0], "y"):
        return np.array([[float(item.x), float(item.y)] for item in obj], dtype=np.float64)
    array = np.array(obj, dtype=np.float64, order="C", copy=True)
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


def _geometry_points(geometry: shapely.geometry.base.BaseGeometry) -> Points:
    if geometry.geom_type not in SINGLE_PART_GEOMETRY:
        raise GeometryError(
            f"cannot read points from a {geometry.geom_type}",
            hint="pass a Point, LineString, LinearRing or Polygon, or one part at a time",
            context={"geometry": geometry.geom_type},
        )
    return np.ascontiguousarray(shapely.get_coordinates(geometry), dtype=np.float64)


def content_hash(obj: object, *, domain: str = "showpy.content") -> str:
    """Hashes a value as SHA-256 over domain-tagged canonical JSON.

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
    """Splits counts into equal parts that sum to `total`, each cut at an exact boundary.

    Raises SchemaError for fewer than one part, a part count over `MAX_POINTS` and a total that
    is NaN or infinite.

    >>> chunk_counts(8.0, 4)
    [2.0, 2.0, 2.0, 2.0]
    """
    if parts < 1:
        raise SchemaError(
            "parts must be at least 1", hint="pass parts >= 1", context={"parts": parts}
        )
    if not math.isfinite(total):
        raise SchemaError(
            f"total must be finite, got {total!r}",
            hint="pass a finite number of counts",
            context={"total": repr(total)},
        )
    require_size(parts, MAX_POINTS, what="chunks")
    bounds = [0.0, *(total / parts * index for index in range(1, parts)), total]
    return [stop - start for start, stop in itertools.pairwise(bounds)]


def asset_uri(data: bytes) -> str:
    """Names bytes by the `asset:sha256:<hex>` URI an asset store returns for them.

    Args:
        data: Exact bytes of the asset.

    Returns:
        uri: `asset:sha256:` followed by the SHA-256 hex digest of `data`.

    Examples:
        >>> asset_uri(b"")
        'asset:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    return f"{ASSET_PREFIX}{hashlib.sha256(data).hexdigest()}"


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
