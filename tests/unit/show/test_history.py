#!/usr/bin/env python3

"""
test_history.py --- tests set, path and show diffs, revert, renames and the changelog

Contains:
    test_revert_restores_an_earlier_show: reverts to the first of two edits as a new revision
    test_regression_undo_does_not_leave_a_path_row_for_a_removed_spot: refuses or repairs a host
        undo (a revert to the parent) that would orphan the agent's path row
    test_regression_undoing_the_first_save_keeps_a_loadable_show: refuses to revert to before the
        first save, or leaves a loadable show
    test_regression_diff_show_reports_a_new_group: reports a group added to the cast
    test_regression_diff_show_reports_a_pack_change: reports a changed pack key
    test_regression_diff_show_reports_a_set_cue_change: reports a changed set cue
    test_regression_diff_show_reports_a_set_label_change: reports a changed set label
    test_regression_diff_show_reports_a_placement_note_change: reports a changed placement note
    test_regression_diff_show_reports_a_selection_change: reports a changed named selection
    test_regression_unpin_normalizes_transition_ids_like_pin: accepts `1->2` in unpin as pin does
"""

from datetime import UTC, datetime, timedelta

import pytest

from e8s.cast.models import Group
from e8s.cast.spots import add_spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import (
    PathRow,
    Route,
    TransitionSpec,
)
from e8s.core.errors import E8sError, UnknownRefError
from e8s.core.ids import FormId, RouteId, SetId, SpotId, make_tid
from e8s.geo.curves import Segment
from e8s.show.history import (
    changelog,
    diff_paths,
    diff_sets,
    diff_show,
    revert,
)
from e8s.show.manage import add_set
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, unpin
from e8s.show.repositories import InMemoryRepository
from e8s.show.storage import load_show, open_workspace, save_show
from e8s.show.validate import validate_show
from tests.fixtures.recipes import LINE_RECIPE
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


def _moved(show: Show, entity: str, x: float) -> Show:
    edited = show.sets[1].with_placements({entity: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(show.sets[0], edited, *show.sets[2:]))


def test_diff_sets_reports_moves_turns_adds_removes_and_forms() -> None:
    first, second = two_set_show().sets
    turned = second.with_placements(
        {"T2": PlacementPatch(facing=90.0), "T3": PlacementPatch(form=FormId("f"), order=0)}
    )
    trimmed = turned.replace(placements=turned.placements[1:], forms=("f",))
    diff = diff_sets(second, trimmed)
    assert diff.moved == ()
    assert diff.facing_changed == ("T2",)
    assert diff.removed == ("T1",)
    assert diff.forms_changed == ("f",)
    assert diff_sets(trimmed, second).added == ("T1",)
    assert diff_sets(first, second).moved == ("T1", "T2", "T3", "T4")
    assert diff_sets(second, second).is_empty


def test_diff_sets_tolerance_and_angle_wrap() -> None:
    _, second = two_set_show().sets
    nudged = second.with_placements({"T1": PlacementPatch(x=1e-9, facing=360.0)})
    assert diff_sets(second, nudged).is_empty
    assert diff_sets(second, nudged, tol=1e-12).moved == ("T1",)


def test_diff_sets_counts_entrances_as_moves() -> None:
    _, second = two_set_show().sets
    assert diff_sets(
        second, second.with_placements({"T4": PlacementPatch(on_field=False)})
    ).moved == ("T4",)


def test_diff_paths_treats_default_rows_as_missing() -> None:
    plain = TransitionSpec(id=make_tid("1", "2"), counts=8.0)
    explicit = plain.replace(rows={"T1": PathRow(entity=SpotId("T1"))})
    route = Route(
        id=RouteId("r"),
        curve=Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
        members=(SpotId("T1"),),
        offsets=(0.0,),
        travel=2.0,
    )
    routed = plain.replace(routes={"r": route})
    assert diff_paths(plain, explicit).is_empty
    assert diff_paths(plain, routed).routes_changed == ("r",)


def test_diff_show_summarizes_structure_and_paths() -> None:
    a = two_set_show()
    form = Form(id=FormId("f"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=1)},
    )
    b = add_set(_moved(a, "T1", 5.0), "3", 4.0).replace(
        forms={"f": form}, transitions=(spec,), pins=(Pin(target="set", id="1"),)
    )
    diff = diff_show(a, b)
    assert diff["sets"]["added"] == ["3"]
    assert diff["sets"]["changed"] == {"2": {"moved": ("T1",)}}
    assert diff["forms"] == {"added": ["f"]}
    assert diff["paths"] == {"1→2": {"changed_rows": ("T1",)}}
    assert diff["pins"] == {"added": ["set:1"]}
    assert "actions" not in diff
    assert diff_show(a, a) == {}


def test_revert_restores_earlier_documents() -> None:
    _, _, designer = open_session(two_set_show(), AT)
    first = designer.revision
    moved = save_show(designer, _moved(load_show(designer), "T1", 7.0), AT + STEP)
    rev = revert(designer, first, AT + 2 * STEP)
    assert rev.parent == moved.id
    assert load_show(designer).set("2").get("T1").x == 0.0
    with pytest.raises(UnknownRefError):
        revert(designer, "nope", AT)


