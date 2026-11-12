#!/usr/bin/env python3

"""
motion_shows.py --- re-exports the example helpers that store path rows and patch targets

Contains:
    with_rows(): returns the example show with path rows stored for transition 1→2
    with_targets(): returns the example show with set 2 placements patched
"""

from stage.examples import EXAMPLE_TID as TID
from stage.examples import with_rows, with_targets

__all__ = ["TID", "with_rows", "with_targets"]
