"""
renumber.py --- renames a show's sets 1..N and rewrites every reference

Contains:
    SEQUENTIAL: names the scheme that numbers sets 1..N in show order
    SCHEMES: lists the renumbering schemes renumber() accepts
    renumber(): renames sets 1..N in show order and rewrites every reference
"""

from collections.abc import Mapping

from e8s.core.errors import (
    UnknownRefError,
    bounded_repr,
)
from e8s.core.ids import (
    SetId,
    check_id,
    format_tid,
)
from e8s.show.models import Pin, Show

SEQUENTIAL = "sequential"
SCHEMES = (SEQUENTIAL,)


def _renamed_pin(pin: Pin, mapping: Mapping[str, str], tids: Mapping[str, str]) -> Pin:
    if pin.target == "set":
        return pin.replace(id=mapping.get(pin.id, pin.id))
    if pin.target == "transition":
        return pin.replace(id=tids.get(pin.id, pin.id))
    return pin


def renumber(show: Show, scheme: str = SEQUENTIAL) -> tuple[Show, dict[SetId, SetId]]:
    """Renames sets 1..N in show order, rewriting transitions, forms and pins.

    Keys stay, so stored set folders and their build scripts stay with their sets.

    Args:
        show: Show to edit.
        scheme: Numbering scheme; only `sequential` exists.

    Returns:
        result: Edited show and the old-to-new ID mapping for every set.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.manage import insert_subset
    >>> show, _ = insert_subset(two_set_show(), "1", 2.0)
    >>> renumber(show)[1]
    {'1': '1', '1A': '2', '2': '3'}
    """
    if scheme not in SCHEMES:
        raise UnknownRefError(
            f"unknown numbering scheme {bounded_repr(repr(scheme))}",
            hint=f"use one of {', '.join(SCHEMES)}",
            context={"scheme": scheme},
        )
    mapping = {s.id: SetId(check_id(str(n), kind="set")) for n, s in enumerate(show.sets, start=1)}
    by_text: dict[str, str] = dict(mapping.items())
    tids = {
        format_tid(t): format_tid((mapping[t[0]], mapping[t[1]])) for t in show.transition_ids()
    }
    renamed = show.replace(
        sets=tuple(s.replace(id=mapping[s.id]) for s in show.sets),
        transitions=tuple(
            spec.replace(id=(mapping[spec.id[0]], mapping[spec.id[1]])) for spec in show.transitions
        ),
        forms={
            f: form.replace(set=mapping.get(form.set, form.set)) for f, form in show.forms.items()
        },
        pins=tuple(_renamed_pin(p, by_text, tids) for p in show.pins),
    )
    return renamed, mapping
