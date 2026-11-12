#!/usr/bin/env python3

"""
changes.py --- applies placement, form, paths and counts ops to a show

Contains:
    store_paths(): stores a transition's paths in show order, dropping default paths
    apply_change(): applies change ops to a show, refusing pinned items unless forced
"""

import functools
from collections.abc import Sequence

from stage.choreo.models import TransitionSpec
from stage.show.locks import require_unlocked
from stage.show.manage import set_counts
from stage.show.models import ChangeOp, FormOp, PathsOp, PlacementOp, Show


def _replace_set(show: Show, op: PlacementOp) -> Show:
    position = show.set_index(op.set_id)
    current = show.sets[position]
    current.get(op.placement.entity)
    edited = current.with_placements({op.placement.entity: op.placement}, force=True)
    return show.model_copy(
        update={"sets": (*show.sets[:position], edited, *show.sets[position + 1 :])}
    )


def _replace_form(show: Show, op: FormOp) -> Show:
    position = show.set_index(op.form.set)
    owner = show.sets[position]
    if op.form.id not in owner.forms:
        owner = owner.model_copy(update={"forms": (*owner.forms, op.form.id)})
    sets = (*show.sets[:position], owner, *show.sets[position + 1 :])
    return show.model_copy(update={"forms": {**show.forms, op.form.id: op.form}, "sets": sets})


def _is_default(spec: TransitionSpec) -> bool:
    return not (spec.rows or spec.routes or spec.notes or spec.source_hash)


def store_paths(show: Show, op: PathsOp) -> Show:
    """Stores a transition's paths in show order; default (empty) paths are not stored."""
    counts = show.transition(op.spec.id).counts
    spec = op.spec.model_copy(update={"counts": counts})
    kept = [s for s in show.transitions if s.id != spec.id]
    stored = kept if _is_default(spec) else [*kept, spec]
    order = show.transition_ids()
    stored.sort(key=lambda s: order.index(s.id))
    return show.model_copy(update={"transitions": tuple(stored)})


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

    >>> from stage.choreo.models import Placement
    >>> from tests.fixtures.shows import two_set_show
    >>> op = PlacementOp(set_id="2", placement=Placement(entity="T1", x=-2.0, y=9.0))
    >>> apply_change(two_set_show(), [op]).set("2").get("T1").x
    -2.0
    """
    edited = functools.reduce(_apply_op, change, show)
    require_unlocked(show, edited, force)
    return edited
