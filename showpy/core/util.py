"""
util.py --- small shared helpers: ID ranges, float comparison, seeded randomness, hashing

Contains:
    natural_sort(): sorts IDs so T2 comes before T10
    parse_range(): expands `T1-T8` into IDs
    format_ranges(): compacts IDs into `T1–T4, T7`
    approx_equal(): compares floats or arrays with an absolute tolerance
    seeded_rng(): returns the only allowed random generator
    as_points(): normalizes point-like inputs to an (N, 2) array
    content_hash(): hashes a model, mapping, string or bytes canonically
    chunk_counts(): splits counts into equal parts that sum exactly
    close_matches(): suggests known names close to a misspelled one
    log(): emits a structured log line on the `showpy` logger
    timer(): measures elapsed time of a block
    Timer: holds the elapsed milliseconds measured by timer()
"""

import difflib
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import shapely
from pydantic import BaseModel

from showpy.core.errors import SchemaError
from showpy.core.types import Points

ID_PARTS = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")
NATURAL_CHUNK = re.compile(r"(\d+)")
RANGE_PATTERN = re.compile(r"^([A-Za-z_]+)(\d+)-([A-Za-z_]+)(\d+)$")
RANGE_DASH = "–"
LOGGER = logging.getLogger("showpy")
CLOSE_MATCH_COUNT = 3
CLOSE_MATCH_CUTOFF = 0.4


def _natural_key(text: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(chunk)) if chunk.isdigit() else (1, chunk)
        for chunk in NATURAL_CHUNK.split(text)
        if chunk
    )


def natural_sort(ids: Iterable[str]) -> list[str]:
    """Sorts IDs with embedded numbers compared numerically.

    >>> natural_sort(["T10", "T2"])
    ['T2', 'T10']
    """
    return sorted(ids, key=_natural_key)


def _split_id(text: str) -> tuple[str, str] | None:
    match = ID_PARTS.match(text)
    return None if match is None else (match["prefix"], match["number"])


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
    if prefix != last_prefix or int(first) > int(last):
        raise SchemaError(
            f"bad range {text!r}",
            hint="use one prefix and an ascending range, e.g. T1-T8",
            context={"text": text},
        )
    width = len(first) if first.startswith("0") else 0
    return [f"{prefix}{n:0{width}d}" for n in range(int(first), int(last) + 1)]


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
    a, b = _split_id(previous), _split_id(current)
    return a is not None and b is not None and a[0] == b[0] and int(b[1]) == int(a[1]) + 1


def approx_equal(a: npt.ArrayLike, b: npt.ArrayLike, tol: float = 1e-6) -> bool:
    """Compares numbers or arrays with an absolute tolerance; shape mismatch is unequal.

    >>> approx_equal(0.1 + 0.2, 0.3)
    True
    """
    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return left.shape == right.shape and bool(np.all(np.abs(left - right) <= tol))


def seeded_rng(seed: int) -> np.random.Generator:
    """Returns a deterministic generator; the only randomness allowed in the library.

    >>> float(seeded_rng(1).integers(0, 10)) >= 0
    True
    """
    return np.random.default_rng(seed)


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


def content_hash(obj: object) -> str:
    """Returns a SHA-256 hex digest of canonical content.

    >>> content_hash({"a": 1}) == content_hash({"a": 1})
    True
    """
    if isinstance(obj, bytes):
        payload = obj
    elif isinstance(obj, str):
        payload = obj.encode()
    else:
        if isinstance(obj, BaseModel):
            obj = obj.model_dump(mode="json")
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


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
