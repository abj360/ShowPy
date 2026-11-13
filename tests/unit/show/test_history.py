#!/usr/bin/env python3

"""
test_history.py --- tests set, path and show diffs, snapshots, revert, undo/redo, renames, changelog

Contains:
    test_regression_undo_does_not_leave_a_path_row_for_a_removed_spot: UP-9.7-36
    test_regression_undoing_the_first_save_keeps_a_loadable_show: UP-9.7-37
    test_regression_diff_show_reports_a_new_group: UP-9.7-38
    test_regression_diff_show_reports_a_pack_change: UP-9.7-39
    test_regression_diff_show_reports_a_set_cue_change: UP-9.7-40
    test_regression_diff_show_reports_a_set_label_change: UP-9.7-41
    test_regression_diff_show_reports_a_placement_note_change: UP-9.7-42
    test_regression_diff_show_reports_a_selection_change: UP-9.7-43
    test_regression_rename_entities_rewrites_expression_selectors: UP-9.7-44
    test_regression_unpin_normalizes_transition_ids_like_pin: UP-9.7-45
"""

from datetime import UTC, datetime, timedelta

import pytest

from showpy.cast.models import Group
from showpy.cast.spots import add_spot
from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    Form,
    PathRow,
    Route,
    TransitionSpec,
)
from showpy.core.errors import (
    ConflictError,
    SchemaError,
    ShowPyError,
    StorageError,
    UnknownRefError,
)
from showpy.notes.models import Anchor, Note
from showpy.show.history import (
    changelog,
    diff_paths,
    diff_sets,
    diff_show,
    redo,
    rename_entities,
    revert,
    snapshot,
    undo,
)
from showpy.show.manage import add_set
from showpy.show.models import Pin, Show
from showpy.show.pinning import pin, unpin
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import load_show, open_workspace, save_show
from showpy.show.validate import validate_show
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


def _moved(show: Show, entity: str, x: float) -> Show:
    edited = show.sets[1].with_placements({entity: {"x": x}}, force=True)
    return show.model_copy(update={"sets": (show.sets[0], edited, *show.sets[2:])})


def test_diff_sets_reports_moves_turns_adds_removes_and_forms() -> None:
    first, second = two_set_show().sets
    turned = second.with_placements({"T2": {"facing": 90.0}, "T3": {"form": "f", "order": 0}})
    trimmed = turned.model_copy(update={"placements": turned.placements[1:], "forms": ("f",)})
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
    nudged = second.with_placements({"T1": {"x": 1e-9, "facing": 360.0}})
    assert diff_sets(second, nudged).is_empty
    assert diff_sets(second, nudged, tol=1e-12).moved == ("T1",)


def test_diff_sets_counts_entrances_as_moves() -> None:
    _, second = two_set_show().sets
    assert diff_sets(second, second.with_placements({"T4": {"on_field": False}})).moved == ("T4",)


def test_diff_paths_treats_default_rows_as_missing() -> None:
    plain = TransitionSpec(id=("1", "2"), counts=8.0)
    explicit = plain.model_copy(update={"rows": {"T1": PathRow(entity="T1")}})
    route = Route(id="r", curve={}, members=("T1",), offsets=(0.0,), travel=2.0)
    routed = plain.model_copy(update={"routes": {"r": route}})
    assert diff_paths(plain, explicit).is_empty
    assert diff_paths(plain, routed).routes_changed == ("r",)


def test_diff_show_summarizes_structure_and_paths() -> None:
    a = two_set_show()
    form = Form(id="f", set="2", kind="line", geometry={}, members=("T1",))
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": PathRow(entity="T1", start=1)})
    b = add_set(_moved(a, "T1", 5.0), "3", 4.0).model_copy(
        update={"forms": {"f": form}, "transitions": (spec,), "pins": (Pin(target="set", id="1"),)}
    )
    diff = diff_show(a, b)
    assert diff["sets"]["added"] == ["3"]
    assert diff["sets"]["changed"] == {"2": {"moved": ("T1",)}}
    assert diff["forms"] == {"added": ["f"]}
    assert diff["paths"] == {"1→2": {"changed_rows": ("T1",)}}
    assert diff["pins"] == {"added": ["set:1"]}
    assert "actions" not in diff
    assert diff_show(a, a) == {}


def test_snapshot_records_empty_revision() -> None:
    repo, _, designer = open_session(two_set_show(), AT)
    rev = snapshot(designer, "before finale", AT + STEP)
    assert (rev.kind, rev.changed, rev.message) == ("snapshot", (), "before finale")
    assert len(repo.revisions("s")) == 2


def test_revert_restores_earlier_documents() -> None:
    _, _, designer = open_session(two_set_show(), AT)
    first = designer.revision
    save_show(designer, _moved(load_show(designer), "T1", 7.0), AT + STEP, "move")
    rev = revert(designer, first, AT + 2 * STEP)
    assert rev.kind == "revert"
    assert rev.target == first
    assert load_show(designer).set("2").get("T1").x == 0.0
    with pytest.raises(UnknownRefError):
        revert(designer, "nope", AT)


