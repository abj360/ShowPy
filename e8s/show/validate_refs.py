"""
validate_refs.py --- checks that form, set and spot references inside a show resolve

Every issue carries the stored item it is about in `context["item"]`, so a consumer can map the
issue to the item without parsing text. Item paths use set keys, as the item table does.

Contains:
    form_issues(): reports one form's unknown members, missing set and unpointed members
    set_form_issues(): reports sets listing forms the show does not store
    override_issues(): reports facing overrides naming unknown spots or outside the show
    reference_issues(): reports forms, sets, pins and facing overrides whose references do not
        resolve
"""

import math
from collections.abc import Iterator

from e8s.cast.select import select
from e8s.choreo.engine import ALL
from e8s.choreo.form_models import Form
from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError
from e8s.core.selection import Selection
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.set_queries import index
from e8s.show.validate_issue import ERROR, item_context, make_issue
from e8s.show.validate_pins import pin_issues

FORMS_ROLE = "forms"
DRILL_ROLE = "drill"


def _form_item(key: str) -> ItemKey:
    return ItemKey(kind="form", path=(key,))


def _unknown_set(key: str, form: Form) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SET,
        f"form {key}",
        f"form {key} belongs to set {form.set}, which does not exist",
        "move the form to an existing set or remove it",
        item_context(_form_item(key), set=form.set),
    )


def _unknown_member(key: str, spot: str) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"form {key}",
        f"form {key} lists {spot}, which is not in the cast",
        "remove the member or add the spot to the cast",
        item_context(_form_item(key), spot=spot),
    )


def _member_mismatch(key: str, owner: Set, spot: str) -> ValidationIssue:
    placed = owner.get(spot).form
    found = f"names form {placed}" if placed else "names no form"
    return make_issue(
        ERROR,
        ErrorCode.MEMBER_MISMATCH,
        f"set {owner.id} {spot}",
        f"{spot} is a member of form {key}, but its placement in set {owner.id} {found}",
        "re-place the set's forms, or remove the member",
        item_context(
            ItemKey(kind="placement", path=(set_key(owner), spot), role=DRILL_ROLE), form=key
        ),
    )


def form_issues(show: Show, key: str, form: Form) -> list[ValidationIssue]:
    """Reports one stored form's members outside the cast, missing set and unpointed members.

    >>> from e8s.choreo.forms import line_form
    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> [str(i.code) for i in form_issues(show, "row", line_form("row", ["Z9"], (0, 1), (2, 1)))]
    ['UNKNOWN_SPOT', 'UNKNOWN_SET']

    Args:
        show: The show that stores the form.
        key: The key the form is stored under.
        form: The form.

    Returns:
        issues: Members outside the cast, then a missing set or each member whose placement in
            the form's set does not name the form.
    """
    return list(_form_issues(show, key, form))


def _form_issues(show: Show, key: str, form: Form) -> Iterator[ValidationIssue]:
    cast_ids = set(show.cast.ids())
    members = [m for m in form.members if m is not None]
    for spot in members:
        if spot not in cast_ids:
            yield _unknown_member(key, spot)
    if form.set not in show.set_ids():
        yield _unknown_set(key, form)
        return
    owner = show.set(form.set)
    if key not in forms_of_set(show, owner.id):
        return
    placed = set(owner.entity_ids())
    for spot in members:
        if spot in placed and owner.get(spot).form != form.id:
            yield _member_mismatch(key, owner, spot)


def set_form_issues(show: Show) -> list[ValidationIssue]:
    """Reports, set by set, the forms a set lists that the show does not store.

    >>> from e8s.examples import two_set_show
    >>> set_form_issues(two_set_show())
    []
    """
    return [issue for owner in show.sets for issue in _set_issues(show, owner)]


def _set_issues(show: Show, owner: Set) -> Iterator[ValidationIssue]:
    for form_id in owner.forms:
        if form_id not in show.forms:
            yield make_issue(
                ERROR,
                ErrorCode.UNKNOWN_FORM,
                f"set {owner.id}",
                f"set {owner.id} lists form {form_id}, which does not exist",
                "remove the form from the set or restore it",
                item_context(
                    ItemKey(kind="set", path=(set_key(owner),), role=FORMS_ROLE), form=form_id
                ),
            )


OVERRIDES_ITEM = ItemKey(kind="facing_override", path=())


def _override_spot(spot: str, number: int) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"facing override {number}",
        f"facing override {number} names {spot}, which is not in the cast",
        "remove the spot from the override or add it to the cast",
        item_context(OVERRIDES_ITEM, spot=spot, override=number),
    )


def _override_selector(number: int, error: E8sError) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"facing override {number}",
        f"facing override {number} selects spots that do not resolve: {error.message}",
        error.hint,
        item_context(OVERRIDES_ITEM, override=number),
    )


def _override_spots(show: Show, override: FacingOverride, number: int) -> Iterator[ValidationIssue]:
    entities = override.entities
    if entities == ALL:
        return
    if isinstance(entities, str):
        try:
            select(show, entities)
        except E8sError as error:
            yield _override_selector(number, error)
        return
    cast_ids = set(show.cast.ids())
    ids = entities.ids if isinstance(entities, Selection) else entities
    for spot in ids:
        if spot not in cast_ids:
            yield _override_spot(spot, number)


def _past_the_show(override: FacingOverride, number: int, total: float) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.PAST_THE_SHOW,
        f"facing override {number}",
        f"facing override {number} starts at count {override.start:g}, but the show ends at"
        f" {total:g}, so it covers no show time",
        f"start the override at a count from 0 to before {total:g}, or remove it",
        item_context(OVERRIDES_ITEM, override=number, start=override.start, total=total),
    )


def _before_the_show(override: FacingOverride, number: int) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.TIME_RANGE,
        f"facing override {number}",
        f"facing override {number} starts at count {override.start:g}, before the show starts",
        "start the override at count 0 or later",
        item_context(OVERRIDES_ITEM, override=number, start=override.start),
    )


def _show_total(show: Show) -> float | None:
    try:
        total = index(show).total_counts
    except E8sError:
        return None
    return total if math.isfinite(total) else None


def override_issues(show: Show) -> list[ValidationIssue]:
    """Reports facing overrides naming unknown spots or starting outside the show's counts.

    >>> from e8s.examples import two_set_show
    >>> override_issues(two_set_show())
    []
    """
    return list(_override_issues(show))


def _override_issues(show: Show) -> Iterator[ValidationIssue]:
    total = _show_total(show)
    for number, override in enumerate(show.facing_overrides):
        yield from _override_spots(show, override, number)
        if override.start < 0:
            yield _before_the_show(override, number)
        elif total is not None and override.start >= total:
            yield _past_the_show(override, number, total)


def reference_issues(show: Show) -> list[ValidationIssue]:
    """Reports forms and sets whose references do not resolve.

    Covers form members outside the cast, forms of missing sets, sets listing missing forms,
    members whose placements do not point back to their form, pins that name missing items or
    leave their flags unset, and facing overrides naming unknown spots or starting before count
    0 or on or after the show's last count.

    >>> from e8s.examples import two_set_show
    >>> reference_issues(two_set_show())
    []
    """
    issues = [i for key, form in show.forms.items() for i in _form_issues(show, key, form)]
    issues += set_form_issues(show)
    if show.pins:
        issues += pin_issues(show, show.pins)
    return issues + override_issues(show)
