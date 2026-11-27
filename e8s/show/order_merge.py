"""
order_merge.py --- merges two edits of one ordered list of IDs, such as a show's set order

Each side is read against the base. An element a side lacks is removed; an element new to a side
is inserted; an element a side holds out of base order is moved, choosing the moves so the side
keeps the longest run of base elements in base order (ties broken the same way every time).

Removals from either side apply. Each side's inserted and moved elements form runs that land
right after their anchor: the element before them on that side that the side neither inserted
nor moved, or the start. A run follows its anchor wherever the other side moved it; an anchor
the other side removed passes to the nearest earlier base element still there. Runs at one place
are ordered by the base position of their anchor, then by the natural sort of the run, so the
result does not depend on which side is which.

Two edits conflict when an element is moved on one side and removed on the other, when the two
sides place one element differently, when each side anchors a move on an element the other side
moved, so that neither can land first, or when the laid-out order puts two elements in the
opposite order from the one both sides keep them in. That last check compares each element with
every element both sides put before it, in O(n log n).

Contains:
    OrderConflict: holds why two edits of one order cannot combine
    merge_order(): merges two edits of an ordered list, or reports why they conflict
"""

from bisect import bisect_left
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from e8s.core.util import natural_key

type Element = str | tuple[str, ...]
type _NaturalKey = tuple[tuple[tuple[tuple[int, int, str], ...], str], ...]

NO_PREVIOUS = -1
START_POSITION = -1
LABEL_SEPARATOR = "/"
REVERSED_PAIR = "the two reorders cannot both keep their order"


@dataclass(frozen=True)
class OrderConflict:
    """Holds why two edits of one order cannot combine.

    Attributes:
        reason: The elements involved and what each side did to them.
    """

    reason: str


@dataclass(frozen=True)
class _Run:
    """Holds elements one side inserted or moved together, after one anchor.

    Attributes:
        anchor: The element before the run that the side kept in place; None at the start.
        elements: The run's elements in that side's order.
    """

    anchor: Element | None
    elements: tuple[Element, ...]


@dataclass(frozen=True)
class _Edit:
    """Holds one side's edit of an order, read against the base.

    Attributes:
        moved: Base elements the side holds out of base order.
        removed: Base elements the side lacks.
        runs: The side's inserted and moved elements, grouped by anchor.
    """

    moved: frozenset[Element]
    removed: frozenset[Element]
    runs: tuple[_Run, ...]

    def run_of(self) -> dict[Element, _Run]:
        """Maps each inserted or moved element to its run."""
        return {element: run for run in self.runs for element in run.elements}


def _natural(element: Element) -> _NaturalKey:
    parts = (element,) if isinstance(element, str) else element
    return tuple(natural_key(part) for part in parts)


def _listed(elements: Iterable[Element]) -> str:
    ordered = sorted(elements, key=_natural)
    return ", ".join(e if isinstance(e, str) else LABEL_SEPARATOR.join(e) for e in ordered)


def _longest_increasing(values: Sequence[int]) -> set[int]:
    """Returns the positions of one longest increasing run of values, the same one every time."""
    tails: list[int] = []
    tail_values: list[int] = []
    previous = [NO_PREVIOUS] * len(values)
    for position, value in enumerate(values):
        slot = bisect_left(tail_values, value)
        if slot:
            previous[position] = tails[slot - 1]
        if slot == len(tails):
            tails.append(position)
            tail_values.append(value)
        else:
            tails[slot], tail_values[slot] = position, value
    kept: set[int] = set()
    position = tails[-1] if tails else NO_PREVIOUS
    while position != NO_PREVIOUS:
        kept.add(position)
        position = previous[position]
    return kept


def _runs(side: Sequence[Element], in_place: frozenset[Element]) -> tuple[_Run, ...]:
    runs: list[_Run] = []
    anchor: Element | None = None
    pending: list[Element] = []
    for element in side:
        if element not in in_place:
            pending.append(element)
            continue
        if pending:
            runs.append(_Run(anchor=anchor, elements=tuple(pending)))
            pending = []
        anchor = element
    if pending:
        runs.append(_Run(anchor=anchor, elements=tuple(pending)))
    return tuple(runs)


def _edit(index: Mapping[Element, int], side: Sequence[Element]) -> _Edit:
    present = [element for element in side if element in index]
    kept = _longest_increasing([index[element] for element in present])
    in_place = frozenset(present[position] for position in kept)
    return _Edit(
        moved=frozenset(present) - in_place,
        removed=frozenset(index) - frozenset(side),
        runs=_runs(side, in_place),
    )


