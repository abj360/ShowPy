#!/usr/bin/env python3

"""
test_manage.py --- tests show creation, set insertion, removal, retiming, renumbering, indexing
"""

import sys
from datetime import UTC, datetime

import pytest

from showpy.cast.models import Cast, Group, Spot
from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    Form,
    PathRow,
    Placement,
    Route,
    TransitionSpec,
    Waypoint,
)
from showpy.core.errors import (
    ConflictError,
    LockedError,
    PathError,
    SchemaError,
    ShowPyError,
    TimeRangeError,
    UnknownRefError,
    VersionError,
)
from showpy.core.util import approx_equal
from showpy.notes.models import Anchor, Note
from showpy.show.documents import decode_show, encode_show
from showpy.show.manage import (
    add_set,
    index,
    insert_subset,
    new_show,
    remove_set,
    renumber,
    set_at_count,
    set_counts,
    transition_at_count,
    transitions,
    upgrade_dependency,
)
from showpy.show.models import Pin, Show
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import load_show, open_workspace, save_show
from showpy.show.validate import validate_show
from showpy.time.models import Hold, MeasureSetting
from tests.fixtures.shows import field_sheet, two_set_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
TOL = 1e-9


def _three_set_show() -> Show:
    return add_set(two_set_show(), "3", 4.0)


def _with_paths(show: Show, spec: TransitionSpec) -> Show:
    return show.model_copy(update={"transitions": (spec,)})


def test_new_show_pins_version_and_places_every_spot_off_field() -> None:
    cast = two_set_show().cast
    show = new_show("s1", "Opener", field_sheet(), two_set_show().timeline, cast=cast, pack="x")
    assert show.dependencies == {"sheet:test-field": "1"}
    assert show.set_ids() == ["1"]
    first = show.sets[0]
    assert first.counts == 0.0
    assert first.entity_ids() == ("T1", "T2", "T3", "T4")
    assert all(not p.on_field and p.point == (0.0, 0.0) for p in first.placements)
    assert show.pack == "x"


def test_new_show_rejects_unversioned_sheet() -> None:
    sheet = field_sheet().model_copy(update={"ref": "sheet:test-field"})
    with pytest.raises(VersionError):
        new_show("s1", "Opener", sheet, two_set_show().timeline)


def test_add_set_copies_previous_positions_without_forms() -> None:
    show = two_set_show()
    form_set = show.sets[1].with_placements({"T1": {"form": "f", "order": 0}})
    show = show.model_copy(update={"sets": (show.sets[0], form_set)})
    added = add_set(show, "3", 4.0, placements=[Placement(entity="T2", x=-3.0, y=1.0)])
    third = added.set("3")
    assert third.get("T1").point == (0.0, 9.0)
    assert third.get("T1").form is None
    assert third.get("T2").point == (-3.0, 1.0)


def test_add_set_after_drops_paths_between_neighbours() -> None:
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": PathRow(entity="T1", start=1)})
    show = add_set(_with_paths(two_set_show(), spec), "1B", 2.0, after="1")
    assert show.set_ids() == ["1", "1B", "2"]
    assert show.transitions == ()


def test_add_set_refuses_to_drop_locked_paths() -> None:
    row = PathRow(entity="T1", locked=True)
    show = _with_paths(two_set_show(), TransitionSpec(id=("1", "2"), counts=8, rows={"T1": row}))
    with pytest.raises(LockedError):
        add_set(show, "1B", 2.0, after="1")
    assert add_set(show, "1B", 2.0, after="1", force=True).transitions == ()


def test_add_set_validates_ids_counts_and_spots() -> None:
    show = two_set_show()
    with pytest.raises(SchemaError):
        add_set(show, "2", 4.0)
    with pytest.raises(SchemaError):
        add_set(show, "../x", 4.0)
    with pytest.raises(TimeRangeError):
        add_set(show, "3", 0.0)
    with pytest.raises(UnknownRefError):
        add_set(show, "3", 4.0, placements=[Placement(entity="Z9", x=0, y=0)])