def test_agent_revert_leaves_designer_files() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    first = designer.revision
    designer.write("/config/limits.yaml", "max: 1\n", at=AT + STEP)
    agent.refresh()
    agent.write("/sets/1/set.py", "pass\n", at=AT + 2 * STEP)
    revert(agent, first, AT + 3 * STEP)
    assert agent.read("/config/limits.yaml") == "max: 1\n"
    assert "set.py" not in agent.ls("/sets/1")


def test_undo_redo_are_per_principal_and_derived_from_history() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    agent.write("/sets/1/set.py", "a = 1\n", at=AT + STEP)
    designer.refresh()
    designer.write("/sets/2/set.py", "b = 2\n", at=AT + 2 * STEP)
    agent.refresh()
    undone = undo(agent, AT + 3 * STEP)
    assert (undone.kind, undone.changed) == ("undo", ("/sets/1/set.py",))
    assert agent.ls("/sets/1") == ["forms.json", "placements.csv"]
    assert agent.read("/sets/2/set.py") == "b = 2\n"
    with pytest.raises(StorageError):
        undo(agent, AT + 4 * STEP)
    redo(agent, AT + 4 * STEP)
    assert agent.read("/sets/1/set.py") == "a = 1\n"
    with pytest.raises(StorageError):
        redo(agent, AT + 5 * STEP)
    fresh = open_workspace(agent.repo, "s", "agent")
    undo(fresh, AT + 5 * STEP)
    assert fresh.ls("/sets/1") == ["forms.json", "placements.csv"]


def test_new_edit_clears_redo_and_conflicting_undo_raises() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    agent.write("/sets/1/set.py", "a = 1\n", at=AT + STEP)
    undo(agent, AT + 2 * STEP)
    agent.write("/sets/2/set.py", "c = 3\n", at=AT + 3 * STEP)
    with pytest.raises(StorageError):
        redo(agent, AT + 4 * STEP)
    designer.refresh()
    designer.write("/sets/2/set.py", "d = 4\n", at=AT + 4 * STEP)
    with pytest.raises(ConflictError):
        undo(agent, AT + 5 * STEP)


def test_agent_undo_refuses_after_designer_pinned_the_same_set() -> None:
    _, agent, designer = open_session(two_set_show(), AT)
    save_show(agent, _moved(load_show(agent), "T1", 3.0), AT + STEP, "agent move")
    designer.refresh()
    current = load_show(designer)
    pinned = current.sets[1].with_placements({"T1": {"pinned": True}})
    save_show(designer, current.model_copy(update={"sets": (current.sets[0], pinned)}), AT, "pin")
    with pytest.raises(ConflictError):
        undo(agent, AT + 3 * STEP)


def test_rename_entities_rewrites_everything() -> None:
    show = two_set_show()
    form = Form(id="f", set="2", kind="line", geometry={}, members=("T1", "T2"))
    row = PathRow(entity="T1", facing_mode=FacingMode.TOWARD_ENTITY, facing_value="T2")
    route = Route(id="r", curve={}, members=("T2",), offsets=(0.0,), travel=1.0)
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": row}, routes={"r": route})
    note = Note(id="n", text="x", anchor=Anchor.on_spots(["T1"]), author="d", created_at=AT)
    override = FacingOverride(entities=("T1",), mode=FacingMode.FIXED, value=90.0, start=0, end=4)
    cast = show.cast.model_copy(update={"selections": {"lead": frozenset({"T1"})}})
    show = show.model_copy(
        update={
            "forms": {"f": form},
            "transitions": (spec,),
            "notes": (note,),
            "facing_overrides": (override,),
            "pins": (Pin(target="entity", id="T1"),),
            "cast": cast,
        }
    )
    renamed = rename_entities(show, {"T1": "S1", "T2": "S2"})
    assert renamed.cast.ids()[:2] == ("S1", "S2")
    assert renamed.cast.selections == {"lead": frozenset({"S1"})}
    assert renamed.sets[0].entity_ids()[:2] == ("S1", "S2")
    assert renamed.forms["f"].members == ("S1", "S2")
    new_spec = renamed.transitions[0]
    assert list(new_spec.rows) == ["S1"]
    assert new_spec.rows["S1"].facing_value == "S2"
    assert new_spec.routes["r"].members == ("S2",)
    assert renamed.notes[0].anchor.spots == ("S1",)
    assert renamed.facing_overrides[0].entities == ("S1",)
    assert renamed.pins[0].id == "S1"


def test_rename_entities_swaps_and_collisions() -> None:
    swapped = rename_entities(two_set_show(), {"T1": "T2", "T2": "T1"})
    assert swapped.sets[0].get("T2").x == 0.0
    with pytest.raises(SchemaError):
        rename_entities(two_set_show(), {"T1": "T2"})
    with pytest.raises(SchemaError):
        rename_entities(two_set_show(), {"T1": "X", "T2": "X"})
    with pytest.raises(UnknownRefError):
        rename_entities(two_set_show(), {"Z": "Y"})


