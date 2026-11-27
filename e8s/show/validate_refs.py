"""
validate_refs.py --- checks that form, set and spot references inside a show resolve

Every issue carries the stored item it is about in `context["item"]`, so a consumer can map the
issue to the item without parsing text. Item paths use set keys, as the item table does.

Contains:
    Level: names the two issue levels
    ERROR: marks an issue that blocks
    WARNING: marks an issue that only informs
    make_issue(): builds a validation issue, with optional context
    reference_issues(): reports forms, sets, pins and facing overrides whose references do not
        resolve
"""

import math
from collections.abc import Iterator, Mapping
from typing import Literal

from e8s.cast.select import select
from e8s.choreo.form_models import Form
from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import parse_tid
from e8s.core.json_types import JsonValue
from e8s.core.selection import Selection
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.document_paths import transition_folder
from e8s.show.models import Pin, Show
from e8s.show.pinning import _covers_form, _covers_placement
from e8s.show.set_keys import set_key
from e8s.show.set_queries import index

type Level = Literal["error", "warning"]
ERROR: Level = "error"
WARNING: Level = "warning"

FORMS_ROLE = "forms"
DRILL_ROLE = "drill"
MAX_NAMED_UNFLAGGED = 5
ALL_SPOTS = "all"


def make_issue(
    level: Level,
    code: ErrorCode,
    where: str,
    message: str,
    hint: str,
    context: dict[str, JsonValue] | None = None,
) -> ValidationIssue:
    """Builds a validation issue from its parts.

    Args:
        level: Whether the issue blocks or only informs.
        code: Stable issue code.
        where: Location of the problem.
        message: What is wrong.
        hint: What would make it valid.
        context: JSON values that help debug the issue; empty when None.

    Returns:
        issue: The validation issue.
    """
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
    if key not in forms_of_set(show, owner.id):
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


def _pin_item_id(keys: Mapping[str, str], record: Pin) -> str:
    if record.target == "set":
        return keys.get(record.id, record.id)
    if record.target != "transition":
        return record.id
    try:
        first, second = parse_tid(record.id)
    except SchemaError:
        return record.id
    return transition_folder((keys.get(first, first), keys.get(second, second)))


def _pin_item(keys: Mapping[str, str], record: Pin) -> ItemKey:
    return ItemKey(kind="pin", path=(record.target, _pin_item_id(keys, record)))


def _pin_exists(show: Show, record: Pin) -> bool:
    if record.target == "entity":
        return record.id in show.cast.ids()
    if record.target == "form":
        return record.id in show.forms
    if record.target == "set":
        return record.id in show.set_ids()
    try:
        show.transition(parse_tid(record.id))
    except E8sError:
        return False
    return True


def _unresolved_pin(record: Pin, item: ItemKey, code: ErrorCode, hint: str) -> ValidationIssue:
    return make_issue(
        ERROR,
        code,
        f"pin {record.target} {record.id}",
        f"the {record.target} pin on {record.id} names something the show does not hold",
        hint,
        _item(item, target=record.target, id=record.id),
    )


def _bad_transition_pin(record: Pin, item: ItemKey) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.BAD_TRANSITION,
        f"pin transition {record.id}",
        f"the transition pin {record.id!r} does not parse as a transition ID",
        "write transitions as 12→13 or 12->13, or remove the pin",
        _item(item, target=record.target, id=record.id),
    )


def _unflagged_placements(show: Show, record: Pin) -> list[str]:
    held = (record,)
    found: list[str] = []
    for owner in show.sets:
        if record.target == "set" and owner.id != record.id:
            continue
        for placement in owner.placements:
            if record.target == "entity" and placement.entity != record.id:
                continue
            if _covers_placement(held, owner.id, placement.entity) and not placement.pinned:
                found.append(f"{owner.id} {placement.entity}")
    return found


def _unflagged_forms(show: Show, record: Pin) -> list[str]:
    held = (record,)
    forms = {k: f for k, f in show.forms.items() if k == record.id}
    if record.target == "set":
        forms = dict(forms_of_set(show, record.id))
    return [k for k, f in forms.items() if _covers_form(show, held, k) and not f.locked]


