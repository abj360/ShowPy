"""
curve_base.py --- the Curve protocol and the arc-length machinery every curve shares

Contains:
    Curve: describes the interface every stage-plane curve implements
    CurveBase: implements arc-length sampling, projection and polyline export generically
    CurveBase.model_copy(): returns a validated copy without stale caches
    CurveBase.point_at(): returns points at arc lengths, wrapping closed curves
    CurveBase.tangent_at(): returns unit tangents at arc lengths
    CurveBase.normal_at(): returns unit left normals at arc lengths
    CurveBase.project(): returns the arc length of the closest point
    CurveBase.to_polyline(): approximates the curve within a chord error
    CurveBase.to_shapely(): returns a LineString or LinearRing
    CurveBase.construction(): returns the JSON recipe of the curve
"""

import itertools
from collections.abc import Mapping
from functools import cached_property
from typing import Any, Protocol, Self, overload, override, runtime_checkable

import numpy as np
import numpy.typing as npt
import shapely

from showpy.core.errors import ZeroLengthError
from showpy.core.types import FrozenModel, Meters, Point, Points

TABLE_SAMPLES = 2048
INITIAL_SPLITS = 8
MAX_SUBDIVISION_DEPTH = 24
PROJECT_SAMPLES = 512
PROJECT_REFINE_STEPS = 60
GOLDEN = (np.sqrt(5.0) - 1.0) / 2.0
DERIVATIVE_STEP = 1e-6

type FloatArray = npt.NDArray[np.float64]


@runtime_checkable
class Curve(Protocol):
    """Describes a curve on the stage plane, parameterized by arc length in meters."""

    @property
    def closed(self) -> bool:
        """Returns whether the curve loops back to its start."""
        ...

    def length(self) -> Meters:
        """Returns the arc length."""
        ...

    def point_at(self, s: npt.ArrayLike) -> Points:
        """Returns points at arc lengths."""
        ...

    def point_at_u(self, u: npt.ArrayLike) -> Points:
        """Returns points at fractions of the length."""
        ...

    def tangent_at(self, s: npt.ArrayLike) -> Points:
        """Returns unit tangents at arc lengths."""
        ...

    def normal_at(self, s: npt.ArrayLike) -> Points:
        """Returns unit left normals at arc lengths."""
        ...

    def project(self, p: Point) -> Meters:
        """Returns the arc length of the closest point to `p`."""
        ...

    def to_polyline(self, tol: float = 0.01) -> Points:
        """Returns vertices approximating the curve within `tol` chord error."""
        ...

    def to_shapely(self, tol: float = 0.01) -> shapely.LineString:
        """Returns the curve as Shapely geometry."""
        ...

    def construction(self) -> dict[str, Any]:
        """Returns the JSON recipe of the curve."""
        ...


