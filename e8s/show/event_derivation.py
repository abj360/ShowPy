"""
event_derivation.py --- derives the canvas edits of a revision from the items it changed

The item changes say what changed; the two sides of the revision give the payloads: the moved
placements, the new counts, the edited paths and the forms. A side is either a whole show or a
stored tree read part by part, so a revision read from its changed documents alone gets the same
edits as one read whole, decoding only the parts its edits name.

Contains:
    Edit: names one derived edit, its event kind and its payload
    ShowSide: reads the parts of one side of a revision that its edits name
    WholeShow: reads the parts of an assembled show, as ShowSide
    pin_edits(): lists the unpin and pin edits between two whole shows
    show_edits(): lists the path, count, placement and form edits of a set of item changes
"""

import itertools
from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import FormId, SetId, TransitionId
from e8s.show.change_models import ItemChange
from e8s.show.collab_models import (
    CountsOp,
    EventKind,
    EventPayload,
    FormOp,
    PathsOp,
    PlacementOp,
)
from e8s.show.document_paths import transition_folder
from e8s.show.item_table import TIMING
from e8s.show.models import Show
from e8s.show.set_keys import set_key

type Edit = tuple[EventKind, EventPayload]


class ShowSide(Protocol):
    """Reads the parts of one side of a revision that its edits name."""

    def set_keys(self) -> Sequence[str]:
        """Lists the set keys in show order."""
        ...

    def set(self, key: str) -> Set:
        """Returns the set stored under a key."""
        ...

    def stored_paths(self, folder: tuple[str, str]) -> TransitionSpec | None:
        """Returns the paths stored for two consecutive sets, by key, or None."""
        ...

    def form(self, form_id: str) -> Form:
        """Returns a form by ID."""
        ...


class WholeShow:
    """Reads the parts of an assembled show, as ShowSide.

    Attributes:
        show: The show read.
    """

    def __init__(self, show: Show) -> None:
        self.show = show
        self._sets = {set_key(current): current for current in show.sets}

    def set_keys(self) -> Sequence[str]:
        """Lists the set keys in show order."""
        return [set_key(current) for current in self.show.sets]

    def set(self, key: str) -> Set:
        """Returns the set stored under a key."""
        return self._sets[key]

    def stored_paths(self, folder: tuple[str, str]) -> TransitionSpec | None:
        """Returns the paths the show stores for two consecutive sets, by key, or None."""
        tid = TransitionId(self._sets[folder[0]].id, self._sets[folder[1]].id)
        return self.show.lookup.stored.get(tid)

    def form(self, form_id: str) -> Form:
        """Returns a form by ID."""
        return self.show.forms[FormId(form_id)]


def pin_edits(before: Show, after: Show) -> list[Edit]:
    """Lists an unpin edit per pin only `before` holds, then a pin edit per pin only `after` holds.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.pinning import pin
    >>> [kind for kind, _ in pin_edits(two_set_show(), pin(two_set_show(), "set", "2", "hold"))]
    ['pin']
    """
    removed = [p for p in before.pins if p not in after.pins]
    added = [p for p in after.pins if p not in before.pins]
    return [
        *(("unpin", p) for p in removed),
        *(("pin", p) for p in added),
    ]


def _move_edit(set_id: SetId, placement: Placement) -> Edit:
    return "move", PlacementOp(set_id=set_id, placement=placement)


def _touched_sets(delta: Iterable[ItemChange], before: ShowSide, after: ShowSide) -> list[Edit]:
    """Lists the count and placement edits of sets present on both sides, in set order."""
    old = set(before.set_keys())
    timing: set[str] = set()
    moved: dict[str, set[str]] = {}
    for change in delta:
        key, path = change.key, change.key.path
        if key.kind not in ("set", "placement"):
            continue
        if change.before is None or change.after is None or path[0] not in old:
            continue
        if key.kind == "set" and key.role == TIMING:
            timing.add(path[0])
        elif key.kind == "placement":
            moved.setdefault(path[0], set()).add(path[1])
    touched = timing | moved.keys()
    counts: list[Edit] = []
    placements: list[Edit] = []
    for name in after.set_keys():
        if name not in touched:
            continue
        current = after.set(name)
        if name in timing:
            counts.append(("set_counts", CountsOp(set_id=current.id, counts=current.counts)))
        entities = moved.get(name, set())
        placements += [
            _move_edit(current.id, p) for p in current.placements if p.entity in entities
        ]
    return [*counts, *placements]


def _without_counts(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {name: field for name, field in value.items() if name != "counts"}


def _edited_transitions(delta: Iterable[ItemChange]) -> set[str]:
    """Names the transitions whose rows, routes, notes or source hash changed, not their counts."""
    found: set[str] = set()
    for change in delta:
        if change.key.kind not in ("transition", "path_row", "route"):
            continue
        if change.key.kind == "transition" and _without_counts(change.before) == _without_counts(
            change.after
        ):
            continue
        found.add(change.key.path[0])
    return found


def _transition_ids(side: ShowSide) -> list[TransitionId]:
    pairs = itertools.pairwise(side.set_keys())
    return [TransitionId(side.set(a).id, side.set(b).id) for a, b in pairs]


def _path_edits(delta: Iterable[ItemChange], before: ShowSide, after: ShowSide) -> list[Edit]:
    """Lists an edit_paths edit per consecutive pair on both sides whose paths changed."""
    names = _edited_transitions(delta)
    if not names:
        return []
    shared = set(_transition_ids(before))
    edits: list[Edit] = []
    for folder in itertools.pairwise(after.set_keys()):
        if transition_folder(folder) not in names:
            continue
        first, second = after.set(folder[0]), after.set(folder[1])
        tid = TransitionId(first.id, second.id)
        if tid not in shared:
            continue
        spec = after.stored_paths(folder)
        if spec is None:
            spec = TransitionSpec(id=tid, counts=second.counts)
        edits.append(("edit_paths", PathsOp(spec=spec)))
    return edits


def _form_edits(delta: Iterable[ItemChange], before: ShowSide, after: ShowSide) -> list[Edit]:
    edits: list[Edit] = []
    for change in sorted(delta, key=lambda c: c.key.path):
        if change.key.kind != "form":
            continue
        form_id = change.key.path[0]
        if change.after is None:
            edits.append(("delete_form", FormOp(form=before.form(form_id))))
        else:
            edits.append(("edit_form", FormOp(form=after.form(form_id))))
    return edits


def show_edits(delta: Sequence[ItemChange], before: ShowSide, after: ShowSide) -> list[Edit]:
    """Lists the path, count, placement and form edits of a revision, in that order.

    Paths are listed in show order, counts before placements set by set, and forms by ID; a
    form only `before` holds is a delete_form edit.

    Args:
        delta: The items the revision changed.
        before: The side before the revision.
        after: The side after it.

    Returns:
        edits: Each edit's event kind and payload.

    >>> from e8s.choreo.models import PlacementPatch
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.items import changes
    >>> show = two_set_show()
    >>> moved = show.replace(sets=(show.sets[0], show.sets[1].with_placements(
    ...     {"T1": PlacementPatch(x=3.0)})))
    >>> delta = changes(show, moved).changes
    >>> [kind for kind, _ in show_edits(delta, WholeShow(show), WholeShow(moved))]
    ['move']
    """
    return [
        *_path_edits(delta, before, after),
        *_touched_sets(delta, before, after),
        *_form_edits(delta, before, after),
    ]
