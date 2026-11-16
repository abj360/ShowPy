"""
collab.py --- live collaboration: change ops, canvas events, pins, proposals, highlights, selection

Contains:
    apply_event(): replays one canvas event as a model operation
    events_since(): derives the canvas events of every revision after a revision
    pins(): lists the designer's locks
    propose(): wraps change ops as a suggestion for the designer
    preview_proposal(): computes the ghost placements and paths a proposal would produce
    accept(): applies a proposal as the designer, refusing when the same items moved since
    reject(): closes a proposal with a reason
    highlight(): points the designer at spots, an area, a set, a transition or a zone
    selection(): returns what the designer has selected
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import TypeAdapter

from showpy.core.errors import (
    ConflictError,
    LockedError,
    SchemaError,
    TimeRangeError,
)
from showpy.core.ids import ZoneId, format_tid
from showpy.core.selection import Selection
from showpy.notes.models import Anchor
from showpy.show.changes import apply_change
from showpy.show.files import COMMENTS_ADAPTER, COMMENTS_DOC, SELECTION_DOC
from showpy.show.locks import locked_changes
from showpy.show.models import (
    CanvasEvent,
    ChangeOp,
    Comment,
    CountsOp,
    EventKind,
    FormOp,
    PathsOp,
    Pin,
    PlacementOp,
    Proposal,
    ProposalPreview,
    Revision,
    Show,
)
from showpy.show.pinning import pin, unpin
from showpy.show.storage import ROOT_REVISION, Workspace, load_show, save_show

OP_ADAPTER: TypeAdapter[ChangeOp] = TypeAdapter(ChangeOp)
OP_KIND: dict[type, EventKind] = {
    PlacementOp: "move",
    FormOp: "edit_form",
    PathsOp: "edit_paths",
    CountsOp: "set_counts",
}
EVENT_ID_LENGTH = 12
DEFAULT_TTL_S = 30.0
SHORT_ID = 7


def _require_designer(event: CanvasEvent) -> None:
    if event.actor == "agent":
        raise LockedError(
            f"agent event {event.id} changes pins",
            hint="pins belong to the designer; propose() a change instead",
            context={"event": event.id},
        )


def apply_event(show: Show, event: CanvasEvent) -> Show:
    """Replays one canvas event; designer and system events may touch pinned items.

    Comment and highlight events leave the show unchanged.

    >>> from datetime import UTC
    >>> from showpy.examples import two_set_show
    >>> event = CanvasEvent(id="e1", at=datetime(2026, 11, 11, tzinfo=UTC), actor="designer",
    ...                     kind="set_counts", payload={"op": "counts", "set_id": "2", "counts": 6})
    >>> apply_event(two_set_show(), event).set("2").counts
    6.0
    """
    if event.kind in ("comment", "highlight"):
        return show
    if event.kind in ("pin", "unpin"):
        _require_designer(event)
        held = Pin.model_validate(event.payload)
        if event.kind == "pin":
            return pin(show, held.target, held.id, held.reason, held.by)
        return unpin(show, held.target, held.id)
    op = OP_ADAPTER.validate_python(event.payload)
    if OP_KIND[type(op)] != event.kind:
        raise SchemaError(
            f"event {event.id} of kind {event.kind} carries a {op.op} op",
            hint="match the payload op to the kind: move=placement, edit_form=form, "
            "edit_paths=paths, set_counts=counts",
            context={"event": event.id},
        )
    return apply_change(show, [op], force=event.actor != "agent")


def _pin_events(before: Show, after: Show) -> list[tuple[EventKind, dict[str, object]]]:
    removed = [p for p in before.pins if p not in after.pins]
    added = [p for p in after.pins if p not in before.pins]
    return [
        *(("unpin", p.model_dump(mode="json")) for p in removed),
        *(("pin", p.model_dump(mode="json")) for p in added),
    ]


def _edit_ops(before: Show, after: Show) -> list[ChangeOp]:
    ops: list[ChangeOp] = []
    shared = [t for t in after.transition_ids() if t in before.transition_ids()]
    ops += [
        PathsOp(spec=after.transition(t))
        for t in shared
        if after.transition(t) != before.transition(t)
    ]
    old = {s.id: s for s in before.sets}
    common = [s for s in after.sets if s.id in old]
    ops += [CountsOp(set_id=s.id, counts=s.counts) for s in common if s.counts != old[s.id].counts]
    for current in common:
        previous = {p.entity: p for p in old[current.id].placements}
        ops += [
            PlacementOp(set_id=current.id, placement=p)
            for p in current.placements
            if p.entity in previous and previous[p.entity] != p
        ]
    ops += [FormOp(form=f) for k, f in sorted(after.forms.items()) if before.forms.get(k) != f]
    return ops


def _show_events(before: Show, after: Show) -> list[tuple[EventKind, dict[str, object]]]:
    edits: list[tuple[EventKind, dict[str, object]]] = [
        (OP_KIND[type(op)], op.model_dump(mode="json")) for op in _edit_ops(before, after)
    ]
    return [*_pin_events(before, after), *edits]


def _comments(ws: Workspace, revision: str) -> dict[str, Comment]:
    raw = ws.tree_at(revision).get(COMMENTS_DOC)
    return {} if raw is None else {c.id: c for c in COMMENTS_ADAPTER.validate_json(raw)}


def _comment_events(ws: Workspace, r: Revision) -> list[tuple[EventKind, dict[str, object]]]:
    if COMMENTS_DOC not in r.changed:
        return []
    old, new = _comments(ws, r.parent), _comments(ws, r.id)
    changed = [c for k, c in new.items() if old.get(k) != c]
    return [("comment", c.model_dump(mode="json")) for c in changed]


def _revision_events(ws: Workspace, r: Revision) -> list[CanvasEvent]:
    before, after = ws.show_at(r.parent), ws.show_at(r.id)
    found = _show_events(before, after) if before and after else []
    found += _comment_events(ws, r)
    return [
        CanvasEvent(
            id=f"{r.id[:EVENT_ID_LENGTH]}-{n}",
            at=r.at,
            actor=r.actor,
            kind=kind,
            payload=payload,
            revision=r.id,
        )
        for n, (kind, payload) in enumerate(found)
    ]


def events_since(ws: Workspace, revision: str) -> list[CanvasEvent]:
    """Derives the canvas events of every revision after `revision`, oldest first.

    Events are derived from stored states, so replaying them with apply_event() on the show
    at `revision` reproduces each later placement, form, path and count edit. Structural
    edits (sets added or removed, cast or sheet changes) show up in changelog() only.
    """
    log = ws.repo.revisions(ws.show_id)
    if revision != ROOT_REVISION:
        ws.repo.get_revision(ws.show_id, revision)
        log = log[[r.id for r in log].index(revision) + 1 :]
    return [event for r in log for event in _revision_events(ws, r)]


def pins(show: Show) -> list[Pin]:
    """Lists the designer's locks in the order they were made."""
    return list(show.pins)


