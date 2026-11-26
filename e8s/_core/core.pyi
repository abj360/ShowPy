"""
core.pyi --- typing stub of e8s._core.core, the compiled counterpart of e8s.core

Contains:
    _finite_probe(): returns a finite number and raises SchemaError for NaN or an infinity
    _cache_probe(): caches a converted Box2 and returns the cached value's address
    _cache_stats(): returns the probe cache's hits, misses and live entries
    format_number(): writes a float in plain decimal notation, never in exponent form
    normalize_angle(): wraps an angle into [0, 360) and rejects NaN and infinity
    angle_diff(): returns the signed shortest turn from a to b
    facing_vector(): converts a facing to a unit direction
    vector_facing(): converts a direction to a facing
    round_to(): rounds half away from zero to a step
    require_size(): rejects a requested size that is negative or over its cap
    parse_ref(): reads `kind:id@version` into its parts, checking the id and version
    format_ref(): writes a reference from its parts
    split_tid(): reads `12→13` or `12->13` into its two set IDs
    format_tid(): writes a transition ID as `12→13`, refusing anything but a pair
    check_id(): validates one ID and returns its NFC-normalized text
    check_sheet_id(): validates one sheet item or sheet reference ID the same way
    is_device_name(): tells whether text is a Windows device name
    find_case_clashes(): lists pairs of IDs that differ only by case
    MAX_POINTS, MAX_TABLE, MAX_GRID_CELLS, MAX_RANGE, MAX_DEPTH, MAX_TEXT_CHARS,
        MIN_LAYOUT_HEIGHT_M, MAX_COORD_M, MAX_COUNTS, MAX_EXTENSION_BYTES, MIN_TEMPO, MAX_SPOTS,
        MAX_SETS: the resource-safety caps of e8s.core.caps
    TURN_DEG, DEFAULT_DECIMALS, MAX_DECIMALS, DEFAULT_STEP, ZERO_VECTOR_TOL: the constants of
        e8s.core.numbers and e8s.core.units
    ID_MAX_LENGTH: bounds the characters of one ID
    RESERVED_IDS: names no ID may take, compared ignoring case
    ID_PATTERN_SOURCE, SHEET_ID_PATTERN_SOURCE, VERSION_PATTERN_SOURCE: state the ID shapes
"""

from collections.abc import Iterable

from e8s.core.errors import E8sError
from e8s.core.types import Box2, Degrees, Point

__all__ = [
    "DEFAULT_DECIMALS",
    "DEFAULT_STEP",
    "ID_MAX_LENGTH",
    "ID_PATTERN_SOURCE",
    "MAX_COORD_M",
    "MAX_COUNTS",
    "MAX_DECIMALS",
    "MAX_DEPTH",
    "MAX_EXTENSION_BYTES",
    "MAX_GRID_CELLS",
    "MAX_POINTS",
    "MAX_RANGE",
    "MAX_SETS",
    "MAX_SPOTS",
    "MAX_TABLE",
    "MAX_TEXT_CHARS",
    "MIN_LAYOUT_HEIGHT_M",
    "MIN_TEMPO",
    "RESERVED_IDS",
    "SHEET_ID_PATTERN_SOURCE",
    "TURN_DEG",
    "VERSION_PATTERN_SOURCE",
    "ZERO_VECTOR_TOL",
    "angle_diff",
    "check_id",
    "check_sheet_id",
    "facing_vector",
    "find_case_clashes",
    "format_number",
    "format_ref",
    "format_tid",
    "is_device_name",
    "normalize_angle",
    "parse_ref",
    "require_size",
    "round_to",
    "split_tid",
    "vector_facing",
]

TURN_DEG: float
DEFAULT_DECIMALS: int
MAX_DECIMALS: int
DEFAULT_STEP: float
ZERO_VECTOR_TOL: float
MAX_POINTS: int
MAX_TABLE: int
MAX_GRID_CELLS: int
MAX_RANGE: int
MAX_DEPTH: int
MAX_TEXT_CHARS: int
MIN_LAYOUT_HEIGHT_M: float
MAX_COORD_M: float
MAX_COUNTS: int
MAX_EXTENSION_BYTES: int
MIN_TEMPO: float
MAX_SPOTS: int
MAX_SETS: int
ID_MAX_LENGTH: int
RESERVED_IDS: frozenset[str]
ID_PATTERN_SOURCE: str
SHEET_ID_PATTERN_SOURCE: str
VERSION_PATTERN_SOURCE: str

def _finite_probe(value: float) -> float: ...
def _cache_probe(part: Box2) -> int: ...
def _cache_stats() -> dict[str, int]: ...
def format_number(value: float, *, max_decimals: int = 6) -> str: ...
def normalize_angle(deg: float) -> float: ...
def angle_diff(a: Degrees, b: Degrees) -> Degrees: ...
def facing_vector(theta: Degrees) -> Point: ...
def vector_facing(v: Point) -> Degrees: ...
def round_to(value: float, step: float = 0.01) -> float: ...
def require_size(requested: int, cap: int, *, what: str, error: type[E8sError] = ...) -> None: ...
def parse_ref(text: str) -> tuple[str, str, str | None]: ...
def format_ref(kind: str, ref_id: str, version: str | None = None) -> str: ...
def split_tid(text: str) -> tuple[str, str]: ...
def format_tid(tid: tuple[str, str]) -> str: ...
def check_id(text: str, *, kind: str) -> str: ...
def check_sheet_id(text: str, *, kind: str) -> str: ...
def is_device_name(text: str) -> bool: ...
def find_case_clashes(ids: Iterable[str]) -> list[tuple[str, str]]: ...
