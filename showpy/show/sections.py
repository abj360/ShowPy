"""
sections.py --- splits a show into movements at a set and joins movements back into one show

Contains:
    split_section(): splits a show at a set into two shows that both hold that set
    join_sections(): appends one show to another, sharing the boundary set when both have it
"""

import itertools
from collections.abc import Sequence

from showpy.choreo.models import FacingOverride, Form, Set
from showpy.core.errors import SchemaError, TimelineError, VersionError
from showpy.core.ids import SetId, parse_tid
from showpy.core.types import Count
from showpy.notes.models import Note
from showpy.sheet.models import Zone
from showpy.show.manage import index
from showpy.show.models import Pin, Show
from showpy.time.clock import count_to_seconds, tempo_at
from showpy.time.measures import count_to_measure
from showpy.time.models import AudioTrack, MeasureSetting, TempoSection, Timeline

TOL = 1e-9

type Window = tuple[Count, Count]


def _clip(window: Window, low: Count, high: Count, shift: Count) -> Window | None:
    start, end = max(window[0], low), min(window[1], high)
    return (start - shift, end - shift) if end - start > TOL else None


def _clip_overrides(
    items: Sequence[FacingOverride], low: Count, high: Count, shift: Count
) -> tuple[FacingOverride, ...]:
    kept = []
    for item in items:
        window = _clip((item.start, item.end), low, high, shift)
        if window is not None:
            kept.append(item.model_copy(update={"start": window[0], "end": window[1]}))
    return tuple(kept)


def _clip_zones(zones: Sequence[Zone], low: Count, high: Count, shift: Count) -> tuple[Zone, ...]:
    kept = []
    for zone in zones:
        if zone.active is None:
            kept.append(zone)
            continue
        windows = tuple(w for w in (_clip(w, low, high, shift) for w in zone.active) if w)
        if windows:
            kept.append(zone.model_copy(update={"active": windows}))
    return tuple(kept)


def _tempo_from(tl: Timeline, offset: Count) -> tuple[TempoSection, ...]:
    if not tl.tempos:
        return ()
    active = [t for t in tl.tempos if t.at_count <= offset + TOL][-1]
    start = TempoSection(at_count=0.0, counts_per_minute=tempo_at(tl, offset))
    ramp_end = active.at_count + (active.ramp_counts or 0.0)
    if active.ramp_to is not None and ramp_end - offset > TOL:
        start = start.model_copy(
            update={"ramp_to": active.ramp_to, "ramp_counts": ramp_end - offset}
        )
    later = [
        t.model_copy(update={"at_count": t.at_count - offset})
        for t in tl.tempos
        if t.at_count > offset + TOL
    ]
    return (start, *later)


def _measures_from(tl: Timeline, offset: Count) -> MeasureSetting | None:
    setting = tl.measures
    label = count_to_measure(tl, offset)
    if setting is None or label is None:
        return None
    if abs(label.count_in_measure - 1.0) > TOL:
        raise TimelineError(
            f"count {offset} falls inside measure {label.measure}",
            hint="split at a set that starts on a measure's first count, or clear measures",
            context={"count": offset, "measure": label.measure},
        )
    lengths = [(0.0, setting.counts_per_measure), *sorted(setting.changes)]
    current = [length for at, length in lengths if at <= offset + TOL][-1]
    changes = tuple((at - offset, length) for at, length in setting.changes if at > offset + TOL)
    return MeasureSetting(counts_per_measure=current, start_measure=label.measure, changes=changes)


def _audio_from(tl: Timeline, offset: Count) -> tuple[AudioTrack, ...]:
    shift_s = count_to_seconds(tl, offset)
    tracks = []
    for track in tl.audio:
        section = (
            None if track.section is None else _clip(track.section, offset, float("inf"), offset)
        )
        if track.section is not None and section is None:
            continue
        tracks.append(
            track.model_copy(
                update={"sync_offset_s": track.sync_offset_s + shift_s, "section": section}
            )
        )
    return tuple(tracks)


def _timeline_until(tl: Timeline, offset: Count) -> Timeline:
    return tl.model_copy(
        update={
            "tempos": tuple(t for t in tl.tempos if t.at_count < offset - TOL or t.at_count == 0),
            "holds": tuple(h for h in tl.holds if h.at_count < offset - TOL),
            "markers": tuple(m for m in tl.markers if m.at_count <= offset + TOL),
            "total_counts": offset,
        }
    )


