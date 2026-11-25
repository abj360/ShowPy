#!/usr/bin/env python3

"""
test_manage.py --- tests show creation, set insertion, removal, retiming, renumbering, indexing

Contains:
    test_regression_renumber_then_save_keeps_scripts_with_their_sets: moves each set's build script
        with the set across renumber and save
    test_regression_remove_last_set_with_merge_counts_keeps_total_time: keeps total counts, or
        refuses, when merging the last set's counts
    test_regression_add_set_rejects_two_placements_for_one_spot: rejects two placements for one spot
        instead of keeping the last
    test_regression_insert_subset_after_a_names_a_subset_of_a: names the subset after set "A" with
        "A" as its base, not "B"
    test_regression_form_keys_equal_form_ids_across_a_round_trip: rejects a form key that differs
        from its ID, or keeps keys stable
    test_regression_frozen_show_forms_cannot_be_mutated: refuses `show.forms[...] = form` on a
        frozen show
    test_regression_frozen_show_is_hashable: hashes a frozen show
    test_regression_stale_save_conflict_message_is_bounded: caps the ConflictError text after many
        landed revisions
    test_regression_set_counts_rescale_shifts_facing_overrides: shifts later facing overrides when
        a set is rescaled
    test_regression_remove_set_leaves_no_transition_pin_on_a_vanished_pair: drops or reports a
        transition pin on a vanished pair
    test_regression_error_messages_truncate_echoed_input: echoes at most about 80 characters of a
        10,000-character scheme name
"""

import sys
from datetime import UTC, datetime

import pytest

from e8s.cast.models import Cast, Group, Spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, PlacementPatch
from e8s.choreo.path_models import (
    FacingMode,
    FacingOverride,
    PathRow,
    Route,
    TransitionSpec,
    Waypoint,
)
from e8s.core.errors import (
    ConflictError,
    E8sError,
    LockedError,
    PathError,
    SchemaError,
    TimeRangeError,
    UnknownRefError,
    VersionError,
)
from e8s.core.ids import FormId, RouteId, SetId, SpotId, make_tid
from e8s.core.util import approx_equal
from e8s.geo.curves import Segment
from e8s.show.dependencies import upgrade_dependency
from e8s.show.documents import decode_show, encode_show
from e8s.show.manage import add_set, insert_subset, new_show, remove_set, set_counts
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Pin, Show
from e8s.show.renumber import renumber
from e8s.show.set_queries import index, set_at_count, transition_at_count, transitions
from e8s.show.show_io import load_show, save_show
from e8s.show.validate import validate_show
from e8s.show.workspace import open_workspace
from e8s.time.models import Hold, MeasureSetting
from tests.fixtures.recipes import LINE_RECIPE
from tests.fixtures.shows import field_sheet, two_set_show
from tests.helpers import fails_after

AT = datetime(2026, 11, 11, tzinfo=UTC)
TOL = 1e-9


def _three_set_show() -> Show:
    return add_set(two_set_show(), "3", 4.0)


def _with_paths(show: Show, spec: TransitionSpec) -> Show:
    return show.replace(transitions=(spec,))


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
    sheet = field_sheet()._copied(update={"ref": "sheet:test-field"})
    with pytest.raises(VersionError):
        new_show("s1", "Opener", sheet, two_set_show().timeline)


def test_add_set_copies_previous_positions_without_forms() -> None:
    show = two_set_show()
    form_set = show.sets[1].with_placements({"T1": PlacementPatch(form=FormId("f"), order=0)})
    show = show.replace(sets=(show.sets[0], form_set))
    added = add_set(show, "3", 4.0, placements=[Placement(entity=SpotId("T2"), x=-3.0, y=1.0)])
    third = added.set("3")
    assert third.get("T1").point == (0.0, 9.0)
    assert third.get("T1").form is None
    assert third.get("T2").point == (-3.0, 1.0)


def test_add_set_after_drops_paths_between_neighbours() -> None:
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=1)},
    )
    show = add_set(_with_paths(two_set_show(), spec), "1B", 2.0, after="1")
    assert show.set_ids() == ["1", "1B", "2"]
    assert show.transitions == ()


def test_add_set_refuses_to_drop_locked_paths() -> None:
    row = PathRow(entity=SpotId("T1"), locked=True)
    show = _with_paths(
        two_set_show(), TransitionSpec(id=make_tid("1", "2"), counts=8, rows={SpotId("T1"): row})
    )
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
        add_set(show, "3", 4.0, placements=[Placement(entity=SpotId("Z9"), x=0, y=0)])


