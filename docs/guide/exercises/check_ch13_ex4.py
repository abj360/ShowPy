#!/usr/bin/env python3

"""
check_ch13_ex4.py --- checks exercise 13.4: a script that returns forms

Contains:
    check(): tests the solution and raises WrongAnswerError with a hint
"""

from types import ModuleType

from checklib import close, expect, run


def check(solution: ModuleType) -> None:
    """Tests the solution to exercise 13.4."""
    got = solution.solve()
    expect(close(got, [[0, 12], [2, 12], [4, 12], [6, 12]]), f"got {got}")


if __name__ == "__main__":
    run("ch13_ex4", check)
