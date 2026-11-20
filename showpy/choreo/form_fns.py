"""
form_fns.py --- the immutable registry of shape generators behind `generated` forms

A generator is a pure function `fn(params, n) -> Points` returning n stage-plane points in
slot order. Every parameter must have a default, so `fn({}, n)` always works; registration
probes that call.

Contains:
    FormFn: names the generator signature
    FormFnRegistry: maps generator names to functions, immutably
    FormFnRegistry.get(): returns a generator, suggesting close names when it is missing
    spiral_points(): spaces spots evenly along an Archimedean spiral
    sine_wave_points(): spaces spots evenly along a sine wave
    rose_points(): spaces spots evenly around a petalled flower outline
    DEFAULT_FORM_FNS: holds the built-in generators
    register_form_fn(): returns a registry extended with one validated generator
    form_fns(): lists the generators in a registry with their summaries
"""

import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from showpy.choreo.form_models import FormFnInfo
from showpy.core.errors import SchemaError, ShowPyError, UnknownRefError
from showpy.core.types import FrozenModel, Point, Points
from showpy.core.util import as_points, close_matches
from showpy.geo.curves import Polyline
from showpy.geo.shapes import spiral
from showpy.geo.spacing import space_along

PROBE_COUNT = 4
CURVE_SAMPLES = 721
DEFAULT_CENTER = (0.0, 10.0)
SPIRAL_DEFAULTS = {"r0": 1.0, "r1": 5.0, "turns": 2.0, "start_deg": 0.0}
SINE_DEFAULTS = {"length": 16.0, "amplitude": 2.0, "wavelength": 8.0, "phase_deg": 0.0}
SINE_START = (-8.0, 10.0)
ROSE_DEFAULTS = {"radius": 5.0, "petals": 5, "inner": 0.4}

# Any: generator parameters are JSON values whose meaning each generator defines.
type FormFn = Callable[[Mapping[str, Any], int], Points]


class FormFnRegistry(FrozenModel):
    """Maps generator names to functions; registering returns a new registry.

    Attributes:
        fns: Generator name to function.
    """

    fns: dict[str, FormFn]

    def get(self, name: str) -> FormFn:
        """Returns a generator, raising UnknownRefError with close names in the hint."""
        if name in self.fns:
            return self.fns[name]
        close = close_matches(name, self.fns)
        raise UnknownRefError(
            f"unknown form generator {name!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "register it first",
            context={"fn": name, "known": sorted(self.fns)},
        )


def _point(value: object, default: Point) -> Point:
    if value is None:
        return default
    x, y = as_points(value)[0]
    return float(x), float(y)


# Any: generator parameters are JSON values.
def _number(params: Mapping[str, Any], defaults: Mapping[str, float], key: str) -> float:
    return float(params.get(key, defaults[key]))


# Any: generator parameters are JSON values.
def spiral_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly along an Archimedean spiral.

    Params: `center` (0, 10), `r0` 1, `r1` 5, `turns` 2, `start_deg` 0 (counter-clockwise
    from +x).

    >>> spiral_points({"turns": 1.0}, 3).shape
    (3, 2)
    """
    curve = spiral(
        _point(params.get("center"), DEFAULT_CENTER),
        _number(params, SPIRAL_DEFAULTS, "r0"),
        _number(params, SPIRAL_DEFAULTS, "r1"),
        _number(params, SPIRAL_DEFAULTS, "turns"),
        _number(params, SPIRAL_DEFAULTS, "start_deg"),
    )
    return space_along(curve, n)


# Any: generator parameters are JSON values.
def sine_wave_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly by arc length along a sine wave running toward +x.

    Params: `start` (-8, 10), `length` 16, `amplitude` 2, `wavelength` 8, `phase_deg` 0.

    >>> sine_wave_points({"amplitude": 0.0}, 3)[:, 0].round(6).tolist()
    [-8.0, 0.0, 8.0]
    """
    x0, y0 = _point(params.get("start"), SINE_START)
    length = _number(params, SINE_DEFAULTS, "length")
    amplitude = _number(params, SINE_DEFAULTS, "amplitude")
    wavelength = _number(params, SINE_DEFAULTS, "wavelength")
    phase = math.radians(_number(params, SINE_DEFAULTS, "phase_deg"))
    x = np.linspace(0.0, length, CURVE_SAMPLES)
    y = amplitude * np.sin(2 * math.pi * x / wavelength + phase)
    pts = tuple((x0 + float(a), y0 + float(b)) for a, b in zip(x, y, strict=True))
    return space_along(Polyline(points=pts), n)


