#!/usr/bin/env python3

"""
generators.py --- deterministic seeded shows of any size, for benchmarks and golden files

Every set after the first puts all groups but one into forms; the remaining group holds,
except for a file that follows a route. Every EXIT_EVERY-th set, the last EXITING held spots
leave the field where they stand; they enter again in the next set, when their group is
placed into a form. Moving spots cycle through the other path styles, so every transition
with enough spots uses all seven. Tempo ramps and holds sit on set boundaries. All
randomness comes from seeded_rng(seed), and floats are scaled in Python so every platform
draws the same bits.

Contains:
    GROUPS: group keys and label prefixes of seeded shows
    MOVING_STYLES: the styles moving spots cycle through
    seeded_show(): builds a deterministic show of `spots` spots and `sets` sets from a seed
"""

import itertools

import numpy as np

from e8s.cast.models import Group
from e8s.cast.spots import add_group, add_spots_along
from e8s.choreo.form_models import Form
from e8s.choreo.form_place import place
from e8s.choreo.forms import arc_form, block_form, circle_form, line_form, wedge_form
from e8s.choreo.match_ops import with_set
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathStyle, TransitionSpec
from e8s.choreo.paths import curve_path, custom_path, delay, hold, set_easing
from e8s.choreo.routes import follow_and_set_targets, make_route, rotate_path, two_part
from e8s.core.types import Point
from e8s.core.util import seeded_rng
from e8s.geo.shapes import line
from e8s.sheet.models import Sheet
from e8s.sheet.templates import _sheet_templates as sheet_templates
from e8s.show.changes import apply_change, store_paths
from e8s.show.manage import add_set, new_show
from e8s.show.models import FormOp, PathsOp, Show
from e8s.time.models import Hold, TempoSection, Timeline, TimelineMode
from e8s.time.timeline import add_hold, add_tempo, new_timeline
from tests.helpers import bisector_rotation

SHOW_ID = "seeded"
TITLE = "Seeded show"
SHEET_REF = "sheet:college-football@1"
GROUPS = {"brass": "B", "woodwinds": "W", "percussion": "P", "guard": "G"}
MOVING_STYLES = (
    PathStyle.STRAIGHT, PathStyle.CURVE, PathStyle.POLYLINE, PathStyle.ROTATE, PathStyle.CUSTOM
)  # fmt: skip
FORM_KINDS = ("line", "arc", "circle", "block", "wedge")
SET_COUNTS = (4.0, 8.0, 12.0, 16.0)
BASE_TEMPO = 144.0
TEMPO_CHOICES = (120.0, 132.0, 144.0, 160.0, 176.0)
RAMP_EVERY = 4
HOLD_EVERY = 5
HOLD_SECONDS = (0.5, 1.0, 2.0)
ROW_SPOTS = 30
SPOT_GAP_M = 1.5
ROW_GAP_M = 2.0
FRONT_M = 4.0
FORM_SPAN_M = (4.0, 12.0)
FORM_CENTER_X = (-30.0, 30.0)
FORM_CENTER_Y = (14.0, 34.0)
FORM_GAP_M = 1.5
ARC_SWEEP_DEG = 180.0
WEDGE_SPREAD_DEG = 90.0
FULL_TURN_DEG = 360.0
FOLLOW_FILE = 3
EXIT_EVERY = 3
EXITING = 2
FOLLOW_STEP_M = 4.0
CURVE_BEND_M = 2.0
REACH_M = 3.0
WEAVE_AMPLITUDE_M = 0.5
WEAVE_CYCLES = 2.0
DELAY_SHARE = 0.25
EASINGS = ("ease_in", "ease_out", "ease_in_out")
FIRST_SET = "1"


def _sheet() -> Sheet:
    return next(s for s in sheet_templates() if s.ref == SHEET_REF)


