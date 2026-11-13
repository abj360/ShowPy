#!/usr/bin/env python3

"""
test_counts.py --- tests on-field spot counts per set for the set strip
"""

from showpy.examples import two_set_show
from showpy.show.counts import spot_counts


def test_counts_every_set_and_flags_changes() -> None:
    show = two_set_show()
    second = show.set("2").with_placements({"T1": {"on_field": False}})
    show = show.model_copy(update={"sets": (show.sets[0], second)})
    counts = spot_counts(show)
    assert [(c.set_id, c.on_field, c.off_field, c.changed) for c in counts] == [
        ("1", 4, 0, False),
        ("2", 3, 1, True),
    ]
