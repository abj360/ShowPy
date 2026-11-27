#!/usr/bin/env python3

"""
test_pin_flags.py --- tests how apply_change keeps the flags held pins imply

Contains:
    test_regression_change_flags_only_the_items_it_wrote: leaves every placement, form and path
        row the change did not write as it was, even on a show whose pins already mismatch
    test_regression_pin_flags_cost_does_not_grow_with_placements_times_pins: applies a forced op
        on an 80,000-placement show with 400 entity pins about as fast as with none
"""

import time

from e8s.core.ids import SetId, SpotId
from e8s.show.changes import apply_change
from e8s.show.collab_models import PlacementOp
from e8s.show.models import Pin, Show
from tests.fixtures.generators import seeded_show

UNFLAGGED_PINS = (
    Pin(target="entity", id="B1"),
    Pin(target="set", id="2"),
    Pin(target="form", id="brass_2"),
    Pin(target="transition", id="1→2"),
)
WIDE_SPOTS = 400
WIDE_SETS = 200
TIMING_RUNS = 3
# The pinned run may cost a little more than the unpinned one, never a multiple of it: the old
# check visited every placement for every pin, 1.5 s against 0.1 s here (measured 2026-11-27).
PIN_COST_FACTOR = 2.0
PIN_COST_SLACK_S = 0.1


def _moved(show: Show, set_id: str, spot: str) -> PlacementOp:
    placement = show.set(set_id).get(SpotId(spot))
    return PlacementOp(set_id=SetId(set_id), placement=placement.replace(x=placement.x + 0.5))


def test_regression_change_flags_only_the_items_it_wrote() -> None:
    """Leaves unwritten items as they were, even where held pins already mismatch their flags."""
    show = seeded_show(3, 8, 4)._copied(update={"pins": UNFLAGGED_PINS})
    unpinned = next(e for e in show.set("3").entity_ids() if e != "B1")
    edited = apply_change(show, [_moved(show, "3", unpinned)], force=True)
    assert edited.forms == show.forms
    assert edited.transitions == show.transitions
    assert [s for s in edited.sets if s.id != "3"] == [s for s in show.sets if s.id != "3"]
    written = apply_change(show, [_moved(show, "3", "B1")], force=True)
    assert written.set("3").get(SpotId("B1")).pinned
    assert not written.set("2").get(SpotId("B1")).pinned


def _wide_show() -> Show:
    show = seeded_show(0, WIDE_SPOTS, 2)
    first, second = show.sets
    plain = second.replace(
        forms=(), placements=tuple(p.replace(form=None, order=None) for p in second.placements)
    )
    more = (plain.replace(id=SetId(str(n)), key=None) for n in range(3, WIDE_SETS + 1))
    return show.replace(sets=(first, second, *more))


def _best_time(show: Show, op: PlacementOp) -> float:
    times = []
    for _ in range(TIMING_RUNS):
        start = time.perf_counter()
        apply_change(show, [op], force=True)
        times.append(time.perf_counter() - start)
    return min(times)


def test_regression_pin_flags_cost_does_not_grow_with_placements_times_pins() -> None:
    """Applies a forced op with 400 entity pins on 80,000 placements about as fast as with none."""
    show = _wide_show()
    op = _moved(show, "2", show.set("2").entity_ids()[0])
    pins = tuple(Pin(target="entity", id=spot) for spot in show.cast.ids())
    pinned = show._copied(update={"pins": pins})
    unpinned_s = _best_time(show, op)
    assert _best_time(pinned, op) <= PIN_COST_FACTOR * unpinned_s + PIN_COST_SLACK_S