def test_add_set_copies_entity_pin() -> None:
    show = two_set_show().model_copy(update={"pins": (Pin(target="entity", id="T1"),)})
    assert add_set(show, "3", 4.0).set("3").get("T1").pinned


def test_insert_subset_keeps_total_time_and_names_letters() -> None:
    show, first = insert_subset(two_set_show(), "1", 3.0)
    show, second = insert_subset(show, "1A", 2.0)
    assert (first, second) == ("1A", "1B")
    assert show.set_ids() == ["1", "1A", "1B", "2"]
    assert approx_equal(index(show).total_counts, 8.0, TOL)
    assert show.set("1A").get("T1").point == show.set("1").get("T1").point


def test_insert_subset_raises_when_counts_do_not_fit() -> None:
    with pytest.raises(TimeRangeError):
        insert_subset(two_set_show(), "1", 8.0)
    with pytest.raises(TimeRangeError):
        insert_subset(two_set_show(), "2", 1.0)


def test_insert_subset_respects_pinned_next_set() -> None:
    show = two_set_show().model_copy(update={"pins": (Pin(target="set", id="2"),)})
    with pytest.raises(LockedError):
        insert_subset(show, "1", 2.0)


def test_remove_set_merges_counts_and_drops_forms_and_paths() -> None:
    show = _three_set_show()
    form = Form(id="f", set="2", kind="line", geometry={}, members=("T1",))
    spec = TransitionSpec(id=("2", "3"), counts=4.0, rows={"T1": PathRow(entity="T1")})
    show = show.model_copy(update={"forms": {"f": form}, "transitions": (spec,)})
    removed = remove_set(show, "2")
    assert removed.set_ids() == ["1", "3"]
    assert removed.set("3").counts == 12.0
    assert removed.forms == {}
    assert removed.transitions == ()


def test_remove_set_without_merge_and_first_set() -> None:
    assert remove_set(_three_set_show(), "2", merge_counts=False).set("3").counts == 4.0
    assert remove_set(_three_set_show(), "1").sets[0].counts == 0.0


def test_remove_set_refuses_pinned_placements_unless_forced() -> None:
    show = two_set_show()
    pinned = show.sets[1].with_placements({"T1": {"pinned": True}})
    show = show.model_copy(update={"sets": (show.sets[0], pinned)})
    with pytest.raises(LockedError):
        remove_set(show, "2")
    assert remove_set(show, "2", force=True).set_ids() == ["1"]


def test_set_counts_updates_transition_counts() -> None:
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": PathRow(entity="T1", end=4.0)})
    show = set_counts(_with_paths(two_set_show(), spec), "2", 12.0)
    assert show.set("2").counts == 12.0
    assert show.transition(("1", "2")).counts == 12.0
    assert show.transition(("1", "2")).rows["T1"].end == 4.0


def test_set_counts_rescales_windows_waypoints_and_routes() -> None:
    row = PathRow(entity="T1", start=2.0, end=6.0, waypoints=(Waypoint(x=0, y=0, at=4.0),))
    route = Route(id="r", curve={}, members=("T2",), offsets=(0.0,), travel=1.0, end=8.0)
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": row}, routes={"r": route})
    scaled = set_counts(_with_paths(two_set_show(), spec), "2", 4.0, rescale=True)
    new = scaled.transition(("1", "2"))
    assert (new.rows["T1"].start, new.rows["T1"].end) == (1.0, 3.0)
    assert new.rows["T1"].waypoints[0].at == 2.0
    assert new.routes["r"].end == 4.0


def test_set_counts_without_rescale_refuses_overrunning_paths() -> None:
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": PathRow(entity="T1", end=6.0)})
    with pytest.raises(PathError):
        set_counts(_with_paths(two_set_show(), spec), "2", 4.0)


def test_set_counts_rules_for_first_and_locked() -> None:
    with pytest.raises(TimeRangeError):
        set_counts(two_set_show(), "1", 4.0)
    row = PathRow(entity="T1", end=6.0, locked=True)
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": row})
    with pytest.raises(LockedError):
        set_counts(_with_paths(two_set_show(), spec), "2", 4.0, rescale=True)


