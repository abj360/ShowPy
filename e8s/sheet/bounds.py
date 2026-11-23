"""
bounds.py --- bounds tests, landmark lookup, nearest landmarks and zones at a point

Contains:
    LandmarkKind: names the landmark geometry kinds
    on_surface(): reports whether a point lies on the sheet, edge included
    walkable(): reports whether a point lies in the walkable area
    landmark_keys(): returns a landmark's normalized name and aliases, for matching
    landmark(): finds a landmark by name, ID or alias, ignoring case
    landmark_anchor(): returns the point a landmark stands for when used as an origin
    nearest_landmarks(): lists the closest landmarks with their distances
    zones_at(): lists the sheet and show zones covering a point, optionally at a count
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

import shapely

from e8s.core.deprecation import leaving_call
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.types import Count, Meters, Point, Region
from e8s.core.util import close_matches
from e8s.sheet.models import Landmark, Sheet, Zone, normalized_name

if TYPE_CHECKING:
    from e8s.show.models import Show

type LandmarkKind = Literal["point", "line", "area"]


def _covers(region: Region, p: Point) -> bool:
    return bool(region.to_shapely().covers(shapely.Point(p)))


def _on_surface(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies on the sheet outline or inside it.

    >>> from e8s.sheet.templates import _blank_sheet
    >>> _on_surface(_blank_sheet(10, 6), (5.0, 6.0)), _on_surface(_blank_sheet(10, 6), (0.0, -1.0))
    (True, False)
    """
    return _covers(Region(exterior=sheet.outline), p)


@leaving_call(since="0.4.0", remove="0.7.0", replacement="on_sheet")
def on_surface(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies on the sheet outline or inside it.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _on_surface(sheet, p)


def walkable(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies in the walkable area, holes excluded.

    >>> from e8s.sheet.templates import _blank_sheet
    >>> walkable(_blank_sheet(10, 6), (1.0, 1.0))
    True
    """
    return _covers(sheet.walkable, p)


def _names(mark: Landmark) -> tuple[str, ...]:
    return (mark.id, mark.name, *mark.aliases)


def _landmark_keys(mark: Landmark) -> tuple[str, ...]:
    """Returns a landmark's name and aliases, case-folded with spaces collapsed, for matching.

    >>> from e8s.sheet.surfaces import _point_landmark
    >>> _landmark_keys(_point_landmark("c", "Center  Stage", (0.0, 0.0), "CS"))
    ('center stage', 'cs')
    """
    return tuple(normalized_name(n) for n in (mark.name, *mark.aliases))


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def landmark_keys(mark: Landmark) -> tuple[str, ...]:
    """Returns a landmark's name and aliases, case-folded with spaces collapsed, for matching.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _landmark_keys(mark)


def _landmark(sheet: Sheet, name: str) -> Landmark:
    """Finds a landmark by name, ID or alias; case and repeated spaces are ignored.

    >>> from e8s.sheet.templates import _get_sheet
    >>> _landmark(_get_sheet("sheet:hs-football"), "Home Hash").id
    'front-hash'
    """
    wanted = normalized_name(name)
    for mark in sheet.landmarks:
        if wanted in (normalized_name(n) for n in _names(mark)):
            return mark
    known = [n for mark in sheet.landmarks for n in _names(mark)]
    close = close_matches(name, known)
    raise UnknownRefError(
        f"{sheet.ref} has no landmark {name!r}",
        hint=f"did you mean {', '.join(close)}?" if close else "use a landmark name, ID or alias",
        context={"ref": sheet.ref, "name": name},
    )


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def landmark(sheet: Sheet, name: str) -> Landmark:
    """Finds a landmark by name, ID or alias; case and repeated spaces are ignored.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _landmark(sheet, name)


def _landmark_anchor(mark: Landmark) -> Point:
    """Returns the point a landmark stands for: the point itself, else its vertex centroid.

    >>> from e8s.sheet.surfaces import _line_landmark
    >>> _landmark_anchor(_line_landmark("l", "line", (0.0, 0.0), (4.0, 2.0)))
    (2.0, 1.0)
    """
    xs = [p[0] for p in mark.geometry]
    ys = [p[1] for p in mark.geometry]
    return sum(xs) / len(xs), sum(ys) / len(ys)


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def landmark_anchor(mark: Landmark) -> Point:
    """Returns the point a landmark stands for: the point itself, else its vertex centroid.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _landmark_anchor(mark)


def _geometry(mark: Landmark) -> shapely.Geometry:
    if mark.kind == "point":
        return shapely.Point(mark.point)
    if mark.kind == "line":
        return shapely.LineString(mark.geometry)
    return shapely.Polygon(mark.geometry)


def _nearest_landmarks(
    sheet: Sheet, p: Point, k: int = 3, kinds: Iterable[LandmarkKind] | None = None
) -> list[tuple[Landmark, Meters]]:
    """Lists the k landmarks closest to `p`, nearest first; ties break by landmark ID.

    Distance is to the landmark's geometry: 0 inside an area, perpendicular to a line.

    Args:
        sheet: Sheet whose landmarks are searched.
        p: Point to measure from.
        k: How many landmarks to return.
        kinds: Only these landmark kinds; None means all.

    Returns:
        nearest: (landmark, distance in meters) pairs.

    >>> from e8s.sheet.templates import _get_sheet
    >>> [lm.id for lm, _ in _nearest_landmarks(_get_sheet("sheet:proscenium-stage"), (0, 4), 1)]
    ['center-stage']
    """
    if k < 0:
        raise SchemaError(f"k must be >= 0, got {k}", hint="pass k >= 0", context={"k": k})
    allowed = None if kinds is None else set(kinds)
    target = shapely.Point(p)
    measured = [
        (mark, float(shapely.distance(_geometry(mark), target)))
        for mark in sheet.landmarks
        if allowed is None or mark.kind in allowed
    ]
    measured.sort(key=lambda pair: (pair[1], pair[0].id))
    return measured[:k]


@leaving_call(since="0.4.0", remove="0.7.0", replacement="markings_near")
def nearest_landmarks(
    sheet: Sheet, p: Point, k: int = 3, kinds: Iterable[LandmarkKind] | None = None
) -> list[tuple[Landmark, Meters]]:
    """Lists the k landmarks closest to `p`, nearest first; ties break by landmark ID.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _nearest_landmarks(sheet, p, k, kinds)


def _zones_at(show: "Show", p: Point, t: Count | None = None) -> list[Zone]:
    """Lists the sheet zones, then the show zones, that cover `p`.

    Args:
        show: Show whose sheet and show-specific zones are searched.
        p: Point to test.
        t: Global count; when given, only zones active at that count are returned.

    Returns:
        zones: Covering zones, sheet zones first, each in stored order.

    >>> from e8s.examples import two_set_show
    >>> [z.id for z in _zones_at(two_set_show(), (0.0, -1.0))]
    ['pit']
    """
    return [
        zone
        for zone in (*show.sheet.zones, *show.zones)
        if (t is None or zone.is_active(t)) and _covers(zone.polygon, p)
    ]


@leaving_call(since="0.4.0", remove="0.7.0", replacement=("Marking", "markings_near"))
def zones_at(show: "Show", p: Point, t: Count | None = None) -> list[Zone]:
    """Lists the sheet zones, then the show zones, that cover `p`.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _zones_at(show, p, t)