def _conflicts(mine: _Edit, other: _Edit) -> list[str]:
    reasons: list[str] = []
    clash = (mine.moved & other.removed) | (other.moved & mine.removed)
    if clash:
        reasons.append(f"{_listed(clash)} moved on one side and removed on the other")
    my_runs, other_runs = mine.run_of(), other.run_of()
    differ = {e for e in my_runs.keys() & other_runs.keys() if my_runs[e] != other_runs[e]}
    if differ:
        reasons.append(f"{_listed(differ)} placed differently on the two sides")
    return reasons


def _landings(base: Sequence[Element], removed: frozenset[Element]) -> dict[Element, int]:
    """Maps each base element to the nearest position at or before it that no side removed.

    START_POSITION stands for the start when every earlier element was removed.
    """
    landing, nearest = {}, START_POSITION
    for position, element in enumerate(base):
        if element not in removed:
            nearest = position
        landing[element] = nearest
    return landing


def _attached(
    base: Sequence[Element], removed: frozenset[Element], runs: Iterable[_Run]
) -> dict[Element | None, list[_Run]]:
    """Groups the runs by the element they land after, None for the start, in landing order."""
    index = {element: position for position, element in enumerate(base)}
    landing = _landings(base, removed)
    found: dict[Element | None, list[tuple[tuple[int, tuple[_NaturalKey, ...]], _Run]]] = {}
    for run in set(runs):
        origin = START_POSITION if run.anchor is None else index[run.anchor]
        at = START_POSITION if run.anchor is None else landing[run.anchor]
        place = None if at == START_POSITION else base[at]
        order = (origin, tuple(_natural(element) for element in run.elements))
        found.setdefault(place, []).append((order, run))
    return {place: [run for _, run in sorted(entries)] for place, entries in found.items()}


def _laid_out(
    roots: Iterable[Element], attached: Mapping[Element | None, list[_Run]]
) -> list[Element]:
    """Lays out each root, each followed by the runs landing after it, depth first."""
    laid: list[Element] = []
    pending: list[Iterator[Element]] = [iter(roots)]
    while pending:
        element = next(pending[-1], None)
        if element is None:
            pending.pop()
            continue
        laid.append(element)
        pending.append(iter([e for run in attached.get(element, ()) for e in run.elements]))
    return laid


def _keeps_shared_order(
    ours: Sequence[Element], theirs: Sequence[Element], laid: Sequence[Element]
) -> bool:
    """Reports whether the laid-out order keeps every pair that both sides hold in one order.

    Walks our order with a max Fenwick tree over their positions: an element must come after,
    in the laid-out order, every element that both sides put before it.
    """
    their_position = {element: position for position, element in enumerate(theirs)}
    laid_position = {element: position for position, element in enumerate(laid)}
    latest = [START_POSITION] * (len(theirs) + 1)
    for element in ours:
        if element not in their_position:
            continue
        slot, earlier = their_position[element], START_POSITION
        while slot > 0:
            earlier, slot = max(earlier, latest[slot]), slot - (slot & -slot)
        if earlier > laid_position[element]:
            return False
        slot = their_position[element] + 1
        while slot < len(latest):
            latest[slot] = max(latest[slot], laid_position[element])
            slot += slot & -slot
    return True


def merge_order(
    base: Sequence[Element], ours: Sequence[Element], theirs: Sequence[Element]
) -> tuple[Element, ...] | OrderConflict:
    """Merges two edits of an ordered list of distinct elements, or reports why they conflict.

    Args:
        base: The order both sides started from.
        ours: One side's edit.
        theirs: The other side's edit.

    Returns:
        merged: The merged order, the same whichever side is which; or the conflict.

    >>> merge_order(["1", "2", "3"], ["2", "1", "3"], ["1", "2", "3", "4"])
    ('2', '1', '3', '4')
    >>> merge_order(["1", "2", "3"], ["2", "3", "1"], ["2", "3"]).reason
    '1 moved on one side and removed on the other'
    """
    if any(len(set(order)) != len(order) for order in (base, ours, theirs)):
        return OrderConflict(reason="an order lists one element twice")
    index = {element: position for position, element in enumerate(base)}
    mine, other = _edit(index, ours), _edit(index, theirs)
    reasons = _conflicts(mine, other)
    if reasons:
        return OrderConflict(reason="; ".join(reasons))
    removed = mine.removed | other.removed
    out_of_place = removed | mine.moved | other.moved
    attached = _attached(base, removed, (*mine.runs, *other.runs))
    starts = [element for run in attached.get(None, ()) for element in run.elements]
    laid = _laid_out([*starts, *(e for e in base if e not in out_of_place)], attached)
    stuck = {e for run in (*mine.runs, *other.runs) for e in run.elements} - set(laid)
    if stuck:
        return OrderConflict(reason=f"{_listed(stuck)} moved after each other on the two sides")
    if not _keeps_shared_order(ours, theirs, laid):
        return OrderConflict(reason=REVERSED_PAIR)
    return tuple(laid)