def test_add_set_copies_entity_pin() -> None:
    show = two_set_show().replace(pins=(Pin(target="entity", id="T1"),))
    assert add_set(show, "3", 4.0).set("3").get("T1").pinned


def test_insert_subset_keeps_total_time_and_names_letters() -> None:
    show, first = insert_subset(two_set_show(), "1", 3.0)
    show, second = insert_subset(show, "1A", 2.0)
    assert (first, second) == ("1A", "1B")
    assert show.set_ids() == ["1", "1A", "1B", "2"]
    assert approx_equal(index(show).total_counts, 8.0, abs_tol=TOL)
    assert show.set("1A").get("T1").point == show.set("1").get("T1").point


def test_insert_subset_raises_when_counts_do_not_fit() -> None:
    with pytest.raises(TimeRangeError):
        insert_subset(two_set_show(), "1", 8.0)
    with pytest.raises(TimeRangeError):
        insert_subset(two_set_show(), "2", 1.0)


def test_insert_subset_respects_pinned_next_set() -> None:
    show = two_set_show().replace(pins=(Pin(target="set", id="2"),))
    with pytest.raises(LockedError):
        insert_subset(show, "1", 2.0)


def test_remove_set_merges_counts_and_drops_forms_and_paths() -> None:
    show = _three_set_show()
    form = Form(id=FormId("f"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    spec = TransitionSpec(
        id=make_tid("2", "3"), counts=4.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"))}
    )
    show = show.replace(forms={"f": form}, transitions=(spec,))
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
    pinned = show.sets[1].with_placements({"T1": PlacementPatch(pinned=True)})
    show = show.replace(sets=(show.sets[0], pinned))
    with pytest.raises(LockedError):
        remove_set(show, "2")
    assert remove_set(show, "2", force=True).set_ids() == ["1"]


def test_set_counts_updates_transition_counts() -> None:
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=4.0)},
    )
    show = set_counts(_with_paths(two_set_show(), spec), "2", 12.0)
    assert show.set("2").counts == 12.0
    assert show.transition(("1", "2")).counts == 12.0
    assert show.transition(("1", "2")).rows[SpotId("T1")].end == 4.0


def test_set_counts_rescales_windows_waypoints_and_routes() -> None:
    row = PathRow(entity=SpotId("T1"), start=2.0, end=6.0, waypoints=(Waypoint(x=0, y=0, at=4.0),))
    route = Route(
        id=RouteId("r"),
        curve=Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
        members=(SpotId("T2"),),
        offsets=(0.0,),
        travel=1.0,
        end=8.0,
    )
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row}, routes={RouteId("r"): route}
    )
    scaled = set_counts(_with_paths(two_set_show(), spec), "2", 4.0, rescale=True)
    new = scaled.transition(("1", "2"))
    assert (new.rows[SpotId("T1")].start, new.rows[SpotId("T1")].end) == (1.0, 3.0)
    assert new.rows[SpotId("T1")].waypoints[0].at == 2.0
    assert new.routes[RouteId("r")].end == 4.0


def test_set_counts_without_rescale_refuses_overrunning_paths() -> None:
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=6.0)},
    )
    with pytest.raises(PathError):
        set_counts(_with_paths(two_set_show(), spec), "2", 4.0)


def test_set_counts_rules_for_first_and_locked() -> None:
    with pytest.raises(TimeRangeError):
        set_counts(two_set_show(), "1", 4.0)
    row = PathRow(entity=SpotId("T1"), end=6.0, locked=True)
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row})
    with pytest.raises(LockedError):
        set_counts(_with_paths(two_set_show(), spec), "2", 4.0, rescale=True)


def test_renumber_rewrites_every_reference() -> None:
    show, sub = insert_subset(_three_set_show(), "2", 2.0)
    form = Form(id=FormId("f"), set=sub, recipe=LINE_RECIPE, members=(SpotId("T1"),))
    spec = TransitionSpec(id=make_tid("2", sub), counts=2.0)
    pins = (Pin(target="set", id=sub), Pin(target="transition", id=f"2→{sub}"))
    show = show.replace(forms={"f": form}, transitions=(spec,), pins=pins)
    renamed, mapping = renumber(show)
    assert mapping == {"1": "1", "2": "2", "2A": "3", "3": "4"}
    assert renamed.set_ids() == ["1", "2", "3", "4"]
    assert renamed.forms[FormId("f")].set == "3"
    assert renamed.transitions[0].id == ("2", "3")
    assert [p.id for p in renamed.pins] == ["3", "2→3"]


