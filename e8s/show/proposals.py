"""
proposals.py --- stored suggestions an agent makes and the designer accepts, rejects or withdraws

Contains:
    PROPOSALS_ROOT: names the folder that holds one JSON document per proposal
    propose(): stores a change as an open proposal against the workspace's revision
    preview_proposal(): computes the ghost placements and paths a proposal would produce
    proposals(): lists the stored proposals by ID
    get_proposal(): returns one stored proposal
    is_stale(): tells whether items an open proposal touches changed since its base
    accept(): applies an open proposal and marks it accepted in one revision
    reject(): closes an open proposal with a reason
    withdraw(): closes an open proposal its author takes back
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, SchemaError, UnknownRefError
from e8s.core.ids import format_tid
from e8s.core.util import close_matches
from e8s.show.changes import apply_change
from e8s.show.collab_models import (
    ChangeOp,
    FormOp,
    PathsOp,
    PlacementOp,
    Proposal,
    ProposalPreview,
)
from e8s.show.document_paths import SEGMENT_PATTERN
from e8s.show.documents import dump_json
from e8s.show.events import edit_ops
from e8s.show.files import show_changes
from e8s.show.history_models import Revision
from e8s.show.locks import locked_changes
from e8s.show.models import Show
from e8s.show.show_io import load_show
from e8s.show.storage import ROOT_REVISION
from e8s.show.workspace import Workspace

PROPOSALS_ROOT: Final = "/proposals/"
PROPOSAL_SUFFIX: Final = ".json"
SHORT_ID = 7


def _proposal_path(proposal_id: str) -> str:
    if not SEGMENT_PATTERN.fullmatch(proposal_id) or ".." in proposal_id:
        raise SchemaError(
            f"proposal id {proposal_id!r} is not a safe file name",
            hint="use letters, digits, `_`, `-` and `.`, starting with a letter, digit or `_`",
            context={"proposal": proposal_id},
            code=ErrorCode.BAD_ID,
        )
    return f"{PROPOSALS_ROOT}{proposal_id}{PROPOSAL_SUFFIX}"


def _encode(proposal: Proposal) -> bytes:
    return dump_json(proposal.to_json())


def _decode(raw: bytes) -> Proposal:
    return Proposal._validated_json(raw)


def propose(
    ws: Workspace, change: Sequence[ChangeOp], note: str, proposal_id: str, at: datetime
) -> Proposal:
    """Stores a change as an open proposal against the workspace's revision.

    The change must apply to the show at that revision; a workspace with no saved show cannot
    check it, so the proposal is checked when it is accepted. The proposal document lands on
    the head, which may be newer than the workspace's revision.

    Args:
        ws: Workspace the proposal is made from; it moves to the new head.
        change: Ops applied in order when accepted.
        note: Why the agent suggests it.
        proposal_id: New proposal ID, also its file name.
        at: Time stamp of the revision.

    Returns:
        proposal: The stored open proposal.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.collab_models import CountsOp
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.show_io import save_show
    >>> from e8s.show.workspace import open_workspace
    >>> now = datetime(2026, 11, 11, tzinfo=UTC)
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> _ = save_show(ws, two_set_show(), now)
    >>> propose(ws, [CountsOp(set_id="2", counts=12)], "slower", "p1", now).state
    'open'
    """
    path = _proposal_path(proposal_id)
    head = ws.repo.head(ws.show_id)
    if path in ws.tree_at(head):
        raise ConflictError(
            f"proposal {proposal_id} already exists",
            hint="pick another proposal ID",
            context={"proposal": proposal_id},
            code=ErrorCode.NAME_TAKEN,
        )
    base = ws.show_at(ws.revision)
    if base is not None:
        apply_change(base, change, force=True)
    proposal = Proposal(id=proposal_id, base_revision=ws.revision, change=tuple(change), note=note)
    ws.commit({path: _encode(proposal)}, at=at, expected_revision=head)
    return proposal


def preview_proposal(show: Show, proposal: Proposal) -> ProposalPreview:
    """Computes what a proposal would produce, for ghosts on the canvas.

    >>> from e8s.choreo.models import Placement
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.collab_models import PlacementOp, Proposal
    >>> op = PlacementOp(set_id="2", placement=Placement(entity="T1", x=-2.0, y=9.0))
    >>> proposal = Proposal(id="p", base_revision="", change=(op,))
    >>> preview = preview_proposal(two_set_show(), proposal)
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


def proposals(ws: Workspace) -> list[Proposal]:
    """Lists the stored proposals at the workspace's revision, sorted by ID.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> proposals(open_workspace(InMemoryRepository(), "show-1"))
    []
    """
    tree = ws.tree_at(ws.revision)
    paths = sorted(p for p in tree if p.startswith(PROPOSALS_ROOT) and p.endswith(PROPOSAL_SUFFIX))
    return [_decode(bytes(tree[p])) for p in paths]


