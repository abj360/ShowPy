#!/usr/bin/env python3

"""
test_validate_values.py --- tests the stored-value checks of a whole show

Contains:
    test_non_finite_scan_skips_values_shared_with_a_known_show: searches only the values that are
        not the very objects of a show known to hold no non-finite number
"""

import math

from e8s.show.validate_values import has_non_finite, non_finite_issues
from tests.fixtures.shows import two_set_show


def test_non_finite_scan_skips_values_shared_with_a_known_show() -> None:
    """Searches only values that are not the very objects of a show holding none."""
    show = two_set_show()
    infinite = show.sets[1]._copied(update={"counts": math.inf})
    edited = show._copied(update={"sets": (show.sets[0], infinite)})
    found = non_finite_issues(edited)
    assert [issue.where for issue in found] == ["sets[1].counts"]
    assert non_finite_issues(edited, known=show) == found
    assert non_finite_issues(edited, known=edited) == []
    assert has_non_finite(infinite)
    assert not has_non_finite(infinite, known=infinite)