def propose(show: Show, change: Sequence[ChangeOp], note: str, proposal_id: str) -> Proposal:
    """Wraps change ops as a suggestion against the show's revision; checks they apply.

    >>> from showpy.examples import two_set_show
    >>> propose(two_set_show(), [CountsOp(set_id="2", counts=12)], "slower", "p1").state
    'open'
    """
    apply_change(show, change, force=True)
    return Proposal(id=proposal_id, base_revision=show.revision, change=tuple(change), note=note)


def preview_proposal(show: Show, proposal: Proposal) -> ProposalPreview:
    """Computes what a proposal would produce, for ghosts on the canvas.

    >>> from showpy.choreo.models import Placement
    >>> from showpy.examples import two_set_show
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
            code="PROPOSAL_CLOSED",
        )


def _require_no_overlap(ws: Workspace, proposal: Proposal, head: Show) -> None:
    if proposal.base_revision == head.revision:
        return
    ws.repo.get_revision(ws.show_id, proposal.base_revision)
    base = ws.show_at(proposal.base_revision)
    moved = _touched(_edit_ops(base, head)) if base else set()
    overlap = sorted(moved & _touched(proposal.change))
    if overlap:
        raise ConflictError(
            f"proposal {proposal.id} touches items changed since "
            f"{proposal.base_revision[:SHORT_ID]}: {', '.join(overlap)}",
            hint="propose again against the current show",
            context={"proposal": proposal.id, "overlap": overlap, "head": head.revision},
        )


def accept(ws: Workspace, proposal: Proposal, at: datetime) -> Revision:
    """Applies an open proposal to the head show as the designer and saves it.

    When the base moved, the proposal still lands if nothing it touches changed since;
    otherwise ConflictError names the overlap. Pins still hold: unpin first to accept edits
    to pinned items.

    Args:
        ws: Designer or system workspace; it moves to the head.
        proposal: Open proposal.
        at: Time stamp of the revision.

    Returns:
        revision: The revision holding the accepted change.
    """
    if ws.principal == "agent":
        raise LockedError(
            "the agent cannot accept proposals",
            hint="the designer accepts or rejects proposals",
            context={"proposal": proposal.id},
        )
    _require_open(proposal)
    ws.refresh()
    head = load_show(ws)
    _require_no_overlap(ws, proposal, head)
    edited = apply_change(head, proposal.change)
    return save_show(ws, edited, at, f"accept proposal {proposal.id}: {proposal.note}")


def reject(proposal: Proposal, reason: str) -> Proposal:
    """Closes an open proposal with a reason.

    >>> reject(Proposal(id="p", base_revision="", change=()), "too busy").state
    'rejected'
    """
    _require_open(proposal)
    return proposal.replace(state="rejected", reason=reason)


def highlight(
    target: Anchor | ZoneId, ttl_s: float = DEFAULT_TTL_S, *, at: datetime, event_id: str
) -> CanvasEvent:
    """Points the designer at spots, an area, a point, a set, a transition or a zone.

    Highlights are pointers, not edits: they carry no revision and change nothing.

    >>> from datetime import UTC
    >>> event = highlight(Anchor.on_spots(["T1"]), at=datetime(2026, 11, 11, tzinfo=UTC),
    ...                   event_id="h1")
    >>> event.kind, event.payload["ttl_s"]
    ('highlight', 30.0)
    """
    if not ttl_s > 0:
        raise TimeRangeError(
            f"highlight lasts {ttl_s}s",
            hint="use ttl_s > 0",
            context={"ttl_s": ttl_s},
        )
    pointed = (
        {"anchor": target.model_dump(mode="json")}
        if isinstance(target, Anchor)
        else {"zone": target}
    )
    return CanvasEvent(
        id=event_id, at=at, actor="agent", kind="highlight", payload={**pointed, "ttl_s": ttl_s}
    )


def selection(ws: Workspace) -> Selection:
    """Returns what the designer has selected on the canvas; empty when nothing is."""
    return Selection.model_validate_json(ws.read(SELECTION_DOC))
