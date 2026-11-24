"""
manage.py --- creates shows and adds, removes and retimes their sets

Contains:
    SET_ID_PATTERN: names the set IDs storage accepts as path segments
    new_show(): creates a show with an off-field first set and pinned sheet version
    add_set(): inserts a set, copying missing placements from the previous set
    insert_subset(): inserts a sub-set such as 12A, keeping the total time
    remove_set(): deletes a set with its forms and paths, optionally keeping the total time
    replace_sets(): replaces a show's sets, syncing transitions and refusing locked edits
    set_counts(): retimes a set and the transition into it
"""

import re
import string
from collections.abc import Sequence

from e8s.cast.models import Cast
from e8s.choreo.models import Placement, Set
from e8s.choreo.path_models import Route, TransitionSpec, Waypoint
from e8s.core.errors import (
    PathError,
    SchemaError,
    TimeRangeError,
    VersionError,
)
from e8s.core.ids import (
    SetId,
    SpotId,
    format_ref,
    format_tid,
    parse_ref,
)
from e8s.sheet.models import Sheet
from e8s.show.locks import require_unlocked
from e8s.show.models import Pin, Show
from e8s.show.set_keys import free_set_key, set_key
from e8s.time.models import Timeline

SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]*$")
FIRST_SET_ID = "1"
# (0, 0) is the sheet's front-centre, the natural wait spot before an entrance.
OFF_FIELD_POINT = (0.0, 0.0)
SUBSET_LETTERS = string.ascii_uppercase


def _checked_set_id(show: Show, set_id: str) -> SetId:
    if not SET_ID_PATTERN.fullmatch(set_id) or ".." in set_id:
        raise SchemaError(
            f"set id {set_id!r} is not allowed",
            hint="use letters, digits, _ and . only, starting with a letter or digit, e.g. 12A",
            context={"set": set_id},
        )
    if set_id in show.set_ids():
        raise SchemaError(
            f"set {set_id} already exists",
            hint="pick an unused id or use insert_subset()",
            context={"set": set_id, "known": show.set_ids()},
        )
    return SetId(set_id)


def _require_counts(counts: float, *, is_first: bool) -> None:
    if is_first and counts != 0:
        raise TimeRangeError(
            f"the first set has counts {counts}",
            hint="the first set starts the show; give it 0 counts",
            context={"counts": counts},
        )
    if not is_first and not counts > 0:
        raise TimeRangeError(
            f"set counts must be positive, got {counts}",
            hint="give every set after the first counts > 0",
            context={"counts": counts},
        )


def _off_field(entity: SpotId, *, pinned: bool = False) -> Placement:
    x, y = OFF_FIELD_POINT
    return Placement(entity=entity, x=x, y=y, on_field=False, pinned=pinned)


def _entity_pinned(show: Show, entity: SpotId) -> bool:
    return any(pin.target == "entity" and pin.id == entity for pin in show.pins)


def _carried(show: Show, previous: Set | None, entity: SpotId) -> Placement:
    pinned = _entity_pinned(show, entity)
    if previous is None or entity not in previous.entity_ids():
        return _off_field(entity, pinned=pinned)
    source = previous.get(entity)
    return source.replace(form=None, order=None, pinned=pinned)


def _sync_transitions(show: Show) -> Show:
    pairs = set(show.transition_ids())
    counts = {s.id: s.counts for s in show.sets}
    kept = tuple(
        spec.replace(counts=counts[spec.id[1]]) for spec in show.transitions if spec.id in pairs
    )
    return show.replace(transitions=kept)


def replace_sets(show: Show, sets: Sequence[Set], *, force: bool, **update: object) -> Show:
    """Replaces a show's sets, keeping transitions in step and refusing pinned or locked edits.

    Transitions between sets that are no longer consecutive are dropped, and each kept one takes
    the counts of the set it leads into.

    Args:
        show: Show to edit.
        sets: New sets, in show order.
        force: Allows edits to pinned placements, locked forms or locked paths.
        update: Other show fields to replace in the same edit, such as `cast` or `forms`.

    Returns:
        edited: The show with the new sets and synced transitions.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> replace_sets(show, show.sets[:1], force=False).transition_ids()
    []
    """
    edited = _sync_transitions(show.replace(sets=tuple(sets), **update))
    require_unlocked(show, edited, force=force)
    return edited


