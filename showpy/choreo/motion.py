"""
motion.py --- easing curves, facing turns and the immutable registry of custom motions

Every easing is a monotone map of [0, 1] onto itself with e(0) = 0 and e(1) = 1, so an eased
move still starts and ends on its placements. Custom motions must return `start` at u = 0 and
`end` at u = 1; registration checks this on a probe move.

Contains:
    ease(): maps time shares to distance shares through an Easing
    easing_breaks(): returns the time shares where an easing has kinks or pauses
    turn(): interpolates a facing in a chosen turn direction
    MotionFn: describes a pure custom motion function
    MotionInfo: describes one registered motion for listings
    MotionRegistry: holds named motion functions immutably
    MotionRegistry.get(): returns a motion by name
    MotionRegistry.names(): lists registered names
    register_motion(): returns a registry extended with one validated motion
    motions(): lists registered motions with their summaries
    weave(): moves with a sinusoidal side-to-side offset
    zigzag(): moves with a triangle-wave side offset
    figure_eight(): moves along a figure-eight offset from the straight line
    spiral(): circles the midpoint of the move, ending on the target
    DEFAULT_MOTIONS: holds the built-in motions
"""

import math
from collections.abc import Mapping
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from showpy.choreo.path_models import Easing, TurnDirection
from showpy.core.errors import PathError, UnknownRefError
from showpy.core.types import Degrees, FrozenModel, Point, Points
from showpy.core.units import angle_diff, normalize_angle
from showpy.core.util import close_matches

type FloatArray = npt.NDArray[np.float64]

PROBE_START: Point = (0.0, 0.0)
PROBE_END: Point = (4.0, 3.0)
PROBE_SAMPLES = 9
ENDPOINT_TOL = 1e-6
DEFAULT_AMPLITUDE_M = 1.0
DEFAULT_CYCLES = 2.0
DEFAULT_TURNS = 0


class MotionFn(Protocol):
    """Describes a pure custom motion: positions at progress u from start to end."""

    # Any: motion parameters are JSON values defined by each motion function.
    def __call__(
        self, u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]
    ) -> Points:
        """Returns positions (len(u), 2) at progress shares u."""
        ...


def _smoothstep(x: FloatArray) -> FloatArray:
    result: FloatArray = x * x * (3.0 - 2.0 * x)
    return result


def ease(easing: Easing, u: FloatArray) -> FloatArray:
    """Maps time shares u in [0, 1] to distance shares through an Easing.

    `strength` is the exponent for ease kinds, the time-share divisor for hold/burst kinds
    (hold_then_burst at 2 holds for the first half), and the number of steps for `steps`.

    >>> ease(Easing(kind="ease_in", strength=2.0), np.array([0.5])).tolist()
    [0.25]
    """
    u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
    k = max(easing.strength, 1.0)
    kind = easing.kind
    if kind == "linear":
        return u
    if kind == "ease_in":
        result: FloatArray = u**k
        return result
    if kind == "ease_out":
        return 1.0 - (1.0 - u) ** k
    if kind == "ease_in_out":
        return np.where(u < 0.5, 0.5 * (2 * u) ** k, 1.0 - 0.5 * (2 - 2 * u) ** k)
    if kind == "hold_then_burst":
        hold = 1.0 - 1.0 / k
        return np.clip((u - hold) * k, 0.0, 1.0)
    if kind == "burst_then_settle":
        return np.clip(u * k, 0.0, 1.0)
    if kind == "steps":
        return _steps(u, max(round(k), 1))
    times, shares = zip(*easing.points, strict=True)
    custom: FloatArray = np.interp(u, times, shares)
    return custom