def test_renumber_rewrites_every_reference() -> None:
    show, sub = insert_subset(_three_set_show(), "2", 2.0)
    form = Form(id="f", set=sub, kind="line", geometry={}, members=("T1",))
    spec = TransitionSpec(id=("2", sub), counts=2.0)
    notes = (
        Note(id="n1", text="hit", anchor=Anchor.on_set(sub), author="d", created_at=AT),
        Note(
            id="n2", text="go", anchor=Anchor.on_transition((sub, "3")), author="d", created_at=AT
        ),
    )
    pins = (Pin(target="set", id=sub), Pin(target="transition", id=f"2→{sub}"))
    show = show.model_copy(
        update={"forms": {"f": form}, "transitions": (spec,), "notes": notes, "pins": pins}
    )
    renamed, mapping = renumber(show)
    assert mapping == {"1": "1", "2": "2", "2A": "3", "3": "4"}
    assert renamed.set_ids() == ["1", "2", "3", "4"]
    assert renamed.forms["f"].set == "3"
    assert renamed.transitions[0].id == ("2", "3")
    assert renamed.notes[0].anchor.set_id == "3"
    assert renamed.notes[1].anchor.tid == ("3", "4")
    assert [p.id for p in renamed.pins] == ["3", "2→3"]


def test_renumber_unknown_scheme() -> None:
    with pytest.raises(UnknownRefError):
        renumber(two_set_show(), "roman")


def test_transitions_lists_pairs() -> None:
    assert transitions(_three_set_show()) == [("1", "2"), ("2", "3")]


def test_index_counts_measures_seconds_and_holds() -> None:
    show = _three_set_show()
    timeline = show.timeline.model_copy(
        update={
            "measures": MeasureSetting(counts_per_measure=4.0),
            "holds": (Hold(at_count=8.0, seconds=3.0),),
        }
    )
    show = show.model_copy(update={"timeline": timeline})
    entries = index(show).entries
    assert [e.start_count for e in entries] == [0.0, 8.0, 12.0]
    assert [e.measure.measure for e in entries if e.measure] == [1, 3, 4]
    assert approx_equal([e.seconds for e in entries], [0.0, 4.0, 9.0], TOL)


def test_index_lookups_at_sets_and_inside_transitions() -> None:
    show = _three_set_show()
    assert set_at_count(show, 0.0) == "1"
    assert set_at_count(show, 8.0) == "2"
    assert set_at_count(show, 9.0) is None
    assert transition_at_count(show, 8.0) is None
    assert transition_at_count(show, 10.0) == (("2", "3"), 2.0)
    shown = index(show)
    assert (shown.next("2"), shown.prev("2"), shown.next("3"), shown.prev("1")) == (
        "3",
        "1",
        None,
        None,
    )
    with pytest.raises(TimeRangeError):
        set_at_count(show, 12.5)
    with pytest.raises(UnknownRefError):
        shown.next("9")


def test_upgrade_dependency_pins_and_swaps_sheet() -> None:
    newer = field_sheet().model_copy(update={"ref": "sheet:test-field@2"})
    show = upgrade_dependency(two_set_show(), "sheet:test-field@1", "2", sheet=newer)
    assert show.dependencies == {"sheet:test-field": "2"}
    assert show.sheet.ref == "sheet:test-field@2"
    with pytest.raises(VersionError):
        upgrade_dependency(two_set_show(), "sheet:test-field", "3", sheet=newer)
    with pytest.raises(UnknownRefError):
        upgrade_dependency(two_set_show(), "sheet:other", "2")


def test_edits_never_mutate_input() -> None:
    show = two_set_show()
    before = show.model_dump()
    add_set(show, "3", 4.0)
    set_counts(show, "2", 10.0)
    remove_set(show, "2")
    assert show.model_dump() == before


def test_new_show_with_empty_cast() -> None:
    show = new_show("s", "t", field_sheet(), two_set_show().timeline, cast=Cast())
    assert show.sets[0].placements == ()
    spot_cast = Cast(
        groups={"g": Group(key="g", label_prefix="G")}, spots=(Spot(id="G1", group="g"),)
    )
    assert new_show("s", "t", field_sheet(), two_set_show().timeline, spot_cast).sets[
        0
    ].entity_ids() == ("G1",)


