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
    MIN_LAYOUT_HEIGHT_M: sets the smallest cap height text layout accepts, in meters
    MAX_COORD_M: bounds every stored coordinate and length, in meters; real shows stay within a
        few hundred meters, and a far larger value overflows geometry to NaN or infinity
    MAX_COUNTS: bounds every stored count; real shows stay under about 10,000 counts, and above
        2**23 counts one ulp exceeds the 1e-9-count clock tolerance
    MAX_EXTENSION_BYTES: bounds the UTF-8 JSON size of a show's `x-` extension values
    MIN_TEMPO: sets the slowest stored tempo, in counts per minute; anything slower is not a
        performance and drives the clock to infinity
    require_size(): rejects a requested size that is negative or over its cap
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
MIN_LAYOUT_HEIGHT_M = 0.001
MAX_COORD_M = 1e6
MAX_COUNTS = 1_000_000
MIN_TEMPO = 1e-3
# One mebibyte: host metadata is a few settings or ids; a larger value is a payload that belongs
# in an asset, and a cap keeps one document from carrying a hostile blob through every save.
MAX_EXTENSION_BYTES = 1_048_576


def require_size(
    requested: int, cap: int, *, what: str, error: type[ShowPyError] = SchemaError
) -> None:
    """Rejects a requested size that is negative or over its cap, before the caller allocates it.

    Args:
        requested: Size the caller is about to allocate, such as width times height; a negative
            size is rejected too.
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
    if 0 <= requested <= cap:
        return
    if requested < 0:
        raise error(
            f"{what} would need {requested}, which is negative",
            hint=f"ask for zero or more {what}",
            context={"what": what, "requested": requested, "cap": cap},
        )
    raise error(
        f"{what} would need {requested}, over the cap of {cap}",
        hint=f"ask for fewer {what} or use a coarser setting",
        context={"what": what, "requested": requested, "cap": cap},
    )
