"""
data.py --- old path of the frame, table and format-registry names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from e8s.core.deprecation import moved, warn_leaving

LEAVING_TABLES = frozenset(
    {
        "FormatRegistry",
        "as_table",
        "export_frames_csv",
        "formats",
        "register_exporter",
        "register_importer",
        "to_csv",
        "to_text",
    }
)

_MOVED_TO_INTERCHANGE = moved(
    __name__,
    {
        "export_json": "e8s.show.interchange.export_json",
        "import_json": "e8s.show.interchange.import_json",
        "import_fonts": "e8s.show.interchange.import_fonts",
    },
    since="0.4.0",
    remove="0.6.0",
    extra_frames=1,
)


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("e8s.export._data")
    if name in LEAVING_TABLES:
        warn_leaving(
            f"e8s.export.data.{name}",
            since="0.4.0",
            remove="0.7.0",
            replacement="frames",
        )
        return getattr(private, name)
    return _MOVED_TO_INTERCHANGE(name)