def test_renumber_unknown_scheme() -> None:
    with pytest.raises(UnknownRefError):
        renumber(two_set_show(), "roman")


def test_transitions_lists_pairs() -> None:
    assert transitions(_three_set_show()) == [("1", "2"), ("2", "3")]


def test_index_counts_measures_seconds_and_holds() -> None:
    show = _three_set_show()
    timeline = show.timeline.replace(
        measures=MeasureSetting(counts_per_measure=4.0), holds=(Hold(at_count=8.0, seconds=3.0),)
    )
    show = show.replace(timeline=timeline)
    entries = index(show).entries
    assert [e.start_count for e in entries] == [0.0, 8.0, 12.0]
    assert [e.measure.measure for e in entries if e.measure] == [1, 3, 4]
    assert approx_equal([e.seconds for e in entries], [0.0, 4.0, 9.0], abs_tol=TOL)


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
    newer = field_sheet().replace(ref="sheet:test-field@2")
    show = upgrade_dependency(two_set_show(), "sheet:test-field@1", "2", sheet=newer)
    assert show.dependencies == {"sheet:test-field": "2"}
    assert show.sheet.ref == "sheet:test-field@2"
    with pytest.raises(VersionError):
        upgrade_dependency(two_set_show(), "sheet:test-field", "3", sheet=newer)
    with pytest.raises(UnknownRefError):
        upgrade_dependency(two_set_show(), "sheet:other", "2")


def test_edits_never_mutate_input() -> None:
    show = two_set_show()
    before = show.to_json()
    add_set(show, "3", 4.0)
    set_counts(show, "2", 10.0)
    remove_set(show, "2")
    assert show.to_json() == before


def test_new_show_with_empty_cast() -> None:
    show = new_show("s", "t", field_sheet(), two_set_show().timeline, cast=Cast())
    assert show.sets[0].placements == ()
    spot_cast = Cast(
        groups={"g": Group(key="g", label_prefix="G")}, spots=(Spot(id=SpotId("G1"), group="g"),)
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
LONG_INPUT_CHARS = 10_000
# Audit 4.2 truncates echoed input to about 80 characters; the message frame needs the rest.
MAX_ECHOING_MESSAGE_CHARS = 200


def test_regression_renumber_then_save_keeps_scripts_with_their_sets() -> None:
    """Moves each set's build script with the set across renumber and save (audit 9.7 #1; P0 #1)."""
    repo = InMemoryRepository()
    ws = open_workspace(repo, "s")
    inserted, _ = insert_subset(two_set_show(), "1", 2.0)
    save_show(ws, inserted, AT)
    ws.commit(
        {"/sets/1A/set.py": SCRIPT_OLD_ONE_A, "/sets/2/set.py": SCRIPT_OLD_TWO},
        at=AT,
    )
    renumbered, mapping = renumber(load_show(ws))
    save_show(ws, renumbered, AT)
    assert mapping == {"1": "1", "1A": "2", "2": "3"}
    ws.refresh()
    assert ws.read("/sets/2/set.py") == SCRIPT_OLD_ONE_A.decode()
    assert ws.read("/sets/3/set.py") == SCRIPT_OLD_TWO.decode()


def test_regression_remove_last_set_with_merge_counts_keeps_total_time() -> None:
    """Keeps total counts, or refuses, when merging the last set's counts (audit 9.7)."""
    show = _three_set_show()
    total = sum(s.counts for s in show.sets)
    try:
        edited = remove_set(show, "3", merge_counts=True)
    except E8sError:
        return
    assert approx_equal(sum(s.counts for s in edited.sets), total, abs_tol=1e-9)


def test_remove_last_set_merges_counts_into_previous_set_and_its_move() -> None:
    show = _three_set_show()
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0)
    edited = remove_set(show.replace(transitions=(spec,)), "3")
    assert edited.set("2").counts == 12.0
    assert edited.transitions[0].counts == 12.0


def test_regression_add_set_rejects_two_placements_for_one_spot() -> None:
    """Rejects two placements for one spot instead of keeping the last (audit 9.7)."""
    rows = (
        Placement(entity=SpotId("T1"), x=1.0, y=1.0),
        Placement(entity=SpotId("T1"), x=2.0, y=2.0),
    )
    with pytest.raises(SchemaError):
        add_set(two_set_show(), "3", 4.0, placements=rows)


