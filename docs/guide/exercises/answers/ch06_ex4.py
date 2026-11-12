#!/usr/bin/env python3

"""
ch06_ex4.py --- reference answer to exercise 6.4: find the bad token

Contains:
    solve(): returns the position of the first bad token in `expr`, or -1 when valid
"""

from stage.cast.select import select
from stage.core.errors import StageError
from stage.show.models import Show


def solve(show: Show, expr: str) -> int:
    """Returns the position of the first bad token in `expr`, or -1 when valid."""
    try:
        select(show, expr)
    except StageError as error:
        return int(error.context.get("position", 0))
    return -1
