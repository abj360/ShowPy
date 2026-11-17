"""
text_edit.py --- changes a text form's spot count by naming the spots to add or remove

A text form has exactly one member per slot, so its count never changes as a side effect.
`edit_text_count` is the one way to change it: the caller names the spots that join or
leave, and the new member count must equal the new slot count.

Contains:
    edit_text_count(): returns a text form with named spots added or removed and its count reset
"""

from collections.abc import Sequence

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from showpy.choreo.form_layout import layout
from showpy.choreo.form_text import require_member_count
from showpy.choreo.forms import check_unlocked
from showpy.choreo.models import Form
from showpy.choreo.text_count import count_recipe
from showpy.core.errors import SchemaError
from showpy.core.ids import SpotId, check_id
from showpy.core.types import Meters
from showpy.geo.fonts import FontRegistry

LISTED_SPOTS = 10


def _preview(spots: Sequence[SpotId]) -> str:
    shown = ", ".join(spots[:LISTED_SPOTS])
    hidden = len(spots) - LISTED_SPOTS
    return f"{shown} and {hidden} more" if hidden > 0 else shown


def _spot_list(form: Form, field: str, spots: Sequence[SpotId]) -> tuple[SpotId, ...]:
    """Returns the spots as a tuple, rejecting a bare string or anything but a list of strings."""
    if isinstance(spots, list | tuple) and all(isinstance(spot, str) for spot in spots):
        return tuple(spots)
    raise SchemaError(
        f"{field} of text form {form.id} must be a list of spot IDs",
        hint=f"pass {field}=[...] with one spot ID per entry, e.g. {field}=['S1']",
        context={"form": form.id, "field": field},
    )


def _repeats(spots: Sequence[SpotId], taken: set[SpotId]) -> list[SpotId]:
    """Returns, once each and in order, the spots already in `taken` or repeated in `spots`."""
    seen = set(taken)
    culprits: dict[SpotId, None] = {}
    for spot in spots:
        if spot in seen:
            culprits[spot] = None
        seen.add(spot)
    return list(culprits)


def _added(form: Form, add: Sequence[SpotId]) -> tuple[SpotId, ...]:
    """Returns the checked, NFC-normalized new spots; none may be a member or repeat."""
    spots = tuple(check_id(spot, kind="spot") for spot in _spot_list(form, "add", add))
    culprits = _repeats(spots, set(form.members))
    if not culprits:
        return spots
    addable = [spot for spot in dict.fromkeys(spots) if spot not in form.members]
    listed = f": {_preview(addable)}" if addable else ""
    raise SchemaError(
        f"text form {form.id} already has {', '.join(culprits)}",
        hint=f"add only spots outside form {form.id}{listed}",
        context={"form": form.id, "spots": culprits},
        code="ALREADY_MEMBER",
    )


def _kept(form: Form, remove: Sequence[SpotId]) -> tuple[SpotId, ...]:
    """Returns the members left after removing `remove`; each must be a member, once."""
    spots = _spot_list(form, "remove", remove)
    outsiders = set(spots) - set(form.members)
    culprits = [spot for spot in dict.fromkeys(spots) if spot in outsiders]
    culprits += [spot for spot in _repeats(spots, set()) if spot not in outsiders]
    if not culprits:
        gone = set(spots)
        return tuple(member for member in form.members if member not in gone)
    raise SchemaError(
        f"text form {form.id} has no member {', '.join(culprits)} to remove",
        hint=f"remove only members of form {form.id}: {_preview(form.members)}",
        context={"form": form.id, "spots": culprits},
        code="NOT_A_MEMBER",
    )


def _require_text_form(form: Form) -> None:
    if form.kind != "text":
        raise SchemaError(
            f"form {form.id} is a {form.kind} form, not text",
            hint="use edit() for other kinds",
            context={"form": form.id, "kind": form.kind},
            code="NOT_A_TEXT_FORM",
        )


def edit_text_count(
    form: Form,
    *,
    add: Sequence[SpotId] = (),
    remove: Sequence[SpotId] = (),
    n: int | None = None,
    spacing: Meters | None = None,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
) -> Form:
    """Returns a text form with named spots added or removed and its slot count reset.

    The new members are the old ones without `remove`, then `add` in the order given. The new
    count is `n`, else the count `spacing` derives for this text, else the new member count,
    and it must equal the new member count. The result is validated by building its slots.

    Args:
        form: Text form to change; it is never modified.
        add: New spots, appended in this order; none may already be a member.
        remove: Members that leave the form.
        n: Exact new spot count; stores no spacing.
        spacing: Meters between spots, stored with the count it derives.
        force: Edit a locked form as the designer.
        registry: Form generators; text forms ignore it.
        font_registry: Registry holding the form's fonts; None means `bundled_fonts()`.

    Returns:
        edited: The new form, with `geometry["n"]` equal to its member count.

    Raises:
        LockedError: The form is locked and `force` is not given.
        SchemaError: `NOT_A_TEXT_FORM`, `N_OR_SPACING`, `ALREADY_MEMBER`, `NOT_A_MEMBER`, or a
            bad spot ID.
        SpacingError: `MEMBER_COUNT_MISMATCH` with how many spots to add or remove;
            `TOO_FEW_SPOTS` or `TOO_MANY_SPOTS` for the new count.

    >>> from showpy.choreo.forms import text_form
    >>> go = text_form("go", [f"S{i}" for i in range(24)], "GO", fonts=["hershey-roman"],
    ...     height=4.0, at=(0, 10), n=24)
    >>> wider = edit_text_count(go, add=["X1", "X2"], n=26)
    >>> wider.members[-2:], wider.geometry["n"]
    (('X1', 'X2'), 26)
    """
    check_unlocked(form, force)
    _require_text_form(form)
    members = (*_kept(form, remove), *_added(form, add))
    count = len(members) if n is None and spacing is None else n
    geometry = count_recipe(form.geometry, count, spacing, font_registry)
    require_member_count(form.id, geometry["n"], len(members))
    edited = form.replace(geometry=geometry, members=members)
    layout(edited, registry, font_registry=font_registry)
    return edited
