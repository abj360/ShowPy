"""
v1_to_v2_timeline.py --- the timeline sub-step of the v1 to v2 format migration

A v2 timeline is valid at construction, so this step makes the stored v1 timeline valid first.
It repairs what a v0.3.0 edit function could have written, recording each repair with
`report.repair()`: tempo sections out of count order are sorted (stable); a first tempo after
count 0 starts at count 0; a ramp with a target but no length, a length but no target, or a
length of 0 or less becomes a steady tempo; holds out of count order are sorted (stable); a hold
after the last count is dropped, which changes no count inside the show; cues that share an
order are renumbered in stored order, each taking the next free order; measure changes out of
count order are sorted and several at one count become the one with the longest length, which
is how v0.3.0 read them. It refuses any other value v2 cannot
hold with SchemaError naming the document, the field and the value: two tempo sections or two
holds at one count, a hold before count 0, a negative length of the show, a measure length of 0
or less and a measure change before count 0.

Contains:
    TIMELINE_DOC: names the stored timeline document, which the step rewrites
    migrate_timeline(): rewrites the timeline document from the v1 shape to the v2 shape
"""

from typing import TYPE_CHECKING

from e8s.core.caps import MIN_TEMPO
from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder

type Item = dict[str, JsonValue]

TIMELINE_DOC = "/time/timeline.json"
AT_COUNT = "at_count"
TEMPO = "counts_per_minute"
RAMP_TO = "ramp_to"
RAMP_COUNTS = "ramp_counts"
MEASURE_CHANGE_PARTS = 2


def _refuse(field: str, value: JsonValue, why: str) -> SchemaError:
    return SchemaError(
        f"{TIMELINE_DOC} field {field} {why}",
        hint=f"edit {field} in {TIMELINE_DOC} so a timeline can be built from it",
        context={"path": TIMELINE_DOC, "field": field, "value": value},
    )


def _items(timeline: Item, field: str) -> list[Item] | None:
    entries = timeline.get(field)
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return None
    return [e for e in entries if isinstance(e, dict)]


def _number(item: Item, key: str) -> float | None:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _sorted_by_count(items: list[Item], field: str, report: "ReportBuilder") -> list[Item]:
    counts = [_number(item, AT_COUNT) for item in items]
    if any(count is None for count in counts):
        return items
    ordered = sorted(items, key=lambda item: _number(item, AT_COUNT) or 0.0)
    if ordered != items:
        report.repair(f"sorted the {field} by count")
    return ordered


def _require_distinct_counts(items: list[Item], field: str) -> None:
    seen: set[float] = set()
    for item in items:
        count = _number(item, AT_COUNT)
        if count is None:
            continue
        if count in seen:
            raise _refuse(field, count, f"holds two entries at count {count:g}")
        seen.add(count)


def _require_tempos_at_or_above_floor(tempos: list[Item]) -> None:
    for section in tempos:
        for field in (TEMPO, RAMP_TO):
            value = _number(section, field)
            if value is not None and value < MIN_TEMPO:
                raise _refuse(field, value, f"is below the slowest tempo {MIN_TEMPO:g}: {value:g}")


def _steady(section: Item, report: "ReportBuilder") -> Item:
    target, length = section.get(RAMP_TO), _number(section, RAMP_COUNTS)
    has_ramp = target is not None or section.get(RAMP_COUNTS) is not None
    is_complete = target is not None and length is not None and length > 0
    if not has_ramp or is_complete:
        return section
    report.repair(
        f"made the tempo at count {section.get(AT_COUNT)} steady: its ramp was incomplete"
    )
    return section | {RAMP_TO: None, RAMP_COUNTS: None}


def _migrate_tempos(timeline: Item, report: "ReportBuilder") -> list[Item] | None:
    tempos = _items(timeline, "tempos")
    if tempos is None:
        return None
    tempos = _sorted_by_count(tempos, "tempo sections", report)
    _require_distinct_counts(tempos, "tempos")
    _require_tempos_at_or_above_floor(tempos)
    tempos = [_steady(section, report) for section in tempos]
    if tempos and _number(tempos[0], AT_COUNT) not in (None, 0.0):
        report.repair(f"moved the first tempo from count {tempos[0].get(AT_COUNT)} to count 0")
        tempos[0] = tempos[0] | {AT_COUNT: 0.0}
    return tempos


