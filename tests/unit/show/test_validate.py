#!/usr/bin/env python3

"""
test_validate.py --- tests structural validation of sets, paths, dependencies and whole shows
"""

import math
from datetime import UTC, datetime

import pytest

from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    Form,
    PathRow,
    PathStyle,
    Placement,
    Set,
    TransitionSpec,
    Waypoint,
)
from showpy.core.types import Region
from showpy.core.validation import ValidationReport
from showpy.notes.models import Anchor, Note
from showpy.sheet.templates import InMemorySheetStore
from showpy.show.models import Pin, Show
from showpy.show.validate import (
    validate_dependencies,
    validate_paths,
    validate_set,
    validate_show,
)
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show


def _codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_show_passes() -> None:
    report = validate_show(two_set_show())
    assert report.ok, str(report)
    assert report.issues == ()


def test_set_missing_and_unknown_spots() -> None:
    show = two_set_show()
    broken = Set(
        id="2",
        counts=8.0,
        placements=(*show.set("2").placements[1:], Placement(entity="Z9", x=0, y=0)),
    )
    codes = _codes(validate_set(show, broken))
    assert {"MISSING_SPOT", "UNKNOWN_SPOT"} <= codes


def test_pinned_rows_must_stay_unchanged() -> None:
    show = with_targets({"T1": {"pinned": True}})
    moved = show.set("2").with_placements({"T1": {"x": 9.0}}, force=True)
    assert "PINNED_CHANGED" in _codes(validate_set(show, moved))


def test_form_membership_is_checked() -> None:
    show = two_set_show()
    form = Form(id="f", set="1", kind="line", geometry={}, members=("T1",))
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
        rows={"T1": PathRow(entity="T1", style=PathStyle.HOLD), "Z9": PathRow(entity="Z9")},
    )
    codes = _codes(validate_paths(show, spec))
    assert {"CONTINUITY", "UNKNOWN_SPOT"} <= codes


def test_paths_counts_must_match_destination_set() -> None:
    spec = TransitionSpec(id=TID, counts=6.0)
    assert "COUNTS_MISMATCH" in _codes(validate_paths(two_set_show(), spec))


