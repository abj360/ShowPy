"""
merge_refs.py --- finds references a merge leaves dangling that the shows it started from did not

A proposal can name a spot the cast never held, or the head can delete a spot the proposal names
in a form member or path row. Either way the merged show holds a reference that does not resolve;
each such reference becomes a merge conflict on its item.

Contains:
    dangling_conflicts(): lists the reference errors a merged show holds that neither the base nor
        the head held
"""

from e8s.core.error_codes import ErrorCode
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey, MergeConflict
from e8s.show.models import Show
from e8s.show.validate_refs import ERROR, path_spot_issues, reference_issues

type _IssueKey = tuple[str, str, str]

# Only references to items that do not exist conflict. A form op never re-places its members, so
# MEMBER_MISMATCH is the next placement edit's job, and pin flags (PIN_MISMATCH) are the lock
# check's, which refuses or previews them on its own.
DANGLING_CODES = frozenset(
    {ErrorCode.UNKNOWN_SPOT, ErrorCode.UNKNOWN_SET, ErrorCode.UNKNOWN_FORM, ErrorCode.UNKNOWN_PIN}
)


def _reference_errors(show: Show) -> list[ValidationIssue]:
    issues = reference_issues(show)
    for tid in show.transition_ids():
        stored = show.lookup.stored.get(tid)
        if stored is not None:
            issues += path_spot_issues(show, stored)
    return [i for i in issues if i.level == ERROR and i.code in DANGLING_CODES]


def _key(issue: ValidationIssue) -> _IssueKey:
    return (str(issue.code), issue.where, issue.message)


def dangling_conflicts(base: Show, head: Show, merged: Show) -> list[MergeConflict]:
    """Lists the reference errors a merged show holds that neither the base nor the head held.

    An error the base or the head already held predates the proposal and is left to validation;
    one the proposal brings, alone or together with the head's edits, is a conflict.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> dangling_conflicts(show, show, show)
    []

    Args:
        base: The show the proposal was made against.
        head: The show the proposal is merged onto.
        merged: The merge of the proposal into the head.

    Returns:
        conflicts: One conflict per new error, keyed by the item the error is about.
    """
    held = {_key(i) for side in (base, head) for i in _reference_errors(side)}
    return [
        MergeConflict(
            key=ItemKey.from_json(issue.context["item"]),
            reason=f"the merge leaves it dangling: {issue.message}",
        )
        for issue in _reference_errors(merged)
        if _key(issue) not in held
    ]
