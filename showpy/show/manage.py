"""
manage.py --- creates shows and adds, removes, retimes, renumbers and indexes their sets

Contains:
    SET_ID_PATTERN: names the set IDs storage accepts as path segments
    new_show(): creates a show with an off-field first set and pinned sheet version
    add_set(): inserts a set, copying missing placements from the previous set
    insert_subset(): inserts a sub-set such as 12A, keeping the total time
    remove_set(): deletes a set with its forms and paths, optionally keeping the total time
    set_counts(): retimes a set and the transition into it
    renumber(): renames sets 1..N in show order and rewrites every reference
    transitions(): lists consecutive set pairs
    index(): computes each set's start count, measure and seconds
    set_at_count(): returns the set reached exactly at a count
    transition_at_count(): returns the transition a count lies in plus its local count
    upgrade_dependency(): pins a newer version of a sheet reference
"""

import re
import string
from collections.abc import Mapping, Sequence

from showpy.cast.models import Cast
from showpy.choreo.models import Placement, Route, Set, TransitionSpec, Waypoint
from showpy.core.errors import PathError, SchemaError, TimeRangeError, UnknownRefError, VersionError
from showpy.core.ids import SetId, SpotId, TransitionId, format_ref, format_tid, parse_ref
from showpy.core.types import Count
from showpy.core.util import close_matches
from showpy.notes.models import Anchor, Note
from showpy.sheet.models import Sheet
from showpy.show.locks import require_unlocked
from showpy.show.models import Pin, Show, ShowIndex, ShowIndexEntry
from showpy.time.clock import count_to_seconds
from showpy.time.measures import count_to_measure
from showpy.time.models import Timeline

SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]*$")
FIRST_SET_ID = "1"
# ADR 0001 item 4: (0, 0) is the sheet's front-centre, the natural wait spot before an entrance.
OFF_FIELD_POINT = (0.0, 0.0)
SUBSET_LETTERS = string.ascii_uppercase
SEQUENTIAL = "sequential"
SCHEMES = (SEQUENTIAL,)


def _require_set_id(show: Show, set_id: SetId) -> None:
    if not SET_ID_PATTERN.match(set_id) or ".." in set_id:
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


def _require_counts(counts: float, is_first: bool) -> None:
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


def _off_field(entity: SpotId, pinned: bool = False) -> Placement:
    x, y = OFF_FIELD_POINT
    return Placement(entity=entity, x=x, y=y, on_field=False, pinned=pinned)


def _entity_pinned(show: Show, entity: SpotId) -> bool:
    return any(pin.target == "entity" and pin.id == entity for pin in show.pins)


def _carried(show: Show, previous: Set | None, entity: SpotId) -> Placement:
    pinned = _entity_pinned(show, entity)
    if previous is None or entity not in previous.entity_ids():
        return _off_field(entity, pinned)
    source = previous.get(entity)
    return source.model_copy(update={"form": None, "order": None, "pinned": pinned})


def _sync_transitions(show: Show) -> Show:
    pairs = set(show.transition_ids())
    counts = {s.id: s.counts for s in show.sets}
    kept = tuple(
        spec.model_copy(update={"counts": counts[spec.id[1]]})
        for spec in show.transitions
        if spec.id in pairs
    )
    return show.model_copy(update={"transitions": kept})


def _with_sets(show: Show, sets: Sequence[Set], force: bool, **update: object) -> Show:
    edited = _sync_transitions(show.model_copy(update={"sets": tuple(sets), **update}))
    require_unlocked(show, edited, force)
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

    >>> from showpy.examples import field_sheet, two_set_show
    >>> show = new_show("s", "Opener", field_sheet(), two_set_show().timeline)
    >>> show.set_ids(), show.dependencies
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
    first = Set(id=FIRST_SET_ID, counts=0.0, placements=tuple(map(_off_field, roster.ids())))
    return Show(
        id=show_id,
        title=title,
        pack=pack,
        sheet=sheet,
        coordinates=sheet.default_coordinates,
        cast=roster,
        timeline=timeline,
        sets=(first,),
        dependencies={format_ref(kind, ref_id): version},
    )


def _given_placements(show: Show, placements: Sequence[Placement]) -> dict[SpotId, Placement]:
    for placement in placements:
        show.cast.spot(placement.entity)
    return {p.entity: p for p in placements}


