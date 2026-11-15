"""
replace.py --- swaps a show's sheet, carrying every position over through one affine map

The map is fitted once per replacement and applied to placements, facings, form curves, path
waypoints, pivots, facing targets, route curves and show zones. Checks are not run here; the
caller runs a checker on the result.

Contains:
    SheetMapping: names the ways positions are carried to the new sheet
    RESTRICTED_ZONE_KINDS: names the zone kinds the report flags
    similarity_fit(): fits the least-squares similarity between matched points (Umeyama)
    replace_sheet(): returns the show on a new sheet plus a report of what moved
"""

import math
from typing import TYPE_CHECKING, Literal

import numpy as np

from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    Form,
    PathRow,
    Route,
    Set,
    TransitionSpec,
    Waypoint,
)
from showpy.core.errors import (
    GeometryError,
    LockedError,
    SchemaError,
    UnknownRefError,
    VersionError,
)
from showpy.core.ids import FormId, SpotId, ZoneId, parse_ref
from showpy.core.selection import Selection
from showpy.core.types import Degrees, Point, Points, Region, Ring
from showpy.core.units import angle_diff
from showpy.core.util import natural_sort
from showpy.geo.affine import (
    Matrix,
    scale_matrix,
    transform_curve,
    transform_facing,
    transform_points,
)
from showpy.geo.curves import curve_from_construction
from showpy.sheet.bounds import landmark, landmark_keys, on_surface, zones_at
from showpy.sheet.models import Landmark, Sheet, SheetReplaceReport, Zone, ZoneKind

if TYPE_CHECKING:
    from showpy.show.models import Show

type SheetMapping = Literal["landmarks", "origin", "scale"]

MAPPINGS = ("landmarks", "origin", "scale")
RESTRICTED_ZONE_KINDS = frozenset(
    {ZoneKind.RESTRICTED, ZoneKind.PIT, ZoneKind.AUDIENCE, ZoneKind.SAFETY}
)
MIN_SHARED_LANDMARKS = 2
SPREAD_TOL = 1e-12
IDENTITY_TOL = 1e-12
MOVE_TOL = 1e-9
FACING_TOL = 1e-9
ALL_SPOTS = "all"
CURVE_KEY = "curve"


def similarity_fit(src: Points, dst: Points) -> Matrix:
    """Fits the rotation, uniform scale and translation that best maps `src` onto `dst`.

    Least squares over matched rows, without reflection (Umeyama 1991).

    Args:
        src: (N, 2) points on the old sheet, N >= 2.
        dst: (N, 2) matching points on the new sheet.

    Returns:
        matrix: 3x3 affine map.

    >>> fit = similarity_fit(np.array([[0.0, 0], [1, 0]]), np.array([[0.0, 1], [2, 1]]))
    >>> fit.round(6).tolist()
    [[2.0, 0.0, 0.0], [0.0, 2.0, 1.0], [0.0, 0.0, 1.0]]
    """
    src, dst = np.asarray(src, dtype=float), np.asarray(dst, dtype=float)
    spread = float(((src - src.mean(axis=0)) ** 2).sum()) / max(len(src), 1)
    if src.shape != dst.shape or len(src) < MIN_SHARED_LANDMARKS or spread < SPREAD_TOL:
        raise GeometryError(
            "similarity fit needs at least two distinct matched points",
            hint="match two or more landmarks that are not on top of each other",
            context={"src": src.tolist(), "dst": dst.tolist()},
        )
    mu_src, mu_dst = src.mean(axis=0), dst.mean(axis=0)
    covariance = (dst - mu_dst).T @ (src - mu_src) / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    keep_handedness = np.diag([1.0, 1.0 if np.linalg.det(u) * np.linalg.det(vt) >= 0 else -1.0])
    rotation = u @ keep_handedness @ vt
    scale = float(np.trace(np.diag(singular) @ keep_handedness)) / spread
    matrix = np.eye(3)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = mu_dst - scale * rotation @ mu_src
    return matrix


def _shared_points(old: Sheet, new: Sheet) -> tuple[Points, Points]:
    by_key: dict[str, Landmark] = {}
    for mark in (m for m in new.landmarks if m.kind == "point"):
        for key in landmark_keys(mark):
            by_key.setdefault(key, mark)
    pairs: list[tuple[Point, Point]] = []
    used: set[str] = set()
    for mark in (m for m in old.landmarks if m.kind == "point"):
        match = next((by_key[k] for k in landmark_keys(mark) if k in by_key), None)
        if match is not None and match.id not in used:
            pairs.append((mark.point, match.point))
            used.add(match.id)
    src = np.array([p for p, _ in pairs], dtype=float).reshape(-1, 2)
    dst = np.array([q for _, q in pairs], dtype=float).reshape(-1, 2)
    return src, dst


