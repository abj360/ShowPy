"""
spots.py --- draws spots on the sheet and manages groups, labels, named selections and footprints

Contains:
    add_spot(): adds one spot at a point, present in every set
    add_spots_along(): adds n spots evenly along a curve
    fill_spots(): fills an area with spots at an interval or a target count
    duplicate_spots(): copies spots at an offset under new labels
    delete_spots(): removes spots from the cast and every reference to them
    regroup(): moves spots to another group and relabels them to its prefix
    add_group(): adds a group
    edit_group(): changes a group's fields
    remove_group(): removes a group, moving its spots to another first
    relabel(): renumbers every group under a label scheme
    define_selection(): stores a named selection
    set_shape(): sets the footprint of spots
    set_claimable(): sets who may claim spots once published
"""

import math
import string
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, get_args

from showpy.cast.models import Cast, Claimable, Group, LabelScheme, Shape, Spot
from showpy.cast.rewrite import faces_entity, rewrite_spot_ids
from showpy.cast.select import label_prefix, select
from showpy.choreo.models import Placement
from showpy.core.errors import (
    LockedError,
    SchemaError,
    SpacingError,
    UnknownRefError,
    field_errors,
)
from showpy.core.errors import ValidationError as ShowPyValidationError
from showpy.core.ids import SetId, SpotId
from showpy.core.selection import Selector
from showpy.core.types import Meters, Point, Region
from showpy.core.util import close_matches, natural_sort
from showpy.geo.curve_base import Curve
from showpy.geo.regions import Area
from showpy.geo.spacing import fill, space_along

if TYPE_CHECKING:
    from showpy.show.models import Show

SELECTION_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "_.-")
FILL_SEARCH_STEPS = 40
FILL_BRACKET_STEPS = 30
FILL_START_FACTOR = 0.5


def _group(cast: Cast, key: str) -> Group:
    if key not in cast.groups:
        close = close_matches(key, list(cast.groups))
        raise UnknownRefError(
            f"unknown group {key!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "add the group first",
            context={"group": key},
        )
    return cast.groups[key]


# Any: changes are arbitrary Cast fields, validated by the rebuilt model.
def _with_cast(show: "Show", **changes: Any) -> "Show":
    cast = Cast.model_validate({**dict(show.cast), **changes})
    return show.replace(cast=cast)


def _allocate_labels(cast: Cast, group_keys: Sequence[str]) -> list[SpotId]:
    scheme = cast.label_scheme
    taken = set(cast.ids())
    cursor: dict[str, int] = {}
    labels = []
    for key in group_keys:
        prefix = scheme.prefix_for(_group(cast, key))
        number = cursor.get(prefix, scheme.start)
        while scheme.label(prefix, number) in taken:
            number += 1
        label = scheme.label(prefix, number)
        taken.add(label)
        labels.append(label)
        cursor[prefix] = number + 1
    return labels


def _default_shape(group: Group) -> Shape:
    radius = group.default_radius
    return Shape(radius_m=radius, width_m=2 * radius, depth_m=2 * radius)


def _new_placements(entries: Sequence[tuple[Spot, Point]], on_field: bool) -> tuple[Placement, ...]:
    return tuple(Placement(entity=s.id, x=p[0], y=p[1], on_field=on_field) for s, p in entries)


def _insert_spots(show: "Show", entries: Sequence[tuple[Spot, Point]], set_id: SetId) -> "Show":
    created_at = show.set_index(set_id)
    sets = tuple(
        s.replace(placements=s.placements + _new_placements(entries, index >= created_at))
        for index, s in enumerate(show.sets)
    )
    new_spots = tuple(spot for spot, _ in entries)
    return _with_cast(show, spots=show.cast.spots + new_spots).replace(sets=sets)


def _add_points(show: "Show", points: Sequence[Point], group: str, set_id: SetId) -> "Show":
    show.set_index(set_id)
    shape = _default_shape(_group(show.cast, group))
    labels = _allocate_labels(show.cast, [group] * len(points))
    entries = [
        (Spot(id=label, group=group, shape=shape), (float(p[0]), float(p[1])))
        for label, p in zip(labels, points, strict=True)
    ]
    return _insert_spots(show, entries, set_id)