def test_regression_insert_subset_after_a_names_a_subset_of_a() -> None:
    """Names the subset after set "A" with "A" as its base, not "B" (audit 9.7 sets defects)."""
    base = two_set_show()
    renamed = base.replace(sets=(base.sets[0].replace(id="A"), base.sets[1]))
    try:
        _, new_id = insert_subset(renamed, "A", 3.0)
    except SchemaError:
        return
    assert new_id.startswith("A")


def test_regression_insert_subset_after_a_is_aa_and_after_aa_is_ab() -> None:
    base = two_set_show()
    renamed = base.replace(sets=(base.sets[0].replace(id="A"), base.sets[1]))
    with_aa, first = insert_subset(renamed, "A", 3.0)
    _, second = insert_subset(with_aa, "AA", 1.0)
    assert (first, second) == ("AA", "AB")


def test_regression_form_keys_equal_form_ids_across_a_round_trip() -> None:
    """Rejects a form key that differs from its ID, or keeps keys stable (audit 9.7)."""
    form = Form(id=FormId("f1"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    try:
        show = two_set_show().replace(forms={"zz": form})
        Show.from_json(show.to_json())
    except E8sError:
        return
    assert set(decode_show(encode_show(show)).forms) == set(show.forms)


def test_regression_frozen_show_forms_cannot_be_mutated() -> None:
    """Refuses `show.forms[...] = form` on a frozen show (audit 9.7 sets defects)."""
    form = Form(id=FormId("zz"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    show = two_set_show()
    with pytest.raises(TypeError):
        show.forms[FormId("zz")] = form  # type: ignore[index]


def test_regression_frozen_show_is_hashable() -> None:
    """Hashes a frozen show (audit 9.7 sets defects)."""
    assert isinstance(hash(two_set_show()), int)


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_stale_save_conflict_message_is_bounded() -> None:
    """Caps the ConflictError text after many landed revisions (audit 9.7 sets defects)."""
    repo = InMemoryRepository()
    first = repo.put_show("s", two_set_show(), expected_revision="", at=AT).id
    head = first
    for n in range(REVISION_FLOOD):
        head = repo.transaction(
            "s",
            {"/renders/a.txt": str(n).encode()},
            expected_revision=head,
            at=AT,
        ).id
    with pytest.raises(ConflictError) as excinfo:
        repo.transaction(
            "s",
            {"/renders/b.txt": b"late"},
            expected_revision=first,
            at=AT,
        )
    assert len(str(excinfo.value)) <= MAX_CONFLICT_MESSAGE_CHARS


@pytest.mark.defect("UP-9.7-60")
@pytest.mark.xfail(strict=True, reason="UP-9.7-60")
def test_regression_set_counts_rescale_shifts_facing_overrides() -> None:
    """Shifts later facing overrides when a set is rescaled (audit 9.7)."""
    show = _three_set_show()
    override = FacingOverride(
        entities="all", mode=FacingMode.TO_TARGET, start=LATER_WINDOW[0], end=LATER_WINDOW[1]
    )
    show = show.replace(facing_overrides=(override,))
    edited = set_counts(show, "2", 16.0, rescale=True)
    shifted = (LATER_WINDOW[0] + SHIFT_COUNTS, LATER_WINDOW[1] + SHIFT_COUNTS)
    assert (edited.facing_overrides[0].start, edited.facing_overrides[0].end) == shifted


@pytest.mark.defect("UP-9.7-85")
@pytest.mark.xfail(strict=True, reason="UP-9.7-85")
def test_regression_remove_set_leaves_no_transition_pin_on_a_vanished_pair() -> None:
    """Drops or reports a transition pin on a vanished pair (audit 9.7 sets).

    The pin ID `2->3` is non-canonical (canonical is `2→3`), so this overlaps the
    normalization defects 45 and 67; canonical pins are already dropped.
    """
    show = _three_set_show().replace(pins=(Pin(target="transition", id="2->3"),))
    edited = remove_set(show, "2", force=True)
    assert not edited.pins or not validate_show(edited).ok


def test_regression_error_messages_truncate_echoed_input() -> None:
    """Echoes at most about 80 characters of a 10,000-character scheme name (audit 4.2)."""
    with pytest.raises(UnknownRefError) as excinfo:
        renumber(two_set_show(), "x" * LONG_INPUT_CHARS)
    assert len(excinfo.value.message) <= MAX_ECHOING_MESSAGE_CHARS