def _timeline(rng: np.random.Generator, counts: list[float]) -> Timeline:
    tl = new_timeline(TimelineMode.COUNTS, counts_per_minute=BASE_TEMPO)
    for index, at in enumerate(itertools.accumulate(counts[:-1]), start=1):
        if index % RAMP_EVERY == 0:
            tempo, target = (float(t) for t in rng.choice(TEMPO_CHOICES, size=2))
            ramp = TempoSection(
                at_count=at, counts_per_minute=tempo, ramp_to=target, ramp_counts=counts[index]
            )
            tl = add_tempo(tl, ramp)
        if index % HOLD_EVERY == 0:
            tl = add_hold(tl, Hold(at_count=at, seconds=float(rng.choice(HOLD_SECONDS))))
    return tl


def _group_sizes(spots: int) -> list[int]:
    share, extra = divmod(spots, len(GROUPS))
    return [share + (1 if i < extra else 0) for i in range(len(GROUPS))]


def _with_cast(show: Show, spots: int) -> Show:
    row = 0
    for (key, prefix), size in zip(GROUPS.items(), _group_sizes(spots), strict=True):
        show = add_group(show, Group(key=key, label_prefix=prefix))
        for start in range(0, size, ROW_SPOTS):
            n = min(ROW_SPOTS, size - start)
            half = max(n - 1, 1) * SPOT_GAP_M / 2
            y = FRONT_M + row * ROW_GAP_M
            show = add_spots_along(show, line((-half, y), (half, y)), n, key, FIRST_SET)
            row += 1
    return show


def _members(show: Show) -> dict[str, list[str]]:
    return {key: [s.id for s in show.cast.spots if s.group == key] for key in GROUPS}


def _uniform(rng: np.random.Generator, low: float, high: float) -> float:
    """Draws a float in [low, high) that is bit-identical on every platform.

    `Generator.uniform` is fused into one multiply-add on arm64 builds of numpy and not on
    x86_64, so its last bit differs between them; Python never fuses `low + scale * u`.
    """
    return low + (high - low) * float(rng.random())


