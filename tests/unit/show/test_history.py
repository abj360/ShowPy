#!/usr/bin/env python3

"""
test_history.py --- tests set, path and show diffs, revert, renames and the changelog

Contains:
    test_revert_restores_an_earlier_show: reverts to the first of two edits as a new revision
    test_revert_reads_no_render_blobs: reads no blob of a file both revisions store unchanged
    test_regression_revert_hint_names_the_stored_path_of_a_derived_table: names the derived
        table a revision stores and says what is allowed
    test_revert_report_lists_restored_items: lists every restored, re-added and deleted path
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
    test_every_item_kind_has_an_edit: lists an edit for each item kind
    test_diff_show_reports_every_item_kind: reports one edit per item kind under its own key
    test_diff_show_names_labels_pack_and_sets_by_display_id: names label, cue, note and pack edits
    test_regression_unpin_normalizes_transition_ids_like_pin: accepts `1->2` in unpin as pin does
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from e8s.cast.models import Group, Spot
from e8s.cast.spots import add_spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import (
    PathRow,
    Route,
    TransitionSpec,
)
from e8s.core.errors import E8sError, LockedError, UnknownRefError
from e8s.core.ids import FormId, RouteId, SetId, SpotId, make_tid
from e8s.core.util import content_hash
from e8s.geo.curves import Segment
from e8s.show.change_models import ItemKind
from e8s.show.document_repository import DocumentRepository
from e8s.show.history import (
    changelog,
    diff_paths,
    diff_sets,
    diff_show,
    revert,
)
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.items import changes
from e8s.show.manage import add_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin, unpin
from e8s.show.show_io import load_show, save_show
from e8s.show.validate import validate_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.cast_shows import rich_show
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
    assert diff == {
        "sets": {"added": ["3"], "changed": {"2": {"moved": ["T1"]}}},
        "forms": {"added": ["f"]},
        "paths": {"1→2": {"changed_rows": ["T1"]}},
        "pins": {"added": ["set:1"]},
    }
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
    assert restored.changed == ("/sets/2/drill.json",)
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


def test_revert_reads_no_render_blobs() -> None:
    repo, _, ws = open_session(two_set_show(), AT)
    ws.write("/renders/set-1.png", b"png-bytes", at=AT + STEP)
    first = ws.revision
    save_show(ws, _moved(load_show(ws), "T1", 7.0), AT + 2 * STEP)
    render_digest = blob_digest(b"png-bytes")
    reads: list[str] = []
    real_blob = repo.backend.blob

    def counting_blob(show_id: str, digest: str) -> bytes:
        reads.append(digest)
        return real_blob(show_id, digest)

    repo.backend.blob = counting_blob  # type: ignore[method-assign]
    # The session above decoded every document, so only a fresh repository counts what revert reads.
    cold = open_workspace(DocumentRepository(repo.backend), ws.show_id)
    revert(cold, first, AT + 3 * STEP)
    assert reads
    assert render_digest not in reads


def _plant_derived_table(repo: InMemoryRepository, ws: Workspace, path: str) -> str:
    raw = b"T1,0.0,5.0\n"
    old_tree = dict(repo.backend.tree(ws.show_id, ws.revision))
    planted = Revision(
        id=content_hash({"planted": path}, domain="test"),
        parent=ws.revision,
        at=AT + STEP,
        changed=(path,),
    )
    repo.backend.commit(
        ws.show_id, planted, {**old_tree, path: blob_digest(raw)}, {blob_digest(raw): raw}
    )
    cleaned = Revision(
        id=content_hash({"cleaned": path}, domain="test"),
        parent=planted.id,
        at=AT + 2 * STEP,
        changed=(path,),
    )
    repo.backend.commit(ws.show_id, cleaned, old_tree, {})
    ws.refresh()
    return planted.id


def test_regression_revert_hint_names_the_stored_path_of_a_derived_table() -> None:
    """Names the derived table an old revision stores, which revert cannot restore."""
    path = "/sets/1/placements.csv"
    repo, _, ws = open_session(two_set_show(), AT)
    planted = _plant_derived_table(repo, ws, path)
    head = ws.revision
    with pytest.raises(LockedError) as raised:
        revert(ws, planted, AT + 3 * STEP)
    assert raised.value.code == "READ_ONLY"
    assert path in raised.value.hint
    assert planted[:7] in str(raised.value)
    assert raised.value.context["path"] == path
    assert "revert" in raised.value.hint
    assert repo.head(ws.show_id) == head


def test_revert_report_lists_restored_items() -> None:
    _, _, ws = open_session(two_set_show(), AT)
    ws.write("/renders/set-1.png", b"png", at=AT + STEP)
    first = ws.revision
    ws.commit({"/renders/set-1.png": None}, at=AT + 2 * STEP)
    ws.write("/config/limits.yaml", "max: 1\n", at=AT + 3 * STEP)
    save_show(ws, _moved(load_show(ws), "T1", 7.0), AT + 4 * STEP)
    report = revert(ws, first, AT + 5 * STEP)
    assert report.changed == (
        "/config/limits.yaml",
        "/renders/set-1.png",
        "/sets/2/drill.json",
    )
    assert report.parent != first


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


def test_regression_diff_show_reports_a_new_group() -> None:
    """Reports a group added to the cast (audit 9.7 history)."""
    base = two_set_show()
    groups = {**base.cast.groups, "extra": Group(key="extra", label_prefix="E")}
    edited = base.replace(cast=base.cast.replace(groups=groups))
    assert diff_show(base, edited)


def test_regression_diff_show_reports_a_pack_change() -> None:
    """Reports a changed pack key (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, base.replace(pack="marching"))


