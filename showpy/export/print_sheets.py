"""
print_sheets.py --- builds coordinate sheets, set charts and the cue sheet of a show

Positions are worded by the show's coordinate system, not a pack language. Facing, behavior
and guide crossings come from PathEngine, the single motion source.

Contains:
    coordinate_sheet(): builds one spot's sheet, one row per set
    coordinate_sheets(): builds the sheets of every selected spot
    chart(): builds the designer chart of one set
    cue_sheet(): lists set labels and cues, timeline markers and cues in count order
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import astuple, dataclass

import pandas as pd
import shapely

from showpy.cast.select import select
from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_models import MotionPlan
from showpy.choreo.models import PathRow, PathStyle, Placement
from showpy.core.ids import SetId, SpotId, TransitionId
from showpy.core.selection import Selection
from showpy.core.types import Count, Point
from showpy.core.units import normalize_angle
from showpy.export.models import Chart, ChartRow, CoordinateSheet, Publication, SheetRow
from showpy.sheet.coords import CoordStyle, set_coordinates, to_coords
from showpy.sheet.models import GuideLine
from showpy.show.models import Show
from showpy.time.clock import count_to_seconds, seconds_to_count
from showpy.time.measures import count_to_measure
from showpy.time.models import TimelineMode
from showpy.time.timeline import cue_to_seconds

OFF_FIELD = "off field"
START = "start"
HOLD = "hold"
ENTER = "enter"
EXIT = "exit"
CLAUSE_SEPARATOR = ", "
DETAIL_SEPARATOR = "; "
COUNT_PLACES = 3
MIN_PATH_POINTS = 2
NO_CROSSINGS: tuple[str, ...] = ()
CUE_COLUMNS = ("count", "seconds", "measure", "kind", "label", "detail")
KIND_ORDER = {"set": 0, "marker": 1, "cue": 2}
STYLE_WORDS = {
    PathStyle.STRAIGHT: "move",
    PathStyle.CURVE: "curve",
    PathStyle.POLYLINE: "path",
    PathStyle.FOLLOW: "follow route",
}


def _counts_text(value: float) -> str:
    return f"{round(value, COUNT_PLACES):g}"


def _with_precision(show: Show, precision: float | None) -> Show:
    if precision is None:
        return show
    return set_coordinates(show, show.coordinates.replace(precision=precision))


def _claimed_names(publication: Publication | None) -> dict[SpotId, str]:
    if publication is None:
        return {}
    return {claim.spot: claim.performer.name for claim in publication.claims}


def _set_starts(show: Show) -> list[Count]:
    return list(itertools.accumulate((s.counts for s in show.sets[1:]), initial=0.0))


def _position_text(show: Show, placement: Placement, style: CoordStyle) -> str:
    return to_coords(show, placement.point, style) if placement.on_field else OFF_FIELD


def _move_text(plan: MotionPlan, row: PathRow) -> str:
    start, end = plan.window
    length = _counts_text(end - start)
    if plan.style == PathStyle.ROTATE:
        return f"rotate {row.sweep_deg:g}° in {length}"
    if plan.style == PathStyle.CUSTOM:
        return f"{row.motion_fn or plan.style.value} {length}"
    return f"{STYLE_WORDS.get(plan.style, plan.style.value)} {length}"


def _field_clause(plan: MotionPlan) -> str | None:
    if plan.end.on_field and not plan.start.on_field:
        return ENTER
    if plan.start.on_field and not plan.end.on_field:
        return EXIT
    return None


def _behavior(plan: MotionPlan, row: PathRow, counts: float) -> str:
    """Words the move into a set, e.g. `delay 2, move 6` or `enter, move 8`."""
    if not (plan.start.on_field or plan.end.on_field):
        return OFF_FIELD
    field = _field_clause(plan)
    if not plan.moves:
        return field or HOLD
    start, end = plan.window
    clauses = [
        field,
        f"delay {_counts_text(start)}" if start > 0 else None,
        _move_text(plan, row),
        f"hold {_counts_text(counts - end)}" if end < counts else None,
    ]
    return CLAUSE_SEPARATOR.join(c for c in clauses if c)


def _travelled(engine: PathEngine, entity: SpotId, tid: TransitionId) -> shapely.LineString | None:
    segments = engine.entity_segments(entity, tid)
    points: list[Point] = [segments[0].p0] if segments else []
    for segment in segments:
        if segment.p1 != points[-1]:
            points.append(segment.p1)
    return shapely.LineString(points) if len(points) >= MIN_PATH_POINTS else None


def _first_crossing(path: shapely.LineString, guide: GuideLine) -> float | None:
    if len(guide.points) < MIN_PATH_POINTS:
        return None
    line = shapely.LineString(guide.points)
    if not path.crosses(line):
        return None
    hits = shapely.get_coordinates(path.intersection(line))
    return min(path.project(shapely.Point(x, y)) for x, y in hits)


def _crossings(
    show: Show, engine: PathEngine, entity: SpotId, tid: TransitionId
) -> tuple[str, ...]:
    """Lists labels of guide lines the spot's travelled path crosses, in crossing order."""
    plan = engine.compile(tid).plans[entity]
    path = _travelled(engine, entity, tid) if plan.moves else None
    if path is None or not (plan.start.on_field or plan.end.on_field):
        return ()
    found = []
    for index, guide in enumerate(show.sheet.guides):
        at = _first_crossing(path, guide)
        if at is not None:
            found.append((at, index, guide.label or guide.id))
    return tuple(dict.fromkeys(label for _, _, label in sorted(found)))