def test_changelog_lines_since_revision() -> None:
    _, agent, _ = open_session(two_set_show(), AT)
    first = agent.revision
    agent.write("/sets/1/set.py", "a = 1\n", at=AT + STEP, message="script 1")
    lines = changelog(agent, first).splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("2026-11-11 12:01 agent: script 1 (1 path)")
    assert len(changelog(agent).splitlines()) == 2
    assert changelog(agent, agent.revision) == ""


def _with_set_two(show: Show, **fields: object) -> Show:
    edited = show.sets[1].model_copy(update=fields)
    return show.model_copy(update={"sets": (show.sets[0], edited, *show.sets[2:])})


@pytest.mark.defect("UP-9.7-36")
@pytest.mark.xfail(strict=True, reason="UP-9.7-36")
def test_regression_undo_does_not_leave_a_path_row_for_a_removed_spot() -> None:
    """Refuses or repairs an undo that would orphan the agent's path row (audit 9.7 history)."""
    _, agent, designer = open_session(two_set_show(), AT)
    save_show(
        designer, add_spot(load_show(designer), (0.0, 5.0), "trumpets", "1"), AT + STEP, "add"
    )
    new_spot = load_show(designer).cast.ids()[-1]
    agent.refresh()
    row = TransitionSpec(
        id=("1", "2"), counts=8.0, rows={new_spot: PathRow(entity=new_spot, start=1)}
    )
    save_show(agent, load_show(agent).model_copy(update={"transitions": (row,)}), AT + 2 * STEP)
    try:
        undo(designer, AT + 3 * STEP)
    except ShowPyError:
        return
    assert validate_show(load_show(designer)).ok


@pytest.mark.defect("UP-9.7-37")
@pytest.mark.xfail(strict=True, reason="UP-9.7-37")
def test_regression_undoing_the_first_save_keeps_a_loadable_show() -> None:
    """Refuses to undo the first save, or leaves a loadable show (audit 9.7 history)."""
    ws = open_workspace(InMemoryRepository(), "s", "designer")
    save_show(ws, two_set_show(), AT)
    try:
        undo(ws, AT + STEP)
    except ShowPyError:
        return
    assert load_show(ws).sets


@pytest.mark.defect("UP-9.7-38")
@pytest.mark.xfail(strict=True, reason="UP-9.7-38")
def test_regression_diff_show_reports_a_new_group() -> None:
    """Reports a group added to the cast (audit 9.7 history)."""
    base = two_set_show()
    groups = {**base.cast.groups, "extra": Group(key="extra", label_prefix="E")}
    edited = base.model_copy(update={"cast": base.cast.model_copy(update={"groups": groups})})
    assert diff_show(base, edited)


@pytest.mark.defect("UP-9.7-39")
@pytest.mark.xfail(strict=True, reason="UP-9.7-39")
def test_regression_diff_show_reports_a_pack_change() -> None:
    """Reports a changed pack key (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, base.model_copy(update={"pack": "marching"}))


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
    noted = base.sets[1].with_placements({"T1": {"note": "hold"}}, force=True)
    assert diff_show(base, _with_set_two(base, placements=noted.placements))


@pytest.mark.defect("UP-9.7-43")
@pytest.mark.xfail(strict=True, reason="UP-9.7-43")
def test_regression_diff_show_reports_a_selection_change() -> None:
    """Reports a changed named selection (audit 9.7 history)."""
    base = two_set_show()
    selections = {"front": frozenset({"T1", "T2"})}
    edited = base.model_copy(
        update={"cast": base.cast.model_copy(update={"selections": selections})}
    )
    assert diff_show(base, edited)


@pytest.mark.defect("UP-9.7-44")
@pytest.mark.xfail(strict=True, reason="UP-9.7-44")
def test_regression_rename_entities_rewrites_expression_selectors() -> None:
    """Rewrites spot IDs inside expression selectors, as cast.rewrite does (audit 9.7 history)."""
    base = two_set_show()
    override = FacingOverride(entities="T1 + T2", mode=FacingMode.TO_TARGET, start=1.0, end=2.0)
    renamed = rename_entities(
        base.model_copy(update={"facing_overrides": (override,)}), {"T1": "S1"}
    )
    entities = renamed.facing_overrides[0].entities
    assert isinstance(entities, str)
    assert "S1" in entities
    assert "T1" not in entities


@pytest.mark.defect("UP-9.7-45")
@pytest.mark.xfail(strict=True, reason="UP-9.7-45")
def test_regression_unpin_normalizes_transition_ids_like_pin() -> None:
    """Accepts `1->2` in unpin as pin does (audit 9.7 history)."""
    pinned = pin(two_set_show(), "transition", "1->2", "hold")
    assert unpin(pinned, "transition", "1->2").pins == ()
