#!/usr/bin/env python3

"""
counts.py --- counts spots on and off the field in every set, for the set strip

Contains:
    spot_counts(): counts on-field and off-field spots per set and flags changes
"""

from showpy.choreo.models import SpotCount
from showpy.show.models import Show


def spot_counts(show: Show) -> list[SpotCount]:
    """Counts on-field and off-field spots per set; `changed` flags a different on-field count.

    >>> from showpy.examples import two_set_show
    >>> [c.on_field for c in spot_counts(two_set_show())]
    [4, 4]
    """
    counts: list[SpotCount] = []
    for s in show.sets:
        on = sum(1 for p in s.placements if p.on_field)
        changed = bool(counts) and counts[-1].on_field != on
        counts.append(
            SpotCount(set_id=s.id, on_field=on, off_field=len(s.placements) - on, changed=changed)
        )
    return counts
