"""
models.py --- old path of the export data classes names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from showpy.core.deprecation import warn_leaving

PRINT_REPLACEMENTS = ("coordinate_table", "describe_transition")
LEAVING_PLAIN = frozenset({"Claim", "Performer", "PerformerView", "Publication"})
LEAVING_PRINT = frozenset({"Chart", "ChartRow", "CoordinateSheet", "SheetRow"})
LEAVING_TABLES = frozenset({"TextTable"})


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("showpy.export._models")
    if name in LEAVING_PLAIN:
        warn_leaving(
            f"showpy.export.models.{name}",
            since="0.4.0",
            remove="0.6.0",
            replacement=None,
        )
        return getattr(private, name)
    if name in LEAVING_PRINT:
        warn_leaving(
            f"showpy.export.models.{name}",
            since="0.4.0",
            remove="0.7.0",
            replacement=PRINT_REPLACEMENTS,
        )
        return getattr(private, name)
    if name in LEAVING_TABLES:
        warn_leaving(
            f"showpy.export.models.{name}",
            since="0.4.0",
            remove="0.7.0",
            replacement="frames",
        )
        return getattr(private, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
