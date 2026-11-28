"""
core.pyi --- typing stub of e8s._core.core, the compiled counterpart of e8s.core

Contains:
    _finite_probe(): returns a finite number and raises SchemaError for NaN or an infinity
    _cache_probe(): caches a converted Box2 and returns the cached value's address
    _cache_stats(): returns the probe cache's hits, misses and live entries
    _pcg64_draws(): draws integers, floats and a sample from the seeded PCG64 generator
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
    canonical_value(): normalizes a supported value into the JSON data model
    jcs(): serializes a value as RFC 8785 (JCS) canonical JSON bytes
    content_hash(): hashes bytes, text or canonical JSON under a domain tag
    asset_uri(): names bytes by the `asset:sha256:<hex>` URI an asset store returns
    json_nesting(): measures how deep arrays and objects nest in JSON bytes, without recursing
    require_nesting(): refuses JSON bytes nested deeper than MAX_NESTING levels
    parse_strict_json(): parses UTF-8 JSON bytes strictly
    strict_json_copy(): copies an already parsed JSON value through the strict parser
    noise_rank(): rounds a float that decides an order so 1-ulp noise ties
    approx_equal(): compares floats or arrays within absolute and relative tolerances
    chunk_counts(): splits counts into equal parts that sum exactly
    natural_key(): returns the sort key that puts T2 before T10
    natural_sort(): sorts IDs so T2 comes before T10
    split_id(): splits an ID into its prefix and trailing number
    parse_range(): expands `T1-T8` into IDs
    format_ranges(): compacts IDs into `T1-T4, T7`
    close_matches(): suggests known names close to a misspelled one
    neutralize_cell(): prefixes a formula-looking cell with a quote
    MAX_POINTS, MAX_TABLE, MAX_GRID_CELLS, MAX_RANGE, MAX_DEPTH, MAX_TEXT_CHARS,
        MIN_LAYOUT_HEIGHT_M, MAX_COORD_M, MAX_COUNTS, MAX_EXTENSION_BYTES, MIN_TEMPO, MAX_SPOTS,
        MAX_SETS: the resource-safety caps of e8s.core.caps
    TURN_DEG, DEFAULT_DECIMALS, MAX_DECIMALS, DEFAULT_STEP, ZERO_VECTOR_TOL: the constants of
        e8s.core.numbers and e8s.core.units
    ID_MAX_LENGTH: bounds the characters of one ID
    RESERVED_IDS: names no ID may take, compared ignoring case
    ID_PATTERN_SOURCE, SHEET_ID_PATTERN_SOURCE, VERSION_PATTERN_SOURCE: state the ID shapes
    TAG_PREFIX, MAP_TAG, MAX_NESTING, MAX_INT_DIGITS, INT_LIMIT, CONTEXT_TEXT_CHARS: the
        constants of the canonical form
    HASH_PREFIX, ASSET_PREFIX: the frozen hash header tag and the asset URI scheme
    UTF8_BOM, STRICT_HINT, SURROGATE_HINT: the strict JSON reader's refused mark and hints
    FORMULA_TRIGGERS, FORMULA_GUARD, PLAIN_DECIMAL: the CSV formula triggers, the guard prefix
        and the plain decimal pattern
"""

import re
from collections.abc import Iterable

import numpy as np
import numpy.typing as npt

from e8s.core.errors import E8sError
from e8s.core.json_types import JsonValue
from e8s.core.types import Box2, Degrees, Point

__all__ = [
    "ASSET_PREFIX",
    "CONTEXT_TEXT_CHARS",
    "DEFAULT_DECIMALS",
    "DEFAULT_STEP",
    "FORMULA_GUARD",
    "FORMULA_TRIGGERS",
    "HASH_PREFIX",
    "ID_MAX_LENGTH",
    "ID_PATTERN_SOURCE",
    "INT_LIMIT",
    "MAP_TAG",
    "MAX_COORD_M",
    "MAX_COUNTS",
    "MAX_DECIMALS",
    "MAX_DEPTH",
    "MAX_EXTENSION_BYTES",
    "MAX_GRID_CELLS",
    "MAX_INT_DIGITS",
    "MAX_NESTING",
    "MAX_POINTS",
    "MAX_RANGE",
    "MAX_SETS",
    "MAX_SPOTS",
    "MAX_TABLE",
    "MAX_TEXT_CHARS",
    "MIN_LAYOUT_HEIGHT_M",
    "MIN_TEMPO",
    "PLAIN_DECIMAL",
    "RESERVED_IDS",
    "SHEET_ID_PATTERN_SOURCE",
    "STRICT_HINT",
    "SURROGATE_HINT",
    "TAG_PREFIX",
    "TURN_DEG",
    "UTF8_BOM",
    "VERSION_PATTERN_SOURCE",
    "ZERO_VECTOR_TOL",
    "angle_diff",
    "approx_equal",
    "asset_uri",
    "canonical_value",
    "check_id",
    "check_sheet_id",
    "chunk_counts",
    "close_matches",
    "content_hash",
    "facing_vector",
    "find_case_clashes",
    "format_number",
    "format_ranges",
    "format_ref",
    "format_tid",
    "is_device_name",
    "jcs",
    "json_nesting",
    "natural_key",
    "natural_sort",
    "neutralize_cell",
    "noise_rank",
    "normalize_angle",
    "parse_range",
    "parse_ref",
    "parse_strict_json",
    "require_nesting",
    "require_size",
    "round_to",
    "split_id",
    "split_tid",
    "strict_json_copy",
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
TAG_PREFIX: str
MAP_TAG: str
MAX_NESTING: int
MAX_INT_DIGITS: int
INT_LIMIT: int
CONTEXT_TEXT_CHARS: int
HASH_PREFIX: str
ASSET_PREFIX: str
UTF8_BOM: bytes
STRICT_HINT: str
SURROGATE_HINT: str
FORMULA_TRIGGERS: tuple[str, ...]
FORMULA_GUARD: str
PLAIN_DECIMAL: re.Pattern[str]

def _finite_probe(value: float) -> float: ...
def _cache_probe(part: Box2) -> int: ...
def _cache_stats() -> dict[str, int]: ...
def _pcg64_draws(
    seed: int, high: int, n: int, count: int
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], npt.NDArray[np.int64]]: ...
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
def canonical_value(obj: object) -> JsonValue: ...
def jcs(obj: object) -> bytes: ...
def content_hash(obj: object, *, domain: str = "showpy.content") -> str: ...
def asset_uri(data: bytes) -> str: ...
def json_nesting(raw: bytes) -> int: ...
def require_nesting(raw: bytes, *, document: str) -> None: ...
def parse_strict_json(raw: bytes, *, document: str) -> JsonValue: ...
def strict_json_copy(value: object, *, document: str) -> JsonValue: ...
def noise_rank(value: float) -> float: ...
def approx_equal(
    a: npt.ArrayLike, b: npt.ArrayLike, *, abs_tol: float = 1e-6, rel_tol: float = 0.0
) -> bool: ...
def chunk_counts(total: float, parts: int) -> list[float]: ...
def natural_key(text: str) -> tuple[tuple[tuple[int, int, str], ...], str]: ...
def natural_sort[T: str](ids: Iterable[T]) -> list[T]: ...
def split_id(text: str) -> tuple[str, int | None]: ...
def parse_range(text: str) -> list[str]: ...
def format_ranges(ids: Iterable[str]) -> str: ...
def close_matches(name: str, candidates: Iterable[str]) -> list[str]: ...
def neutralize_cell(cell: str) -> str: ...
