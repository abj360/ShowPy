"""
revalidate_kept.py --- sorts a previous validation report's issues by the unit that reruns them

Each issue names its stored item in `context["item"]`; the item kind and the issue code say
which check produced it, so revalidation keeps it under that check's bucket and the unit the
check reruns by: a set, transition, form or pin, or one bucket for all facing overrides. An
issue that cannot be placed makes revalidation fall back to a full validation.

Contains:
    UnboundedError: signals that an edit or a previous report cannot be bounded by item
    item_key(): returns the stored item an issue names, or None when it names none
    kept_issues(): sorts a previous report's issues by bucket and unit
"""

from collections.abc import Callable, Hashable, Mapping, Sequence

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError
from e8s.core.validation import ValidationIssue, ValidationReport
from e8s.show.change_models import ItemKey

type Kept = Mapping[Hashable, Sequence[ValidationIssue]]

TRANSITION_KINDS = frozenset({"path_row", "route", "transition"})
SET_CHECK_CODES = frozenset({ErrorCode.MISSING_SPOT, ErrorCode.UNKNOWN_SPOT, ErrorCode.STALE_FORM})
RERUN_SET_CODES = frozenset({ErrorCode.UNKNOWN_FORM, ErrorCode.TIME_RANGE})
SETS = "sets"
TRANSITIONS = "transitions"
FORM_ISSUES = "forms"
PINS = "pins"
OVERRIDES = "overrides"
INACTIVE = "inactive"
FAR_OFF = "far_off"
PLACEMENT_BUCKETS: Mapping[str, str] = {
    ErrorCode.MEMBER_MISMATCH: FORM_ISSUES,
    ErrorCode.INACTIVE_ON_FIELD: INACTIVE,
    ErrorCode.FAR_OFF_SHEET: FAR_OFF,
    ErrorCode.UNKNOWN_FORM: SETS,
    ErrorCode.NOT_IN_FORM: SETS,
}
FORM_FIELD = "form"


class UnboundedError(Exception):
    """Signals that the edit or the previous report cannot be bounded by item."""


def item_key(issue: ValidationIssue) -> ItemKey | None:
    """Returns the stored item an issue names, or None when it names none.

    Raises UnboundedError when the item does not parse.
    """
    raw = issue.context.get("item")
    if raw is None:
        return None
    try:
        return ItemKey.from_json(raw)
    except E8sError as error:
        raise UnboundedError from error


def _placement_slot(issue: ValidationIssue, key: ItemKey) -> tuple[str, Hashable]:
    bucket = PLACEMENT_BUCKETS.get(issue.code)
    if bucket is None:
        raise UnboundedError
    if bucket != FORM_ISSUES:
        return bucket, key.path[0]
    form = issue.context.get(FORM_FIELD)
    if not isinstance(form, str):
        raise UnboundedError
    return bucket, form


def _set_slot(issue: ValidationIssue, key: ItemKey) -> tuple[str, Hashable] | None:
    if issue.code in SET_CHECK_CODES:
        return SETS, key.path[0]
    if issue.code in RERUN_SET_CODES:
        return None
    raise UnboundedError


def _slot(issue: ValidationIssue) -> tuple[str, Hashable] | None:
    """Finds the bucket and unit a previous issue is kept under, or None for one always rerun.

    Raises UnboundedError for a non-finite issue, an unreadable item, or an item kind or code
    the buckets do not hold.

    Args:
        issue: One issue of the previous report.

    Returns:
        slot: The bucket name and the unit within it, such as a set key or a pin's item path.
    """
    if issue.code == ErrorCode.NOT_FINITE:
        raise UnboundedError
    key = item_key(issue)
    if key is None:
        return None
    if key.kind in TRANSITION_KINDS:
        return TRANSITIONS, key.path[0]
    slots: Mapping[str, Callable[[], tuple[str, Hashable] | None]] = {
        "placement": lambda: _placement_slot(issue, key),
        "set": lambda: _set_slot(issue, key),
        "form": lambda: (FORM_ISSUES, key.path[0]),
        "pin": lambda: (PINS, key.path),
        "facing_override": lambda: (OVERRIDES, ()),
    }
    if key.kind not in slots:
        raise UnboundedError
    return slots[key.kind]()


def kept_issues(previous: ValidationReport) -> dict[str, Kept]:
    """Sorts a previous report's issues by bucket, then by the unit whose check reruns them.

    Issues of checks that always rerun are left out. Raises UnboundedError when an issue
    cannot be placed.

    Args:
        previous: validate_show's report of the show before the edit.

    Returns:
        kept: Bucket name to unit to that unit's issues, in report order.
    """
    kept: dict[str, dict[Hashable, list[ValidationIssue]]] = {}
    for issue in previous.issues:
        slot = _slot(issue)
        if slot is not None:
            kept.setdefault(slot[0], {}).setdefault(slot[1], []).append(issue)
    return dict(kept)
