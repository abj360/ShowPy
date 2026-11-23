"""
pdf.py --- old path of the PDF names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from e8s.core.deprecation import warn_leaving

PRINT_REPLACEMENTS = ("coordinate_table", "describe_transition")
LEAVING_PRINT = frozenset({"escape_text", "to_pdf"})


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("e8s.export._pdf")
    if name in LEAVING_PRINT:
        warn_leaving(
            f"e8s.export.pdf.{name}",
            since="0.4.0",
            remove="0.7.0",
            replacement=PRINT_REPLACEMENTS,
        )
        return getattr(private, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