# Any: generator parameters are JSON values.
def rose_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly around a flower whose petals never reach the center.

    The outline is r(θ) = radius · (inner + (1 - inner) · |cos(petals · θ / 2)|), a simple
    closed curve, so spots never coincide. Params: `center` (0, 10), `radius` 5,
    `petals` 5, `inner` 0.4 (fraction of the radius between petals).

    >>> len(rose_points({}, 10))
    10
    """
    cx, cy = _point(params.get("center"), DEFAULT_CENTER)
    radius = _number(params, ROSE_DEFAULTS, "radius")
    petals = _number(params, ROSE_DEFAULTS, "petals")
    inner = _number(params, ROSE_DEFAULTS, "inner")
    theta = np.linspace(0.0, 2 * math.pi, CURVE_SAMPLES)[:-1]
    r = radius * (inner + (1 - inner) * np.abs(np.cos(petals * theta / 2)))
    pts = tuple(
        (cx + float(a), cy + float(b))
        for a, b in zip(r * np.cos(theta), r * np.sin(theta), strict=True)
    )
    return space_along(Polyline(points=pts, closed=True), n)


DEFAULT_FORM_FNS = FormFnRegistry(
    fns={"rose": rose_points, "sine_wave": sine_wave_points, "spiral": spiral_points}
)


def _probe(name: str, fn: FormFn) -> None:
    try:
        pts = np.asarray(fn({}, PROBE_COUNT), dtype=float)
    except (ShowPyError, TypeError, ValueError, KeyError) as error:
        raise SchemaError(
            f"form generator {name!r} fails with default parameters: {error}",
            hint="give every parameter a default so fn({}, n) works",
            context={"fn": name},
        ) from error
    if pts.shape != (PROBE_COUNT, 2) or not np.all(np.isfinite(pts)):
        raise SchemaError(
            f"form generator {name!r} returned shape {pts.shape} for n={PROBE_COUNT}",
            hint="return n finite (x, y) rows",
            context={"fn": name, "shape": pts.shape},
        )


def register_form_fn(registry: FormFnRegistry, name: str, fn: FormFn) -> FormFnRegistry:
    """Returns a new registry that also holds `fn`, after probing it with `fn({}, 4)`.

    >>> def dots(params, n):
    ...     return np.stack([np.arange(n, dtype=float), np.zeros(n)], axis=1)
    >>> sorted(register_form_fn(DEFAULT_FORM_FNS, "dots", dots).fns)
    ['dots', 'rose', 'sine_wave', 'spiral']
    """
    if not name or name in registry.fns:
        raise SchemaError(
            f"form generator name {name!r} is empty or taken",
            hint="pick a new, non-empty name",
            context={"known": sorted(registry.fns)},
        )
    _probe(name, fn)
    return FormFnRegistry(fns={**registry.fns, name: fn})


def form_fns(registry: FormFnRegistry = DEFAULT_FORM_FNS) -> list[FormFnInfo]:
    """Lists the generators in a registry, by name, with their docstring summaries.

    >>> [info.name for info in form_fns()]
    ['rose', 'sine_wave', 'spiral']
    """
    infos = []
    for name in sorted(registry.fns):
        doc = (registry.fns[name].__doc__ or "").strip()
        infos.append(FormFnInfo(name=name, summary=doc.splitlines()[0] if doc else ""))
    return infos
