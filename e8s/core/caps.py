"""
caps.py --- resource-safety caps on output sizes, checked before anything is allocated

Contains:
    MAX_POINTS: bounds the points one call returns
    MAX_TABLE: bounds the samples in a curve lookup table
    MAX_GRID_CELLS: bounds the cells of a raster
    MAX_RANGE: bounds the IDs one range expands to
    MAX_DEPTH: bounds the nesting of a selection expression
    MAX_TEXT_CHARS: bounds the characters of one parser input
    MIN_LAYOUT_HEIGHT_M: sets the smallest cap height text layout accepts, in meters
    MAX_COORD_M: bounds every stored coordinate and length, in meters; real shows stay within a
        few hundred meters, and a far larger value overflows geometry to NaN or infinity
    MAX_COUNTS: bounds every stored count; real shows stay under about 10,000 counts, and above
        2**23 counts one ulp exceeds the 1e-9-count clock tolerance
    MAX_EXTENSION_BYTES: bounds the UTF-8 JSON size of a show's `x-` extension values
    MIN_TEMPO: sets the slowest stored tempo, in counts per minute; anything slower is not a
        performance and drives the clock to infinity
    MAX_SPOTS: bounds the spots of one cast; a large ensemble has a few hundred
    MAX_SETS: bounds the sets of one show; a long show has a few hundred
    require_size(): rejects a requested size that is negative or over its cap
"""

from e8s._core.core import MAX_COORD_M as MAX_COORD_M
from e8s._core.core import MAX_COUNTS as MAX_COUNTS
from e8s._core.core import MAX_DEPTH as MAX_DEPTH
from e8s._core.core import MAX_EXTENSION_BYTES as MAX_EXTENSION_BYTES
from e8s._core.core import MAX_GRID_CELLS as MAX_GRID_CELLS
from e8s._core.core import MAX_POINTS as MAX_POINTS
from e8s._core.core import MAX_RANGE as MAX_RANGE
from e8s._core.core import MAX_SETS as MAX_SETS
from e8s._core.core import MAX_SPOTS as MAX_SPOTS
from e8s._core.core import MAX_TABLE as MAX_TABLE
from e8s._core.core import MAX_TEXT_CHARS as MAX_TEXT_CHARS
from e8s._core.core import MIN_LAYOUT_HEIGHT_M as MIN_LAYOUT_HEIGHT_M
from e8s._core.core import MIN_TEMPO as MIN_TEMPO
from e8s._core.core import require_size as require_size