SCRIPT_OLD_ONE_A = b"# script of 1A\n"
SCRIPT_OLD_TWO = b"# script of 2\n"
REVISION_FLOOD = 1000
MAX_CONFLICT_MESSAGE_CHARS = 4096
LATER_WINDOW = (9.0, 11.0)
SHIFT_COUNTS = 8.0


@pytest.mark.defect("UP-9.7-01")
@pytest.mark.xfail(strict=True, reason="UP-9.7-01")
def test_regression_renumber_then_save_keeps_scripts_with_their_sets() -> None:
    """Moves each set's build script with the set across renumber and save (audit 9.7 #1; P0 #1)."""
    repo = InMemoryRepository()
    ws = open_workspace(repo, "s", "designer")
    inserted, _ = insert_subset(two_set_show(), "1", 2.0)
    save_show(ws, inserted, AT)
    ws.commit(
        {"/sets/1A/set.py": SCRIPT_OLD_ONE_A, "/sets/2/set.py": SCRIPT_OLD_TWO},
        at=AT,
        message="scripts",
    )
    renumbered, mapping = renumber(load_show(ws))
    save_show(ws, renumbered, AT, "renumber")
    assert mapping == {"1": "1", "1A": "2", "2": "3"}
    assert repo.read("s", "/sets/2/set.py") == SCRIPT_OLD_ONE_A
    assert repo.read("s", "/sets/3/set.py") == SCRIPT_OLD_TWO


@pytest.mark.defect("UP-9.7-52")
@pytest.mark.xfail(strict=True, reason="UP-9.7-52")
def test_regression_remove_set_leaves_no_note_on_the_removed_set() -> None:
    """Drops or reports notes anchored to a removed set (audit 9.7 sets defects)."""
    note = Note(id="n", text="t", anchor=Anchor.on_set("2"), author="d", created_at=AT)
    show = _three_set_show().model_copy(update={"notes": (note,)})
    edited = remove_set(show, "2")
    still_anchored = [n for n in edited.notes if n.anchor.set_id == "2"]
    assert not still_anchored or not validate_show(edited).ok


@pytest.mark.defect("UP-9.7-53")
@pytest.mark.xfail(strict=True, reason="UP-9.7-53")
def test_regression_remove_set_leaves_no_note_on_a_vanished_transition() -> None:
    """Drops or reports notes anchored to a transition that no longer exists (audit 9.7)."""
    note = Note(
        id="n", text="t", anchor=Anchor.on_transition(("2", "3")), author="d", created_at=AT
    )
    show = _three_set_show().model_copy(update={"notes": (note,)})
    edited = remove_set(show, "2")
    stale = [n for n in edited.notes if n.anchor.tid not in (None, *edited.transition_ids())]
    assert not stale or not validate_show(edited).ok


@pytest.mark.defect("UP-9.7-54")
@pytest.mark.xfail(strict=True, reason="UP-9.7-54")
def test_regression_remove_last_set_with_merge_counts_keeps_total_time() -> None:
    """Keeps total counts, or refuses, when merging the last set's counts (audit 9.7)."""
    show = _three_set_show()
    total = sum(s.counts for s in show.sets)
    try:
        edited = remove_set(show, "3", merge_counts=True)
    except ShowPyError:
        return
    assert approx_equal(sum(s.counts for s in edited.sets), total, 1e-9)


@pytest.mark.defect("UP-9.7-55")
@pytest.mark.xfail(strict=True, reason="UP-9.7-55")
def test_regression_add_set_rejects_two_placements_for_one_spot() -> None:
    """Rejects two placements for one spot instead of keeping the last (audit 9.7)."""
    rows = (Placement(entity="T1", x=1.0, y=1.0), Placement(entity="T1", x=2.0, y=2.0))
    with pytest.raises(SchemaError):
        add_set(two_set_show(), "3", 4.0, placements=rows)


