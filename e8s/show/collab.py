"""
collab.py --- live collaboration: pins, proposals, highlights, selection

Contains:
    pins(): lists the designer's locks
    propose(): wraps change ops as a suggestion for the designer
    preview_proposal(): computes the ghost placements and paths a proposal would produce
    accept(): applies a proposal to the head show, refusing when the same items moved since
    reject(): closes a proposal with a reason
    highlight(): points the designer at spots, an area, a set, a transition or a host-defined
        area
    selection(): returns what the designer has selected
"""

from collections.abc import Sequence
from datetime import datetime

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    ConflictError,
    SchemaError,
    TimeRangeError,
)
from e8s.core.ids import format_tid
from e8s.core.selection import Selection
from e8s.show.anchors import Anchor
from e8s.show.changes import apply_change
from e8s.show.collab_models import (
    CanvasEvent,
    ChangeOp,
    FormOp,
    Highlight,
    PathsOp,
    PlacementOp,
    Proposal,
    ProposalPreview,
)
from e8s.show.events import edit_ops
from e8s.show.history_models import Revision
from e8s.show.locks import locked_changes
from e8s.show.models import Pin, Show
from e8s.show.show_io import load_show, save_show
from e8s.show.structured import SELECTION_DOC, decode_selection
from e8s.show.workspace import Workspace

DEFAULT_TTL_S = 30.0
SHORT_ID = 7


def pins(show: Show) -> list[Pin]:
    """Lists the designer's locks in the order they were made."""
    return list(show.pins)


def propose(show: Show, change: Sequence[ChangeOp], note: str, proposal_id: str) -> Proposal:
    """Wraps change ops as a suggestion against the show's revision; checks they apply.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.collab_models import CountsOp
    >>> propose(two_set_show(), [CountsOp(set_id="2", counts=12)], "slower", "p1").state
    'open'
    """
    apply_change(show, change, force=True)
    return Proposal(id=proposal_id, base_revision=show.revision, change=tuple(change), note=note)


def preview_proposal(show: Show, proposal: Proposal) -> ProposalPreview:
    """Computes what a proposal would produce, for ghosts on the canvas.

    >>> from e8s.choreo.models import Placement
    >>> from e8s.examples import two_set_show
    >>> op = PlacementOp(set_id="2", placement=Placement(entity="T1", x=-2.0, y=9.0))
    >>> preview = preview_proposal(two_set_show(), propose(two_set_show(), [op], "", "p"))
    >>> [p.entity for p in preview.placements["2"]], preview.blocked
    (['T1'], ())
    """
    after = apply_change(show, proposal.change, force=True)
    old = {s.id: s for s in show.sets}
    placements = {
        s.id: tuple(p for p in s.placements if p not in old[s.id].placements)
        for s in after.sets
        if s.id in old and s != old[s.id]
    }
    tids = [t for t in after.transition_ids() if after.transition(t) != show.transition(t)]
    return ProposalPreview(
        proposal_id=proposal.id,
        placements={k: v for k, v in placements.items() if v},
        paths=tuple(after.transition(t) for t in tids),
        counts={s.id: s.counts for s in after.sets if s.counts != old[s.id].counts},
        forms=tuple(f for k, f in sorted(after.forms.items()) if show.forms.get(k) != f),
        blocked=tuple(locked_changes(show, after)),
    )


def _touched(ops: Sequence[ChangeOp]) -> set[str]:
    keys: set[str] = set()
    for op in ops:
        if isinstance(op, PlacementOp):
            keys.add(f"placement {op.placement.entity} in set {op.set_id}")
        elif isinstance(op, FormOp):
            keys.add(f"form {op.form.id}")
        elif isinstance(op, PathsOp):
            keys.add(f"paths {format_tid(op.spec.id)}")
        else:
            keys.add(f"counts of set {op.set_id}")
    return keys


def _require_open(proposal: Proposal) -> None:
    if proposal.state != "open":
        raise SchemaError(
            f"proposal {proposal.id} is {proposal.state}",
            hint="only open proposals can be accepted or rejected",
            context={"proposal": proposal.id},
            code=ErrorCode.PROPOSAL_CLOSED,
        )


def _require_no_overlap(ws: Workspace, proposal: Proposal, head: Show) -> None:
    if proposal.base_revision == head.revision:
        return
    ws.repo.get_revision(ws.show_id, proposal.base_revision)
    base = ws.show_at(proposal.base_revision)
    moved = _touched(edit_ops(base, head)) if base else set()
    overlap = sorted(moved & _touched(proposal.change))
    if overlap:
        raise ConflictError(
            f"proposal {proposal.id} touches items changed since "
            f"{proposal.base_revision[:SHORT_ID]}: {', '.join(overlap)}",
            hint="propose again against the current show",
            context={"proposal": proposal.id, "overlap": overlap, "head": head.revision},
        )


def accept(ws: Workspace, proposal: Proposal, at: datetime) -> Revision:
    """Applies an open proposal to the head show and saves it.

    When the base moved, the proposal still lands if nothing it touches changed since;
    otherwise ConflictError names the overlap. Pins still hold: unpin first to accept edits
    to pinned items.

    Args:
        ws: Workspace; it moves to the head.
        proposal: Open proposal.
        at: Time stamp of the revision.

    Returns:
        revision: The revision holding the accepted change.
    """
    _require_open(proposal)
    ws.refresh()
    head = load_show(ws)
    _require_no_overlap(ws, proposal, head)
    edited = apply_change(head, proposal.change)
    return save_show(ws, edited, at)


def reject(proposal: Proposal, reason: str) -> Proposal:
    """Closes an open proposal with a reason.

    >>> reject(Proposal(id="p", base_revision="", change=()), "too busy").state
    'rejected'
    """
    _require_open(proposal)
    return proposal.replace(state="rejected", reason=reason)


def highlight(
    target: Anchor | str, ttl_s: float = DEFAULT_TTL_S, *, at: datetime, event_id: str
) -> CanvasEvent:
    """Points the designer at spots, an area, a point, a set, a transition or a named area.

    A string target is the name of an area the host defines; the library keeps no zones.

    Highlights are pointers, not edits: they carry no revision and change nothing.

    >>> from datetime import UTC
    >>> event = highlight(Anchor.on_spots(["T1"]), at=datetime(2026, 11, 11, tzinfo=UTC),
    ...                   event_id="h1")
    >>> event.kind, event.payload.ttl_s
    ('highlight', 30.0)
    """
    if not ttl_s > 0:
        raise TimeRangeError(
            f"highlight lasts {ttl_s}s",
            hint="use ttl_s > 0",
            context={"ttl_s": ttl_s},
        )
    payload = (
        Highlight(anchor=target, ttl_s=ttl_s)
        if isinstance(target, Anchor)
        else Highlight(zone=target, ttl_s=ttl_s)
    )
    return CanvasEvent(id=event_id, at=at, kind="highlight", payload=payload)


def selection(ws: Workspace) -> Selection:
    """Returns what the designer has selected on the canvas; empty when nothing is."""
    return decode_selection(ws.read(SELECTION_DOC))
