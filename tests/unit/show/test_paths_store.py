#!/usr/bin/env python3

"""
test_paths_store.py --- tests saving and loading a transition's paths through the workspace
"""

from datetime import UTC, datetime

from showpy.choreo.models import TransitionSpec
from showpy.choreo.paths import delay
from showpy.examples import two_set_show
from showpy.show.paths_store import load_paths, save_paths
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import open_workspace

AT = datetime(2026, 11, 11, tzinfo=UTC)
SHOW_ID = "show-1"


def test_save_and_load_paths_round_trip() -> None:
    repo = InMemoryRepository()
    repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    ws = open_workspace(repo, SHOW_ID)
    spec = delay(TransitionSpec(id=("1", "2"), counts=8.0), ["T1"], 2.0)
    save_paths(ws, spec, at=AT)
    assert load_paths(ws, ("1", "2")) == spec


def test_load_paths_defaults_to_straight_moves() -> None:
    repo = InMemoryRepository()
    repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    spec = load_paths(open_workspace(repo, SHOW_ID), ("1", "2"))
    assert spec.rows == {}
    assert spec.counts == 8.0
