"""
proposals.py --- stored suggestions an agent makes and the designer accepts, rejects or withdraws

Contains:
    PROPOSALS_ROOT: names the folder that holds one JSON document per proposal
    propose(): stores a change as an open proposal against the workspace's revision
    preview_proposal(): computes the ghost placements and paths a proposal would produce on the head
    proposals(): lists the stored proposals by ID
    get_proposal(): returns one stored proposal
    is_stale(): tells whether an open proposal no longer merges with the head
    accept(): merges an open proposal into the head and marks it accepted in one revision
    reject(): closes an open proposal with a reason
    withdraw(): closes an open proposal its author takes back
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    MAX_LISTED_ERRORS,
    ConflictError,
    SchemaError,
    UnknownRefError,
    join_listed,
)
from e8s.core.ids import SetId, TransitionId
from e8s.core.strict_json import parse_strict_json
from e8s.core.types import JsonAny
from e8s.core.util import close_matches
from e8s.show.change_models import ItemChange, ItemKey, ItemKind, MergeConflict, MergeResult
from e8s.show.changes import apply_change
from e8s.show.collab_models import ChangeOp, CountsOp, Proposal, ProposalPreview
from e8s.show.document_paths import SEGMENT_PATTERN
from e8s.show.documents import dump_json
from e8s.show.files import show_changes
from e8s.show.history_models import Revision
from e8s.show.item_table import set_keys_by_id, transition_key
from e8s.show.items import changes
from e8s.show.locks import locked_changes, require_unlocked
from e8s.show.manage import late_routes, late_rows
from e8s.show.merge import merge
from e8s.show.merge_refs import dangling_conflicts
from e8s.show.models import Show
from e8s.show.show_io import load_show, load_show_at
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


def _decode(raw: bytes, path: str) -> Proposal:
    return Proposal.from_json(parse_strict_json(raw, document=path))


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
    >>> ws = open_workspace(InMemoryRepository(), "show_1")
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


def proposals(ws: Workspace) -> list[Proposal]:
    """Lists the stored proposals at the workspace's revision, sorted by ID.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> proposals(open_workspace(InMemoryRepository(), "show_1"))
    []
    """
    return _stored(ws, ws.revision)


def _stored(ws: Workspace, revision: str) -> list[Proposal]:
    tree = ws.tree_at(revision)
    paths = sorted(p for p in tree if p.startswith(PROPOSALS_ROOT) and p.endswith(PROPOSAL_SUFFIX))
    return [_decode(bytes(tree[p]), p) for p in paths]


def get_proposal(ws: Workspace, proposal_id: str) -> Proposal:
    """Returns one stored proposal; an unknown ID raises UnknownRefError with close IDs.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> try:
    ...     get_proposal(open_workspace(InMemoryRepository(), "show_1"), "p1")
    ... except UnknownRefError as exc:
    ...     print(exc.context["proposal"])
    p1
    """
    return _proposal_at(ws, proposal_id, ws.revision)


def _proposal_at(ws: Workspace, proposal_id: str, revision: str) -> Proposal:
    path = _proposal_path(proposal_id)
    tree = ws.tree_at(revision)
    if path not in tree:
        close = close_matches(proposal_id, [p.id for p in _stored(ws, revision)])
        raise UnknownRefError(
            f"no proposal {proposal_id}",
            hint=f"did you mean {', '.join(close)}?" if close else "proposals() lists them",
            context={"proposal": proposal_id},
        )
    return _decode(bytes(tree[path]), path)


def _require_open(proposal: Proposal) -> None:
    if proposal.state != "open":
        raise SchemaError(
            f"proposal {proposal.id} is {proposal.state}",
            hint="only open proposals can be accepted, rejected or withdrawn",
            context={"proposal": proposal.id},
            code=ErrorCode.PROPOSAL_CLOSED,
        )


def _base_show(ws: Workspace, proposal: Proposal, head: Show) -> Show:
    """Returns the show the proposal was made on; a root or proposal-only base is the head."""
    if proposal.base_revision in (ROOT_REVISION, head.revision):
        return head
    ws.repo.get_revision(ws.show_id, proposal.base_revision)
    base = ws.show_at(proposal.base_revision)
    return head if base is None else base


def _rescaled_transitions(base: Show, change: Sequence[ChangeOp]) -> list[TransitionId]:
    """Returns the transitions a rescaling counts op retimes, judged on the base show."""
    rescaled = {op.set_id: op.counts for op in change if isinstance(op, CountsOp) and op.rescale}
    return [
        tid
        for tid in base.transition_ids()
        if tid[1] in rescaled and base.transition(tid).counts != rescaled[SetId(tid[1])]
    ]


def _set_start(show: Show, set_id: str) -> float:
    return sum(s.counts for s in show.sets[: show.set_index(set_id)])


def _ends_after(override: JsonAny, start: float) -> bool:
    if not isinstance(override, Mapping):
        return False
    end = override.get("end")
    return isinstance(end, int | float) and end > start


def _shifts_overrides(value: JsonAny, start: float) -> bool:
    """Tells whether a stored override list holds one ending after `start`; a rescale moves it."""
    return isinstance(value, Sequence) and any(_ends_after(o, start) for o in value)


def _rescale_conflicts(
    base: Show, head: Show, theirs: Show, change: Sequence[ChangeOp]
) -> list[MergeConflict]:
    """Lists items the head changed that a rescale times against the base's counts.

    A rescale derives every row and route time of the transition, and the count of every facing
    override after the set, from the base's counts. Such an item the head added, removed or
    edited is timed against counts the rescale replaces; an equal value is no agreement.
    """
    tids = _rescaled_transitions(base, change)
    if not tids:
        return []
    keys = set_keys_by_id(base)
    folders = {transition_key(keys, tid) for tid in tids}
    first_start = min(_set_start(base, tid[1]) for tid in tids)
    rescaled = {c.key.sort_key(): c for c in changes(base, theirs).changes}

    def is_retimed(row: ItemChange) -> bool:
        if row.key.kind in ("path_row", "route"):
            return row.key.path[0] in folders
        return row.key.kind == "facing_override" and _shifts_overrides(row.after, first_start)

    return [
        MergeConflict(
            key=row.key,
            base=row.before,
            ours=row.after,
            theirs=rescaled[row.key.sort_key()].after
            if row.key.sort_key() in rescaled
            else row.before,
            reason="the head's item is timed against the base's counts, which the rescale replaces",
        )
        for row in changes(base, head).changes
        if is_retimed(row)
    ]


def _head_retime_conflicts(base: Show, head: Show, theirs: Show) -> list[MergeConflict]:
    """Lists rows and routes the proposal changed in a transition the head retimed after the base.

    The proposal times them against the base's counts. The head does not record whether it
    rescaled, so any change of the destination set's counts counts as a retime.
    """
    keys = set_keys_by_id(base)
    folders = {
        transition_key(keys, tid)
        for tid in base.transition_ids()
        if tid[1] in head.set_ids() and head.set(tid[1]).counts != base.set(tid[1]).counts
    }
    if not folders:
        return []
    on_head = {c.key.sort_key(): c for c in changes(base, head).changes}
    return [
        MergeConflict(
            key=row.key,
            base=row.before,
            ours=on_head[row.key.sort_key()].after if row.key.sort_key() in on_head else row.before,
            theirs=row.after,
            reason="the head retimed the transition after the base, so the proposal's timing is "
            "against the old counts",
        )
        for row in changes(base, theirs).changes
        if row.key.kind in ("path_row", "route") and row.key.path[0] in folders
    ]


def _overrun_conflicts(head: Show, merged: Show) -> list[MergeConflict]:
    """Lists rows and routes the merge leaves running past the counts it gives their transition.

    The proposal's change was checked against the base only, so a row or route one side added
    is not covered by the check `set_counts` makes against the counts the other side set. A
    transition the merge leaves as the head holds it is not checked again.
    """
    keys = set_keys_by_id(merged)
    now = {(c.key.kind, c.key.path): c for c in changes(head, merged).changes}
    conflicts: list[MergeConflict] = []
    for tid in merged.transition_ids():
        counts = merged.set(tid[1]).counts
        spec = merged.transition(tid)
        if counts == head.set(tid[1]).counts and spec == head.transition(tid):
            continue
        folder = transition_key(keys, tid)
        late: list[tuple[ItemKind, str]] = []
        late.extend(("path_row", name) for name in late_rows(spec, counts))
        late.extend(("route", name) for name in late_routes(spec, counts))
        for kind, name in late:
            change = now.get((kind, (folder, name)))
            conflicts.append(
                MergeConflict(
                    key=ItemKey(kind=kind, path=(folder, name)),
                    ours=None if change is None else change.before,
                    theirs=None if change is None else change.after,
                    reason=f"it runs past the {counts} counts the merge gives its transition",
                )
            )
    return conflicts


def _merge_onto_head(ws: Workspace, proposal: Proposal, head: Show) -> MergeResult:
    base = _base_show(ws, proposal, head)
    theirs = apply_change(base, proposal.change, force=True)
    result = merge(base, head, theirs)
    conflicts = [*result.conflicts]
    known = {c.key.sort_key() for c in conflicts}
    found = _rescale_conflicts(base, head, theirs, proposal.change)
    found += _head_retime_conflicts(base, head, theirs)
    if result.show is not None and not found:
        found = _overrun_conflicts(head, result.show)
    if result.show is not None:
        found += dangling_conflicts(base, head, result.show)
    for conflict in found:
        if conflict.key.sort_key() not in known:
            known.add(conflict.key.sort_key())
            conflicts.append(conflict)
    ordered = tuple(sorted(conflicts, key=lambda c: c.key.sort_key()))
    return MergeResult(conflicts=ordered) if ordered else result


def _describe(conflict: MergeConflict) -> str:
    key = conflict.key
    name = " ".join(part for part in (key.kind, "/".join(key.path)) if part)
    role = f" ({key.role})" if key.role else ""
    return f"{name}{role}: {conflict.reason}"


def is_stale(ws: Workspace, proposal_id: str) -> bool:
    """Tells whether an open proposal no longer merges with the head show.

    Derived from the same merge accept runs, each time, and never stored. The proposal is read at
    the head, as accept and preview read it, whatever revision the workspace is at. A closed
    proposal, one made before any show was saved and a repository with no saved show are never
    stale.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> from datetime import UTC, datetime
    >>> from e8s.show.collab_models import CountsOp
    >>> ws = open_workspace(InMemoryRepository(), "show_1")
    >>> now = datetime(2026, 1, 1, tzinfo=UTC)
    >>> _ = propose(ws, [CountsOp(set_id="2", counts=12)], "", "p1", now)
    >>> is_stale(ws, "p1")
    False
    """
    head_revision = ws.repo.head(ws.show_id)
    proposal = _proposal_at(ws, proposal_id, head_revision)
    head = ws.show_at(head_revision)
    if proposal.state != "open" or head is None:
        return False
    return bool(_merge_onto_head(ws, proposal, head).conflicts)


def preview_proposal(ws: Workspace, proposal_id: str) -> ProposalPreview:
    """Computes what accepting a proposal would change on the head, for ghosts on the canvas.

    The preview compares the head with the merge of the proposal into it, so edits made since
    the proposal's base are not ghosts. A proposal that no longer merges previews its conflicts
    and nothing else. The workspace stays at its revision, so a later write from it still
    conflicts if the head moved.

    Args:
        ws: Workspace; unchanged.
        proposal_id: ID of the proposal to preview.

    Returns:
        preview: Placements, paths, counts and forms the merge changes, the pinned or locked
            items it touches, or the conflicts that stop it.

    >>> from datetime import UTC, datetime
    >>> from e8s.choreo.models import Placement
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.collab_models import PlacementOp
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.show_io import save_show
    >>> from e8s.show.workspace import open_workspace
    >>> now = datetime(2026, 11, 11, tzinfo=UTC)
    >>> ws = open_workspace(InMemoryRepository(), "show_1")
    >>> _ = save_show(ws, two_set_show(), now)
    >>> op = PlacementOp(set_id="2", placement=Placement(entity="T1", x=-2.0, y=9.0))
    >>> _ = propose(ws, [op], "", "p1", now)
    >>> preview = preview_proposal(ws, "p1")
    >>> [p.entity for p in preview.placements["2"]], preview.blocked, preview.conflicts
    (['T1'], (), ())
    """
    head_revision = ws.repo.head(ws.show_id)
    proposal = _proposal_at(ws, proposal_id, head_revision)
    head = load_show_at(ws, head_revision)
    result = _merge_onto_head(ws, proposal, head)
    if result.show is None:
        return ProposalPreview(proposal_id=proposal.id, conflicts=result.conflicts)
    after = result.show
    old = {s.id: s for s in head.sets}
    placements = {
        s.id: tuple(p for p in s.placements if p not in old[s.id].placements)
        for s in after.sets
        if s.id in old and s != old[s.id]
    }
    tids = [t for t in after.transition_ids() if after.transition(t) != head.transition(t)]
    return ProposalPreview(
        proposal_id=proposal.id,
        placements={k: v for k, v in placements.items() if v},
        paths=tuple(after.transition(t) for t in tids),
        counts={s.id: s.counts for s in after.sets if s.counts != old[s.id].counts},
        forms=tuple(f for k, f in sorted(after.forms.items()) if head.forms.get(k) != f),
        blocked=tuple(locked_changes(head, after)),
    )


def accept(ws: Workspace, proposal_id: str, at: datetime) -> Revision:
    """Merges an open proposal into the head show and marks it accepted in one revision.

    The change is applied to the show at the proposal's base and merged with the head, so edits
    made since land beside it. If an item changed differently on both sides, or the merge leaves
    a reference dangling that the base and head did not hold (a form member or path row naming a
    spot outside the cast),
    ConflictError with code PROPOSAL_STALE lists the items. A proposal made before any show was
    saved merges with the head as its base. Pins and locks on the head still hold: unpin first to
    accept edits to pinned items. The stored proposal records the head it was merged onto.

    Args:
        ws: Workspace; it moves to the head, then to the new revision.
        proposal_id: ID of the open proposal.
        at: Time stamp of the revision.

    Returns:
        revision: The revision holding the merged change and the accepted proposal.
    """
    ws.refresh()
    proposal = get_proposal(ws, proposal_id)
    _require_open(proposal)
    head = load_show(ws)
    result = _merge_onto_head(ws, proposal, head)
    if result.show is None:
        listed = [_describe(c) for c in result.conflicts]
        raise ConflictError(
            f"proposal {proposal.id} conflicts with changes since "
            f"{proposal.base_revision[:SHORT_ID]}: {join_listed(listed)}",
            hint="propose again against the current show",
            context={
                "proposal": proposal.id,
                "conflicts": listed[:MAX_LISTED_ERRORS],
                "conflict_count": len(listed),
                "head": head.revision,
            },
            code=ErrorCode.PROPOSAL_STALE,
        )
    require_unlocked(head, result.show)
    accepted = proposal.replace(state="accepted", accepted_revision=head.revision)
    changes = show_changes(result.show, ws.tree_at(head.revision))
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