def add_spot(show: "Show", point: Point, group: str, set_id: SetId) -> "Show":
    """Adds one spot at `point`, on the field from `set_id` on and off it before.

    The label is the lowest free number under the group's prefix (the label scheme's
    override wins); the footprint is a circle of the group's default radius.

    >>> from showpy.examples import two_set_show
    >>> show = add_spot(two_set_show(), (1.0, 2.0), "trumpets", "2")
    >>> show.cast.ids()[-1], show.set("1").get("T5").on_field
    ('T5', False)
    """
    return _add_points(show, [point], group, set_id)


def add_spots_along(show: "Show", curve: Curve, n: int, group: str, set_id: SetId) -> "Show":
    """Adds n spots spaced evenly by arc length along a curve, in curve order.

    >>> from showpy.examples import two_set_show
    >>> from showpy.geo.shapes import line
    >>> len(add_spots_along(two_set_show(), line((0, 0), (4, 0)), 3, "trumpets", "1").cast.spots)
    7
    """
    points = space_along(curve, n)
    return _add_points(show, [(p[0], p[1]) for p in points.tolist()], group, set_id)


def _fill_count(area: Area, interval: Meters) -> int:
    return len(fill(area, interval))


def _bracket_interval(area: Area, n: int) -> tuple[Meters, Meters]:
    guess = math.sqrt(area.area / n)
    lo, hi = guess * FILL_START_FACTOR, guess / FILL_START_FACTOR
    for _ in range(FILL_BRACKET_STEPS):
        if _fill_count(area, lo) >= n:
            break
        lo *= FILL_START_FACTOR
    else:
        raise SpacingError(
            f"{n} spots do not fit in the area", hint="lower n", context={"area": area.area}
        )
    for _ in range(FILL_BRACKET_STEPS):
        if _fill_count(area, hi) < n:
            return lo, hi
        lo, hi = hi, hi / FILL_START_FACTOR
    return hi, hi


def _interval_for(area: Area, n: int) -> Meters:
    """Bisects for the largest interval found at which at least n lattice spots fit."""
    if n < 1 or area.is_empty or area.area <= 0:
        raise SpacingError(
            "need n >= 1 and a non-empty area",
            hint="pass n >= 1 and a polygon with area",
            context={"n": n},
        )
    lo, hi = _bracket_interval(area, n)
    for _ in range(FILL_SEARCH_STEPS):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _fill_count(area, mid) >= n else (lo, mid)
    return lo


def fill_spots(
    show: "Show",
    region: Area | Region,
    group: str,
    set_id: SetId,
    interval: Meters | None = None,
    n: int | None = None,
) -> "Show":
    """Fills an area with a grid of spots, given either the interval or the count.

    With only n, bisection finds the largest interval at which at least n spots fit, then
    the n spots nearest the area's centroid are kept.

    Args:
        show: Show to add to.
        region: Area to fill.
        group: Group key of the new spots.
        set_id: Set where the spots enter the field.
        interval: Grid spacing in meters.
        n: Number of spots.

    >>> from showpy.examples import two_set_show
    >>> from showpy.geo.regions import region
    >>> square = region([(0, 0), (2, 0), (2, 2), (0, 2)])
    >>> len(fill_spots(two_set_show(), square, "trumpets", "1", n=5).cast.spots)
    9
    """
    area = region.to_shapely() if isinstance(region, Region) else region
    if interval is not None and n is None:
        points = fill(area, interval)
    elif n is not None and interval is None:
        points = fill(area, _interval_for(area, n), n=n)
    else:
        raise SchemaError(
            "fill_spots needs exactly one of interval and n",
            hint="pass interval=<meters> or n=<count>",
            context={"interval": interval, "n": n},
        )
    return _add_points(show, [(p[0], p[1]) for p in points.tolist()], group, set_id)


