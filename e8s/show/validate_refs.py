"""
validate_refs.py --- checks that form, set and spot references inside a show resolve

Every issue carries the stored item it is about in `context["item"]`, so a consumer can map the
issue to the item without parsing text. Item paths use set keys, as the item table does.

Contains:
    Level: names the two issue levels
    ERROR: marks an issue that blocks
    WARNING: marks an issue that only informs
    make_issue(): builds a validation issue, with optional context
    reference_issues(): reports forms and sets whose references do not resolve
"""

from collections.abc import Iterator
from typing import Literal

from e8s.choreo.form_models import Form
from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import Set
from e8s.core.error_codes import ErrorCode
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.set_keys import set_key

type Level = Literal["error", "warning"]
ERROR: Level = "error"
WARNING: Level = "warning"

FORMS_ROLE = "forms"
DRILL_ROLE = "drill"


def make_issue(
    level: Level,
    code: ErrorCode,
    where: str,
    message: str,
    hint: str,
    context: dict[str, JsonValue] | None = None,
) -> ValidationIssue:
    """Builds a validation issue from its parts; context defaults to empty."""
    return ValidationIssue(
        level=level,
        code=code,
        where=where,
        message=message,
        hint=hint,
        context=context or {},
    )


def _item(key: ItemKey, **extra: JsonValue) -> dict[str, JsonValue]:
    return {"item": key.to_json(), **extra}


def _form_item(key: str) -> ItemKey:
    return ItemKey(kind="form", path=(key,))


def _unknown_set(key: str, form: Form) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SET,
        f"form {key}",
        f"form {key} belongs to set {form.set}, which does not exist",
        "move the form to an existing set or remove it",
        _item(_form_item(key), set=form.set),
    )


def _unknown_member(key: str, spot: str) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"form {key}",
        f"form {key} lists {spot}, which is not in the cast",
        "remove the member or add the spot to the cast",
        _item(_form_item(key), spot=spot),
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
        _item(ItemKey(kind="placement", path=(set_key(owner), spot), role=DRILL_ROLE), form=key),
    )


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
    if form.id not in forms_of_set(show, owner.id):
        return
    placed = set(owner.entity_ids())
    for spot in members:
        if spot in placed and owner.get(spot).form != form.id:
            yield _member_mismatch(key, owner, spot)


def _set_issues(show: Show, owner: Set) -> Iterator[ValidationIssue]:
    for form_id in owner.forms:
        if form_id not in show.forms:
            yield make_issue(
                ERROR,
                ErrorCode.UNKNOWN_FORM,
                f"set {owner.id}",
                f"set {owner.id} lists form {form_id}, which does not exist",
                "remove the form from the set or restore it",
                _item(ItemKey(kind="set", path=(set_key(owner),), role=FORMS_ROLE), form=form_id),
            )


def reference_issues(show: Show) -> list[ValidationIssue]:
    """Reports forms and sets whose references do not resolve.

    Covers form members outside the cast, forms of missing sets, sets listing missing forms and
    members whose placements do not point back to their form.

    >>> from e8s.examples import two_set_show
    >>> reference_issues(two_set_show())
    []
    """
    issues = [i for key, form in show.forms.items() for i in _form_issues(show, key, form)]
    return issues + [i for owner in show.sets for i in _set_issues(show, owner)]
