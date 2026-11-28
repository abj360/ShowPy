"""
validate_values.py --- checks a show's stored values: counts, set order, keys, IDs, finite numbers

Each check reads the whole show and reports every problem as a ValidationIssue; none raises on
bad data.

Contains:
    MAX_NON_FINITE_ISSUES: caps how many non-finite values one report lists
    sequence_issues(): reports sets that arrive in 0 counts and paths of non-consecutive sets
    first_set_issues(): reports a first set that has counts
    negative_count_issues(): reports later sets with negative counts
    too_long_issues(): reports set counts that add up past MAX_COUNTS
    form_key_issues(): reports forms stored under a key other than their ID
    selection_issues(): reports named selections holding spots outside the cast
    id_issues(): reports show, set and form IDs that cannot be stored
    has_non_finite(): tells whether a value holds an infinite or NaN number anywhere inside
    non_finite_issues(): reports infinite and NaN numbers by their field path, skipping values
        shared with a show known to hold none
"""

import math
from collections.abc import Callable, Iterator, Mapping
from itertools import islice

from e8s.core.caps import MAX_COUNTS
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import check_id, check_sheet_id, format_tid
from e8s.core.types import FrozenModel
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.document_paths import is_storable_folder
from e8s.show.item_table import TIMING
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.validate_issue import ERROR, WARNING, make_issue

MAX_NON_FINITE_ISSUES = 100


def sequence_issues(show: Show) -> list[ValidationIssue]:
    """Reports later sets that arrive in 0 counts, then stored paths of non-consecutive sets.

    >>> from e8s.examples import two_set_show
    >>> sequence_issues(two_set_show())
    []
    """
    zero_counts = [
        make_issue(
            WARNING,
            ErrorCode.ZERO_COUNTS,
            f"set {s.id}",
            "set arrives in 0 counts",
            "give the set counts > 0",
        )
        for s in show.sets[1:]
        if s.counts == 0
    ]
    valid = set(show.transition_ids())
    stale = [
        make_issue(
            ERROR,
            ErrorCode.STALE_TRANSITION,
            format_tid(spec.id),
            "paths for sets that are not consecutive",
            "delete or re-create these paths",
        )
        for spec in show.transitions
        if spec.id not in valid
    ]
    return zero_counts + stale


def first_set_issues(show: Show) -> list[ValidationIssue]:
    """Reports a first set with finite counts other than 0, since no move leads into it.

    >>> from e8s.examples import two_set_show
    >>> first_set_issues(two_set_show())
    []
    """
    first = show.sets[0] if show.sets else None
    if first is None or first.counts == 0 or not math.isfinite(first.counts):
        return []
    item = ItemKey(kind="set", path=(set_key(first),), role=TIMING)
    return [
        make_issue(
            ERROR,
            ErrorCode.TIME_RANGE,
            f"set {first.id}",
            f"the first set has counts {first.counts:g}, but no move leads into it",
            "the first set starts the show; give it 0 counts",
            {"item": item.to_json(), "counts": first.counts},
        )
    ]


def negative_count_issues(show: Show) -> list[ValidationIssue]:
    """Reports later sets with finite negative counts; non-finite counts are left to NOT_FINITE.

    >>> from e8s.examples import two_set_show
    >>> negative_count_issues(two_set_show())
    []
    """
    return [
        make_issue(
            ERROR,
            ErrorCode.TIME_RANGE,
            f"set {later.id}",
            f"set {later.id} arrives in {later.counts:g} counts",
            "give every set after the first counts > 0",
            {
                "item": ItemKey(kind="set", path=(set_key(later),), role=TIMING).to_json(),
                "counts": later.counts,
            },
        )
        for later in show.sets[1:]
        if math.isfinite(later.counts) and later.counts < 0
    ]


def too_long_issues(show: Show) -> list[ValidationIssue]:
    """Reports finite set counts that add up past MAX_COUNTS, or overflow a float.

    >>> from e8s.examples import two_set_show
    >>> too_long_issues(two_set_show())
    []
    """
    try:
        total = math.fsum(s.counts for s in show.sets if math.isfinite(s.counts))
    except OverflowError:
        total = math.inf
    if total <= MAX_COUNTS:
        return []
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.SHOW_TOO_LONG,
            where="sets",
            message=f"set counts add up to {total:g}, past MAX_COUNTS",
            hint=f"keep the whole show within MAX_COUNTS ({MAX_COUNTS:,} counts)",
            context={"total": total if math.isfinite(total) else str(total), "max": MAX_COUNTS},
        )
    ]