def _timeline_from(tl: Timeline, offset: Count) -> Timeline:
    total = None if tl.total_counts is None else max(tl.total_counts - offset, 0.0)
    return tl.model_copy(
        update={
            "tempos": _tempo_from(tl, offset),
            "holds": tuple(
                h.model_copy(update={"at_count": h.at_count - offset})
                for h in tl.holds
                if h.at_count >= offset - TOL
            ),
            "markers": tuple(
                m.model_copy(update={"at_count": m.at_count - offset})
                for m in tl.markers
                if m.at_count >= offset - TOL
            ),
            "measures": _measures_from(tl, offset),
            "audio": _audio_from(tl, offset),
            "total_counts": total,
        }
    )


def _note_in(note: Note, ids: set[SetId]) -> bool:
    anchor = note.anchor
    if anchor.kind == "set":
        return anchor.set_id in ids
    if anchor.kind == "transition" and anchor.tid is not None:
        return set(anchor.tid) <= ids
    return True


def _pin_in(item: Pin, ids: set[SetId], forms: dict[str, Form]) -> bool:
    if item.target == "set":
        return item.id in ids
    if item.target == "form":
        return item.id in forms
    if item.target == "transition":
        return set(parse_tid(item.id)) <= ids
    return True


def _section(show: Show, sets: Sequence[Set], timeline: Timeline, low: Count, high: Count) -> Show:
    ids = {s.id for s in sets}
    forms = {k: f for k, f in show.forms.items() if f.set in ids}
    pairs = {(a.id, b.id) for a, b in itertools.pairwise(sets)}
    return show.model_copy(
        update={
            "sets": tuple(sets),
            "forms": forms,
            "transitions": tuple(t for t in show.transitions if t.id in pairs),
            "notes": tuple(n for n in show.notes if _note_in(n, ids)),
            "pins": tuple(p for p in show.pins if _pin_in(p, ids, forms)),
            "facing_overrides": _clip_overrides(show.facing_overrides, low, high, low),
            "zones": _clip_zones(show.zones, low, high, low),
            "timeline": timeline,
            "revision": "",
        }
    )


def split_section(show: Show, at: SetId) -> tuple[Show, Show]:
    """Splits a show at set `at`; both shows hold `at`, and the second starts its clock there.

    The second show's tempo, holds, markers, measures, audio sync, facing overrides and zone
    windows shift so its count 0 is `at`. Raises TimelineError when `at` starts mid-measure.

    Args:
        show: Show to split.
        at: Boundary set: last set of the first show, first set (0 counts) of the second.

    Returns:
        sections: The first and second show.

    >>> from showpy.examples import two_set_show
    >>> from showpy.show.manage import add_set
    >>> first, second = split_section(add_set(two_set_show(), "3", 4.0), "2")
    >>> first.set_ids(), second.set_ids(), second.sets[0].counts
    (['1', '2'], ['2', '3'], 0.0)
    """
    position = show.set_index(at)
    offset = index(show).entries[position].start_count
    head = list(show.sets[: position + 1])
    tail = [show.sets[position].model_copy(update={"counts": 0.0}), *show.sets[position + 1 :]]
    first = _section(show, head, _timeline_until(show.timeline, offset), 0.0, offset)
    second = _section(show, tail, _timeline_from(show.timeline, offset), offset, float("inf"))
    return first, second


def _require_compatible(a: Show, b: Show) -> None:
    problems = {
        "cast": a.cast.ids() != b.cast.ids(),
        "sheet": a.sheet.ref != b.sheet.ref,
        "dependencies": any(a.dependencies.get(k, v) != v for k, v in b.dependencies.items()),
    }
    bad = [name for name, is_bad in problems.items() if is_bad]
    if bad:
        error = VersionError if bad == ["dependencies"] else SchemaError
        raise error(
            f"sections differ in {', '.join(bad)}",
            hint="join sections that share the cast, the sheet and its pinned version",
            context={"differ": bad},
        )