class CurveBase(FrozenModel):
    """Implements the Curve protocol from a native parameterization t in [0, 1].

    Subclasses either implement `_native` (and inherit table-based arc length) or override
    `length`, `_points` and `_tangents` with exact formulas.

    Attributes:
        closed: Whether the curve loops back to its start.
    """

    closed: bool = False

    # Any: update values are arbitrary field values, validated by the rebuilt model.
    @override
    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Returns a validated copy; cached arc-length tables never leak into the copy."""
        return self.model_validate({**self.model_dump(), **(update or {})})

    def _native(self, t: FloatArray) -> Points:
        raise NotImplementedError

    def _table_size(self) -> int:
        return TABLE_SAMPLES

    @cached_property
    def _table(self) -> tuple[FloatArray, FloatArray]:
        t = np.linspace(0.0, 1.0, self._table_size())
        pts = self._native(t)
        s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
        return t, s

    def length(self) -> Meters:
        """Returns the arc length in meters."""
        return float(self._table[1][-1])

    def _points(self, s: FloatArray) -> Points:
        t, table_s = self._table
        return self._native(np.interp(s, table_s, t))

    def _tangents(self, s: FloatArray) -> Points:
        total = self.length()
        h = max(DERIVATIVE_STEP * total, DERIVATIVE_STEP)
        lo = self._wrap(s - h) if self.closed else np.clip(s - h, 0.0, total)
        hi = self._wrap(s + h) if self.closed else np.clip(s + h, 0.0, total)
        return _unit(self._points(hi) - self._points(lo))

    def _breaks(self) -> FloatArray:
        return np.array([0.0, self.length()])

    def _wrap(self, s: FloatArray) -> FloatArray:
        total = self.length()
        if self.closed and total > 0:
            wrapped: FloatArray = np.mod(s, total)
            return wrapped
        return np.clip(s, 0.0, total)

    @overload
    def _vectorized(self, fn: str, s: float) -> Points: ...
    @overload
    def _vectorized(self, fn: str, s: npt.ArrayLike) -> Points: ...
    def _vectorized(self, fn: str, s: npt.ArrayLike) -> Points:
        values = np.asarray(s, dtype=float)
        result: Points = getattr(self, fn)(self._wrap(values.ravel()))
        return result[0] if values.ndim == 0 else result

    def point_at(self, s: npt.ArrayLike) -> Points:
        """Returns points at arc lengths: shape (2,) for a scalar, (N, 2) for an array.

        Open curves clamp `s` to [0, length]; closed curves wrap it.
        """
        return self._vectorized("_points", s)

    def point_at_u(self, u: npt.ArrayLike) -> Points:
        """Returns points at fractions 0..1 of the length."""
        return self.point_at(np.asarray(u, dtype=float) * self.length())

    def tangent_at(self, s: npt.ArrayLike) -> Points:
        """Returns unit tangents at arc lengths."""
        return self._vectorized("_tangents", s)

    def normal_at(self, s: npt.ArrayLike) -> Points:
        """Returns unit left normals (tangent turned 90° counter-clockwise)."""
        tangent = self.tangent_at(s)
        return np.stack([-tangent[..., 1], tangent[..., 0]], axis=-1)

    def project(self, p: Point) -> Meters:
        """Returns the arc length of the point on the curve closest to `p`.

        A dense scan finds the best sample, then golden-section search refines it.
        """
        total = self.length()
        grid = np.linspace(0.0, total, PROJECT_SAMPLES)
        target = np.asarray(p, dtype=float)
        best = int(np.argmin(np.linalg.norm(self._points(self._wrap(grid)) - target, axis=1)))
        step = total / (PROJECT_SAMPLES - 1)
        return self._golden_refine(target, grid[best] - step, grid[best] + step)

    def _golden_refine(self, target: FloatArray, lo: float, hi: float) -> Meters:
        def distance(s: float) -> float:
            point = self._points(self._wrap(np.array([s])))[0]
            return float(np.linalg.norm(point - target))

        if not self.closed:
            lo, hi = max(lo, 0.0), min(hi, self.length())
        for _ in range(PROJECT_REFINE_STEPS):
            a, b = hi - GOLDEN * (hi - lo), lo + GOLDEN * (hi - lo)
            lo, hi = (lo, b) if distance(a) < distance(b) else (a, hi)
        return float(self._wrap(np.array([(lo + hi) / 2]))[0])

    def to_polyline(self, tol: float = 0.01) -> Points:
        """Returns vertices from start to end whose chords stay within `tol` of the curve."""
        breaks = np.unique(np.concatenate([self._breaks(), [0.0, self.length()]]))
        stations: list[float] = [float(breaks[0])]
        for a, b in itertools.pairwise(breaks):
            for lo, hi in _pairs(np.linspace(a, b, INITIAL_SPLITS + 1)):
                stations.extend(self._subdivide(lo, hi, tol, 0))
        return self._points(np.array(stations))

    def _subdivide(self, lo: float, hi: float, tol: float, depth: int) -> list[float]:
        probes = np.array([lo, lo + (hi - lo) / 4, (lo + hi) / 2, lo + 3 * (hi - lo) / 4, hi])
        pts = self._points(probes)
        chord = pts[0] + np.outer((probes[1:4] - lo) / (hi - lo), pts[4] - pts[0])
        error = float(np.max(np.linalg.norm(pts[1:4] - chord, axis=1)))
        if error <= tol or depth >= MAX_SUBDIVISION_DEPTH:
            return [hi]
        mid = (lo + hi) / 2
        return self._subdivide(lo, mid, tol, depth + 1) + self._subdivide(mid, hi, tol, depth + 1)

    def to_shapely(self, tol: float = 0.01) -> shapely.LineString:
        """Returns a LinearRing for closed curves and a LineString otherwise."""
        pts = self.to_polyline(tol)
        return shapely.LinearRing(pts) if self.closed else shapely.LineString(pts)

    # Any: construction recipes are JSON objects whose values depend on the curve kind.
    def construction(self) -> dict[str, Any]:
        """Returns the JSON recipe that rebuilds this curve."""
        return self.model_dump(mode="json")


def _pairs(values: FloatArray) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in itertools.pairwise(values) if b > a]


def _unit(vectors: Points) -> Points:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    if np.any(norms < 1e-15):
        raise ZeroLengthError(
            "tangent of a zero-length curve is undefined",
            hint="give the curve a non-zero length",
        )
    unit: Points = vectors / norms
    return unit