@pytest.mark.defect("UP-9.7-56")
@pytest.mark.xfail(strict=True, reason="UP-9.7-56")
def test_regression_insert_subset_after_a_names_a_subset_of_a() -> None:
    """Names the subset after set "A" with "A" as its base, not "B" (audit 9.7 sets defects)."""
    base = two_set_show()
    renamed = base.model_copy(
        update={"sets": (base.sets[0].model_copy(update={"id": "A"}), base.sets[1])}
    )
    _, new_id = insert_subset(renamed, "A", 3.0)
    assert new_id.startswith("A")


@pytest.mark.defect("UP-9.7-57")
@pytest.mark.xfail(strict=True, reason="UP-9.7-57")
def test_regression_form_keys_equal_form_ids_across_a_round_trip() -> None:
    """Rejects a form key that differs from its ID, or keeps keys stable (audit 9.7)."""
    form = Form(id="f1", set="2", kind="line", geometry={}, members=("T1",))
    try:
        show = two_set_show().model_copy(update={"forms": {"zz": form}})
        Show.model_validate(show.model_dump())
    except ShowPyError:
        return
    assert set(decode_show(encode_show(show)).forms) == set(show.forms)


@pytest.mark.defect("UP-9.7-58")
@pytest.mark.xfail(strict=True, reason="UP-9.7-58")
def test_regression_frozen_show_forms_cannot_be_mutated() -> None:
    """Refuses `show.forms[...] = form` on a frozen show (audit 9.7 sets defects)."""
    form = Form(id="zz", set="2", kind="line", geometry={}, members=("T1",))
    show = two_set_show()
    with pytest.raises(TypeError):
        show.forms["zz"] = form


@pytest.mark.defect("UP-9.7-59")
@pytest.mark.xfail(strict=True, reason="UP-9.7-59")
def test_regression_frozen_show_is_hashable() -> None:
    """Hashes a frozen show (audit 9.7 sets defects)."""
    assert isinstance(hash(two_set_show()), int)


@pytest.mark.defect("UP-9.7-60")
@pytest.mark.xfail(strict=True, reason="UP-9.7-60")
@pytest.mark.timeout(5, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_stale_save_conflict_message_is_bounded() -> None:
    """Caps the ConflictError text after many landed revisions (audit 9.7 sets defects)."""
    repo = InMemoryRepository()
    first = repo.put_show(
        "s", two_set_show(), expected_revision="", actor="designer", at=AT, message="create"
    ).id
    head = first
    for n in range(REVISION_FLOOD):
        head = repo.transaction(
            "s",
            {"/renders/a.txt": str(n).encode()},
            expected_revision=head,
            actor="agent",
            at=AT,
            message=f"render {n}",
        ).id
    with pytest.raises(ConflictError) as excinfo:
        repo.transaction(
            "s",
            {"/renders/b.txt": b"late"},
            expected_revision=first,
            actor="agent",
            at=AT,
            message="stale",
        )
    assert len(str(excinfo.value)) <= MAX_CONFLICT_MESSAGE_CHARS


@pytest.mark.defect("UP-9.7-61")
@pytest.mark.xfail(strict=True, reason="UP-9.7-61")
def test_regression_set_counts_rescale_shifts_facing_overrides_and_zone_windows() -> None:
    """Shifts later facing overrides and zone windows when a set is rescaled (audit 9.7)."""
    show = _three_set_show()
    override = FacingOverride(
        entities="all", mode=FacingMode.TO_TARGET, start=LATER_WINDOW[0], end=LATER_WINDOW[1]
    )
    zone = show.sheet.zones[0].model_copy(update={"id": "late", "active": (LATER_WINDOW,)})
    show = show.model_copy(update={"facing_overrides": (override,), "zones": (zone,)})
    edited = set_counts(show, "2", 16.0, rescale=True)
    shifted = (LATER_WINDOW[0] + SHIFT_COUNTS, LATER_WINDOW[1] + SHIFT_COUNTS)
    assert (edited.facing_overrides[0].start, edited.facing_overrides[0].end) == shifted
    assert edited.zones[0].active == (shifted,)