def _sheet_row(
    show: Show, engine: PathEngine, entity: SpotId, index: int, cumulative: Count
) -> SheetRow:
    current = show.sets[index]
    placement = current.get(entity)
    behavior, crossings = START, NO_CROSSINGS
    if index > 0:
        tid = TransitionId(show.sets[index - 1].id, current.id)
        plan = engine.compile(tid).plans[entity]
        behavior = _behavior(plan, show.transition(tid).row(entity), current.counts)
        crossings = _crossings(show, engine, entity, tid)
    return SheetRow(
        set=current.id,
        counts=current.counts,
        cumulative=cumulative,
        measure=count_to_measure(show.timeline, cumulative),
        position_text=_position_text(show, placement, "long"),
        xy=placement.point,
        facing=normalize_angle(engine.facing_at(entity, cumulative)),
        behavior=behavior,
        crossings=crossings,
        note=placement.note,
    )


def _build_sheet(
    show: Show, engine: PathEngine, entity: SpotId, names: dict[SpotId, str]
) -> CoordinateSheet:
    show.cast.spot(entity)
    starts = _set_starts(show)
    rows = tuple(_sheet_row(show, engine, entity, i, starts[i]) for i in range(len(show.sets)))
    return CoordinateSheet(entity=entity, performer_name=names.get(entity), rows=rows)


def coordinate_sheet(
    show: Show,
    entity: SpotId,
    precision: float | None = None,
    publication: Publication | None = None,
) -> CoordinateSheet:
    """Builds one spot's coordinate sheet, one row per set.

    Args:
        show: Show to read.
        entity: Spot whose part is printed.
        precision: Rounding step for position text; None keeps the show's.
        publication: Publication whose claims fill in the performer's name.

    Returns:
        sheet: The spot's rows in show order.

    >>> from showpy.examples import two_set_show
    >>> [r.behavior for r in coordinate_sheet(two_set_show(), "T1").rows]
    ['start', 'move 8']
    """
    worded = _with_precision(show, precision)
    return _build_sheet(worded, PathEngine(worded), entity, _claimed_names(publication))


def coordinate_sheets(
    show: Show,
    sel: str | Sequence[SpotId] | Selection = "all",
    publication: Publication | None = None,
    precision: float | None = None,
) -> list[CoordinateSheet]:
    """Builds the coordinate sheets of every selected spot, sharing one engine.

    >>> from showpy.examples import two_set_show
    >>> [s.entity for s in coordinate_sheets(two_set_show(), "T1-T2")]
    ['T1', 'T2']
    """
    worded = _with_precision(show, precision)
    engine, names = PathEngine(worded), _claimed_names(publication)
    return [_build_sheet(worded, engine, e, names) for e in select(worded, sel).ids]


