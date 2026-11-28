#!/usr/bin/env python3

"""
test_validate_comments.py --- tests the comment anchor checks of validate_show

Contains:
    test_regression_comment_on_a_missing_set_is_unknown_set: reports a set anchor whose set does not
        exist, naming the comment and the set
    test_regression_comment_on_a_transition_checks_both_sets_and_their_order: reports each missing
        set of a transition anchor, and a pair of sets that are not consecutive
    test_regression_comment_on_spots_reports_each_unknown_spot: reports every anchored spot the
        cast does not hold
    test_comment_anchors_that_resolve_report_nothing: reports nothing for anchors that resolve and
        for point and area anchors
    test_regression_validate_show_reports_comment_issues_only_when_given_comments: adds comment
        issues to the report only for the comments passed in
"""

from e8s.core.types import Region
from e8s.core.validation import ValidationIssue
from e8s.examples import two_set_show
from e8s.show.anchors import Anchor
from e8s.show.collab_models import Comment
from e8s.show.manage import add_set
from e8s.show.validate import validate_show
from e8s.show.validate_comments import comment_issues

GHOST = "GHOST"
THIRD_SET = "3"
THIRD_SET_COUNTS = 4.0


def _comment(anchor: Anchor, comment_id: str = "c1") -> Comment:
    return Comment(id=comment_id, anchor=anchor, text="check")


def _summary(issues: list[ValidationIssue]) -> list[tuple[str, str, str]]:
    return [(str(i.code), i.level, i.where) for i in issues]


def test_regression_comment_on_a_missing_set_is_unknown_set() -> None:
    """Reports a set anchor whose set does not exist, naming the comment and the set."""
    (issue,) = comment_issues(two_set_show(), (_comment(Anchor.on_set(GHOST)),))
    assert _summary([issue]) == [("UNKNOWN_SET", "error", "comment c1")]
    assert GHOST in issue.message
    assert dict(issue.context) == {"comment": "c1", "anchor": "set", "set": GHOST}


def test_regression_comment_on_a_transition_checks_both_sets_and_their_order() -> None:
    """Reports each missing set of a transition anchor, and sets that are not consecutive."""
    show = add_set(two_set_show(), THIRD_SET, THIRD_SET_COUNTS)
    missing = comment_issues(show, (_comment(Anchor.on_transition((GHOST, "x"))),))
    assert [i.context["set"] for i in missing] == [GHOST, "x"]
    assert {i.code for i in missing} == {"UNKNOWN_SET"}
    (skipping,) = comment_issues(show, (_comment(Anchor.on_transition(("1", THIRD_SET))),))
    assert skipping.code == "STALE_TRANSITION"
    assert dict(skipping.context) == {"comment": "c1", "anchor": "transition", "tid": "1→3"}


def test_regression_comment_on_spots_reports_each_unknown_spot() -> None:
    """Reports every anchored spot the cast does not hold, one issue each."""
    anchor = Anchor.on_spots(["T1", GHOST, "ZZ"])
    issues = comment_issues(two_set_show(), (_comment(anchor),))
    assert [(i.code, i.context["spot"]) for i in issues] == [
        ("UNKNOWN_SPOT", GHOST),
        ("UNKNOWN_SPOT", "ZZ"),
    ]


def test_comment_anchors_that_resolve_report_nothing() -> None:
    """Reports nothing for anchors that resolve, nor for point and area anchors."""
    area = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)))
    anchors = (
        Anchor.on_set("1"),
        Anchor.on_transition(("1", "2")),
        Anchor.on_spots(["T1", "T2"]),
        Anchor.at_point((1e5, 1e5)),
        Anchor.on_area(area),
    )
    comments = tuple(_comment(a, f"c{n}") for n, a in enumerate(anchors))
    assert comment_issues(two_set_show(), comments) == []


def test_regression_validate_show_reports_comment_issues_only_when_given_comments() -> None:
    """Adds comment issues to the report only for the comments passed in."""
    show = two_set_show()
    comment = _comment(Anchor.on_set(GHOST))
    assert validate_show(show).ok
    report = validate_show(show, comments=(comment,))
    assert [i.code for i in report.issues] == ["UNKNOWN_SET"]
    assert not report.ok
