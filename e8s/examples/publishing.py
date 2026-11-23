"""
publishing.py --- example performers and publications for publishing doctests and tests

Contains:
    EXAMPLE_TIME: fixed time stamp for examples that need one
    EXAMPLE_LINK: link of the example publication
    performer(): builds a performer whose ID is the lower-case name
    live_publication(): returns a live, unclaimed publication of a show
"""

from datetime import UTC, datetime

from e8s.export._models import Performer, PerformerRole, Publication
from e8s.show.models import Show

EXAMPLE_TIME = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
EXAMPLE_LINK = "https://eight.example/p/pub-1"


def performer(name: str, role: PerformerRole = "performer") -> Performer:
    """Builds a performer whose ID is the lower-case name.

    >>> performer("Ada").id
    'ada'
    """
    return Performer(id=name.lower(), name=name, role=role)


def live_publication(show: Show) -> Publication:
    """Returns a live publication of `show` with no claims.

    >>> from e8s.examples import two_set_show
    >>> live_publication(two_set_show()).status
    'live'
    """
    return Publication(
        id="pub-1", show_id=show.id, revision="r1", link=EXAMPLE_LINK, published_at=EXAMPLE_TIME
    )
