#!/usr/bin/env python3

"""
test_validate.py --- tests structural validation of sets, paths, dependencies and whole shows

Contains:
    test_regression_validate_show_reports_form_members_naming_unknown_spots: reports a form member
        that is not in the cast
    test_regression_validate_show_reports_forms_of_a_missing_set: reports a form whose set does not
        exist
    test_regression_validate_show_reports_sets_declaring_missing_forms: reports a set that lists a
        form that does not exist
    test_regression_validate_show_reports_form_keys_that_differ_from_ids: reports a form stored
        under a key other than its ID
    test_regression_validate_show_reports_members_whose_placements_do_not_point_back: reports a form
        member whose placement names no form
    test_regression_validate_show_reports_pins_naming_missing_items: reports a form pin on a form
        that does not exist
    test_regression_validate_show_reports_pins_on_unparsable_transitions: reports a transition pin
        whose ID does not parse
    test_regression_validate_show_reports_pins_that_disagree_with_placement_flags: reports a pin
        record whose placements are not flagged pinned
    test_regression_validate_show_reports_notes_anchored_to_missing_items: reports a note anchored
        to a set that does not exist
    test_regression_validate_show_reports_duplicate_note_ids: reports two notes with one ID
    test_regression_validate_show_reports_facing_overrides_naming_unknown_spots: reports a facing
        override on a spot that does not exist
    test_regression_validate_show_reports_facing_overrides_past_the_show: reports a facing override
        that starts after the show ends
    test_regression_validate_show_reports_zone_selectors_naming_unknown_spots: reports a zone that
        applies to a spot that does not exist
    test_regression_validate_show_reports_degenerate_zones: reports a zone whose polygon has no area
    test_regression_validate_show_reports_selections_naming_unknown_spots: reports a named selection
        holding a spot that does not exist
    test_regression_validate_show_names_the_selection_and_spot_in_context: puts the selection path
        and the unknown spot in the issue context
    test_regression_validate_show_reports_infinite_set_counts: reports a set whose counts are
        infinite
    test_regression_validate_show_reports_non_finite_tempo: reports a tempo section whose tempo is
        infinite
    test_regression_validate_show_reports_non_finite_waypoints: reports a path waypoint with an
        infinite coordinate
    test_regression_validate_show_warns_about_placements_far_off_the_sheet: warns about a placement
        a million meters off the sheet
    test_regression_validate_show_reports_unpinned_sheet_without_a_store: reports an unpinned sheet
        even when no sheet store is passed
    test_regression_validate_show_reports_empty_ids: reports an empty show, note or form ID, each
        alone
    test_regression_validate_show_reports_set_ids_that_cannot_be_stored: reports a set ID that no
        workspace path can hold
    test_regression_validate_show_reports_placements_of_inactive_spots: reports an on-field
        placement of a spot marked inactive
    test_regression_validate_show_reports_a_nonzero_first_set_count: reports a first set that has
        counts
    test_regression_validate_show_reports_nan_counts_on_the_first_set: reports NaN counts on the
        first set by name
    test_regression_validate_show_reports_nan_waypoints: reports a path waypoint with a NaN
        coordinate
    test_regression_validate_show_reports_comments_anchored_to_missing_items: reports a comment
        anchored to a set that does not exist
    test_regression_validate_show_warns_about_stale_forms: warns about a form its set no longer
        lists
    test_validate_show_accepts_a_set_whose_forms_are_all_listed: raises no stale-form warning when
        every form is listed
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from showpy.choreo.form_models import Form
from showpy.choreo.models import Placement, Set
from showpy.choreo.path_models import (
    FacingMode,
    FacingOverride,
    PathRow,
    PathStyle,
    TransitionSpec,
    Waypoint,
)
from showpy.core.ids import FormId, SetId, SpotId, make_tid
from showpy.core.types import Region
from showpy.core.validation import ValidationReport
from showpy.notes.models import Anchor, Note
from showpy.sheet.templates import InMemorySheetStore
from showpy.show.models import Comment, Pin, Show
from showpy.show.validate import (
    validate_dependencies,
    validate_paths,
    validate_set,
    validate_show,
)
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import replaced_form_show


def _codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_show_passes() -> None:
    report = validate_show(two_set_show())
    assert report.ok, str(report)
    assert report.issues == ()


def test_set_missing_and_unknown_spots() -> None:
    show = two_set_show()
    broken = Set(
        id=SetId("2"),
        counts=8.0,
        placements=(*show.set("2").placements[1:], Placement(entity=SpotId("Z9"), x=0, y=0)),
    )
    codes = _codes(validate_set(show, broken))
    assert {"MISSING_SPOT", "UNKNOWN_SPOT"} <= codes


def test_pinned_rows_must_stay_unchanged() -> None:
    show = with_targets({"T1": {"pinned": True}})
    moved = show.set("2").with_placements({"T1": {"x": 9.0}}, force=True)
    assert "PINNED_CHANGED" in _codes(validate_set(show, moved))


def test_form_membership_is_checked() -> None:
    show = two_set_show()
    form = Form(id=FormId("f"), set=SetId("1"), kind="line", geometry={}, members=(SpotId("T1"),))
    show = show.model_copy(update={"forms": {"f": form}})
    first = show.set("1").with_placements({"T2": {"form": "f", "order": 0}, "T3": {"form": "zz"}})
    codes = _codes(validate_set(show, first))
    assert {"NOT_IN_FORM", "UNKNOWN_FORM"} <= codes


def test_facing_out_of_range_warns() -> None:
    show = two_set_show()
    turned = show.set("1").with_placements({"T1": {"facing": 400.0}})
    report = validate_set(show, turned)
    assert report.ok
    assert "FACING_RANGE" in _codes(report)


def test_paths_continuity_and_windows() -> None:
    show = two_set_show()
    spec = TransitionSpec(
        id=TID,
        counts=8.0,
        rows={
            SpotId("T1"): PathRow(entity=SpotId("T1"), style=PathStyle.HOLD),
            SpotId("Z9"): PathRow(entity=SpotId("Z9")),
        },
    )
    codes = _codes(validate_paths(show, spec))
    assert {"CONTINUITY", "UNKNOWN_SPOT"} <= codes


def test_paths_counts_must_match_destination_set() -> None:
    spec = TransitionSpec(id=TID, counts=6.0)
    assert "COUNTS_MISMATCH" in _codes(validate_paths(two_set_show(), spec))


def test_paths_report_bad_window_and_missing_rotate_params() -> None:
    show = with_rows(
        {
            "T1": PathRow(entity=SpotId("T1"), end=12.0),
            "T2": PathRow(entity=SpotId("T2"), style=PathStyle.ROTATE),
        }
    )
    codes = _codes(validate_paths(show, show.transition(TID)))
    assert {"PATH"} <= codes


def test_dependencies_resolve_through_store() -> None:
    show = two_set_show()
    assert "UNKNOWN_SHEET" in _codes(validate_dependencies(show))
    store = InMemorySheetStore()
    store.put(show.sheet, "mine")
    assert validate_dependencies(show, store).ok


def test_dependency_version_must_be_pinned() -> None:
    show = two_set_show().model_copy(update={"dependencies": {}})
    assert "UNPINNED_SHEET" in _codes(validate_dependencies(show))


def test_show_flags_stale_transitions_and_zero_counts() -> None:
    show = two_set_show()
    stale = TransitionSpec(id=make_tid("2", "1"), counts=8.0)
    zero = show.set("2").model_copy(update={"counts": 0.0})
    broken = show.model_copy(update={"transitions": (stale,), "sets": (show.sets[0], zero)})
    codes = _codes(validate_show(broken))
    assert {"STALE_TRANSITION", "ZERO_COUNTS"} <= codes


NOTE_AT = datetime(2026, 11, 11, tzinfo=UTC)
FAR_OFF_SHEET_M = 1_000_000.0
PAST_SHOW_COUNT = 777.0
FORM_ID = "f1"
SET_TWO = "2"
GHOST = "GHOST"


def _edited(**fields: object) -> Show:
    return two_set_show().model_copy(update=fields)


def _mentions(show: Show, token: str) -> bool:
    return any(token in f"{i.where} {i.message} {i.hint}" for i in validate_show(show).issues)


def _formed(
    key: str = FORM_ID,
    members: tuple[str, ...] = ("T1", "T2"),
    form_set: str = SET_TWO,
    points_back: bool = True,
    declares: bool = True,
    form_id: str = FORM_ID,
) -> Show:
    """Builds a show with one form of set 2 that is valid unless a parameter breaks it."""
    show = two_set_show()
    form = Form(
        id=FormId(form_id),
        set=SetId(form_set),
        kind="line",
        geometry={},
        members=tuple(map(SpotId, members)),
    )
    rows = tuple(
        p.model_copy(update={"form": key, "order": members.index(p.entity)})
        if points_back and p.entity in members
        else p
        for p in show.sets[1].placements
    )
    second = show.sets[1].model_copy(
        update={"placements": rows, "forms": (key,) if declares else ()}
    )
    return show.model_copy(update={"sets": (show.sets[0], second), "forms": {key: form}})


def _pinned_entity(entity: str = "T1", flagged: bool = True) -> Show:
    show = two_set_show()
    pinned = tuple(
        s.model_copy(
            update={
                "placements": tuple(
                    p.model_copy(update={"pinned": flagged}) if p.entity == entity else p
                    for p in s.placements
                )
            }
        )
        for s in show.sets
    )
    return show.model_copy(update={"sets": pinned})


def _with_pin(show: Show, pin: Pin) -> Show:
    return show.model_copy(update={"pins": (pin,)})


def _set_two_with(**fields: object) -> tuple[Set, ...]:
    show = two_set_show()
    return (show.sets[0], show.sets[1].model_copy(update=fields))


@pytest.mark.parametrize(
    "show",
    [
        _formed(),
        _formed(members=()),
        _with_pin(_pinned_entity(), Pin(target="entity", id="T1")),
        _edited(id="show-2"),
    ],
)
def test_valid_validation_fixtures_report_nothing(show: Show) -> None:
    assert validate_show(show).issues == ()


@pytest.mark.defect("UP-9.7-61")
@pytest.mark.xfail(strict=True, reason="UP-9.7-61")
def test_regression_validate_show_reports_form_members_naming_unknown_spots() -> None:
    """Reports a form member that is not in the cast (audit 9.7 validation gaps)."""
    assert _mentions(_formed(members=("T1", "T2", "ZZ")), "ZZ")


@pytest.mark.defect("UP-9.7-62")
@pytest.mark.xfail(strict=True, reason="UP-9.7-62")
def test_regression_validate_show_reports_forms_of_a_missing_set() -> None:
    """Reports a form whose set does not exist (audit 9.7 validation gaps)."""
    show = _formed(members=(), form_set=GHOST, declares=False)
    assert _mentions(show, GHOST)


@pytest.mark.defect("UP-9.7-63")
@pytest.mark.xfail(strict=True, reason="UP-9.7-63")
def test_regression_validate_show_reports_sets_declaring_missing_forms() -> None:
    """Reports a set that lists a form that does not exist (audit 9.7 validation gaps)."""
    assert _mentions(_edited(sets=_set_two_with(forms=("ghost-form",))), "ghost-form")


def test_regression_validate_show_reports_form_keys_that_differ_from_ids() -> None:
    """Reports a form stored under a key other than its ID (audit 9.7 validation gaps)."""
    assert _mentions(_formed(key="zz"), "zz")


@pytest.mark.defect("UP-9.7-65")
@pytest.mark.xfail(strict=True, reason="UP-9.7-65")
def test_regression_validate_show_reports_members_whose_placements_do_not_point_back() -> None:
    """Reports a form member whose placement names no form (audit 9.7 validation gaps)."""
    assert _mentions(_formed(points_back=False), FORM_ID)


@pytest.mark.defect("UP-9.7-66")
@pytest.mark.xfail(strict=True, reason="UP-9.7-66")
def test_regression_validate_show_reports_pins_naming_missing_items() -> None:
    """Reports a form pin on a form that does not exist (audit 9.7 validation gaps)."""
    assert _mentions(_with_pin(two_set_show(), Pin(target="form", id="ghost-form")), "ghost-form")


@pytest.mark.defect("UP-9.7-67")
@pytest.mark.xfail(strict=True, reason="UP-9.7-67")
def test_regression_validate_show_reports_pins_on_unparsable_transitions() -> None:
    """Reports a transition pin whose ID does not parse (audit 9.7 validation gaps)."""
    show = _with_pin(two_set_show(), Pin(target="transition", id="garbage"))
    assert _mentions(show, "garbage")


@pytest.mark.defect("UP-9.7-68")
@pytest.mark.xfail(strict=True, reason="UP-9.7-68")
def test_regression_validate_show_reports_pins_that_disagree_with_placement_flags() -> None:
    """Reports a pin record whose placements are not flagged pinned (audit 9.7 validation gaps)."""
    assert _mentions(_with_pin(two_set_show(), Pin(target="entity", id="T1")), "T1")


def _note(note_id: str = "n", set_id: str = "1") -> Note:
    return Note(id=note_id, text="t", anchor=Anchor.on_set(set_id), author="d", created_at=NOTE_AT)


@pytest.mark.defect("UP-9.7-69")
@pytest.mark.xfail(strict=True, reason="UP-9.7-69")
def test_regression_validate_show_reports_notes_anchored_to_missing_items() -> None:
    """Reports a note anchored to a set that does not exist (audit 9.7 validation gaps)."""
    assert _mentions(_edited(notes=(_note(set_id=GHOST),)), GHOST)


@pytest.mark.defect("UP-9.7-70")
@pytest.mark.xfail(strict=True, reason="UP-9.7-70")
def test_regression_validate_show_reports_duplicate_note_ids() -> None:
    """Reports two notes with one ID (audit 9.7 validation gaps)."""
    assert _mentions(_edited(notes=(_note("dup"), _note("dup"))), "dup")


def _override(entity: str = "T1", start: float = 1.0, end: float = 2.0) -> FacingOverride:
    return FacingOverride(
        entities=(SpotId(entity),), mode=FacingMode.FIXED, value=90.0, start=start, end=end
    )


@pytest.mark.defect("UP-9.7-71")
@pytest.mark.xfail(strict=True, reason="UP-9.7-71")
def test_regression_validate_show_reports_facing_overrides_naming_unknown_spots() -> None:
    """Reports a facing override on a spot that does not exist (audit 9.7 validation gaps)."""
    assert _mentions(_edited(facing_overrides=(_override("ZZ"),)), "ZZ")


@pytest.mark.defect("UP-9.7-72")
@pytest.mark.xfail(strict=True, reason="UP-9.7-72")
def test_regression_validate_show_reports_facing_overrides_past_the_show() -> None:
    """Reports a facing override that starts after the show ends (audit 9.7 validation gaps)."""
    late = _override(start=PAST_SHOW_COUNT, end=PAST_SHOW_COUNT + 1)
    assert _mentions(_edited(facing_overrides=(late,)), "777")


@pytest.mark.defect("UP-9.7-73")
@pytest.mark.xfail(strict=True, reason="UP-9.7-73")
def test_regression_validate_show_reports_zone_selectors_naming_unknown_spots() -> None:
    """Reports a zone that applies to a spot that does not exist (audit 9.7 validation gaps)."""
    zone = two_set_show().sheet.zones[0].model_copy(update={"id": "z", "applies_to": ("ZZ",)})
    assert _mentions(_edited(zones=(zone,)), "ZZ")


@pytest.mark.defect("UP-9.7-74")
@pytest.mark.xfail(strict=True, reason="UP-9.7-74")
def test_regression_validate_show_reports_degenerate_zones() -> None:
    """Reports a zone whose polygon has no area (audit 9.7 validation gaps)."""
    line = Region(exterior=((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)))
    zone = two_set_show().sheet.zones[0].model_copy(update={"id": "flat", "polygon": line})
    assert _mentions(_edited(zones=(zone,)), "flat")


def test_regression_validate_show_reports_selections_naming_unknown_spots() -> None:
    """Reports a named selection holding a spot that does not exist (audit 9.7 validation gaps)."""
    cast = two_set_show().cast
    selections = {"front": frozenset({"T1", "ZZ"})}
    assert _mentions(_edited(cast=cast.model_copy(update={"selections": selections})), "ZZ")


def test_regression_validate_show_names_the_selection_and_spot_in_context() -> None:
    """Puts the selection path and the unknown spot in the issue context."""
    cast = two_set_show().cast
    show = _edited(cast=cast.model_copy(update={"selections": {"front": ("T1", "ZZ")}}))
    (issue,) = [i for i in validate_show(show).issues if i.code == "UNKNOWN_SPOT"]
    assert dict(issue.context) == {"path": "cast.selections.front", "spot": "ZZ"}


@pytest.mark.defect("UP-9.7-76")
@pytest.mark.xfail(strict=True, reason="UP-9.7-76")
def test_regression_validate_show_reports_infinite_set_counts() -> None:
    """Reports a set whose counts are infinite (audit 9.7 validation gaps)."""
    assert _mentions(_edited(sets=_set_two_with(counts=math.inf)), "inf")


@pytest.mark.defect("UP-9.7-77")
@pytest.mark.xfail(strict=True, reason="UP-9.7-77")
def test_regression_validate_show_reports_non_finite_tempo() -> None:
    """Reports a tempo section whose tempo is infinite (audit 9.7 validation gaps)."""
    timeline = two_set_show().timeline
    tempo = timeline.tempos[0].model_copy(update={"counts_per_minute": math.inf})
    show = _edited(timeline=timeline.model_copy(update={"tempos": (tempo,)}))
    assert _mentions(show, "inf")


@pytest.mark.defect("UP-9.7-78")
@pytest.mark.xfail(strict=True, reason="UP-9.7-78")
def test_regression_validate_show_reports_non_finite_waypoints() -> None:
    """Reports a path waypoint with an infinite coordinate (audit 9.7 validation gaps)."""
    waypoint = Waypoint(x=1.0, y=1.0).model_copy(update={"x": math.inf})
    row = PathRow(entity=SpotId("T1")).model_copy(update={"waypoints": (waypoint,)})
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row})
    assert _mentions(_edited(transitions=(spec,)), "inf")


@pytest.mark.defect("UP-9.7-79")
@pytest.mark.xfail(strict=True, reason="UP-9.7-79")
def test_regression_validate_show_warns_about_placements_far_off_the_sheet() -> None:
    """Warns about a placement a million meters off the sheet (audit 9.7 validation gaps)."""
    moved = two_set_show().sets[1].with_placements({"T1": {"x": FAR_OFF_SHEET_M}}, force=True)
    assert _mentions(_edited(sets=_set_two_with(placements=moved.placements)), "T1")


@pytest.mark.defect("UP-9.7-80")
@pytest.mark.xfail(strict=True, reason="UP-9.7-80")
def test_regression_validate_show_reports_unpinned_sheet_without_a_store() -> None:
    """Reports an unpinned sheet even when no sheet store is passed (audit 9.7 validation gaps)."""
    assert "UNPINNED_SHEET" in _codes(validate_show(_edited(dependencies={})))


@pytest.mark.defect("UP-9.7-81")
@pytest.mark.xfail(strict=True, reason="UP-9.7-81")
def test_regression_validate_show_reports_empty_ids() -> None:
    """Reports an empty show, note or form ID, each alone (audit 9.7 validation gaps)."""
    empty_form = _formed(key="", form_id="")
    variants = {
        "show": _edited(id=""),
        "note": _edited(notes=(_note(note_id=""),)),
        "form": empty_form,
    }
    silent = [name for name, show in variants.items() if not validate_show(show).issues]
    assert silent == []


@pytest.mark.defect("UP-9.7-82")
@pytest.mark.xfail(strict=True, reason="UP-9.7-82")
def test_regression_validate_show_reports_set_ids_that_cannot_be_stored() -> None:
    """Reports a set ID that no workspace path can hold (audit 9.7 validation gaps)."""
    show = two_set_show()
    renamed = show.sets[1].model_copy(update={"id": "a/b"})
    assert _mentions(_edited(sets=(show.sets[0], renamed)), "a/b")


@pytest.mark.defect("UP-9.7-83")
@pytest.mark.xfail(strict=True, reason="UP-9.7-83")
def test_regression_validate_show_reports_placements_of_inactive_spots() -> None:
    """Reports an on-field placement of a spot marked inactive (audit 9.7 validation gaps)."""
    cast = two_set_show().cast
    spots = (cast.spots[0].model_copy(update={"active": False}), *cast.spots[1:])
    assert _mentions(_edited(cast=cast.model_copy(update={"spots": spots})), "T1")


@pytest.mark.defect("UP-9.7-84")
@pytest.mark.xfail(strict=True, reason="UP-9.7-84")
def test_regression_validate_show_reports_a_nonzero_first_set_count() -> None:
    """Reports a first set that has counts (audit 9.7 validation gaps)."""
    show = two_set_show()
    first = show.sets[0].model_copy(update={"counts": 3.5})
    assert _mentions(_edited(sets=(first, show.sets[1])), "3.5")


@pytest.mark.defect("UP-9.7-88")
@pytest.mark.xfail(strict=True, reason="UP-9.7-88")
def test_regression_validate_show_reports_nan_counts_on_the_first_set() -> None:
    """Reports NaN counts on the first set by name (audit 9.7 validation gaps)."""
    show = two_set_show()
    first = show.sets[0].model_copy(update={"counts": math.nan})
    report = validate_show(_edited(sets=(first, show.sets[1])))
    nonzero_first = _edited(sets=(show.sets[0].model_copy(update={"counts": 3.5}), show.sets[1]))
    known_codes = _codes(validate_show(nonzero_first))
    assert any(
        "nan" in f"{i.message} {i.hint}".lower() and i.code not in known_codes
        for i in report.issues
    )


@pytest.mark.defect("UP-9.7-89")
@pytest.mark.xfail(strict=True, reason="UP-9.7-89")
def test_regression_validate_show_reports_nan_waypoints() -> None:
    """Reports a path waypoint with a NaN coordinate (audit 9.7 validation gaps)."""
    waypoint = Waypoint(x=1.0, y=1.0).model_copy(update={"y": math.nan})
    row = PathRow(entity=SpotId("T1")).model_copy(update={"waypoints": (waypoint,)})
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row})
    assert _mentions(_edited(transitions=(spec,)), "nan")


@pytest.mark.defect("UP-9.7-90")
@pytest.mark.xfail(strict=True, reason="UP-9.7-90")
def test_regression_validate_show_reports_comments_anchored_to_missing_items() -> None:
    """Reports a comment anchored to a set that does not exist (audit 9.7 validation gaps)."""
    validate: Callable[..., ValidationReport] = validate_show  # comments= is the audit's new API
    comment = Comment(id="c1", anchor=Anchor.on_set(GHOST), text="check")
    report = validate(two_set_show(), comments=(comment,))
    assert any(GHOST in f"{i.where} {i.message}" for i in report.issues)


def test_regression_validate_show_warns_about_stale_forms() -> None:
    """Warns about a form its set no longer lists, naming the form and the set (ruling L-G3)."""
    report = validate_show(replaced_form_show())
    (issue,) = [i for i in report.issues if i.code == "STALE_FORM"]
    assert issue.level == "warning"
    assert "a" in issue.message
    assert "set 2" in issue.where
    assert report.ok


def test_validate_show_accepts_a_set_whose_forms_are_all_listed() -> None:
    """Raises no stale-form warning when every form is listed."""
    show = replaced_form_show()
    clean = show.replace(forms={"b": show.forms[FormId("b")]})
    assert [i for i in validate_show(clean).issues if i.code == "STALE_FORM"] == []
