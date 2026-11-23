"""
shows.py --- small, fully specified example shows for docs, doctests and tests

These are deterministic so examples print stable output. Tests re-export them from
tests/fixtures, so there is one definition of each example.

Contains:
    EXAMPLE_TID: the transition between the two example sets
    TEMPO: tempo of the example timeline in counts per minute
    COUNTS: length of the example transition in counts
    two_set_show(): builds four trumpets moving 4 m upstage over 8 counts
    with_rows(): returns the example show with path rows stored for transition 1→2
    with_targets(): returns the example show with set 2 placements patched
    crossing_show(): returns two spots whose straight moves cross at count 4
"""

from collections.abc import Mapping
from typing import Any

from e8s.cast.models import Cast, Group, Spot
from e8s.choreo.models import Placement, PlacementPatch, Set
from e8s.choreo.path_models import PathRow, Route, TransitionSpec
from e8s.core.ids import make_tid
from e8s.examples.sheets import field_sheet
from e8s.show.models import Show
from e8s.time.models import TempoSection, Timeline, TimelineMode

EXAMPLE_TID = make_tid("1", "2")
TEMPO = 120.0
COUNTS = 8.0


def two_set_show() -> Show:
    """Builds four trumpets in a line at y=5 (set 1) moving 4 m upstage over 8 counts (set 2).

    >>> [p.point for p in two_set_show().set("2").placements][:2]
    [(0.0, 9.0), (2.0, 9.0)]
    """
    spots = tuple(Spot._validated({"id": f"T{i}", "group": "trumpets"}) for i in range(1, 5))
    first = tuple(Placement(entity=s.id, x=2.0 * i, y=5.0) for i, s in enumerate(spots))
    second = tuple(p.replace(y=9.0) for p in first)
    return Show(
        id="show-1",
        title="Test show",
        sheet=field_sheet(),
        cast=Cast(groups={"trumpets": Group(key="trumpets", label_prefix="T")}, spots=spots),
        timeline=Timeline(
            mode=TimelineMode.COUNTS, tempos=(TempoSection(at_count=0.0, counts_per_minute=TEMPO),)
        ),
        sets=(
            Set._validated({"id": "1", "counts": 0.0, "placements": first}),
            Set._validated({"id": "2", "counts": COUNTS, "placements": second}),
        ),
        dependencies={"sheet:test-field": "1"},
    )


def with_rows(
    rows: Mapping[str, PathRow], routes: Mapping[str, Route] | None = None, show: Show | None = None
) -> Show:
    """Returns a show whose transition 1→2 stores `rows` (and `routes`).

    >>> with_rows({"T1": PathRow(entity="T1", start=2.0)}).transition(("1", "2")).row("T1").start
    2.0
    """
    base = show or two_set_show()
    spec = TransitionSpec._validated(
        {"id": EXAMPLE_TID, "counts": COUNTS, "rows": dict(rows), "routes": dict(routes or {})}
    )
    return base.replace(transitions=(spec,))


# Any: target updates are partial placement dicts.
def with_targets[K: str](updates: Mapping[K, Mapping[str, Any]], show: Show | None = None) -> Show:
    """Returns a show whose set 2 placements are patched by `updates`.

    >>> with_targets({"T1": {"x": 3.0}}).set("2").get("T1").x
    3.0
    """
    base = show or two_set_show()
    patches = {entity: PlacementPatch._validated(change) for entity, change in updates.items()}
    second = base.sets[1].with_placements(patches)
    return base.replace(sets=(base.sets[0], second))


def crossing_show() -> Show:
    """Returns T1 (0,5)→(4,9) and T2 (4,5)→(0,9), meeting at (2,7) at count 4; T3, T4 hold.

    >>> crossing_show().set("1").get("T2").point
    (4.0, 5.0)
    """
    base = two_set_show()
    first = base.sets[0].with_placements(
        {"T2": PlacementPatch(x=4.0), "T3": PlacementPatch(x=8.0), "T4": PlacementPatch(x=12.0)}
    )
    show = base.replace(sets=(first, base.sets[1]))
    targets = {
        "T1": {"x": 4.0, "y": 9.0},
        "T2": {"x": 0.0, "y": 9.0},
        "T3": {"x": 8.0, "y": 5.0},
        "T4": {"x": 12.0, "y": 5.0},
    }
    return with_targets(targets, show=show)
