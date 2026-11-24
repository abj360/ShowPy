"""
sections.py --- splits a show into movements at a set and joins movements back into one show

Contains:
    split_section(): splits a show at a set into two shows that both hold that set
    join_sections(): appends one show to another, sharing the boundary set when both have it
"""

import itertools
from collections.abc import Mapping, Sequence

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride
from e8s.core.errors import SchemaError, TimelineError, VersionError
from e8s.core.ids import FormId, SetId, parse_tid
from e8s.core.types import Count
from e8s.show.models import Pin, Show
from e8s.show.set_keys import free_set_key, set_key
from e8s.show.set_queries import index
from e8s.time.clock import count_to_seconds, tempo_at
from e8s.time.measures import count_to_measure
from e8s.time.models import AudioTrack, Marker, MeasureSetting, TempoSection, Timeline

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
            kept.append(item.replace(start=window[0], end=window[1]))
    return tuple(kept)


def _tempo_from(tl: Timeline, offset: Count) -> tuple[TempoSection, ...]:
    if not tl.tempos:
        return ()
    active = [t for t in tl.tempos if t.at_count <= offset + TOL][-1]
    start = TempoSection(at_count=0.0, counts_per_minute=tempo_at(tl, offset))
    ramp_end = active.at_count + (active.ramp_counts or 0.0)
    if active.ramp_to is not None and ramp_end - offset > TOL:
        start = start.replace(ramp_to=active.ramp_to, ramp_counts=ramp_end - offset)
    later = [
        t.replace(at_count=t.at_count - offset) for t in tl.tempos if t.at_count > offset + TOL
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
        tracks.append(track.replace(sync_offset_s=track.sync_offset_s + shift_s, section=section))
    return tuple(tracks)


def _ramp_until(tl: Timeline, section: TempoSection, offset: Count) -> TempoSection:
    ramp_end = section.at_count + (section.ramp_counts or 0.0)
    if section.ramp_to is None or ramp_end - offset <= TOL:
        return section
    return section.replace(ramp_to=tempo_at(tl, offset), ramp_counts=offset - section.at_count)


def _audio_until(tl: Timeline, offset: Count) -> tuple[AudioTrack, ...]:
    tracks = []
    for track in tl.audio:
        if track.section is None:
            tracks.append(track)
            continue
        section = _clip(track.section, 0.0, offset, 0.0)
        if section is not None:
            tracks.append(track.replace(section=section))
    return tuple(tracks)


def _timeline_until(tl: Timeline, offset: Count) -> Timeline:
    return tl.replace(
        tempos=tuple(
            _ramp_until(tl, t, offset)
            for t in tl.tempos
            if t.at_count < offset - TOL or t.at_count == 0
        ),
        holds=tuple(h for h in tl.holds if h.at_count < offset - TOL),
        markers=tuple(m for m in tl.markers if m.at_count <= offset + TOL),
        audio=_audio_until(tl, offset),
        total_counts=offset,
    )


def _timeline_from(tl: Timeline, offset: Count) -> Timeline:
    total = None if tl.total_counts is None else max(tl.total_counts - offset, 0.0)
    return tl.replace(
        tempos=_tempo_from(tl, offset),
        holds=tuple(
            h.replace(at_count=h.at_count - offset) for h in tl.holds if h.at_count >= offset - TOL
        ),
        markers=tuple(
            m.replace(at_count=m.at_count - offset)
            for m in tl.markers
            if m.at_count >= offset - TOL
        ),
        measures=_measures_from(tl, offset),
        audio=_audio_from(tl, offset),
        total_counts=total,
    )


def _pin_in(item: Pin, ids: set[SetId], forms: Mapping[FormId, Form]) -> bool:
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
    return show.replace(
        sets=tuple(sets),
        forms=forms,
        transitions=tuple(t for t in show.transitions if t.id in pairs),
        pins=tuple(p for p in show.pins if _pin_in(p, ids, forms)),
        facing_overrides=_clip_overrides(show.facing_overrides, low, high, low),
        timeline=timeline,
        revision="",
    )


def split_section(show: Show, at: str) -> tuple[Show, Show]:
    """Splits a show at set `at`; both shows hold `at`, and the second starts its clock there.

    The second show's tempo, holds, markers, measures, audio sync and facing overrides shift
    so its count 0 is `at`. A tempo ramp that runs past `at` ends there in the first
    show, at the tempo the whole show has at `at`. Raises TimelineError when `at` starts
    mid-measure.

    An audio track goes to every show its section overlaps, with the section clipped to it;
    a track with no section goes to both.

    Args:
        show: Show to split.
        at: Boundary set: last set of the first show, first set (0 counts) of the second.

    Returns:
        sections: The first and second show.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.manage import add_set
    >>> first, second = split_section(add_set(two_set_show(), "3", 4.0), "2")
    >>> first.set_ids(), second.set_ids(), second.sets[0].counts
    (['1', '2'], ['2', '3'], 0.0)
    """
    position = show.set_index(at)
    offset = index(show).entries[position].start_count
    head = list(show.sets[: position + 1])
    tail = [show.sets[position].replace(counts=0.0), *show.sets[position + 1 :]]
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


def _keyed_after(a: Show, tail: Sequence[Set]) -> tuple[Set, ...]:
    taken = [set_key(s) for s in a.sets]
    folded = {key.casefold() for key in taken}
    keyed: list[Set] = []
    for current in tail:
        key = set_key(current)
        if key.casefold() in folded:
            key = free_set_key(taken, current.id)
        taken.append(key)
        folded.add(key.casefold())
        keyed.append(current if key == current.key else current.replace(key=key))
    return tuple(keyed)


def _merged_forms(a: Show, b: Show) -> dict[FormId, Form]:
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


def _new_markers(a: Sequence[Marker], b: Sequence[Marker], offset: Count) -> list[Marker]:
    """Shifts `b`'s markers by `offset`, skipping one `a` holds at the same count with its label."""
    moved = (m.replace(at_count=m.at_count + offset) for m in b)
    return [
        m
        for m in moved
        if not any(k.label == m.label and abs(k.at_count - m.at_count) <= TOL for k in a)
    ]


def _joined_audio(a: Timeline, b: Timeline, offset: Count) -> tuple[AudioTrack, ...]:
    """Rejoins tracks by label: `a`'s track takes its section end from `b`'s copy."""
    later = {t.label: t for t in b.audio}
    held = {t.label for t in a.audio}
    shift_s = count_to_seconds(a, offset)
    joined = []
    for track in a.audio:
        other = later.get(track.label)
        if track.section is None or other is None or other.section is None:
            joined.append(track)
        else:
            joined.append(track.replace(section=(track.section[0], other.section[1] + offset)))
    for track in b.audio:
        if track.label in held:
            continue
        section = (
            None
            if track.section is None
            else (track.section[0] + offset, track.section[1] + offset)
        )
        joined.append(track.replace(sync_offset_s=track.sync_offset_s - shift_s, section=section))
    return tuple(joined)


def _joined_timeline(a: Timeline, b: Timeline, offset: Count) -> Timeline:
    def moved(count: Count) -> Count:
        return count + offset

    total = None if a.total_counts is None or b.total_counts is None else offset + b.total_counts
    return a.replace(
        tempos=_merge_tempos(
            (
                *a.tempos,
                *(t.replace(at_count=moved(t.at_count)) for t in b.tempos),
            )
        ),
        holds=(
            *a.holds,
            *(h.replace(at_count=moved(h.at_count)) for h in b.holds),
        ),
        markers=(
            *a.markers,
            *_new_markers(a.markers, b.markers, offset),
        ),
        audio=_joined_audio(a, b, offset),
        total_counts=total,
    )


def _fusable(first: FacingOverride, second: FacingOverride, offset: Count) -> bool:
    if abs(first.end - offset) > TOL or abs(second.start - offset) > TOL:
        return False
    return (first.entities, first.mode, first.value, first.turn_counts) == (
        second.entities,
        second.mode,
        second.value,
        second.turn_counts,
    )


def _joined_overrides(
    a: Sequence[FacingOverride], b: Sequence[FacingOverride], offset: Count
) -> tuple[FacingOverride, ...]:
    """Appends `b`'s overrides shifted by `offset`, fusing one that continues an `a` override."""
    joined = list(a)
    for item in _clip_overrides(b, 0.0, float("inf"), -offset):
        match = next((i for i, kept in enumerate(joined) if _fusable(kept, item, offset)), None)
        if match is None:
            joined.append(item)
        else:
            joined[match] = joined[match].replace(end=item.end)
    return tuple(joined)


def _first_pins(pins: Sequence[Pin]) -> tuple[Pin, ...]:
    kept: dict[tuple[str, str], Pin] = {}
    for record in pins:
        kept.setdefault((record.target, record.id), record)
    return tuple(kept.values())


def join_sections(a: Show, b: Show) -> Show:
    """Appends show `b` after show `a`; the inverse of split_section() for sets and paths.

    When `b` starts with `a`'s last set (same ID and placements) they become one set.
    Otherwise set IDs must not repeat, and `b`'s first set keeps its counts (0), so retime it
    with set_counts(). `b`'s tempos, holds, markers and facing overrides shift by `a`'s length.
    A facing override that ends where `a` ends and continues unchanged at `b`'s count 0 becomes
    one override.
    `b`'s sets keep their keys; one whose key `a` already holds gets a free key, as in add_set().
    One pin is kept per target and ID; when both halves pin the same spot, form, set or
    transition, `a`'s pin wins.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.manage import add_set
    >>> whole = add_set(two_set_show(), "3", 4.0)
    >>> join_sections(*split_section(whole, "2")).sets == whole.sets
    True
    """
    _require_compatible(a, b)
    offset = index(a).total_counts
    return a.replace(
        sets=(*a.sets, *_keyed_after(a, _tail_sets(a, b))),
        forms=_merged_forms(a, b),
        transitions=(*a.transitions, *b.transitions),
        pins=_first_pins((*a.pins, *b.pins)),
        facing_overrides=_joined_overrides(a.facing_overrides, b.facing_overrides, offset),
        dependencies={**a.dependencies, **b.dependencies},
        timeline=_joined_timeline(a.timeline, b.timeline, offset),
        revision="",
    )