def chart(show: Show, set_id: SetId, publication: Publication | None = None) -> Chart:
    """Builds the designer chart of one set: every spot's position, facing and form.

    >>> from showpy.examples import two_set_show
    >>> [r.xy for r in chart(two_set_show(), "2").rows][:2]
    [(0.0, 9.0), (2.0, 9.0)]
    """
    current = show.set(set_id)
    engine, names = PathEngine(show), _claimed_names(publication)
    at = _set_starts(show)[show.set_index(set_id)]
    rows = []
    for spot in show.cast.spots:
        placement = current.get(spot.id)
        rows.append(
            ChartRow(
                entity=spot.id,
                group=spot.group,
                performer_name=names.get(spot.id),
                position_text=_position_text(show, placement, "long"),
                xy=placement.point,
                facing=normalize_angle(engine.facing_at(spot.id, at)),
                form=placement.form,
                on_field=placement.on_field,
                note=placement.note,
            )
        )
    return Chart(set_id=set_id, rows=tuple(rows))


def _clock_known(show: Show) -> bool:
    """Reports whether counts convert to seconds; a cue-mode timeline without tempo cannot."""
    return bool(show.timeline.tempos) or show.timeline.mode is not TimelineMode.CUES


def _seconds(show: Show, count: Count) -> float:
    return count_to_seconds(show.timeline, count) if _clock_known(show) else math.nan


def _count(show: Show, seconds: float) -> float:
    return seconds_to_count(show.timeline, seconds) if _clock_known(show) else math.nan


def _measure_text(show: Show, count: float) -> str:
    label = None if math.isnan(count) else count_to_measure(show.timeline, count)
    return "" if label is None else f"{label.measure}:{label.count_in_measure:g}"


@dataclass(frozen=True)
class _CueEntry:
    count: float
    seconds: float
    measure: str
    kind: str
    label: str
    detail: str

    def key(self) -> tuple[float, float, int, str]:
        def _last(v: float) -> float:
            return math.inf if math.isnan(v) else v

        return _last(self.count), _last(self.seconds), KIND_ORDER[self.kind], self.label


def _entry(show: Show, count: float, seconds: float, kind: str, text: tuple[str, str]) -> _CueEntry:
    label, detail = text
    measure = _measure_text(show, count)
    return _CueEntry(count, seconds, measure, kind, label, detail)


def _set_entries(show: Show) -> list[_CueEntry]:
    entries = []
    for start, current in zip(_set_starts(show), show.sets, strict=True):
        parts = [current.label, f"cue {current.cue}" if current.cue else None]
        detail = DETAIL_SEPARATOR.join(p for p in parts if p)
        if detail:
            text = (f"set {current.id}", detail)
            entries.append(_entry(show, start, _seconds(show, start), "set", text))
    return entries


def _marker_entries(show: Show) -> list[_CueEntry]:
    return [
        _entry(show, m.at_count, _seconds(show, m.at_count), "marker", (m.label, m.kind.value))
        for m in show.timeline.markers
    ]


def _cue_entries(show: Show) -> list[_CueEntry]:
    entries = []
    for cue in show.timeline.cues:
        seconds = cue_to_seconds(show.timeline, cue.label)
        text = (cue.label, f"{cue.estimated_seconds:g} s")
        entries.append(_entry(show, _count(show, seconds), seconds, "cue", text))
    return entries


def cue_sheet(show: Show) -> pd.DataFrame:
    """Lists set labels and cues, timeline markers and cue-mode cues in count order.

    In cue mode without a tempo, counts and seconds cannot convert and show as NaN.

    >>> from showpy.examples import two_set_show
    >>> list(cue_sheet(two_set_show()).columns)
    ['count', 'seconds', 'measure', 'kind', 'label', 'detail']
    """
    entries = _set_entries(show) + _marker_entries(show) + _cue_entries(show)
    rows = [astuple(entry) for entry in sorted(entries, key=_CueEntry.key)]
    table = pd.DataFrame(rows, columns=list(CUE_COLUMNS))
    table.attrs["title"] = f"{show.title} cue sheet"
    return table