def test_paths_report_bad_window_and_missing_rotate_params() -> None:
    show = with_rows(
        {"T1": PathRow(entity="T1", end=12.0), "T2": PathRow(entity="T2", style=PathStyle.ROTATE)}
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
    stale = TransitionSpec(id=("2", "1"), counts=8.0)
    zero = show.set("2").model_copy(update={"counts": 0.0})
    broken = show.model_copy(update={"transitions": (stale,), "sets": (show.sets[0], zero)})
    codes = _codes(validate_show(broken))
    assert {"STALE_TRANSITION", "ZERO_COUNTS"} <= codes


NOTE_AT = datetime(2026, 11, 11, tzinfo=UTC)
FAR_OFF_SHEET_M = 1_000_000.0
PAST_SHOW_COUNT = 1000.0


def _edited(**fields: object) -> Show:
    return two_set_show().model_copy(update=fields)


def _reports_an_issue(show: Show) -> bool:
    return bool(validate_show(show).issues)


def _form(**fields: object) -> Form:
    base = Form(id="f1", set="2", kind="line", geometry={}, members=("T1", "T2"))
    return base.model_copy(update=fields)


def _set_two_with(**fields: object) -> tuple[Set, ...]:
    show = two_set_show()
    return (show.sets[0], show.sets[1].model_copy(update=fields))


@pytest.mark.defect("UP-9.7-62")
@pytest.mark.xfail(strict=True, reason="UP-9.7-62")
def test_regression_validate_show_reports_form_members_naming_unknown_spots() -> None:
    """Reports a form member that is not in the cast (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(forms={"f1": _form(members=("T1", "ZZ"))}))


@pytest.mark.defect("UP-9.7-63")
@pytest.mark.xfail(strict=True, reason="UP-9.7-63")
def test_regression_validate_show_reports_forms_of_a_missing_set() -> None:
    """Reports a form whose set does not exist (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(forms={"f1": _form(set="9")}))


@pytest.mark.defect("UP-9.7-64")
@pytest.mark.xfail(strict=True, reason="UP-9.7-64")
def test_regression_validate_show_reports_sets_declaring_missing_forms() -> None:
    """Reports a set that lists a form that does not exist (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(sets=_set_two_with(forms=("ghost",))))


@pytest.mark.defect("UP-9.7-65")
@pytest.mark.xfail(strict=True, reason="UP-9.7-65")
def test_regression_validate_show_reports_form_keys_that_differ_from_ids() -> None:
    """Reports a form stored under a key other than its ID (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(forms={"zz": _form()}))


@pytest.mark.defect("UP-9.7-66")
@pytest.mark.xfail(strict=True, reason="UP-9.7-66")
def test_regression_validate_show_reports_members_whose_placements_do_not_point_back() -> None:
    """Reports a form member whose placement names no form (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(forms={"f1": _form()}))


@pytest.mark.defect("UP-9.7-67")
@pytest.mark.xfail(strict=True, reason="UP-9.7-67")
def test_regression_validate_show_reports_pins_naming_missing_items() -> None:
    """Reports a pin on a spot that does not exist (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(pins=(Pin(target="entity", id="ZZ"),)))


@pytest.mark.defect("UP-9.7-68")
@pytest.mark.xfail(strict=True, reason="UP-9.7-68")
def test_regression_validate_show_reports_pins_on_unparsable_transitions() -> None:
    """Reports a transition pin whose ID does not parse (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(pins=(Pin(target="transition", id="garbage"),)))


@pytest.mark.defect("UP-9.7-69")
@pytest.mark.xfail(strict=True, reason="UP-9.7-69")
def test_regression_validate_show_reports_pins_that_disagree_with_placement_flags() -> None:
    """Reports a pin record whose placements are not flagged pinned (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(pins=(Pin(target="entity", id="T1"),)))


@pytest.mark.defect("UP-9.7-70")
@pytest.mark.xfail(strict=True, reason="UP-9.7-70")
def test_regression_validate_show_reports_notes_anchored_to_missing_items() -> None:
    """Reports a note anchored to a set that does not exist (audit 9.7 validation gaps)."""
    note = Note(id="n", text="t", anchor=Anchor.on_set("9"), author="d", created_at=NOTE_AT)
    assert _reports_an_issue(_edited(notes=(note,)))


@pytest.mark.defect("UP-9.7-71")
@pytest.mark.xfail(strict=True, reason="UP-9.7-71")
def test_regression_validate_show_reports_duplicate_note_ids() -> None:
    """Reports two notes with one ID (audit 9.7 validation gaps)."""
    note = Note(id="n", text="t", anchor=Anchor.on_set("1"), author="d", created_at=NOTE_AT)
    assert _reports_an_issue(_edited(notes=(note, note)))


@pytest.mark.defect("UP-9.7-72")
@pytest.mark.xfail(strict=True, reason="UP-9.7-72")
def test_regression_validate_show_reports_facing_overrides_naming_unknown_spots() -> None:
    """Reports a facing override on a spot that does not exist (audit 9.7 validation gaps)."""
    override = FacingOverride(entities=("ZZ",), mode=FacingMode.FIXED, value=90.0, start=1, end=2)
    assert _reports_an_issue(_edited(facing_overrides=(override,)))


@pytest.mark.defect("UP-9.7-73")
@pytest.mark.xfail(strict=True, reason="UP-9.7-73")
def test_regression_validate_show_reports_facing_overrides_past_the_show() -> None:
    """Reports a facing override that starts after the show ends (audit 9.7 validation gaps)."""
    override = FacingOverride(
        entities=("T1",),
        mode=FacingMode.FIXED,
        value=90.0,
        start=PAST_SHOW_COUNT,
        end=PAST_SHOW_COUNT + 1,
    )
    assert _reports_an_issue(_edited(facing_overrides=(override,)))


@pytest.mark.defect("UP-9.7-74")
@pytest.mark.xfail(strict=True, reason="UP-9.7-74")
def test_regression_validate_show_reports_zone_selectors_naming_unknown_spots() -> None:
    """Reports a zone that applies to a spot that does not exist (audit 9.7 validation gaps)."""
    zone = two_set_show().sheet.zones[0].model_copy(update={"id": "z", "applies_to": ("ZZ",)})
    assert _reports_an_issue(_edited(zones=(zone,)))


@pytest.mark.defect("UP-9.7-75")
@pytest.mark.xfail(strict=True, reason="UP-9.7-75")
def test_regression_validate_show_reports_degenerate_zones() -> None:
    """Reports a zone whose polygon has no area (audit 9.7 validation gaps)."""
    line = Region(exterior=((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)))
    zone = two_set_show().sheet.zones[0].model_copy(update={"id": "flat", "polygon": line})
    assert _reports_an_issue(_edited(zones=(zone,)))


@pytest.mark.defect("UP-9.7-76")
@pytest.mark.xfail(strict=True, reason="UP-9.7-76")
def test_regression_validate_show_reports_selections_naming_unknown_spots() -> None:
    """Reports a named selection holding a spot that does not exist (audit 9.7 validation gaps)."""
    cast = two_set_show().cast
    selections = {"front": frozenset({"T1", "ZZ"})}
    assert _reports_an_issue(_edited(cast=cast.model_copy(update={"selections": selections})))


@pytest.mark.defect("UP-9.7-77")
@pytest.mark.xfail(strict=True, reason="UP-9.7-77")
def test_regression_validate_show_reports_infinite_set_counts() -> None:
    """Reports a set whose counts are infinite (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(sets=_set_two_with(counts=math.inf)))


@pytest.mark.defect("UP-9.7-78")
@pytest.mark.xfail(strict=True, reason="UP-9.7-78")
def test_regression_validate_show_reports_non_finite_tempo() -> None:
    """Reports a tempo section whose tempo is infinite (audit 9.7 validation gaps)."""
    timeline = two_set_show().timeline
    tempo = timeline.tempos[0].model_copy(update={"counts_per_minute": math.inf})
    assert _reports_an_issue(_edited(timeline=timeline.model_copy(update={"tempos": (tempo,)})))


@pytest.mark.defect("UP-9.7-79")
@pytest.mark.xfail(strict=True, reason="UP-9.7-79")
def test_regression_validate_show_reports_non_finite_waypoints() -> None:
    """Reports a path waypoint with an infinite coordinate (audit 9.7 validation gaps)."""
    waypoint = Waypoint(x=1.0, y=1.0).model_copy(update={"x": math.inf})
    row = PathRow(entity="T1").model_copy(update={"waypoints": (waypoint,)})
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows={"T1": row})
    assert _reports_an_issue(_edited(transitions=(spec,)))


@pytest.mark.defect("UP-9.7-80")
@pytest.mark.xfail(strict=True, reason="UP-9.7-80")
def test_regression_validate_show_warns_about_placements_far_off_the_sheet() -> None:
    """Warns about a placement a million meters off the sheet (audit 9.7 validation gaps)."""
    moved = two_set_show().sets[1].with_placements({"T1": {"x": FAR_OFF_SHEET_M}}, force=True)
    assert _reports_an_issue(_edited(sets=_set_two_with(placements=moved.placements)))


@pytest.mark.defect("UP-9.7-81")
@pytest.mark.xfail(strict=True, reason="UP-9.7-81")
def test_regression_validate_show_reports_unpinned_sheet_without_a_store() -> None:
    """Reports an unpinned sheet even when no sheet store is passed (audit 9.7 validation gaps)."""
    assert "UNPINNED_SHEET" in _codes(validate_show(_edited(dependencies={})))


@pytest.mark.defect("UP-9.7-82")
@pytest.mark.xfail(strict=True, reason="UP-9.7-82")
def test_regression_validate_show_reports_an_empty_show_id() -> None:
    """Reports a show with an empty ID (audit 9.7 validation gaps)."""
    assert _reports_an_issue(_edited(id=""))


@pytest.mark.defect("UP-9.7-83")
@pytest.mark.xfail(strict=True, reason="UP-9.7-83")
def test_regression_validate_show_reports_set_ids_that_cannot_be_stored() -> None:
    """Reports a set ID that no workspace path can hold (audit 9.7 validation gaps)."""
    show = two_set_show()
    renamed = show.sets[1].model_copy(update={"id": "a/b"})
    assert _reports_an_issue(_edited(sets=(show.sets[0], renamed)))


@pytest.mark.defect("UP-9.7-84")
@pytest.mark.xfail(strict=True, reason="UP-9.7-84")
def test_regression_validate_show_reports_placements_of_inactive_spots() -> None:
    """Reports an on-field placement of a spot marked inactive (audit 9.7 validation gaps)."""
    cast = two_set_show().cast
    spots = (cast.spots[0].model_copy(update={"active": False}), *cast.spots[1:])
    assert _reports_an_issue(_edited(cast=cast.model_copy(update={"spots": spots})))


@pytest.mark.defect("UP-9.7-85")
@pytest.mark.xfail(strict=True, reason="UP-9.7-85")
def test_regression_validate_show_reports_a_nonzero_first_set_count() -> None:
    """Reports a first set that has counts (audit 9.7 validation gaps)."""
    show = two_set_show()
    first = show.sets[0].model_copy(update={"counts": 3.0})
    assert _reports_an_issue(_edited(sets=(first, show.sets[1])))
