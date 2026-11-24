"""
examples --- deterministic example shows for docs, doctests and tests

Unlike other subpackages, this `__init__.py` re-exports its names so that
`from e8s.examples import two_set_show` stays the one import path.

Contains:
    sheets: the example sheet
    shows: the example shows and their variants
"""

from e8s.examples.sheets import DEPTH, WIDTH, field_sheet
from e8s.examples.shows import (
    COUNTS,
    EXAMPLE_TID,
    EXAMPLE_TIME,
    TEMPO,
    crossing_show,
    two_set_show,
    with_rows,
    with_targets,
)

__all__ = [
    "COUNTS",
    "DEPTH",
    "EXAMPLE_TID",
    "EXAMPLE_TIME",
    "TEMPO",
    "WIDTH",
    "crossing_show",
    "field_sheet",
    "two_set_show",
    "with_rows",
    "with_targets",
]