def duplicate_spots(show: "Show", sel: Selector, offset: Point, set_id: SetId) -> "Show":
    """Copies spots, shifted by `offset` from their placement in `set_id`, under new labels.

    Copies keep group, tags, footprint and claimability; they enter the field at `set_id`.

    >>> from showpy.examples import two_set_show
    >>> duplicate_spots(two_set_show(), ["T1"], (0.0, 1.0), "1").set("1").get("T5").point
    (0.0, 6.0)
    """
    source = show.set(set_id)
    originals = [show.cast.spot(i) for i in select(show, sel)]
    labels = _allocate_labels(show.cast, [spot.group for spot in originals])
    entries = []
    for spot, label in zip(originals, labels, strict=True):
        x, y = source.get(spot.id).point
        entries.append((spot.replace(id=label), (x + offset[0], y + offset[1])))
    return _insert_spots(show, entries, set_id)


def _locked_items(show: "Show", ids: frozenset[SpotId]) -> list[str]:
    pinned = [
        f"{p.entity} in set {s.id}"
        for s in show.sets
        for p in s.placements
        if p.pinned and p.entity in ids
    ]
    forms = [f"form {f.id}" for f in show.forms.values() if f.locked and ids & set(f.members)]
    rows = [
        f"path of {row.entity}"
        for spec in show.transitions
        for row in spec.rows.values()
        if row.locked and row.entity in ids
    ]
    return pinned + forms + rows


def _facing_targets(show: "Show", ids: frozenset[SpotId]) -> list[str]:
    rows = [
        f"{row.entity} faces {row.facing_value} in {spec.id}"
        for spec in show.transitions
        for row in spec.rows.values()
        if row.entity not in ids and faces_entity(row.facing_mode, row.facing_value) in ids
    ]
    overrides = [
        f"override at {o.start} faces {o.value}"
        for o in show.facing_overrides
        if faces_entity(o.mode, o.value) in ids
    ]
    return rows + overrides


def delete_spots(show: "Show", sel: Selector, force: bool = False) -> "Show":
    """Removes spots from the cast, every set, forms, paths, routes, notes and selections.

    Pinned placements, locked forms and locked paths raise LockedError unless `force=True`.
    Spots that other spots face raise ValidationError until those facings change.

    >>> from showpy.examples import two_set_show
    >>> delete_spots(two_set_show(), "T2-T3").cast.ids()
    ('T1', 'T4')
    """
    ids = frozenset(select(show, sel))
    locked = _locked_items(show, ids)
    if locked and not force:
        raise LockedError(
            f"deleting touches locked items: {', '.join(locked)}",
            hint="unlock them first, or pass force=True as the designer",
            context={"locked": locked},
        )
    facing = _facing_targets(show, ids)
    if facing:
        raise ShowPyValidationError(
            f"other spots face deleted spots: {'; '.join(facing)}",
            hint="re-aim those facings at another spot or a point first",
            context={"references": facing},
        )
    return rewrite_spot_ids(show, lambda spot_id: None if spot_id in ids else spot_id)


def _relabel_targets(cast: Cast, moved: Sequence[SpotId], group: Group) -> dict[SpotId, SpotId]:
    prefix = cast.label_scheme.prefix_for(group)
    needs_label = [i for i in moved if label_prefix(i) != prefix]
    labels = _allocate_labels(cast, [group.key] * len(needs_label))
    return dict(zip(needs_label, labels, strict=True))


def regroup(show: "Show", sel: Selector, group: str) -> tuple["Show", dict[SpotId, SpotId]]:
    """Moves spots to another group and relabels them to its prefix.

    Spots whose label already uses the new prefix keep it; the others take the lowest free
    numbers in selection order. IDs are rewritten everywhere, as in relabel().

    Returns:
        show: The edited show.
        mapping: Old label to new label, for every spot whose label changed.

    >>> from showpy.examples import two_set_show
    >>> show = add_group(two_set_show(), Group(key="brass", label_prefix="B"))
    >>> regroup(show, ["T4"], "brass")[1]
    {'T4': 'B1'}
    """
    target = _group(show.cast, group)
    moved = [i for i in select(show, sel) if show.cast.spot(i).group != group]
    mapping = _relabel_targets(show.cast, moved, target)
    moved_set = set(moved)
    spots = tuple(s.replace(group=group) if s.id in moved_set else s for s in show.cast.spots)
    return _rename_everywhere(_with_cast(show, spots=spots), mapping), mapping


