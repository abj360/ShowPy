#!/usr/bin/env python3

"""
ch13_ex4.py --- reference answer to exercise 13.4: a script that returns forms

Contains:
    solve(): returns the built set 2 positions after building a forms script
"""

from datetime import UTC, datetime

from stage.examples import two_set_show
from stage.show.build import build_set
from stage.show.repositories import InMemoryRepository
from stage.show.storage import open_workspace, save_show

SCRIPT = '''from stage.choreo.forms import line_form


def build(ctx):
    """Places T1-T4 on a line at y = 12."""
    return [line_form("row", ["T1", "T2", "T3", "T4"], (0.0, 12.0), (6.0, 12.0))]
'''


def solve() -> list[list[float]]:
    """Returns the built set 2 positions after building a forms script."""
    at = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
    ws = open_workspace(InMemoryRepository(), "demo", "designer")
    save_show(ws, two_set_show(), at, "create")
    ws.write("/sets/2/set.py", SCRIPT, at=at, message="script set 2")
    result = build_set(ws, "2", at)
    positions: list[list[float]] = result.set.positions().tolist()
    return positions
