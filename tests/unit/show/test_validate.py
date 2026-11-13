#!/usr/bin/env python3

"""
test_validate.py --- tests structural validation of sets, paths, dependencies and whole shows
"""

from stage.choreo.models import Form, PathRow, PathStyle, Placement, Set, TransitionSpec
from stage.core.validation import ValidationReport
from stage.sheet.templates import InMemorySheetStore
from stage.show.validate import (
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