def add_group(show: "Show", group: Group) -> "Show":
    """Adds a group; its key must be new.

    >>> from showpy.examples import two_set_show
    >>> sorted(add_group(two_set_show(), Group(key="flags", label_prefix="F")).cast.groups)
    ['flags', 'trumpets']
    """
    if group.key in show.cast.groups:
        raise SchemaError(
            f"group {group.key!r} already exists",
            hint="use edit_group to change it, or pick a new key",
            context={"group": group.key},
        )
    return _with_cast(show, groups={**show.cast.groups, group.key: group})


# Any: changes are arbitrary Group fields, validated by the rebuilt model.
def edit_group(show: "Show", key: str, /, **changes: Any) -> "Show":
    """Changes a group's fields; the key is fixed, and spot labels stay until relabel().

    >>> from showpy.examples import two_set_show
    >>> edit_group(two_set_show(), "trumpets", moves=False).cast.groups["trumpets"].moves
    False
    """
    current = _group(show.cast, key)
    if "key" in changes:
        raise SchemaError(
            "a group key cannot change",
            hint="add a new group, regroup the spots, then remove the old group",
            context={"group": key},
        )
    try:
        edited = Group.model_validate({**current.model_dump(), **changes})
    except SchemaError as error:
        raise SchemaError(
            f"bad change to group {key!r}: {field_errors(error)[0]['msg']}",
            hint=f"change only these fields: {', '.join(sorted(Group.model_fields))}",
            context={"group": key, "changes": sorted(changes)},
        ) from error
    return _with_cast(show, groups={**show.cast.groups, key: edited})


def remove_group(
    show: "Show", key: str, reassign_to: str | None = None
) -> tuple["Show", dict[SpotId, SpotId]]:
    """Removes a group, first moving its spots to `reassign_to` as regroup() does.

    Returns:
        show: The edited show.
        mapping: Old label to new label for the moved spots whose label changed.

    >>> from showpy.examples import two_set_show
    >>> show = add_group(two_set_show(), Group(key="flags", label_prefix="F"))
    >>> sorted(remove_group(show, "flags")[0].cast.groups)
    ['trumpets']
    """
    _group(show.cast, key)
    members = [s.id for s in show.cast.spots if s.group == key]
    if members and reassign_to in (None, key):
        raise SchemaError(
            f"group {key!r} still has {len(members)} spots",
            hint="pass reassign_to=<another group key>, or delete the spots first",
            context={"group": key, "reassign_to": reassign_to},
        )
    mapping: dict[SpotId, SpotId] = {}
    if members and reassign_to is not None:
        show, mapping = regroup(show, members, reassign_to)
    scheme = show.cast.label_scheme
    prefixes = {k: v for k, v in scheme.prefixes.items() if k != key}
    groups = {k: g for k, g in show.cast.groups.items() if k != key}
    new_scheme = scheme.replace(prefixes=prefixes)
    return _with_cast(show, groups=groups, label_scheme=new_scheme), mapping


def _scheme_labels(cast: Cast, scheme: LabelScheme) -> dict[SpotId, SpotId]:
    labels: dict[SpotId, SpotId] = {}
    for group in cast.groups.values():
        members = natural_sort(s.id for s in cast.spots if s.group == group.key)
        prefix = scheme.prefix_for(group)
        for offset, spot_id in enumerate(members):
            labels[spot_id] = scheme.label(prefix, scheme.start + offset)
    return labels


