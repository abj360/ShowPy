"""
examples --- deterministic example shows for docs, doctests and tests

Unlike other subpackages, this `__init__.py` re-exports its names so that
`from showpy.examples import two_set_show` stays the one import path.

Contains:
    sheets: the example sheet
    shows: the example shows and their variants
    publishing: example performers and publications
"""

from showpy.examples.publishing import EXAMPLE_LINK, EXAMPLE_TIME, live_publication, performer
from showpy.examples.sheets import DEPTH, WIDTH, field_sheet
from showpy.examples.shows import (
    COUNTS,
    EXAMPLE_TID,
    TEMPO,
    crossing_show,
    two_set_show,
    with_rows,
    with_targets,
)

__all__ = [
    "COUNTS",
    "DEPTH",
    "EXAMPLE_LINK",
    "EXAMPLE_TID",
    "EXAMPLE_TIME",
    "TEMPO",
    "WIDTH",
    "crossing_show",
    "field_sheet",
    "live_publication",
    "performer",
    "two_set_show",
    "with_rows",
    "with_targets",
]
