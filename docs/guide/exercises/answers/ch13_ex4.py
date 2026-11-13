#!/usr/bin/env python3

"""
ch13_ex4.py --- reference answer to exercise 13.4: an agent saves a form-built set

Contains:
    solve(): returns set 2 positions at the head and the paths the agent's save changed
"""

from datetime import UTC, datetime

from stage.choreo.form_place import filled_forms, place
from stage.choreo.forms import line_form
from stage.choreo.match_ops import with_set
from stage.examples import two_set_show
from stage.show.changes import apply_change
from stage.show.models import FormOp
from stage.show.repositories import InMemoryRepository
from stage.show.storage import load_show, open_workspace, save_show


def solve() -> tuple[list[list[float]], list[str]]:
    """Returns set 2 positions at the head and the paths the agent's save changed."""
    at = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
    repo = InMemoryRepository()
    save_show(open_workspace(repo, "demo", "designer"), two_set_show(), at, "create")
    agent = open_workspace(repo, "demo", "agent")
    show = load_show(agent)
    row = line_form("row", ["T1", "T2", "T3", "T4"], (0.0, 12.0), (6.0, 12.0))
    placed = place(show, "2", [row])
    show = apply_change(
        with_set(show, placed), [FormOp(form=f) for f in filled_forms(placed, [row])]
    )
    revision = save_show(agent, show, at, "line up set 2")
    positions: list[list[float]] = repo.get_show("demo").set("2").positions().tolist()
    return positions, sorted(revision.changed)
