"""
set_queries.py --- reads a show's set order and clock: transitions, index, count lookups

Contains:
    transitions(): lists consecutive set pairs
    index(): computes each set's start count, measure and seconds
    set_at_count(): returns the set reached exactly at a count
    transition_at_count(): returns the transition a count lies in plus its local count
"""

from e8s.core.ids import (
    SetId,
    TransitionId,
)
from e8s.core.types import Count
from e8s.show.models import Show, ShowIndex, ShowIndexEntry
from e8s.time.clock import count_to_seconds
from e8s.time.measures import count_to_measure


def transitions(show: Show) -> list[TransitionId]:
    """Lists consecutive set pairs in show order.

    >>> from e8s.examples import two_set_show
    >>> transitions(two_set_show())
    [TransitionId(from_set='1', to_set='2')]
    """
    return show.transition_ids()


def index(show: Show) -> ShowIndex:
    """Computes each set's start count, measure label and seconds; the first set starts at 0.

    >>> from e8s.examples import two_set_show
    >>> [(e.set, e.start_count, e.seconds) for e in index(two_set_show()).entries]
    [('1', 0.0, 0.0), ('2', 8.0, 4.0)]
    """
    entries: list[ShowIndexEntry] = []
    start = 0.0
    for position, current in enumerate(show.sets):
        start += current.counts if position > 0 else 0.0
        entries.append(
            ShowIndexEntry(
                set=current.id,
                counts=current.counts,
                start_count=start,
                measure=count_to_measure(show.timeline, start),
                seconds=count_to_seconds(show.timeline, start),
                cue=current.cue,
            )
        )
    return ShowIndex(entries=tuple(entries))


def set_at_count(show: Show, c: Count) -> SetId | None:
    """Returns the set reached exactly at count `c`, or None while moving between sets.

    >>> from e8s.examples import two_set_show
    >>> set_at_count(two_set_show(), 8.0)
    '2'
    """
    return index(show).set_at(c)


def transition_at_count(show: Show, c: Count) -> tuple[TransitionId, Count] | None:
    """Returns the transition count `c` lies in, `start(a) < c < start(b)`, plus local count tau.

    >>> from e8s.examples import two_set_show
    >>> transition_at_count(two_set_show(), 2.0)
    (TransitionId(from_set='1', to_set='2'), 2.0)
    """
    return index(show).transition_at(c)