def get_proposal(ws: Workspace, proposal_id: str) -> Proposal:
    """Returns one stored proposal; an unknown ID raises UnknownRefError with close IDs.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> try:
    ...     get_proposal(open_workspace(InMemoryRepository(), "show-1"), "p1")
    ... except UnknownRefError as exc:
    ...     print(exc.context["proposal"])
    p1
    """
    path = _proposal_path(proposal_id)
    tree = ws.tree_at(ws.revision)
    if path not in tree:
        close = close_matches(proposal_id, [p.id for p in proposals(ws)])
        raise UnknownRefError(
            f"no proposal {proposal_id}",
            hint=f"did you mean {', '.join(close)}?" if close else "proposals() lists them",
            context={"proposal": proposal_id},
        )
    return _decode(bytes(tree[path]))


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
            hint="only open proposals can be accepted, rejected or withdrawn",
            context={"proposal": proposal.id},
            code=ErrorCode.PROPOSAL_CLOSED,
        )


def _overlap(ws: Workspace, proposal: Proposal, head: Show) -> list[str]:
    """Lists the items the proposal touches that changed between its base and the head."""
    if proposal.base_revision in (ROOT_REVISION, head.revision):
        return []
    ws.repo.get_revision(ws.show_id, proposal.base_revision)
    base = ws.show_at(proposal.base_revision)
    if base is None:
        return []
    return sorted(_touched(edit_ops(base, head)) & _touched(proposal.change))


def is_stale(ws: Workspace, proposal_id: str) -> bool:
    """Tells whether an open proposal touches items that changed since its base revision.

    Derived from the head each time and never stored. A closed proposal, one made before any
    show was saved and a repository with no saved show are never stale.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> from datetime import UTC, datetime
    >>> from e8s.show.collab_models import CountsOp
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> now = datetime(2026, 1, 1, tzinfo=UTC)
    >>> _ = propose(ws, [CountsOp(set_id="2", counts=12)], "", "p1", now)
    >>> is_stale(ws, "p1")
    False
    """
    proposal = get_proposal(ws, proposal_id)
    head = ws.show_at(ws.repo.head(ws.show_id))
    if proposal.state != "open" or head is None:
        return False
    return bool(_overlap(ws, proposal, head))


def accept(ws: Workspace, proposal_id: str, at: datetime) -> Revision:
    """Applies an open proposal to the head show and marks it accepted in one revision.

    When the base moved, the proposal still lands if nothing it touches changed since;
    otherwise ConflictError with code PROPOSAL_STALE names the overlap. A proposal made before
    any show was saved lands on the head without that check. Pins still hold: unpin first to
    accept edits to pinned items. The stored proposal records the head it was applied onto.

    Args:
        ws: Workspace; it moves to the head, then to the new revision.
        proposal_id: ID of the open proposal.
        at: Time stamp of the revision.

    Returns:
        revision: The revision holding the accepted change and the accepted proposal.
    """
    ws.refresh()
    proposal = get_proposal(ws, proposal_id)
    _require_open(proposal)
    head = load_show(ws)
    overlap = _overlap(ws, proposal, head)
    if overlap:
        raise ConflictError(
            f"proposal {proposal.id} touches items changed since "
            f"{proposal.base_revision[:SHORT_ID]}: {', '.join(overlap)}",
            hint="propose again against the current show",
            context={"proposal": proposal.id, "overlap": overlap, "head": head.revision},
            code=ErrorCode.PROPOSAL_STALE,
        )
    edited = apply_change(head, proposal.change)
    accepted = proposal.replace(state="accepted", accepted_revision=head.revision)
    changes = show_changes(edited, ws.tree_at(head.revision))
    changes[_proposal_path(proposal.id)] = _encode(accepted)
    return ws._commit_normalizing(changes, at=at, expected_revision=head.revision, is_complete=True)


def _close(ws: Workspace, proposal_id: str, at: datetime, **closing: str) -> Proposal:
    ws.refresh()
    proposal = get_proposal(ws, proposal_id)
    _require_open(proposal)
    closed = proposal.replace(**closing)
    ws.commit({_proposal_path(proposal_id): _encode(closed)}, at=at)
    return closed


def reject(ws: Workspace, proposal_id: str, reason: str, at: datetime) -> Proposal:
    """Closes an open proposal with a reason and stores it.

    Args:
        ws: Workspace; it moves to the new head.
        proposal_id: ID of the open proposal.
        reason: Why the designer rejects it.
        at: Time stamp of the revision.

    Returns:
        proposal: The stored rejected proposal.
    """
    return _close(ws, proposal_id, at, state="rejected", reason=reason)


def withdraw(ws: Workspace, proposal_id: str, at: datetime) -> Proposal:
    """Closes an open proposal its author takes back and stores it.

    Args:
        ws: Workspace; it moves to the new head.
        proposal_id: ID of the open proposal.
        at: Time stamp of the revision.

    Returns:
        proposal: The stored withdrawn proposal.
    """
    return _close(ws, proposal_id, at, state="withdrawn")
