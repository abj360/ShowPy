"""
changes.py --- applies placement, form, paths and counts ops to a show

Contains:
    require_form_owner(): refuses a form whose ID is stored under another set
    store_paths(): stores a transition's paths in show order, dropping default paths
    apply_change(): applies change ops to a show, refusing pinned items unless forced
"""

import functools
from collections.abc import Sequence

from showpy.choreo.models import Form, TransitionSpec
from showpy.core.errors import SchemaError
from showpy.show.locks import require_unlocked
from showpy.show.manage import set_counts
from showpy.show.models import ChangeOp, FormOp, PathsOp, PlacementOp, Show


def _replace_set(show: Show, op: PlacementOp) -> Show:
    position = show.set_index(op.set_id)
    current = show.sets[position]
    current.get(op.placement.entity)
    edited = current.with_placements({op.placement.entity: op.placement}, force=True)
    return show.replace(sets=(*show.sets[:position], edited, *show.sets[position + 1 :]))


def require_form_owner(show: Show, form: Form) -> None:
    """Refuses a form whose ID is stored under a different set than the form names.

    Args:
        show: Show that holds the stored forms.
        form: Form about to be stored.
    """
    stored = show.forms.get(form.id)
    if stored is None or stored.set == form.set:
        return
    raise SchemaError(
        f"form {form.id} belongs to set {stored.set}, not {form.set}",
        hint=f"give the form a new ID, or put it on set {stored.set}",
        context={"form": form.id, "owner": stored.set, "set": form.set},
    )


def _replace_form(show: Show, op: FormOp) -> Show:
    require_form_owner(show, op.form)
    position = show.set_index(op.form.set)
    owner = show.sets[position]
    if op.form.id not in owner.forms:
        owner = owner.replace(forms=(*owner.forms, op.form.id))
    sets = (*show.sets[:position], owner, *show.sets[position + 1 :])
    return show.replace(forms={**show.forms, op.form.id: op.form}, sets=sets)


def _is_default(spec: TransitionSpec) -> bool:
    return not (spec.rows or spec.routes or spec.notes or spec.source_hash)


def store_paths(show: Show, op: PathsOp) -> Show:
    """Stores a transition's paths in show order; default (empty) paths are not stored."""
    counts = show.transition(op.spec.id).counts
    spec = op.spec.replace(counts=counts)
    kept = [s for s in show.transitions if s.id != spec.id]
    stored = kept if _is_default(spec) else [*kept, spec]
    order = show.transition_ids()
    stored.sort(key=lambda s: order.index(s.id))
    return show.replace(transitions=tuple(stored))


def _apply_op(show: Show, op: ChangeOp) -> Show:
    if isinstance(op, PlacementOp):
        return _replace_set(show, op)
    if isinstance(op, FormOp):
        return _replace_form(show, op)
    if isinstance(op, PathsOp):
        return store_paths(show, op)
    return set_counts(show, op.set_id, op.counts, rescale=op.rescale, force=True)


def apply_change(show: Show, change: Sequence[ChangeOp], force: bool = False) -> Show:
    """Applies change ops in order, then refuses the result if it touches pinned items.

    Args:
        show: Show to edit.
        change: Placement, form, paths and counts ops.
        force: Allows touching pinned or locked items, for the designer.

    Returns:
        show: Edited show; paths always take the destination set's counts.

    >>> from showpy.choreo.models import Placement
    >>> from showpy.examples import two_set_show
    >>> op = PlacementOp(set_id="2", placement=Placement(entity="T1", x=-2.0, y=9.0))
    >>> apply_change(two_set_show(), [op]).set("2").get("T1").x
    -2.0
    """
    edited = functools.reduce(_apply_op, change, show)
    require_unlocked(show, edited, force)
    return edited