def relabel(show: "Show", scheme: LabelScheme) -> tuple["Show", dict[SpotId, SpotId]]:
    """Renumbers every group under `scheme`, in natural order of the current labels.

    IDs are rewritten everywhere: cast, placements, forms, path rows, routes, note anchors,
    facing overrides, zones and named selections.

    Returns:
        show: The relabeled show, carrying `scheme`.
        mapping: Old label to new label, for history renames; unchanged labels are left out.

    >>> from showpy.examples import two_set_show
    >>> relabel(two_set_show(), LabelScheme(pad=2))[1]["T1"]
    'T01'
    """
    labels = _scheme_labels(show.cast, scheme)
    clashes = _repeated(labels.values())
    if clashes:
        raise SchemaError(
            f"scheme gives several spots the labels {', '.join(clashes)}",
            hint="give each group its own prefix",
            context={"clashes": clashes},
        )
    mapping = {old: new for old, new in labels.items() if old != new}
    return _with_cast(_rename_everywhere(show, mapping), label_scheme=scheme), mapping


def define_selection(show: "Show", name: str, sel: Selector) -> "Show":
    """Stores a named selection, usable later as `sel:<name>`; an existing name is replaced.

    >>> from showpy.examples import two_set_show
    >>> define_selection(two_set_show(), "ends", "T1 + T4").cast.selections["ends"]
    ('T1', 'T4')
    """
    if not name or not set(name) <= SELECTION_NAME_CHARS:
        raise SchemaError(
            f"bad selection name {name!r}",
            hint="use letters, digits, _, . and - only",
            context={"name": name},
        )
    ids = select(show, sel).ids
    return _with_cast(show, selections={**show.cast.selections, name: ids})


# Any: changes are arbitrary Spot fields, validated by the rebuilt model.
def _edit_spots(show: "Show", sel: Selector, **changes: Any) -> "Show":
    chosen = set(select(show, sel))
    spots = tuple(
        Spot.model_validate({**dict(s), **changes}) if s.id in chosen else s
        for s in show.cast.spots
    )
    return _with_cast(show, spots=spots)


def set_shape(show: "Show", sel: Selector, shape: Shape) -> "Show":
    """Sets the footprint of spots.

    >>> from showpy.examples import two_set_show
    >>> set_shape(two_set_show(), "T1", Shape(kind="rectangle")).cast.spot("T1").shape.kind
    'rectangle'
    """
    return _edit_spots(show, sel, shape=shape)


def set_claimable(show: "Show", sel: Selector, value: Claimable) -> "Show":
    """Sets who may claim spots once the show is published: performer, crew or none.

    >>> from showpy.examples import two_set_show
    >>> set_claimable(two_set_show(), "T1", "crew").cast.spot("T1").claimable
    'crew'
    """
    allowed = get_args(Claimable.__value__)
    if value not in allowed:
        raise SchemaError(
            f"bad claimable value {value!r}",
            hint=f"use one of {', '.join(allowed)}",
            context={"value": value},
        )
    return _edit_spots(show, sel, claimable=value)


def _repeated(ids: Iterable[SpotId]) -> list[SpotId]:
    return sorted(spot_id for spot_id, count in Counter(ids).items() if count > 1)


def _rename_everywhere(show: "Show", mapping: Mapping[SpotId, SpotId]) -> "Show":
    """Renames spot IDs simultaneously in every place a show refers to them.

    Covers the cast, placements, forms, path rows (keys, entities and entity facings),
    routes, note anchors, facing overrides, zones and named selections. String selectors
    that name labels are frozen to the renamed ID list; others stay as written.
    """
    if not mapping:
        return show
    known = set(show.cast.ids())
    for old in mapping:
        if old not in known:
            show.cast.spot(old)
    duplicates = _repeated(mapping.get(i, i) for i in show.cast.ids())
    if duplicates:
        raise SchemaError(
            f"renaming would repeat {', '.join(duplicates)}",
            hint="map to labels no other spot keeps",
            context={"duplicates": duplicates},
        )
    return rewrite_spot_ids(show, lambda spot_id: mapping.get(spot_id, spot_id))