def _fit(
    old: Sheet, new: Sheet, mapping: SheetMapping
) -> tuple[Matrix, SheetMapping, tuple[str, ...]]:
    if mapping == "origin":
        return np.eye(3), "origin", ()
    if mapping == "scale":
        sx, sy = new.size_m[0] / old.size_m[0], new.size_m[1] / old.size_m[1]
        return scale_matrix(sx, sy, (0.0, 0.0)), "scale", ()
    src, dst = _shared_points(old, new)
    try:
        return similarity_fit(src, dst), "landmarks", ()
    except GeometryError:
        note = (
            f"{old.ref} and {new.ref} share fewer than 2 distinct point landmarks; "
            "kept coordinates (origin mapping)"
        )
        return np.eye(3), "origin", (note,)


def _point(p: Point, matrix: Matrix) -> Point:
    q = transform_points(np.asarray([p], dtype=float), matrix)[0]
    return float(q[0]) + 0.0, float(q[1]) + 0.0


def _facing(theta: Degrees, matrix: Matrix) -> Degrees:
    mapped = transform_facing(theta, matrix)
    return theta if abs(angle_diff(theta, mapped)) < FACING_TOL else mapped


def _map_set(s: Set, matrix: Matrix, force: bool) -> Set:
    updates = {}
    for placement in s.placements:
        x, y = _point(placement.point, matrix)
        facing = _facing(placement.facing, matrix)
        if math.dist((x, y), placement.point) > MOVE_TOL or facing != placement.facing:
            updates[placement.entity] = {"x": x, "y": y, "facing": facing}
    return s.with_placements(updates, force=force) if updates else s


def _locked(what: str) -> LockedError:
    return LockedError(
        f"{what} is locked and the new sheet moves it",
        hint="unlock it first, or pass force=True as the designer",
        context={"item": what},
    )


def _map_recipe(recipe: dict[str, object], matrix: Matrix, owner: str) -> dict[str, object]:
    try:
        curve = curve_from_construction(recipe)
    except SchemaError as error:
        raise SchemaError(
            f"{owner} holds a curve recipe that does not parse",
            hint="store curves as curve.construction() recipes",
            context={"owner": owner, "errors": error.context.get("errors", [])},
        ) from error
    return transform_curve(curve, matrix).construction()


def _map_form(form: Form, matrix: Matrix, force: bool) -> tuple[Form, str | None]:
    recipe = form.geometry.get(CURVE_KEY)
    if not isinstance(recipe, dict):
        note = f"form {form.id} ({form.kind}) has no stored curve; regenerate it on the new sheet"
        return form, note
    if form.locked and not force:
        raise _locked(f"form {form.id}")
    mapped = _map_recipe(recipe, matrix, f"form {form.id}")
    return form.model_copy(update={"geometry": {**form.geometry, CURVE_KEY: mapped}}), None


def _map_target(
    mode: FacingMode, value: Degrees | Point | SpotId | None, matrix: Matrix
) -> Degrees | Point | SpotId | None:
    if isinstance(value, tuple):
        return _point(value, matrix)
    if isinstance(value, float) and mode == FacingMode.FIXED:
        return _facing(value, matrix)
    return value


def _map_waypoint(waypoint: Waypoint, matrix: Matrix) -> Waypoint:
    x, y = _point((waypoint.x, waypoint.y), matrix)
    return Waypoint(x=x, y=y, at=waypoint.at)


def _map_row(row: PathRow, matrix: Matrix, force: bool) -> PathRow:
    mapped = row.model_copy(
        update={
            "waypoints": tuple(_map_waypoint(wp, matrix) for wp in row.waypoints),
            "pivot": None if row.pivot is None else _point(row.pivot, matrix),
            "facing_value": _map_target(row.facing_mode, row.facing_value, matrix),
        }
    )
    if mapped != row and row.locked and not force:
        raise _locked(f"path of {row.entity}")
    return mapped


def _map_route(route: Route, matrix: Matrix) -> Route:
    return route.model_copy(update={"curve": _map_recipe(route.curve, matrix, f"route {route.id}")})


def _map_transition(spec: TransitionSpec, matrix: Matrix, force: bool) -> TransitionSpec:
    return spec.model_copy(
        update={
            "rows": {k: _map_row(row, matrix, force) for k, row in spec.rows.items()},
            "routes": {k: _map_route(route, matrix) for k, route in spec.routes.items()},
        }
    )


def _map_override(override: FacingOverride, matrix: Matrix) -> FacingOverride:
    value = _map_target(override.mode, override.value, matrix)
    return override.model_copy(update={"value": value})


def _map_ring(ring: Ring, matrix: Matrix) -> Ring:
    return tuple(_point(p, matrix) for p in ring)


def _map_zone(zone: Zone, matrix: Matrix) -> Zone:
    exterior = _map_ring(zone.polygon.exterior, matrix)
    holes = tuple(_map_ring(hole, matrix) for hole in zone.polygon.holes)
    return zone.model_copy(update={"polygon": Region(exterior=exterior, holes=holes)})


def _dependencies(show: "Show", sheet: Sheet) -> dict[str, str]:
    kind, ref_id, version = parse_ref(sheet.ref)
    if version is None:
        raise VersionError(
            f"{sheet.ref} has no version to pin",
            hint="save the sheet first so it has a version, e.g. sheet:my-field@2",
            context={"ref": sheet.ref},
        )
    old_kind, old_id, _ = parse_ref(show.sheet.ref)
    kept = {k: v for k, v in show.dependencies.items() if k != f"{old_kind}:{old_id}"}
    return {**kept, f"{kind}:{ref_id}": version}