def add_set(
    show: Show,
    set_id: SetId,
    counts: float,
    after: SetId | None = None,
    placements: Sequence[Placement] | None = None,
    force: bool = False,
) -> Show:
    """Inserts a set; spots without a given placement copy their place in the previous set.

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

    >>> from showpy.examples import two_set_show
    >>> show = add_set(two_set_show(), "3", 4.0)
    >>> show.set_ids(), show.set("3").get("T1").y
    (['1', '2', '3'], 9.0)
    """
    _require_set_id(show, set_id)
    position = len(show.sets) if after is None else show.set_index(after) + 1
    _require_counts(counts, is_first=position == 0)
    given = _given_placements(show, placements or ())
    previous = show.sets[position - 1] if position > 0 else None
    rows = tuple(given.get(e) or _carried(show, previous, e) for e in show.cast.ids())
    new = Set(id=set_id, counts=counts, placements=rows)
    return _with_sets(show, [*show.sets[:position], new, *show.sets[position:]], force)


def _subset_id(show: Show, after: SetId) -> SetId:
    base = after.rstrip(SUBSET_LETTERS)
    suffix = after[len(base) :]
    start = SUBSET_LETTERS.index(suffix[-1]) + 1 if suffix else 0
    for letter in SUBSET_LETTERS[start:]:
        if f"{base}{letter}" not in show.set_ids():
            return f"{base}{letter}"
    raise SchemaError(
        f"no free sub-set letter after {after}",
        hint="renumber the show first with renumber()",
        context={"after": after},
    )


