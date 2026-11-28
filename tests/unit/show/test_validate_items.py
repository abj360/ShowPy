#!/usr/bin/env python3

"""
test_validate_items.py --- tests that set and path issues name the item they are about

Contains:
    test_regression_set_issues_name_their_item: puts the set or placement item, with the spot or
        form it names, in the context of every set issue
    test_regression_path_issues_name_their_item: puts the transition or path row item in the
        context of the counts and continuity issues of a transition
"""

from collections.abc import Callable

import pytest

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, PlacementPatch, Set
from e8s.choreo.path_models import PathRow, PathStyle, TransitionSpec
from e8s.core.ids import FormId, SetId, SpotId
from e8s.core.validation import ValidationReport
from e8s.show.change_models import ItemKey
from e8s.show.validate import validate_paths, validate_set, validate_show
from tests.fixtures.motion_shows import TID, with_targets
from tests.fixtures.recipes import LINE_RECIPE
from tests.fixtures.shows import two_set_show
from tests.helpers import replaced_form_show

TRANSITION_FOLDER = "1-2"


def _context(report: ValidationReport, code: str) -> dict[str, object]:
    (issue,) = [i for i in report.issues if i.code == code]
    context: dict[str, object] = dict(issue.context)
    context["item"] = ItemKey.model_validate(context["item"])
    return context


def _missing_and_unknown() -> ValidationReport:
    show = two_set_show()
    broken = Set(
        id=SetId("2"),
        counts=8.0,
        placements=(*show.set("2").placements[1:], Placement(entity=SpotId("Z9"), x=0, y=0)),
    )
    return validate_set(show, broken)


def _pinned_changed() -> ValidationReport:
    show = with_targets({"T1": {"pinned": True}})
    moved = show.set("2").with_placements({"T1": PlacementPatch(x=9.0)}, force=True)
    return validate_set(show, moved)


def _form_references() -> ValidationReport:
    show = two_set_show()
    form = Form(id=FormId("f"), set=SetId("1"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    show = show.replace(forms={"f": form})
    first = show.set("1").with_placements(
        {"T2": PlacementPatch(form=FormId("f"), order=0), "T3": PlacementPatch(form=FormId("zz"))}
    )
    return validate_set(show, first)


def _item(kind: str, path: tuple[str, ...], role: str | None = None) -> ItemKey:
    return ItemKey.model_validate({"kind": kind, "path": path, "role": role})


@pytest.mark.parametrize(
    ("report", "code", "expected"),
    [
        (
            _missing_and_unknown,
            "MISSING_SPOT",
            {"item": _item("set", ("2",), "placements"), "spot": "T1"},
        ),
        (
            _missing_and_unknown,
            "UNKNOWN_SPOT",
            {"item": _item("set", ("2",), "placements"), "spot": "Z9"},
        ),
        (_pinned_changed, "PINNED_CHANGED", {"item": _item("placement", ("2", "T1"), "pin")}),
        (
            _form_references,
            "NOT_IN_FORM",
            {"item": _item("placement", ("1", "T2"), "drill"), "form": "f"},
        ),
        (
            _form_references,
            "UNKNOWN_FORM",
            {"item": _item("placement", ("1", "T3"), "drill"), "form": "zz"},
        ),
        (
            lambda: validate_show(replaced_form_show()),
            "STALE_FORM",
            {"item": _item("set", ("2",), "forms"), "form": "a"},
        ),
    ],
    ids=["missing", "unknown", "pinned", "not_in_form", "unknown_form", "stale_form"],
)
def test_regression_set_issues_name_their_item(
    report: Callable[[], ValidationReport], code: str, expected: dict[str, object]
) -> None:
    """Puts the set or placement item, with the spot or form it names, in each set issue."""
    assert _context(report(), code) == expected


def test_regression_path_issues_name_their_item() -> None:
    """Puts the transition item on a counts mismatch and the row item on a continuity failure."""
    show = two_set_show()
    held = TransitionSpec(
        id=TID,
        counts=6.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), style=PathStyle.HOLD)},
    )
    report = validate_paths(show, held)
    assert _context(report, "COUNTS_MISMATCH") == {
        "item": _item("transition", (TRANSITION_FOLDER,)),
        "counts": 6.0,
        "expected": 8.0,
    }
    assert _context(report, "CONTINUITY") == {
        "item": _item("path_row", (TRANSITION_FOLDER, "T1")),
    }
