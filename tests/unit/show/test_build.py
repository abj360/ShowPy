#!/usr/bin/env python3

"""
test_build.py --- tests building sets from workspace scripts, staleness and analysis scripts
"""

from datetime import UTC, datetime

import pytest

from stage.choreo.models import TransitionSpec
from stage.choreo.paths import delay
from stage.choreo.paths_store import load_paths, save_paths
from stage.core.errors import SchemaError, ValidationError
from stage.show.build import build_all, build_set, new_set_script, run_script, stale
from stage.show.repositories import InMemoryRepository
from stage.show.storage import Workspace, load_show, open_workspace
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
SHOW_ID = "show-1"
UPSTAGE = '''
def build(ctx):
    """Moves everyone 2 m upstage from the previous set."""
    moved = ctx.prev.with_placements({p.entity: {"y": p.y + 2.0} for p in ctx.prev.placements})
    return moved.model_copy(update={"id": ctx.set_id, "counts": ctx.counts})
'''


def _workspace() -> Workspace:
    repo = InMemoryRepository()
    repo.put_show(
        SHOW_ID, two_set_show(), expected_revision="", actor="designer", at=AT, message="create"
    )
    return open_workspace(repo, SHOW_ID, "agent")


def test_build_set_runs_script_and_stores_result() -> None:
    ws = _workspace()
    ws.write("/sets/2/set.py", UPSTAGE, at=AT)
    result = build_set(ws, "2", at=AT)
    assert result.set.get("T1").y == 7.0
    assert load_show(ws).set("2").source_hash == result.set.source_hash


def test_build_set_without_script_raises() -> None:
    with pytest.raises(SchemaError):
        build_set(_workspace(), "2", at=AT)


def test_build_set_rejects_invalid_result() -> None:
    ws = _workspace()
    dropping = (
        "def build(ctx):\n"
        "    return ctx.prev.model_copy(update={'id': ctx.set_id, 'placements': ()})\n"
    )
    ws.write("/sets/2/set.py", dropping, at=AT)
    with pytest.raises(ValidationError):
        build_set(ws, "2", at=AT)


def test_stale_tracks_script_hashes() -> None:
    ws = _workspace()
    assert stale(ws) == []
    ws.write("/sets/2/set.py", UPSTAGE, at=AT)
    assert stale(ws) == ["2"]
    build_set(ws, "2", at=AT)
    assert stale(ws) == []


def test_new_set_script_templates_build() -> None:
    ws = _workspace()
    for template in ("copy_prev", "hold", "blank"):
        path = new_set_script(ws, "2", template=template, at=AT)
        assert path == "/sets/2/set.py"
        built = build_set(ws, "2", at=AT)
        assert built.set.get("T1").y == 5.0
    with pytest.raises(SchemaError):
        new_set_script(ws, "2", template="nope", at=AT)  # type: ignore[arg-type]


def test_build_all_builds_every_scripted_set_in_order() -> None:
    ws = _workspace()
    ws.write("/sets/2/set.py", UPSTAGE, at=AT)
    results = build_all(ws, at=AT)
    assert [r.set_id for r in results] == ["2"]


def test_run_script_runs_analysis_from_drafts() -> None:
    ws = _workspace()
    ws.write("/lib/drafts/count.py", "def main(ctx):\n    return len(ctx.show.sets)\n", at=AT)
    assert run_script(ws, "/lib/drafts/count.py").result == 2


def test_save_and_load_paths_round_trip() -> None:
    ws = _workspace()
    spec = delay(TransitionSpec(id=("1", "2"), counts=8.0), ["T1"], 2.0)
    save_paths(ws, spec, at=AT)
    assert load_paths(ws, ("1", "2")) == spec
