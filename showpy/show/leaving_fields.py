"""
leaving_fields.py --- warns when code outside the library reads a stored Show field that leaves

Reading `Show.coordinates` or `Show.zones` from outside the library warns; the library's own
reads and the decoding of stored shows do not.

Contains:
    read_coordinates(): returns a show's coordinate system, warning when the caller is outside
    read_zones(): returns a show's zones, warning when the caller is outside
"""

import sys
from typing import Final

from showpy.core.deprecation import warn_leaving

LIBRARY_MODULE_PREFIXES: Final = ("showpy.", "pydantic.")
CALLER_FRAME_DEPTH: Final = 2


def _is_library_caller() -> bool:
    """Tells whether the code that read a field belongs to showpy or to pydantic."""
    module = str(sys._getframe(CALLER_FRAME_DEPTH).f_globals.get("__name__", ""))
    return module == "showpy" or module.startswith(LIBRARY_MODULE_PREFIXES)


def read_coordinates(show: object) -> object:
    """Returns the stored coordinate system of a show, warning when the caller is outside.

    Args:
        show: The show whose field is read.

    Returns:
        coordinates: The show's `coordinates` value.
    """
    if not _is_library_caller():
        warn_leaving("showpy.Show.coordinates", since="0.4.0", remove="0.7.0")
    return vars(show)["coordinates"]


def read_zones(show: object) -> object:
    """Returns the stored show zones of a show, warning when the caller is outside.

    Args:
        show: The show whose field is read.

    Returns:
        zones: The show's `zones` value.
    """
    if not _is_library_caller():
        warn_leaving("showpy.Show.zones", since="0.4.0", remove="0.7.0")
    return vars(show)["zones"]
