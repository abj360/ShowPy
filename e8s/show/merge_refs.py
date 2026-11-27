"""
merge_refs.py --- finds references a merge leaves dangling that neither side left alone

Two sides can each be sound while their merge is not: one side deletes a spot, the other adds a
form member or a path row naming it. Such a reference becomes a merge conflict on its item.

Contains:
    dangling_conflicts(): lists the reference errors a merged show holds that neither side held
"""

from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey, MergeConflict
from e8s.show.models import Show
from e8s.show.validate_refs import ERROR, path_spot_issues, reference_issues

type _IssueKey = tuple[str, str, str]


def _reference_errors(show: Show) -> list[ValidationIssue]:
    issues = reference_issues(show)
    for tid in show.transition_ids():
        stored = show.lookup.stored.get(tid)
        if stored is not None:
            issues += path_spot_issues(show, stored)
    return [i for i in issues if i.level == ERROR]


def _key(issue: ValidationIssue) -> _IssueKey:
    return (str(issue.code), issue.where, issue.message)


def dangling_conflicts(ours: Show, theirs: Show, merged: Show) -> list[MergeConflict]:
    """Lists the reference errors a merged show holds that neither side held on its own.

    An error either side already held is that side's own problem and is left to validation.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> dangling_conflicts(show, show, show)
    []

    Args:
        ours: One side of the merge, such as the head.
        theirs: The other side, such as the base with a proposal applied.
        merged: The merge of the two.

    Returns:
        conflicts: One conflict per new error, keyed by the item the error is about.
    """
    held = {_key(i) for side in (ours, theirs) for i in _reference_errors(side)}
    return [
        MergeConflict(
            key=ItemKey.from_json(issue.context["item"]),
            reason=f"the merge leaves it dangling: {issue.message}",
        )
        for issue in _reference_errors(merged)
        if _key(issue) not in held
    ]
