"""
form_fns.py --- the immutable registry of shape generators behind `generated` forms

A generator is a pure function `fn(params, n) -> Points` returning n stage-plane points in
slot order. Every parameter must have a default, so `fn({}, n)` always works; registration
probes that call and refuses a generator that crashes. The bundled generators validate their
parameters through typed models, so a wrong parameter raises SchemaError before any geometry
is built.

Contains:
    FormFn: names the generator signature
    FormFnRegistry: maps generator names to functions, read-only
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
from typing import Annotated, Any, ClassVar

import numpy as np

from e8s.choreo.form_models import (
    FormFnInfo,
    RoseFormParams,
    SineWaveFormParams,
    SpiralFormParams,
)
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.readonly import ReadOnly
from e8s.core.types import FrozenModel, Points
from e8s.core.util import close_matches
from e8s.geo.curves import Polyline
from e8s.geo.shapes import spiral
from e8s.geo.spacing import space_along

PROBE_COUNT = 4
CURVE_SAMPLES = 721

# Any: generator parameters are JSON values whose meaning each generator defines.
type FormFn = Callable[[Mapping[str, Any], int], Points]


class FormFnRegistry(FrozenModel):
    """Maps generator names to functions; registering returns a new registry.

    Attributes:
        fns: Generator name to function; read-only, so the shared default never changes.
    """

    fns: Annotated[Mapping[str, FormFn], ReadOnly()]
    _json_refusal: ClassVar[str | None] = "registries are not stored; rebuild with register_form_fn"

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


# Any: generator parameters are JSON values.
def spiral_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly along an Archimedean spiral.

    Params are the fields of `SpiralFormParams`: `center` (0, 10), `r0` 1, `r1` 5, `turns` 2,
    `start_deg` 0 (counter-clockwise from +x). A bad or unknown parameter raises SchemaError.

    >>> spiral_points({"turns": 1.0}, 3).shape
    (3, 2)
    """
    p = SpiralFormParams._validated(params)
    curve = spiral(p.center, p.r0, p.r1, p.turns, p.start_deg)
    return space_along(curve, n)


# Any: generator parameters are JSON values.
def sine_wave_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly by arc length along a sine wave running toward +x.

    Params are the fields of `SineWaveFormParams`: `start` (-8, 10), `length` 16, `amplitude` 2,
    `wavelength` 8, `phase_deg` 0. A bad or unknown parameter raises SchemaError.

    >>> sine_wave_points({"amplitude": 0.0}, 3)[:, 0].round(6).tolist()
    [-8.0, 0.0, 8.0]
    """
    p = SineWaveFormParams._validated(params)
    x0, y0 = p.start
    x = np.linspace(0.0, p.length, CURVE_SAMPLES)
    y = p.amplitude * np.sin(2 * math.pi * x / p.wavelength + math.radians(p.phase_deg))
    pts = tuple((x0 + float(a), y0 + float(b)) for a, b in zip(x, y, strict=True))
    return space_along(Polyline(points=pts), n)


# Any: generator parameters are JSON values.
def rose_points(params: Mapping[str, Any], n: int) -> Points:
    """Spaces n spots evenly around a flower whose petals never reach the center.

    The outline is r(θ) = radius · (inner + (1 - inner) · |cos(petals · θ / 2)|), a simple
    closed curve, so spots never coincide. Params are the fields of `RoseFormParams`: `center`
    (0, 10), `radius` 5, `petals` 5, `inner` 0.4 (share of the radius between petals). A bad
    or unknown parameter raises SchemaError.

    >>> len(rose_points({}, 10))
    10
    """
    p = RoseFormParams._validated(params)
    cx, cy = p.center
    theta = np.linspace(0.0, 2 * math.pi, CURVE_SAMPLES)[:-1]
    r = p.radius * (p.inner + (1 - p.inner) * np.abs(np.cos(p.petals * theta / 2)))
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
    # A generator is caller code, so its probe may raise anything; every crash means the
    # generator cannot be registered, and the error names it instead of leaking its type.
    except Exception as error:
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

    The probe must return 4 finite (x, y) rows; any exception it raises becomes SchemaError.

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