def form_key_issues(show: Show) -> list[ValidationIssue]:
    """Reports forms stored under a key other than their own ID.

    >>> from e8s.examples import two_set_show
    >>> form_key_issues(two_set_show())
    []
    """
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.KEY_MISMATCH,
            where=f"form {key}",
            message=f"form stored under {key!r} has id {form.id!r}",
            hint="store every form under its own id",
            context={"key": key, "id": form.id},
        )
        for key, form in show.forms.items()
        if key != form.id
    ]


def selection_issues(show: Show) -> list[ValidationIssue]:
    """Reports named selections holding spots outside the cast, in selection then spot order.

    >>> from e8s.examples import two_set_show
    >>> selection_issues(two_set_show())
    []
    """
    cast_ids = set(show.cast.ids())
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.UNKNOWN_SPOT,
            where=f"selection {name}",
            message=f"{spot} is not in the cast",
            hint="remove the spot from the selection or add it to the cast",
            context={"path": f"cast.selections.{name}", "spot": spot},
        )
        for name, members in show.cast.selections.items()
        for spot in sorted(members)
        if spot not in cast_ids
    ]


def _bad_id(kind: str, path: str, value: str, error: E8sError) -> ValidationIssue:
    return ValidationIssue(
        level=ERROR,
        code=ErrorCode.BAD_ID,
        where=f"{kind} {value!r}",
        message=f"{kind} ID {value!r} cannot be stored",
        hint=error.hint,
        context={"path": path, "id": value},
    )


def _checked(
    kind: str, path: str, value: str, check: Callable[[str], str]
) -> list[ValidationIssue]:
    try:
        check(value)
    except SchemaError as error:
        return [_bad_id(kind, path, value, error)]
    return []


def _set_id_issue(index: int, set_id: str) -> list[ValidationIssue]:
    if is_storable_folder(set_id):
        return []
    return _checked("set", f"sets[{index}].id", set_id, lambda text: check_id(text, kind="set"))


def id_issues(show: Show) -> list[ValidationIssue]:
    """Reports the show ID, then set IDs, then form IDs and keys that cannot be stored.

    >>> from e8s.examples import two_set_show
    >>> id_issues(two_set_show())
    []
    """
    issues = _checked("show", "id", show.id, lambda text: check_sheet_id(text, kind="show"))
    for index, candidate in enumerate(show.sets):
        issues += _set_id_issue(index, candidate.id)
    for key, form in show.forms.items():
        issues += _checked("form", f"forms.{key}.id", form.id, lambda t: check_id(t, kind="form"))
        if key != form.id:
            issues += _checked("form", f"forms.{key}", key, lambda t: check_id(t, kind="form"))
    return issues


def _non_finite_values(value: object, path: str, known: object) -> Iterator[tuple[str, float]]:
    if value is known:
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            yield path, value
    elif isinstance(value, FrozenModel):
        same = type(known) is type(value)
        for name in type(value)._field_names():
            inner = getattr(known, name) if same else None
            yield from _non_finite_values(
                getattr(value, name), f"{path}.{name}" if path else name, inner
            )
    elif isinstance(value, Mapping):
        for key, item in value.items():
            inner = known.get(key) if isinstance(known, Mapping) else None
            yield from _non_finite_values(item, f"{path}.{key}", inner)
    elif isinstance(value, tuple | list):
        for index, item in enumerate(value):
            inner = known[index] if isinstance(known, tuple | list) and index < len(known) else None
            yield from _non_finite_values(item, f"{path}[{index}]", inner)


def has_non_finite(value: object, known: object = None) -> bool:
    """Tells whether a value holds an infinite or NaN number in any field, item or entry.

    Args:
        value: The model, mapping, sequence or number to search.
        known: The value's counterpart in a show that holds no non-finite number; parts of
            `value` that are the very objects found at the same place in it are skipped.

    Returns:
        found: Whether a non-finite number was found.

    >>> from e8s.examples import two_set_show
    >>> has_non_finite(two_set_show())
    False
    """
    return next(_non_finite_values(value, "", known), None) is not None


def non_finite_issues(show: Show, known: Show | None = None) -> list[ValidationIssue]:
    """Reports the first MAX_NON_FINITE_ISSUES infinite or NaN numbers by field path.

    Args:
        show: The show to search, in field order.
        known: An earlier show that holds no non-finite number; values of `show` that are the
            very objects at the same place in it are skipped, so only edited values are read.

    Returns:
        issues: One NOT_FINITE issue per value found, in field order.

    >>> from e8s.examples import two_set_show
    >>> non_finite_issues(two_set_show())
    []
    """
    found = islice(_non_finite_values(show, "", known), MAX_NON_FINITE_ISSUES)
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.NOT_FINITE,
            where=path,
            message=f"{path} is {value}",
            hint="use a finite number",
            context={"path": path, "value": str(value)},
        )
        for path, value in found
    ]
