#!/usr/bin/env python3

"""
check_ch08_ex3.py --- checks exercise 8.3: a drafter for every transition

Contains:
    check(): tests the solution and raises WrongAnswerError with a hint
"""

from types import ModuleType

from checklib import expect, run


def check(solution: ModuleType) -> None:
    """Tests the solution to exercise 8.3."""
    drafts = solution.solve()
    expect(len(drafts) == 1, "the example show has one transition")
    d = drafts[0]
    expect(d.kind == "warning" and not d.visible, "drafts are hidden warnings")
    expect(d.anchor.tid == ("1", "2") and d.id == "draft-1", "anchored on (1, 2), id draft-1")


if __name__ == "__main__":
    run("ch08_ex3", check)