def easing_breaks(easing: Easing) -> list[float]:
    """Returns the time shares inside (0, 1) where an easing has a kink or a pause.

    Segment sampling splits at these so periodic easings cannot alias the probes.

    >>> easing_breaks(Easing(kind="steps", strength=2.0))
    [0.5]
    """
    k = max(easing.strength, 1.0)
    breaks: list[float] = []
    if easing.kind == "steps":
        n = max(round(k), 1)
        breaks = [i / n for i in range(1, n)]
    elif easing.kind == "hold_then_burst":
        breaks = [1.0 - 1.0 / k]
    elif easing.kind == "burst_then_settle":
        breaks = [1.0 / k]
    elif easing.kind == "ease_in_out":
        breaks = [0.5]
    elif easing.kind == "custom":
        breaks = [t for t, _ in easing.points]
    return [b for b in breaks if 0.0 < b < 1.0]


def _steps(u: FloatArray, n: int) -> FloatArray:
    scaled = u * n
    whole = np.minimum(np.floor(scaled), n - 1)
    result: FloatArray = (whole + _smoothstep(scaled - whole)) / n
    return result


def turn(
    start: Degrees, end: Degrees, u: float, direction: TurnDirection = TurnDirection.SHORTEST
) -> Degrees:
    """Interpolates a facing from `start` to `end` at share u, turning the chosen way.

    >>> turn(350.0, 10.0, 0.5)
    0.0
    """
    if direction == TurnDirection.SHORTEST:
        delta = angle_diff(start, end)
    elif direction == TurnDirection.COUNTERCLOCKWISE:
        delta = normalize_angle(end - start)
    else:
        delta = -normalize_angle(start - end)
    return normalize_angle(start + delta * min(max(u, 0.0), 1.0))


def _frame(start: Point, end: Point, u: FloatArray) -> tuple[Points, FloatArray]:
    a, b = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    chord = b - a
    length = float(np.linalg.norm(chord))
    side = np.array([-chord[1], chord[0]]) / length if length > 0 else np.zeros(2)
    base: Points = a + np.outer(u, chord)
    return base, side