def _tail_sets(a: Show, b: Show) -> tuple[Set, ...]:
    if b.sets and a.sets and b.sets[0].id == a.sets[-1].id:
        if b.sets[0].placements != a.sets[-1].placements:
            raise SchemaError(
                f"boundary set {b.sets[0].id} differs between the sections",
                hint="make the shared set identical, or renumber one section",
                context={"set": b.sets[0].id},
            )
        return b.sets[1:]
    clash = sorted(set(a.set_ids()) & set(b.set_ids()))
    if clash:
        raise SchemaError(
            f"set ids repeat across sections: {', '.join(clash)}",
            hint="renumber one section, or share only the boundary set",
            context={"sets": clash},
        )
    return b.sets


def _merged_forms(a: Show, b: Show) -> dict[str, Form]:
    clash = [k for k, f in b.forms.items() if k in a.forms and a.forms[k] != f]
    if clash:
        raise SchemaError(
            f"form ids repeat with different content: {', '.join(sorted(clash))}",
            hint="rename the forms in one section",
            context={"forms": sorted(clash)},
        )
    return {**a.forms, **b.forms}


def _redundant(previous: TempoSection, current: TempoSection) -> bool:
    if current.ramp_to is not None:
        return False
    if previous.ramp_to is None:
        return previous.counts_per_minute == current.counts_per_minute
    ramp_end = previous.at_count + (previous.ramp_counts or 0.0)
    return ramp_end <= current.at_count + TOL and previous.ramp_to == current.counts_per_minute


def _merge_tempos(tempos: tuple[TempoSection, ...]) -> tuple[TempoSection, ...]:
    """Drops tempo sections that restate the tempo already in force at their count."""
    kept: list[TempoSection] = []
    for section in tempos:
        if kept and _redundant(kept[-1], section):
            continue
        kept.append(section)
    return tuple(kept)


def _joined_timeline(a: Timeline, b: Timeline, offset: Count) -> Timeline:
    def moved(count: Count) -> Count:
        return count + offset

    labels = {m.label for m in a.markers}
    total = None if a.total_counts is None or b.total_counts is None else offset + b.total_counts
    return a.model_copy(
        update={
            "tempos": _merge_tempos(
                (
                    *a.tempos,
                    *(t.model_copy(update={"at_count": moved(t.at_count)}) for t in b.tempos),
                )
            ),
            "holds": (
                *a.holds,
                *(h.model_copy(update={"at_count": moved(h.at_count)}) for h in b.holds),
            ),
            "markers": (
                *a.markers,
                *(
                    m.model_copy(update={"at_count": moved(m.at_count)})
                    for m in b.markers
                    if m.label not in labels
                ),
            ),
            "total_counts": total,
        }
    )


def join_sections(a: Show, b: Show) -> Show:
    """Appends show `b` after show `a`; the inverse of split_section() for sets and paths.

    When `b` starts with `a`'s last set (same ID and placements) they become one set.
    Otherwise set IDs must not repeat, and `b`'s first set keeps its counts (0), so retime it
    with set_counts(). `b`'s tempos, holds, markers and facing overrides shift by `a`'s length.

    >>> from showpy.examples import two_set_show
    >>> from showpy.show.manage import add_set
    >>> whole = add_set(two_set_show(), "3", 4.0)
    >>> join_sections(*split_section(whole, "2")).sets == whole.sets
    True
    """
    _require_compatible(a, b)
    offset = index(a).total_counts
    zones = {z.id: z for z in a.zones}
    for zone in _clip_zones(b.zones, 0.0, float("inf"), -offset):
        zones.setdefault(zone.id, zone)
    return a.model_copy(
        update={
            "sets": (*a.sets, *_tail_sets(a, b)),
            "forms": _merged_forms(a, b),
            "transitions": (*a.transitions, *b.transitions),
            "notes": (*a.notes, *(n for n in b.notes if n not in a.notes)),
            "pins": (*a.pins, *(p for p in b.pins if p not in a.pins)),
            "facing_overrides": (
                *a.facing_overrides,
                *_clip_overrides(b.facing_overrides, 0.0, float("inf"), -offset),
            ),
            "zones": tuple(zones.values()),
            "dependencies": {**a.dependencies, **b.dependencies},
            "timeline": _joined_timeline(a.timeline, b.timeline, offset),
            "revision": "",
        }
    )
