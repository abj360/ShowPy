"""
caps.py --- resource-safety caps on output sizes, checked before anything is allocated

Contains:
    MAX_POINTS: bounds the points one call returns
    MAX_TABLE: bounds the samples in a curve lookup table
    MAX_GRID_CELLS: bounds the cells of a raster
    MAX_RANGE: bounds the IDs one range expands to
    MAX_DEPTH: bounds the nesting of a selection expression
    MAX_TEXT_CHARS: bounds the characters of one parser input
    MAX_IMAGE_PIXELS: bounds the pixels of one raster image
    MAX_VIDEO_PIXELS: bounds the pixels of one video frame
    require_size(): rejects a requested size that is over its cap
"""

from showpy.core.errors import SchemaError, ShowPyError

MAX_POINTS = 1_000_000
MAX_TABLE = 1_000_000
MAX_GRID_CELLS = 4_000_000
MAX_RANGE = 10_000
MAX_DEPTH = 64
MAX_TEXT_CHARS = 1_000_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_VIDEO_PIXELS = 3840 * 2160


def require_size(
    requested: int, cap: int, *, what: str, error: type[ShowPyError] = SchemaError
) -> None:
    """Rejects a requested size that is over its cap, before the caller allocates it.

    Args:
        requested: Size the caller is about to allocate, such as width times height.
        cap: Largest size allowed.
        what: Name of the thing being sized, used in the message.
        error: Error class to raise.

    Examples:
        >>> from showpy.core.caps import MAX_POINTS, require_size
        >>> require_size(1_000, MAX_POINTS, what="points")
        >>> require_size(1_000_001, MAX_POINTS, what="points")
        Traceback (most recent call last):
            ...
        showpy.core.errors.SchemaError: SCHEMA: points would need 1000001, over the cap of \
1000000 (hint: ask for fewer points or use a coarser setting)
    """
    if requested <= cap:
        return
    raise error(
        f"{what} would need {requested}, over the cap of {cap}",
        hint=f"ask for fewer {what} or use a coarser setting",
        context={"what": what, "requested": requested, "cap": cap},
    )