def test_revert_restores_an_earlier_show() -> None:
    _, _, ws = open_session(two_set_show(), AT)
    first = save_show(ws, _moved(load_show(ws), "T1", 3.0), AT + STEP)
    first_show = load_show(ws)
    second = save_show(ws, _moved(first_show, "T2", 6.0), AT + 2 * STEP)
    restored = revert(ws, first.id, AT + 3 * STEP)
    assert restored.parent == second.id
    assert restored.id not in (first.id, second.id)
    assert restored.changed == ("/sets/2/set.json",)
    assert set(restored.to_json()) == {"id", "parent", "at", "changed"}
    assert load_show(ws).replace(revision="") == first_show.replace(revision="")


def test_revert_restores_every_file() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    first = designer.revision
    original = designer.read("/config/limits.yaml")
    designer.write("/config/limits.yaml", "max: 1\n", at=AT + STEP)
    agent.refresh()
    agent.write("/sets/1/set.py", "pass\n", at=AT + 2 * STEP)
    revert(agent, first, AT + 3 * STEP)
    assert agent.read("/config/limits.yaml") == original
    assert "set.py" not in agent.ls("/sets/1")


def test_changelog_lines_since_revision() -> None:
    _, agent, _ = open_session(two_set_show(), AT)
    first = agent.revision
    rev = agent.write("/sets/1/set.py", "a = 1\n", at=AT + STEP)
    lines = changelog(agent, first).splitlines()
    assert lines == [f"{rev.id[:7]} 2026-11-11 12:01 (/sets/1/set.py)"]
    assert len(changelog(agent).splitlines()) == 2
    assert changelog(agent, agent.revision) == ""


def _with_set_two(show: Show, **fields: object) -> Show:
    edited = show.sets[1].replace(**fields)
    return show.replace(sets=(show.sets[0], edited, *show.sets[2:]))


def test_regression_undo_does_not_leave_a_path_row_for_a_removed_spot() -> None:
    """Refuses or repairs an undo that would orphan the agent's path row (audit 9.7 history)."""
    _, agent, designer = open_session(two_set_show(), AT)
    added = save_show(
        designer, add_spot(load_show(designer), (0.0, 5.0), "trumpets", "1"), AT + STEP
    )
    new_spot = load_show(designer).cast.ids()[-1]
    agent.refresh()
    row = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows={new_spot: PathRow(entity=new_spot, start=1)}
    )
    save_show(agent, load_show(agent).replace(transitions=(row,)), AT + 2 * STEP)
    try:
        revert(designer, added.parent, AT + 3 * STEP)
    except E8sError:
        return
    assert validate_show(load_show(designer)).ok


def test_regression_undoing_the_first_save_keeps_a_loadable_show() -> None:
    """Refuses to undo the first save, or leaves a loadable show (audit 9.7 history).

    A third legitimate fix, an explicit empty-root state that load_show names, would need this
    assertion relaxed.
    """
    ws = open_workspace(InMemoryRepository(), "s")
    first = save_show(ws, two_set_show(), AT)
    try:
        revert(ws, first.parent, AT + STEP)
    except E8sError:
        return
    assert load_show(ws).sets


@pytest.mark.defect("UP-9.7-38")
@pytest.mark.xfail(strict=True, reason="UP-9.7-38")
def test_regression_diff_show_reports_a_new_group() -> None:
    """Reports a group added to the cast (audit 9.7 history)."""
    base = two_set_show()
    groups = {**base.cast.groups, "extra": Group(key="extra", label_prefix="E")}
    edited = base.replace(cast=base.cast.replace(groups=groups))
    assert diff_show(base, edited)


@pytest.mark.defect("UP-9.7-39")
@pytest.mark.xfail(strict=True, reason="UP-9.7-39")
def test_regression_diff_show_reports_a_pack_change() -> None:
    """Reports a changed pack key (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, base.replace(pack="marching"))


@pytest.mark.defect("UP-9.7-40")
@pytest.mark.xfail(strict=True, reason="UP-9.7-40")
def test_regression_diff_show_reports_a_set_cue_change() -> None:
    """Reports a changed set cue (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, _with_set_two(base, cue="hit"))


@pytest.mark.defect("UP-9.7-41")
@pytest.mark.xfail(strict=True, reason="UP-9.7-41")
def test_regression_diff_show_reports_a_set_label_change() -> None:
    """Reports a changed set label (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, _with_set_two(base, label="Pose"))


@pytest.mark.defect("UP-9.7-42")
@pytest.mark.xfail(strict=True, reason="UP-9.7-42")
def test_regression_diff_show_reports_a_placement_note_change() -> None:
    """Reports a changed placement note (audit 9.7 history)."""
    base = two_set_show()
    noted = base.sets[1].with_placements({"T1": PlacementPatch(note="hold")}, force=True)
    assert diff_show(base, _with_set_two(base, placements=noted.placements))


@pytest.mark.defect("UP-9.7-43")
@pytest.mark.xfail(strict=True, reason="UP-9.7-43")
def test_regression_diff_show_reports_a_selection_change() -> None:
    """Reports a changed named selection (audit 9.7 history)."""
    base = two_set_show()
    selections = {"front": frozenset({"T1", "T2"})}
    edited = base.replace(cast=base.cast.replace(selections=selections))
    assert diff_show(base, edited)


def test_regression_unpin_normalizes_transition_ids_like_pin() -> None:
    """Accepts `1->2` in unpin as pin does (audit 9.7 history)."""
    pinned = pin(two_set_show(), "transition", "1->2", "hold")
    assert unpin(pinned, "transition", "1->2").pins == ()
