"""
print_sheets.py --- old path of the coordinate sheet, chart and cue sheet names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from showpy.core.deprecation import warn_leaving

PRINT_REPLACEMENTS = ("coordinate_table", "describe_transition")
LEAVING_PRINT = frozenset({"chart", "coordinate_sheet", "coordinate_sheets", "cue_sheet"})


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("showpy.export._print_sheets")
    if name in LEAVING_PRINT:
        warn_leaving(
            f"showpy.export.print_sheets.{name}",
            since="0.4.0",
            remove="0.7.0",
            replacement=PRINT_REPLACEMENTS,
        )
        return getattr(private, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