# Any: motion parameters are JSON values defined by each motion function.
def weave(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
    """Moves with a sinusoidal side-to-side offset.

    Params: `amplitude` meters (default 1), `cycles` (multiple of 0.5, default 2).

    >>> (weave(np.array([0.0, 1.0]), (0, 0), (4, 0), {}).round(9) + 0.0).tolist()
    [[0.0, 0.0], [4.0, 0.0]]
    """
    base, side = _frame(start, end, u)
    amplitude = float(params.get("amplitude", DEFAULT_AMPLITUDE_M))
    cycles = _half_integer(params.get("cycles", DEFAULT_CYCLES), "cycles")
    return base + np.outer(amplitude * np.sin(2 * np.pi * cycles * u), side)


# Any: motion parameters are JSON values defined by each motion function.
def zigzag(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
    """Moves with a triangle-wave side offset (sharp corners).

    Params: `amplitude` meters (default 1), `cycles` (multiple of 0.5, default 2).

    >>> zigzag(np.array([0.125]), (0, 0), (8, 0), {"cycles": 1}).round(9).tolist()
    [[1.0, 0.5]]
    """
    base, side = _frame(start, end, u)
    amplitude = float(params.get("amplitude", DEFAULT_AMPLITUDE_M))
    cycles = _half_integer(params.get("cycles", DEFAULT_CYCLES), "cycles")
    phase = cycles * u
    triangle = 4 * np.abs(phase - np.floor(phase + 0.75) + 0.25) - 1
    return base + np.outer(amplitude * triangle, side)


# Any: motion parameters are JSON values defined by each motion function.
def figure_eight(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
    """Moves along a figure-eight offset from the straight line.

    Params: `amplitude` meters of side offset (default 1).

    >>> (figure_eight(np.array([1.0]), (0, 0), (4, 0), {}).round(9) + 0.0).tolist()
    [[4.0, 0.0]]
    """
    base, side = _frame(start, end, u)
    chord = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    amplitude = float(params.get("amplitude", DEFAULT_AMPLITUDE_M))
    lateral = amplitude * np.sin(2 * np.pi * u)
    along = amplitude * np.sin(4 * np.pi * u) / 2
    unit = chord / max(float(np.linalg.norm(chord)), ENDPOINT_TOL)
    return base + np.outer(lateral, side) + np.outer(along, unit)


# Any: motion parameters are JSON values defined by each motion function.
def spiral(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
    """Circles the midpoint of the move: half a turn plus `turns` extra full turns.

    Params: `turns` (whole number >= 0, default 0), `clockwise` (default False).

    >>> (spiral(np.array([0.5]), (-1, 0), (1, 0), {}).round(9) + 0.0).tolist()
    [[0.0, -1.0]]
    """
    a, b = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    mid = (a + b) / 2
    radius = float(np.linalg.norm(a - mid))
    turns = int(params.get("turns", DEFAULT_TURNS))
    sign = -1.0 if params.get("clockwise", False) else 1.0
    theta0 = math.atan2(a[1] - mid[1], a[0] - mid[0])
    theta = theta0 + sign * math.pi * (1 + 2 * turns) * u
    result: Points = mid + radius * np.stack([np.cos(theta), np.sin(theta)], axis=1)
    return result


def _half_integer(value: object, name: str) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isclose(2 * number, round(2 * number)):
        raise PathError(
            f"{name} must be a multiple of 0.5 so the move ends on target",
            hint=f"use {name} like 1, 1.5 or 2",
            context={name: number},
        )
    return number


class MotionInfo(FrozenModel):
    """Describes one registered motion for listings.

    Attributes:
        name: Registered name.
        summary: First docstring line of the motion function.
    """

    name: str
    summary: str


class MotionRegistry:
    """Holds named motion functions; registration returns a new registry."""

    def __init__(self, fns: Mapping[str, MotionFn]) -> None:
        self._fns = dict(fns)

    def get(self, name: str) -> MotionFn:
        """Returns a motion by name, raising UnknownRefError with suggestions."""
        if name not in self._fns:
            close = close_matches(name, self._fns)
            raise UnknownRefError(
                f"unknown motion {name!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {self.names()}",
                context={"motion": name},
            )
        return self._fns[name]

    def names(self) -> list[str]:
        """Lists registered motion names, sorted."""
        return sorted(self._fns)

    def with_motion(self, name: str, fn: MotionFn) -> "MotionRegistry":
        """Returns a copy that also holds `fn` under `name`."""
        return MotionRegistry({**self._fns, name: fn})


def register_motion(registry: MotionRegistry, name: str, fn: MotionFn) -> MotionRegistry:
    """Returns a registry extended with `fn`, after checking it starts and ends on target.

    >>> "weave" in register_motion(DEFAULT_MOTIONS, "w2", weave).names()
    True
    """
    u = np.linspace(0.0, 1.0, PROBE_SAMPLES)
    pts = np.asarray(fn(u, PROBE_START, PROBE_END, {}), dtype=float)
    ends_ok = pts.shape == (PROBE_SAMPLES, 2) and np.allclose(
        pts[[0, -1]], [PROBE_START, PROBE_END], atol=ENDPOINT_TOL
    )
    if not ends_ok:
        raise PathError(
            f"motion {name!r} does not start at start and end at end",
            hint="return start at u=0 and end at u=1, shape (len(u), 2)",
            context={"motion": name},
        )
    return registry.with_motion(name, fn)


def motions(registry: MotionRegistry) -> list[MotionInfo]:
    """Lists registered motions with the first line of their docstrings.

    >>> [m.name for m in motions(DEFAULT_MOTIONS)][:2]
    ['figure_eight', 'spiral']
    """
    infos = []
    for name in registry.names():
        doc = (registry.get(name).__doc__ or "").strip()
        infos.append(MotionInfo(name=name, summary=doc.splitlines()[0] if doc else ""))
    return infos


DEFAULT_MOTIONS = MotionRegistry(
    {"weave": weave, "zigzag": zigzag, "figure_eight": figure_eight, "spiral": spiral}
)