def _migrate_holds(timeline: Item, report: "ReportBuilder") -> list[Item] | None:
    holds = _items(timeline, "holds")
    if holds is None:
        return None
    for hold in holds:
        count = _number(hold, AT_COUNT)
        if count is not None and count < 0:
            raise _refuse("holds", count, f"holds a hold before count 0, at count {count:g}")
    holds = _sorted_by_count(holds, "holds", report)
    _require_distinct_counts(holds, "holds")
    end = _number(timeline, "total_counts")
    if end is None:
        return holds
    kept = [h for h in holds if (_number(h, AT_COUNT) or 0.0) <= end]
    if len(kept) != len(holds):
        report.repair(f"dropped {len(holds) - len(kept)} hold(s) after the last count {end:g}")
    return kept


def _order(cue: Item) -> int | None:
    order = cue.get("order")
    return order if isinstance(order, int) and not isinstance(order, bool) else None


def _migrate_cues(timeline: Item, report: "ReportBuilder") -> list[Item] | None:
    cues = _items(timeline, "cues")
    if cues is None or any(_order(cue) is None for cue in cues):
        return None
    renumbered: list[Item] = []
    last_order: int | None = None
    for cue in sorted(cues, key=lambda cue: _order(cue) or 0):
        order = _order(cue) or 0
        if last_order is not None and order <= last_order:
            order = last_order + 1
            cue = cue | {"order": order}
        renumbered.append(cue)
        last_order = order
    if renumbered != cues:
        report.repair("renumbered cues that shared one order, in stored order")
    return renumbered


def _pair(change: JsonValue) -> tuple[float, float] | None:
    if not isinstance(change, list) or len(change) != MEASURE_CHANGE_PARTS:
        return None
    first, second = change
    if isinstance(first, bool) or isinstance(second, bool):
        return None
    if not isinstance(first, int | float) or not isinstance(second, int | float):
        return None
    return float(first), float(second)


def _migrate_measures(timeline: Item, report: "ReportBuilder") -> Item | None:
    measures = timeline.get("measures")
    if not isinstance(measures, dict):
        return None
    length = _number(measures, "counts_per_measure")
    if length is not None and length <= 0:
        raise _refuse("measures.counts_per_measure", length, f"is not above 0: {length:g}")
    changes = measures.get("changes")
    if not isinstance(changes, list):
        return None
    parsed = [_pair(c) for c in changes]
    pairs = [p for p in parsed if p is not None]
    if len(pairs) != len(parsed):
        return None
    for at_count, new_length in pairs:
        if at_count < 0:
            raise _refuse(
                "measures.changes", at_count, f"changes length before count 0: {at_count:g}"
            )
        if new_length <= 0:
            raise _refuse(
                "measures.changes", new_length, f"has a length not above 0: {new_length:g}"
            )
    kept = dict(sorted(pairs))
    ordered: list[JsonValue] = [[at_count, length] for at_count, length in kept.items()]
    if ordered == changes:
        return None
    report.repair("sorted the measure changes by count and kept the longest at a shared count")
    reordered: Item = dict(measures)
    reordered["changes"] = ordered
    return reordered


def _check_total(timeline: Item) -> None:
    end = _number(timeline, "total_counts")
    if end is not None and end < 0:
        raise _refuse("total_counts", end, f"is negative: {end:g}")


def migrate_timeline(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites the timeline document from the v1 shape to the v2 shape.

    Args:
        docs: Stored path to parsed document, without a format header; never mutated.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents with a timeline that builds as a v2 Timeline.
    """
    timeline = docs.get(TIMELINE_DOC)
    if not isinstance(timeline, dict):
        return docs
    _check_total(timeline)
    changes = {
        "tempos": _migrate_tempos(timeline, report),
        "holds": _migrate_holds(timeline, report),
        "cues": _migrate_cues(timeline, report),
    }
    repaired: Item = dict(timeline)
    for field, items in changes.items():
        if items is not None:
            repaired[field] = list[JsonValue](items)
    measures = _migrate_measures(timeline, report)
    if measures is not None:
        repaired["measures"] = measures
    migrated = dict(docs)
    migrated[TIMELINE_DOC] = repaired
    return migrated
