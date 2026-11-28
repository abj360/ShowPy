"""
validate_comments.py --- checks that each review comment's anchor still points at the show

Comments live in the canvas document, not in the show, so these issues name the comment in
`context["comment"]` instead of a show item.

Contains:
    comment_issues(): reports comment anchors whose set, transition or spots do not resolve
"""

from collections.abc import Iterator, Sequence

from e8s.core.error_codes import ErrorCode
from e8s.core.ids import TransitionId, format_tid
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue
from e8s.show.collab_models import Comment
from e8s.show.models import Show
from e8s.show.validate_issue import ERROR, make_issue

REANCHOR_HINT = "re-anchor the comment to an item the show holds, or resolve and delete it"


def _issue(
    comment: Comment, code: ErrorCode, message: str, **context: JsonValue
) -> ValidationIssue:
    return make_issue(
        ERROR,
        code,
        f"comment {comment.id}",
        f"comment {comment.id} {message}",
        REANCHOR_HINT,
        {"comment": comment.id, "anchor": comment.anchor.kind, **context},
    )


def _missing_set(comment: Comment, set_id: str) -> ValidationIssue:
    message = f"is anchored to set {set_id}, which does not exist"
    return _issue(comment, ErrorCode.UNKNOWN_SET, message, set=set_id)


def _transition_issues(
    show: Show, comment: Comment, tid: TransitionId
) -> Iterator[ValidationIssue]:
    known = show.lookup.positions
    missing = [set_id for set_id in tid if set_id not in known]
    for set_id in missing:
        yield _missing_set(comment, set_id)
    if missing or tid in show.lookup.consecutive:
        return
    text = format_tid(tid)
    message = f"is anchored to transition {text}, whose sets are not consecutive"
    yield _issue(comment, ErrorCode.STALE_TRANSITION, message, tid=text)


def _spot_issues(show: Show, comment: Comment, spots: Sequence[str]) -> Iterator[ValidationIssue]:
    cast_ids = set(show.cast.ids())
    for spot in spots:
        if spot not in cast_ids:
            message = f"is anchored to {spot}, which is not in the cast"
            yield _issue(comment, ErrorCode.UNKNOWN_SPOT, message, spot=spot)


def _anchor_issues(show: Show, comment: Comment) -> Iterator[ValidationIssue]:
    anchor = comment.anchor
    if anchor.set_id is not None and anchor.set_id not in show.lookup.positions:
        yield _missing_set(comment, anchor.set_id)
    if anchor.tid is not None:
        yield from _transition_issues(show, comment, anchor.tid)
    if anchor.spots is not None:
        yield from _spot_issues(show, comment, anchor.spots)


def comment_issues(show: Show, comments: Sequence[Comment]) -> list[ValidationIssue]:
    """Reports comment anchors whose set, transition or spots the show does not hold.

    A set anchor needs its set; a transition anchor needs both sets, one right after the other;
    a spots anchor needs every spot in the cast. Point and area anchors always resolve.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.anchors import Anchor
    >>> from e8s.show.collab_models import Comment
    >>> lost = Comment(id="c1", anchor=Anchor.on_set("9"), text="tighten the arc")
    >>> [str(i.code) for i in comment_issues(two_set_show(), [lost])]
    ['UNKNOWN_SET']

    Args:
        show: The show the comments are about.
        comments: Review comments, such as the canvas comments document holds.

    Returns:
        issues: One issue per unresolved reference, in comment order.
    """
    return [issue for comment in comments for issue in _anchor_issues(show, comment)]
