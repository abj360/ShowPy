"""
util.py --- small shared helpers: ID ranges, float comparison, seeded randomness, hashing

The computed helpers come from the compiled core; this module keeps the logging and timing
helpers, the numpy generator and the point reader, whose Shapely branch stays in Python.

Contains:
    noise_rank(): rounds a float that decides an order so 1-ulp noise ties
    natural_key(): returns the sort key that puts T2 before T10
    natural_sort(): sorts IDs so T2 comes before T10
    split_id(): splits an ID into its prefix and trailing number
    parse_range(): expands `T1-T8` into IDs
    format_ranges(): compacts IDs into `T1-T4, T7`
    approx_equal(): compares floats or arrays within absolute and relative tolerances
    seeded_rng(): returns the only allowed random generator
    as_points(): copies point-like inputs into a new (N, 2) float64 array
    content_hash(): hashes bytes, text or canonical JSON under a domain tag
    asset_uri(): names bytes by the `asset:sha256:<hex>` URI an asset store returns
    chunk_counts(): splits counts into equal parts that sum exactly
    close_matches(): suggests known names close to a misspelled one
    log(): emits a structured log line on the `e8s` logger
    timer(): measures elapsed time of a block
    Timer: holds the elapsed milliseconds measured by timer()
    RANGE_PATTERN: matches text that reads as a range, `T1-T8`
"""

import logging
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import shapely

from e8s._core.core import ASSET_PREFIX as ASSET_PREFIX
from e8s._core.core import HASH_PREFIX as HASH_PREFIX
from e8s._core.core import approx_equal as approx_equal
from e8s._core.core import asset_uri as asset_uri
from e8s._core.core import chunk_counts as chunk_counts
from e8s._core.core import close_matches as close_matches
from e8s._core.core import content_hash as content_hash
from e8s._core.core import format_ranges as format_ranges
from e8s._core.core import natural_key as natural_key
from e8s._core.core import natural_sort as natural_sort
from e8s._core.core import noise_rank as noise_rank
from e8s._core.core import parse_range as parse_range
from e8s._core.core import split_id as split_id
from e8s.core.errors import GeometryError, SchemaError
from e8s.core.types import Points

RANGE_PATTERN = re.compile(r"^(?=.*[0-9]$).*[0-9]-")
LOGGER = logging.getLogger("e8s")
SINGLE_PART_GEOMETRY = frozenset({"Point", "LineString", "LinearRing", "Polygon"})


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


def log(msg: str, **fields: object) -> None:
    """Emits `msg key=value ...` at INFO on the `e8s` logger, where builds capture it."""
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