def insert_subset(
    show: Show, after: SetId, counts: float, force: bool = False
) -> tuple[Show, SetId]:
    """Inserts a sub-set after `after`, taking its counts from the following set.

    The sub-set holds the spots where they stand in `after`; place them afterwards. The total
    show time stays the same.

    Args:
        show: Show to edit.
        after: Set the sub-set follows.
        counts: Counts of the move into the sub-set; must be less than the next set's counts.
        force: Allows retiming a pinned next set or dropping locked paths.

    Returns:
        result: Edited show and the new set ID, e.g. `12A`.

    >>> from showpy.examples import two_set_show
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
    new = Set(id=new_id, counts=counts, placements=rows)
    shortened = following.model_copy(update={"counts": following.counts - counts})
    sets = [*show.sets[: position + 1], new, shortened, *show.sets[position + 2 :]]
    return _with_sets(show, sets, force), new_id


def _pins_without_set(show: Show, set_id: SetId) -> tuple[Pin, ...]:
    dropped_forms = {f for f, form in show.forms.items() if form.set == set_id}
    tids = {format_tid(t) for t in show.transition_ids() if set_id in t}

    def kept(pin: Pin) -> bool:
        return not (
            (pin.target == "set" and pin.id == set_id)
            or (pin.target == "form" and pin.id in dropped_forms)
            or (pin.target == "transition" and pin.id in tids)
        )

    return tuple(filter(kept, show.pins))


def remove_set(show: Show, set_id: SetId, merge_counts: bool = True, force: bool = False) -> Show:
    """Deletes a set, its forms, its pins and the stored paths into and out of it.

    Args:
        show: Show to edit.
        set_id: Set to delete.
        merge_counts: Adds its counts to the next set so the total time stays the same.
        force: Allows deleting pinned placements, locked forms or locked paths.

    Returns:
        show: Edited show; when the first set goes, the new first set gets 0 counts.

    >>> from showpy.examples import two_set_show
    >>> remove_set(two_set_show(), "2").set_ids()
    ['1']
    """
    position = show.set_index(set_id)
    removed = show.sets[position]
    sets = [s for s in show.sets if s.id != set_id]
    if merge_counts and position < len(sets):
        following = sets[position]
        sets[position] = following.model_copy(update={"counts": following.counts + removed.counts})
    if position == 0 and sets:
        sets[0] = sets[0].model_copy(update={"counts": 0.0})
    forms = {f: form for f, form in show.forms.items() if form.set != set_id}
    pins = _pins_without_set(show, set_id)
    return _with_sets(show, sets, force, forms=forms, pins=pins)


def _scale_route(route: Route, factor: float) -> Route:
    end = None if route.end is None else route.end * factor
    return route.model_copy(update={"start": route.start * factor, "end": end})


def _scale_spec(spec: TransitionSpec, factor: float) -> TransitionSpec:
    rows = {}
    for entity, row in spec.rows.items():
        waypoints = tuple(
            w if w.at is None else Waypoint(x=w.x, y=w.y, at=w.at * factor) for w in row.waypoints
        )
        end = None if row.end is None else row.end * factor
        rows[entity] = row.model_copy(
            update={"start": row.start * factor, "end": end, "waypoints": waypoints}
        )
    routes = {key: _scale_route(route, factor) for key, route in spec.routes.items()}
    return spec.model_copy(update={"rows": rows, "routes": routes})


def _overruns(spec: TransitionSpec, counts: float) -> list[str]:
    late = [
        entity
        for entity, row in spec.rows.items()
        if row.window(counts)[1] > counts
        or row.start >= counts
        or any(w.at is not None and w.at > counts for w in row.waypoints)
    ]
    late += [key for key, r in spec.routes.items() if (r.end or counts) > counts]
    return late


def _retimed_spec(spec: TransitionSpec, counts: float, rescale: bool) -> TransitionSpec:
    if rescale and spec.counts > 0:
        spec = _scale_spec(spec, counts / spec.counts)
    late = _overruns(spec, counts)
    if late:
        raise PathError(
            f"paths in {format_tid(spec.id)} run past {counts} counts: {', '.join(late)}",
            hint="pass rescale=True to scale the paths, or shorten them first",
            context={"tid": spec.id, "late": late},
        )
    return spec.model_copy(update={"counts": counts})


def set_counts(
    show: Show, set_id: SetId, counts: float, rescale: bool = False, force: bool = False
) -> Show:
    """Retimes a set and keeps the incoming transition's counts equal to it (ADR 0001 item 16).

    Args:
        show: Show to edit.
        set_id: Set to retime.
        counts: New counts; 0 for the first set, positive otherwise.
        rescale: Scales stored path windows, waypoint times and route windows proportionally.
        force: Allows retiming a pinned set or rescaling locked path rows.

    Returns:
        show: Edited show; raises PathError when stored paths would run past the new counts.

    >>> from showpy.examples import two_set_show
    >>> set_counts(two_set_show(), "2", 12.0).set("2").counts
    12.0
    """
    position = show.set_index(set_id)
    _require_counts(counts, is_first=position == 0)
    sets = list(show.sets)
    sets[position] = sets[position].model_copy(update={"counts": counts})
    specs = tuple(
        _retimed_spec(spec, counts, rescale) if spec.id[1] == set_id else spec
        for spec in show.transitions
    )
    return _with_sets(show, sets, force, transitions=specs)


def _renamed_anchor(anchor: Anchor, mapping: Mapping[SetId, SetId]) -> Anchor:
    if anchor.kind == "set" and anchor.set_id is not None:
        return Anchor.on_set(mapping.get(anchor.set_id, anchor.set_id))
    if anchor.kind == "transition" and anchor.tid is not None:
        a, b = anchor.tid
        return Anchor.on_transition((mapping.get(a, a), mapping.get(b, b)))
    return anchor


def _renamed_pin(pin: Pin, mapping: Mapping[SetId, SetId], tids: Mapping[str, str]) -> Pin:
    if pin.target == "set":
        return pin.model_copy(update={"id": mapping.get(pin.id, pin.id)})
    if pin.target == "transition":
        return pin.model_copy(update={"id": tids.get(pin.id, pin.id)})
    return pin


def _renamed_note(note: Note, mapping: Mapping[SetId, SetId]) -> Note:
    return note.model_copy(update={"anchor": _renamed_anchor(note.anchor, mapping)})


def renumber(show: Show, scheme: str = SEQUENTIAL) -> tuple[Show, dict[SetId, SetId]]:
    """Renames sets 1..N in show order, rewriting transitions, forms, notes and pins.

    Args:
        show: Show to edit.
        scheme: Numbering scheme; only `sequential` exists.

    Returns:
        result: Edited show and the old-to-new ID mapping for every set.

    >>> from showpy.examples import two_set_show
    >>> show, _ = insert_subset(two_set_show(), "1", 2.0)
    >>> renumber(show)[1]
    {'1': '1', '1A': '2', '2': '3'}
    """
    if scheme not in SCHEMES:
        raise UnknownRefError(
            f"unknown numbering scheme {scheme!r}",
            hint=f"use one of {', '.join(SCHEMES)}",
            context={"scheme": scheme},
        )
    mapping = {s.id: str(n) for n, s in enumerate(show.sets, start=1)}
    tids = {
        format_tid(t): format_tid((mapping[t[0]], mapping[t[1]])) for t in show.transition_ids()
    }
    renamed = show.model_copy(
        update={
            "sets": tuple(s.model_copy(update={"id": mapping[s.id]}) for s in show.sets),
            "transitions": tuple(
                spec.model_copy(update={"id": (mapping[spec.id[0]], mapping[spec.id[1]])})
                for spec in show.transitions
            ),
            "forms": {
                f: form.model_copy(update={"set": mapping.get(form.set, form.set)})
                for f, form in show.forms.items()
            },
            "notes": tuple(_renamed_note(n, mapping) for n in show.notes),
            "pins": tuple(_renamed_pin(p, mapping, tids) for p in show.pins),
        }
    )
    return renamed, mapping


def transitions(show: Show) -> list[TransitionId]:
    """Lists consecutive set pairs in show order.

    >>> from showpy.examples import two_set_show
    >>> transitions(two_set_show())
    [('1', '2')]
    """
    return show.transition_ids()


def index(show: Show) -> ShowIndex:
    """Computes each set's start count, measure label and seconds; the first set starts at 0.

    >>> from showpy.examples import two_set_show
    >>> [(e.set, e.start_count, e.seconds) for e in index(two_set_show()).entries]
    [('1', 0.0, 0.0), ('2', 8.0, 4.0)]
    """
    entries: list[ShowIndexEntry] = []
    start = 0.0
    for position, current in enumerate(show.sets):
        start += current.counts if position > 0 else 0.0
        entries.append(
            ShowIndexEntry(
                set=current.id,
                counts=current.counts,
                start_count=start,
                measure=count_to_measure(show.timeline, start),
                seconds=count_to_seconds(show.timeline, start),
                cue=current.cue,
            )
        )
    return ShowIndex(entries=tuple(entries))


def set_at_count(show: Show, c: Count) -> SetId | None:
    """Returns the set reached exactly at count `c`, or None while moving between sets.

    >>> from showpy.examples import two_set_show
    >>> set_at_count(two_set_show(), 8.0)
    '2'
    """
    return index(show).set_at(c)


def transition_at_count(show: Show, c: Count) -> tuple[TransitionId, Count] | None:
    """Returns the transition count `c` lies in, `start(a) < c < start(b)`, plus local count tau.

    >>> from showpy.examples import two_set_show
    >>> transition_at_count(two_set_show(), 2.0)
    (('1', '2'), 2.0)
    """
    return index(show).transition_at(c)


def upgrade_dependency(show: Show, ref: str, version: str, sheet: Sheet | None = None) -> Show:
    """Pins a newer version of a sheet reference, swapping in that sheet when it is given.

    Stored positions never change. Without `sheet`, the show keeps its current sheet object
    until one is loaded, and dependency validation reports the mismatch.

    Args:
        show: Show to edit.
        ref: Sheet reference, with or without a version.
        version: Version to pin.
        sheet: The sheet at that version, to adopt it now.

    >>> from showpy.examples import two_set_show
    >>> upgrade_dependency(two_set_show(), "sheet:test-field", "2").dependencies
    {'sheet:test-field': '2'}
    """
    kind, ref_id, _ = parse_ref(ref)
    key = format_ref(kind, ref_id)
    if key not in show.dependencies:
        close = close_matches(key, show.dependencies)
        raise UnknownRefError(
            f"show does not depend on {key}",
            hint=f"did you mean {', '.join(close)}?" if close else "check show.dependencies",
            context={"ref": key, "known": sorted(show.dependencies)},
        )
    update: dict[str, object] = {"dependencies": {**show.dependencies, key: version}}
    if sheet is not None:
        if sheet.ref != format_ref(kind, ref_id, version):
            raise VersionError(
                f"sheet {sheet.ref} is not {format_ref(kind, ref_id, version)}",
                hint="pass the sheet saved at the version being pinned",
                context={"sheet": sheet.ref, "ref": key, "version": version},
            )
        update["sheet"] = sheet
    return show.model_copy(update=update)
