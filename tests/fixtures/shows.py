#!/usr/bin/env python3

"""
shows.py --- re-exports the library's example shows for tests

Contains:
    field_sheet(): builds the 40 x 20 m example sheet
    two_set_show(): builds four trumpets moving 4 m upstage over 8 counts
"""

from e8s.examples import COUNTS, DEPTH, TEMPO, WIDTH, field_sheet, two_set_show

__all__ = ["COUNTS", "DEPTH", "TEMPO", "WIDTH", "field_sheet", "two_set_show"]