def _form(rng: np.random.Generator, kind: str, form_id: str, ids: list[str], set_id: str) -> Form:
    center = (_uniform(rng, *FORM_CENTER_X), _uniform(rng, *FORM_CENTER_Y))
    span = max(_uniform(rng, *FORM_SPAN_M), len(ids) * FORM_GAP_M / 2)
    heading = _uniform(rng, 0.0, FULL_TURN_DEG)
    if kind == "line":
        a, b = (center[0] - span, center[1]), (center[0] + span, center[1])
        return line_form(form_id, ids, a, b, set_id=set_id)
    if kind == "arc":
        return arc_form(form_id, ids, center, span, heading, ARC_SWEEP_DEG, set_id=set_id)
    if kind == "circle":
        return circle_form(form_id, ids, center, span, heading, set_id=set_id)
    if kind == "wedge":
        return wedge_form(form_id, ids, center, span, WEDGE_SPREAD_DEG, heading, set_id=set_id)
    cols = min(len(ids), ROW_SPOTS // 3)
    while len(ids) % cols:
        cols -= 1
    rows = len(ids) // cols
    return block_form(form_id, ids, center, rows, cols, FORM_GAP_M, heading, set_id=set_id)


def _place_forms(rng: np.random.Generator, show: Show, set_id: str, still: str) -> Show:
    forms = []
    for index, (key, ids) in enumerate(_members(show).items()):
        if key == still or not ids:
            continue
        kind = FORM_KINDS[(int(set_id) + index) % len(FORM_KINDS)]
        forms.append(_form(rng, kind, f"{key}_{set_id}", ids, set_id))
    placed_set, filled = place(show, set_id, forms)
    return apply_change(with_set(show, placed_set), [FormOp(form=f) for f in filled])


def _follow_and_hold(show: Show, tid: tuple[str, str], ids: list[str]) -> Show:
    if not ids:
        return show
    file = ids[:FOLLOW_FILE]
    x, y = show.set(tid[0]).get(file[0]).point
    route = make_route(show, tid, file[0], [(x + FOLLOW_STEP_M, y)], members=file)
    show = follow_and_set_targets(show, tid, route)
    rest = ids[FOLLOW_FILE:]
    if not rest:
        return show
    show = store_paths(show, PathsOp(spec=hold(show.transition(tid), rest)))
    return _exit(show, tid[1], rest[-EXITING:]) if int(tid[1]) % EXIT_EVERY == 0 else show


def _exit(show: Show, set_id: str, leaving: list[str]) -> Show:
    off = {e: PlacementPatch(on_field=False) for e in leaving}
    return with_set(show, show.set(set_id).with_placements(off))


def _bend(a: Point, b: Point, sign: float) -> Point:
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    return (mid[0] - sign * CURVE_BEND_M, mid[1] + sign * CURVE_BEND_M)


def _styled(
    rng: np.random.Generator, show: Show, spec: TransitionSpec, by_style: dict[PathStyle, list[str]]
) -> TransitionSpec:
    start, target = show.set(spec.id[0]), show.set(spec.id[1])
    for entity in by_style[PathStyle.CURVE]:
        bend = _bend(start.get(entity).point, target.get(entity).point, float(rng.choice((-1, 1))))
        spec = curve_path(spec, entity, bend)
    if by_style[PathStyle.POLYLINE]:
        spec = two_part(show, spec, by_style[PathStyle.POLYLINE], "x_first")
    for entity in by_style[PathStyle.ROTATE]:
        reach = REACH_M * float(rng.choice((-1, 1)))
        pivot, sweep = bisector_rotation(
            start.get(entity).point, target.get(entity).point, reach, long_way=False
        )
        spec = rotate_path(show, spec, [entity], pivot, sweep)
    if by_style[PathStyle.CUSTOM]:
        custom = by_style[PathStyle.CUSTOM]
        spec = custom_path(spec, custom, "weave", amplitude=WEAVE_AMPLITUDE_M, cycles=WEAVE_CYCLES)
    return spec


def _timed(rng: np.random.Generator, spec: TransitionSpec, moving: list[str]) -> TransitionSpec:
    late = [e for e in moving if rng.random() < DELAY_SHARE]
    if late:
        spec = delay(spec, late, spec.counts * DELAY_SHARE)
    eased = [e for e in moving if rng.random() < DELAY_SHARE]
    if eased:
        spec = set_easing(spec, eased, str(rng.choice(EASINGS)))
    return spec


def _with_paths(rng: np.random.Generator, show: Show, tid: tuple[str, str], still: str) -> Show:
    groups = _members(show)
    show = _follow_and_hold(show, tid, groups[still])
    moving = [e for key, ids in groups.items() if key != still for e in ids]
    by_style: dict[PathStyle, list[str]] = {style: [] for style in MOVING_STYLES}
    for index, entity in enumerate(moving):
        by_style[MOVING_STYLES[(index + int(tid[1])) % len(MOVING_STYLES)]].append(entity)
    spec = show.transition(tid)
    spec = _timed(rng, _styled(rng, show, spec, by_style), moving)
    return store_paths(show, PathsOp(spec=spec))


def seeded_show(seed: int, spots: int, sets: int) -> Show:
    """Builds a deterministic show with `spots` spots in four groups and `sets` sets.

    Args:
        seed: Seed for seeded_rng; equal seeds give equal shows.
        spots: Number of spots, split evenly across the four groups.
        sets: Number of sets, at least 1.

    Returns:
        show: A show that validates, with forms, all seven path styles, ramps and holds.
    """
    rng = seeded_rng(seed)
    counts = [float(c) for c in rng.choice(SET_COUNTS, size=sets - 1)]
    show = new_show(SHOW_ID, TITLE, _sheet(), _timeline(rng, counts))
    show = _with_cast(show, spots)
    keys = list(GROUPS)
    for number, set_counts in enumerate(counts, start=2):
        set_id, still = str(number), keys[number % len(keys)]
        show = add_set(show, set_id, set_counts)
        show = _place_forms(rng, show, set_id, still)
        show = _with_paths(rng, show, (str(number - 1), set_id), still)
    return show