def new_show(
    show_id: str,
    title: str,
    sheet: Sheet,
    timeline: Timeline,
    cast: Cast | None = None,
    pack: str = "",
) -> Show:
    """Creates a show whose first set "1" holds every spot off the field at the front-center.

    Args:
        show_id: Explicit show ID; the library never invents IDs.
        title: Display title.
        sheet: Design surface; its reference must carry a version, which gets pinned.
        timeline: Show clock.
        cast: Cast of spots, or None for an empty cast.
        pack: Domain pack key.

    Returns:
        show: Show with one 0-count set and `dependencies[kind:id] = version`.

    >>> from e8s.examples import field_sheet, two_set_show
    >>> show = new_show("s", "Opener", field_sheet(), two_set_show().timeline)
    >>> show.set_ids(), dict(show.dependencies)
    (['1'], {'sheet:test-field': '1'})
    """
    kind, ref_id, version = parse_ref(sheet.ref)
    if version is None:
        raise VersionError(
            f"sheet {sheet.ref} has no version to pin",
            hint="save the sheet first so its reference reads kind:id@version",
            context={"ref": sheet.ref},
        )
    roster = cast or Cast()
    placements = tuple(map(_off_field, roster.ids()))
    first = Set._validated({"id": FIRST_SET_ID, "counts": 0.0, "placements": placements})
    return Show(
        id=show_id,
        title=title,
        pack=pack,
        sheet=sheet,
        cast=roster,
        timeline=timeline,
        sets=(first,),
        dependencies={format_ref(kind, ref_id): version},
    )


def _new_key(show: Show, set_id: SetId) -> str:
    return free_set_key(map(set_key, show.sets), set_id)


def _given_placements(show: Show, placements: Sequence[Placement]) -> dict[SpotId, Placement]:
    given: dict[SpotId, Placement] = {}
    for placement in placements:
        show.cast.spot(placement.entity)
        if placement.entity in given:
            raise SchemaError(
                f"two placements given for {placement.entity}",
                hint="give one placement per spot",
                context={"entity": placement.entity},
            )
        given[placement.entity] = placement
    return given


def add_set(
    show: Show,
    set_id: str,
    counts: float,
    after: str | None = None,
    placements: Sequence[Placement] | None = None,
    *,
    force: bool = False,
) -> Show:
    """Inserts a set; spots without a given placement copy their place in the previous set.

    The set's key is its ID, or the first free `<id>.<n>` when a renumbered set holds that key.
    Only live keys count: after remove_set() in the same unsaved show, a new set with the removed
    set's ID reuses its key, so on save it takes over that folder and its build script.

    Copied placements keep position, facing and on-field state, but not form membership,
    because forms belong to one set. They are pinned when the spot has an entity pin.
    Inserting between two sets drops the stored paths between them.

    Args:
        show: Show to edit.
        set_id: New set ID.
        counts: Counts of the move into the new set; 0 when it is the first set.
        after: Set to insert after; None appends.
        placements: Placements for some or all spots.
        force: Allows dropping locked paths between `after` and its successor.

    >>> from e8s.examples import two_set_show
    >>> show = add_set(two_set_show(), "3", 4.0)
    >>> show.set_ids(), show.set("3").get("T1").y
    (['1', '2', '3'], 9.0)
    """
    new_id = _checked_set_id(show, set_id)
    position = len(show.sets) if after is None else show.set_index(after) + 1
    _require_counts(counts, is_first=position == 0)
    given = _given_placements(show, placements or ())
    previous = show.sets[position - 1] if position > 0 else None
    rows = tuple(given.get(e) or _carried(show, previous, e) for e in show.cast.ids())
    new = Set(id=new_id, key=_new_key(show, new_id), counts=counts, placements=rows)
    return replace_sets(show, [*show.sets[:position], new, *show.sets[position:]], force=force)


def _subset_id(show: Show, after: str) -> SetId:
    base = after.rstrip(SUBSET_LETTERS)
    suffix = after[len(base) :]
    start = SUBSET_LETTERS.index(suffix[-1]) + 1 if suffix else 0
    for letter in SUBSET_LETTERS[start:]:
        if f"{base}{letter}" not in show.set_ids():
            return _checked_set_id(show, f"{base}{letter}")
    raise SchemaError(
        f"no free sub-set letter after {after}",
        hint="renumber the show first with renumber()",
        context={"after": after},
    )


def insert_subset(
    show: Show, after: str, counts: float, *, force: bool = False
) -> tuple[Show, SetId]:
    """Inserts a sub-set after `after`, taking its counts from the following set.

    The sub-set holds the spots where they stand in `after`; place them afterwards. The total
    show time stays the same. Its key follows the add_set() rule.

    Args:
        show: Show to edit.
        after: Set the sub-set follows.
        counts: Counts of the move into the sub-set; must be less than the next set's counts.
        force: Allows retiming a pinned next set or dropping locked paths.

    Returns:
        result: Edited show and the new set ID, e.g. `12A`.

    >>> from e8s.examples import two_set_show
    >>> show, new_id = insert_subset(two_set_show(), "1", 3.0)
    >>> new_id, [s.counts for s in show.sets]
    ('1A', [0.0, 3.0, 5.0])
    """
    position = show.set_index(after)
    if position + 1 >= len(show.sets):
        raise TimeRangeError(
            f"set {after} is the last set; there are no counts to take",
            hint="use add_set() to extend the show instead",
            context={"after": after},
        )
    following = show.sets[position + 1]
    if not 0 < counts < following.counts:
        raise TimeRangeError(
            f"set {following.id} has {following.counts} counts; cannot give {counts} away",
            hint=f"use 0 < counts < {following.counts}",
            context={"counts": counts, "available": following.counts},
        )
    new_id = _subset_id(show, after)
    rows = tuple(_carried(show, show.sets[position], e) for e in show.cast.ids())
    new = Set(id=new_id, key=_new_key(show, new_id), counts=counts, placements=rows)
    shortened = following.replace(counts=following.counts - counts)
    sets = [*show.sets[: position + 1], new, shortened, *show.sets[position + 2 :]]
    return replace_sets(show, sets, force=force), new_id


