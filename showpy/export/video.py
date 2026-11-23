"""
video.py --- old path of the video names; warns on each use

Contains:
    __getattr__(): returns a leaving name from its private module and warns that it leaves
"""

import importlib

from showpy.core.deprecation import warn_leaving

LEAVING_PLAIN = frozenset({"export_video", "render_frame", "video_canvas"})


def __getattr__(name: str) -> object:
    """Returns a leaving name from its private module, warning that it leaves the library."""
    private = importlib.import_module("showpy.export._video")
    if name in LEAVING_PLAIN:
        warn_leaving(
            f"showpy.export.video.{name}",
            since="0.4.0",
            remove="0.6.0",
            replacement=None,
        )
        return getattr(private, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