def _unflagged_rows(show: Show, record: Pin) -> list[str]:
    # a spot added after the pin has no row yet, and adding it is a legal edit
    rows = show.transition(parse_tid(record.id)).rows
    return [entity for entity, row in rows.items() if not row.locked]


def _unflagged(show: Show, record: Pin) -> list[str]:
    if record.target == "transition":
        return _unflagged_rows(show, record)
    found = [] if record.target == "form" else _unflagged_placements(show, record)
    return found + (_unflagged_forms(show, record) if record.target in ("form", "set") else [])


def _pin_mismatch(record: Pin, item: ItemKey, unflagged: list[str]) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.PIN_MISMATCH,
        f"pin {record.target} {record.id}",
        f"the {record.target} pin on {record.id} leaves {len(unflagged)} locked item(s) unflagged:"
        f" {', '.join(unflagged[:MAX_NAMED_UNFLAGGED])}",
        "re-pin it with pin(show, target, id), which sets every flag the pin covers",
        _item(item, target=record.target, id=record.id, unflagged=len(unflagged)),
    )


def _pin_issues(show: Show, record: Pin, keys: Mapping[str, str]) -> Iterator[ValidationIssue]:
    item = _pin_item(keys, record)
    if record.target == "transition":
        try:
            parse_tid(record.id)
        except SchemaError:
            yield _bad_transition_pin(record, item)
            return
    if not _pin_exists(show, record):
        yield _unresolved_pin(record, item, ErrorCode.UNKNOWN_PIN, "unpin it or restore the item")
        return
    unflagged = _unflagged(show, record)
    if unflagged:
        yield _pin_mismatch(record, item, unflagged)


OVERRIDES_ITEM = ItemKey(kind="facing_override", path=())


def _override_spot(spot: str, number: int) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"facing override {number}",
        f"facing override {number} names {spot}, which is not in the cast",
        "remove the spot from the override or add it to the cast",
        _item(OVERRIDES_ITEM, spot=spot, override=number),
    )


def _override_selector(number: int, error: E8sError) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"facing override {number}",
        f"facing override {number} selects spots that do not resolve: {error.message}",
        error.hint,
        _item(OVERRIDES_ITEM, override=number),
    )


def _override_spots(show: Show, override: FacingOverride, number: int) -> Iterator[ValidationIssue]:
    entities = override.entities
    if entities == ALL_SPOTS:
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
        f"facing override {number} starts at count {override.start:g}, after the show ends at"
        f" {total:g}",
        f"start the override at a count from 0 to {total:g}, or remove it",
        _item(OVERRIDES_ITEM, override=number, start=override.start, total=total),
    )


def _show_total(show: Show) -> float | None:
    try:
        total = index(show).total_counts
    except (E8sError, ValueError, OverflowError):
        return None
    return total if math.isfinite(total) else None


def _override_issues(show: Show) -> Iterator[ValidationIssue]:
    total = _show_total(show)
    for number, override in enumerate(show.facing_overrides):
        yield from _override_spots(show, override, number)
        if total is not None and override.start > total:
            yield _past_the_show(override, number, total)


def reference_issues(show: Show) -> list[ValidationIssue]:
    """Reports forms and sets whose references do not resolve.

    Covers form members outside the cast, forms of missing sets, sets listing missing forms,
    members whose placements do not point back to their form, pins that name missing items or
    leave their flags unset, and facing overrides naming unknown spots or starting after the
    show.

    >>> from e8s.examples import two_set_show
    >>> reference_issues(two_set_show())
    []
    """
    issues = [i for key, form in show.forms.items() for i in _form_issues(show, key, form)]
    issues += [i for owner in show.sets for i in _set_issues(show, owner)]
    keys: dict[str, str] = {owner.id: set_key(owner) for owner in show.sets}
    issues += [i for record in show.pins for i in _pin_issues(show, record, keys)]
    return issues + list(_override_issues(show))