def _pins_without_set(show: Show, set_id: str) -> tuple[Pin, ...]:
    dropped_forms = {f for f, form in show.forms.items() if form.set == set_id}
    tids = {format_tid(t) for t in show.transition_ids() if set_id in t}

    def kept(pin: Pin) -> bool:
        return not (
            (pin.target == "set" and pin.id == set_id)
            or (pin.target == "form" and pin.id in dropped_forms)
            or (pin.target == "transition" and pin.id in tids)
        )

    return tuple(filter(kept, show.pins))


def remove_set(show: Show, set_id: str, *, merge_counts: bool = True, force: bool = False) -> Show:
    """Deletes a set, its forms, its pins and the stored paths into and out of it.

    Args:
        show: Show to edit.
        set_id: Set to delete.
        merge_counts: Adds its counts to the next set so the total time stays the same.
        force: Allows deleting pinned placements, locked forms or locked paths.

    Returns:
        show: Edited show; when the first set goes, the new first set gets 0 counts.

    >>> from e8s.examples import two_set_show
    >>> remove_set(two_set_show(), "2").set_ids()
    ['1']
    """
    position = show.set_index(set_id)
    removed = show.sets[position]
    sets = [s for s in show.sets if s.id != set_id]
    if merge_counts and position < len(sets):
        following = sets[position]
        sets[position] = following.replace(counts=following.counts + removed.counts)
    if position == 0 and sets:
        sets[0] = sets[0].replace(counts=0.0)
    forms = {f: form for f, form in show.forms.items() if form.set != set_id}
    pins = _pins_without_set(show, set_id)
    return replace_sets(show, sets, force=force, forms=forms, pins=pins)


def _scale_route(route: Route, factor: float) -> Route:
    end = None if route.end is None else route.end * factor
    return route.replace(start=route.start * factor, end=end)


def _scale_spec(spec: TransitionSpec, factor: float) -> TransitionSpec:
    rows = {}
    for entity, row in spec.rows.items():
        waypoints = tuple(
            w if w.at is None else Waypoint(x=w.x, y=w.y, at=w.at * factor) for w in row.waypoints
        )
        end = None if row.end is None else row.end * factor
        rows[entity] = row.replace(start=row.start * factor, end=end, waypoints=waypoints)
    routes = {key: _scale_route(route, factor) for key, route in spec.routes.items()}
    return spec.replace(rows=rows, routes=routes)


def _overruns(spec: TransitionSpec, counts: float) -> list[str]:
    late: list[str] = [
        entity
        for entity, row in spec.rows.items()
        if row.window(counts)[1] > counts
        or row.start >= counts
        or any(w.at is not None and w.at > counts for w in row.waypoints)
    ]
    late += [key for key, r in spec.routes.items() if (r.end or counts) > counts]
    return late


def _retimed_spec(spec: TransitionSpec, counts: float, *, rescale: bool) -> TransitionSpec:
    if rescale and spec.counts > 0:
        spec = _scale_spec(spec, counts / spec.counts)
    late = _overruns(spec, counts)
    if late:
        raise PathError(
            f"paths in {format_tid(spec.id)} run past {counts} counts: {', '.join(late)}",
            hint="pass rescale=True to scale the paths, or shorten them first",
            context={"tid": spec.id, "late": late},
        )
    return spec.replace(counts=counts)


def set_counts(
    show: Show, set_id: str, counts: float, *, rescale: bool = False, force: bool = False
) -> Show:
    """Retimes a set and keeps the incoming transition's counts equal to it.

    The destination set's counts win over the transition's.

    Args:
        show: Show to edit.
        set_id: Set to retime.
        counts: New counts; 0 for the first set, positive otherwise.
        rescale: Scales stored path windows, waypoint times and route windows proportionally.
        force: Allows retiming a pinned set or rescaling locked path rows.

    Returns:
        show: Edited show; raises PathError when stored paths would run past the new counts.

    >>> from e8s.examples import two_set_show
    >>> set_counts(two_set_show(), "2", 12.0).set("2").counts
    12.0
    """
    position = show.set_index(set_id)
    _require_counts(counts, is_first=position == 0)
    sets = list(show.sets)
    sets[position] = sets[position].replace(counts=counts)
    specs = tuple(
        _retimed_spec(spec, counts, rescale=rescale) if spec.id[1] == set_id else spec
        for spec in show.transitions
    )
    return replace_sets(show, sets, force=force, transitions=specs)