def test_regression_diff_show_reports_a_set_cue_change() -> None:
    """Reports a changed set cue (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, _with_set_two(base, cue="hit"))


def test_regression_diff_show_reports_a_set_label_change() -> None:
    """Reports a changed set label (audit 9.7 history)."""
    base = two_set_show()
    assert diff_show(base, _with_set_two(base, label="Pose"))


def test_regression_diff_show_reports_a_placement_note_change() -> None:
    """Reports a changed placement note (audit 9.7 history)."""
    base = two_set_show()
    noted = base.sets[1].with_placements({"T1": PlacementPatch(note="hold")}, force=True)
    assert diff_show(base, _with_set_two(base, placements=noted.placements))


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


def _rich() -> Show:
    return pin(rich_show(), "entity", "T1", reason="soloist")


def _with_spec(show: Show, **fields: object) -> Show:
    return show.replace(transitions=(show.transitions[0].replace(**fields),))


def _row_edit(show: Show) -> Show:
    rows = show.transitions[0].rows
    return _with_spec(show, rows={**rows, SpotId("T1"): rows[SpotId("T1")].replace(start=2.0)})


def _route_edit(show: Show) -> Show:
    route = show.transitions[0].routes[RouteId("r1")]
    return _with_spec(show, routes={"r1": route.replace(travel=3.0)})


def _spot_edit(show: Show) -> Show:
    spots = show.cast.spots
    return show.replace(
        cast=show.cast.replace(spots=(*spots, Spot(id=SpotId("T9"), group="trumpets")))
    )


def _two_pins(show: Show) -> Show:
    return pin(show, "entity", "T2", reason="second")


def _swap_pins(show: Show) -> Show:
    both = _two_pins(show)
    return both.replace(pins=both.pins[::-1])


def _edit_cast(**fields: object) -> Callable[[Show], Show]:
    return lambda show: show.replace(cast=show.cast.replace(**fields))


KIND_EDITS: dict[str, tuple[Callable[[Show], Show], Callable[[Show], Show], tuple[str, ...]]] = {
    "placement": (lambda s: s, lambda s: _moved(s, "T1", 5.0), ("sets", "changed", "2", "moved")),
    "set": (lambda s: s, lambda s: _with_set_two(s, counts=9.0), ("sets", "counts", "2")),
    "set_order": (lambda s: s, lambda s: s.replace(sets=s.sets[::-1]), ("sets", "order_changed")),
    "spot_order": (
        lambda s: s,
        lambda s: _edit_cast(spots=s.cast.spots[::-1])(s),
        ("cast", "order_changed"),
    ),
    "form": (
        lambda s: s,
        lambda s: s.replace(forms={"line1": s.forms[FormId("line1")].replace(locked=True)}),
        ("forms", "changed"),
    ),
    "path_row": (lambda s: s, _row_edit, ("paths", "1→2", "changed_rows")),
    "transition": (lambda s: s, lambda s: _with_spec(s, notes="slow"), ("paths", "1→2", "notes")),
    "route": (lambda s: s, _route_edit, ("paths", "1→2", "routes_changed")),
    "spot": (lambda s: s, _spot_edit, ("cast", "added")),
    "group": (
        lambda s: s,
        lambda s: _edit_cast(groups={**s.cast.groups, "x": Group(key="x", label_prefix="X")})(s),
        ("groups", "added"),
    ),
    "selection": (
        lambda s: s,
        lambda s: _edit_cast(selections={"pair": frozenset({"T1"})})(s),
        ("selections", "changed"),
    ),
    "pin": (
        lambda s: s,
        lambda s: s.replace(pins=(s.pins[0].replace(reason="other"),)),
        ("pins", "changed"),
    ),
    "pin_order": (_two_pins, _swap_pins, ("pins", "order_changed")),
    "facing_override": (
        lambda s: s,
        lambda s: s.replace(facing_overrides=()),
        ("facing_overrides",),
    ),
    "sheet": (lambda s: s, lambda s: s.replace(sheet=s.sheet.replace(name="Other")), ("sheet",)),
    "timeline": (
        lambda s: s,
        lambda s: s.replace(timeline=s.timeline.replace(total_counts=99.0)),
        ("timeline",),
    ),
    "show": (lambda s: s, lambda s: s.replace(title="B"), ("title",)),
}


def test_every_item_kind_has_an_edit() -> None:
    assert set(KIND_EDITS) == set(get_args(ItemKind.__value__))


@pytest.mark.parametrize("kind", sorted(KIND_EDITS))
def test_diff_show_reports_every_item_kind(kind: str) -> None:
    prepare, edit, expected = KIND_EDITS[kind]
    before = prepare(_rich())
    after = edit(before)
    assert {c.key.kind for c in changes(before, after).changes} >= {kind}
    reached: object = diff_show(before, after)
    for name in expected:
        assert isinstance(reached, dict), expected
        assert name in reached, expected
        reached = reached[name]
    assert reached


def test_regression_diff_show_reports_a_sub_tolerance_move() -> None:
    """Reports a drill change the set differ's tolerance hides, so no item change vanishes."""
    base = two_set_show()
    nudged = _moved(base, "T1", base.sets[1].get("T1").x + 1e-9)
    assert diff_sets(base.sets[1], nudged.sets[1]).is_empty
    assert diff_show(base, nudged) == {"sets": {"changed": {"2": {"items": ["T1:drill"]}}}}


def test_diff_show_reports_a_transition_the_path_differ_calls_equal() -> None:
    base = rich_show()
    rows = base.transitions[0].rows
    spec = base.transitions[0].replace(rows={**rows, SpotId("T2"): PathRow(entity=SpotId("T2"))})
    edited = base.replace(transitions=(spec,))
    assert diff_show(base, edited) == {"paths": {"1→2": {"items": ["T2:path_row"]}}}


def test_diff_show_names_labels_pack_and_sets_by_display_id() -> None:
    base = two_set_show()
    noted = base.sets[1].with_placements({"T1": PlacementPatch(note="hold")}, force=True)
    edited = _with_set_two(base, label="Pose", cue="hit", placements=noted.placements)
    assert diff_show(base, edited) == {
        "labels": {
            "sets": {"2": {"label": [None, "Pose"], "cue": [None, "hit"]}},
            "notes": {"2": {"T1": [None, "hold"]}},
        }
    }
    assert diff_show(base, base.replace(pack="marching")) == {"pack": ["", "marching"]}
    assert diff_show(base, _with_set_two(base, counts=9.0))["sets"] == {"counts": {"2": [8.0, 9.0]}}