def _coordinates_note(show: "Show", sheet: Sheet) -> tuple[dict[str, object], tuple[str, ...]]:
    origin = show.coordinates.origin
    if not isinstance(origin, str):
        return {}, ()
    try:
        landmark(sheet, origin)
    except UnknownRefError:
        note = f"coordinate origin {origin!r} is not on {sheet.ref}; using its default coordinates"
        return {"coordinates": sheet.default_coordinates}, (note,)
    return {}, ()


def _mapped_show(show: "Show", matrix: Matrix, force: bool) -> tuple["Show", tuple[str, ...]]:
    forms: dict[FormId, Form] = {}
    notes: list[str] = []
    for form_id, form in show.forms.items():
        forms[form_id], note = _map_form(form, matrix, force)
        notes += [note] if note else []
    mapped = show.model_copy(
        update={
            "sets": tuple(_map_set(s, matrix, force) for s in show.sets),
            "forms": forms,
            "transitions": tuple(_map_transition(t, matrix, force) for t in show.transitions),
            "facing_overrides": tuple(_map_override(o, matrix) for o in show.facing_overrides),
            "zones": tuple(_map_zone(z, matrix) for z in show.zones),
        }
    )
    return mapped, tuple(notes)


def _moved(old: "Show", new: "Show") -> dict[SpotId, tuple[Point, Point]]:
    moved: dict[SpotId, tuple[Point, Point]] = {}
    farthest: dict[SpotId, float] = {}
    for before, after in zip(old.sets, new.sets, strict=True):
        for a, b in zip(before.placements, after.placements, strict=True):
            distance = math.dist(a.point, b.point)
            if distance > max(MOVE_TOL, farthest.get(a.entity, 0.0)):
                farthest[a.entity] = distance
                moved[a.entity] = (a.point, b.point)
    return {spot: moved[spot] for spot in natural_sort(moved)}


def _applies(zone: Zone, spot: SpotId) -> bool:
    if isinstance(zone.applies_to, str):
        # An unresolved selection expression counts as everyone, so nothing slips through.
        return True
    targets = zone.applies_to.ids if isinstance(zone.applies_to, Selection) else zone.applies_to
    return spot in targets


def _flags(show: "Show") -> tuple[tuple[SpotId, ...], tuple[tuple[SpotId, ZoneId], ...]]:
    outside: set[SpotId] = set()
    hits: dict[tuple[SpotId, ZoneId], None] = {}
    count = 0.0
    for s in show.sets:
        count += s.counts
        for p in (p for p in s.placements if p.on_field):
            if not on_surface(show.sheet, p.point):
                outside.add(p.entity)
            for zone in zones_at(show, p.point, t=count):
                if zone.kind in RESTRICTED_ZONE_KINDS and _applies(zone, p.entity):
                    hits[(p.entity, zone.id)] = None
    return tuple(natural_sort(outside)), tuple(hits)


def replace_sheet(
    show: "Show", sheet: Sheet, mapping: SheetMapping = "landmarks", force: bool = False
) -> tuple["Show", SheetReplaceReport]:
    """Swaps the show's sheet and carries every position over; checks are left to the caller.

    `landmarks` fits a similarity to point landmarks sharing a name or alias (falling back to
    `origin` below two), `origin` keeps coordinates, `scale` scales x and y by the size ratio.

    Args:
        show: Show to move onto the new sheet.
        sheet: The new sheet.
        mapping: How positions are carried over.
        force: Move pinned placements and locked forms and paths too.

    Returns:
        replaced: The show on the new sheet, with its sheet dependency re-pinned.
        report: What moved, what lands off the sheet or in a restricted zone, form notes.

    >>> from showpy.sheet.templates import blank_sheet
    >>> from showpy.examples import two_set_show
    >>> replace_sheet(two_set_show(), blank_sheet(80, 40), "scale")[1].moved["T2"]
    ((2.0, 9.0), (4.0, 18.0))
    """
    if mapping not in MAPPINGS:
        raise SchemaError(
            f"unknown mapping {mapping!r}",
            hint=f"use one of {', '.join(MAPPINGS)}",
            context={"mapping": mapping},
        )
    matrix, used, fit_notes = _fit(show.sheet, sheet, mapping)
    moved_show: Show = show
    form_notes: tuple[str, ...] = ()
    if not np.allclose(matrix, np.eye(3), rtol=0.0, atol=IDENTITY_TOL):
        moved_show, form_notes = _mapped_show(show, matrix, force)
    coordinates, coordinate_notes = _coordinates_note(show, sheet)
    replaced = moved_show.model_copy(
        update={"sheet": sheet, "dependencies": _dependencies(show, sheet), **coordinates}
    )
    outside, in_zones = _flags(replaced)
    report = SheetReplaceReport(
        mapping=used,
        moved=_moved(show, replaced),
        outside=outside,
        in_zones=in_zones,
        form_notes=(*fit_notes, *form_notes, *coordinate_notes),
    )
    return replaced, report
