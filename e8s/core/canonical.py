"""
canonical.py --- normalizes values to the JSON data model and encodes them per RFC 8785

Contains:
    JsonValue: the JSON data model every canonical value belongs to, re-exported from json_types
    TAG_PREFIX: the key prefix that marks type tags
    MAP_TAG: the single key of the tagged form of a mapping with a key that is not text
    MAX_NESTING: the deepest container nesting a canonical value may hold
    MAX_INT_DIGITS: the most digits a canonical int may have
    INT_LIMIT: 10**MAX_INT_DIGITS, the bound every canonical int stays under
    CONTEXT_TEXT_CHARS: caps the characters of text an error repeats
    canonical_value(): normalizes a supported value into the JSON data model
    jcs(): serializes a value as RFC 8785 (JCS) canonical JSON bytes
"""

from e8s._core.core import CONTEXT_TEXT_CHARS as CONTEXT_TEXT_CHARS
from e8s._core.core import INT_LIMIT as INT_LIMIT
from e8s._core.core import MAP_TAG as MAP_TAG
from e8s._core.core import MAX_INT_DIGITS as MAX_INT_DIGITS
from e8s._core.core import MAX_NESTING as MAX_NESTING
from e8s._core.core import TAG_PREFIX as TAG_PREFIX
from e8s._core.core import canonical_value as canonical_value
from e8s._core.core import jcs as jcs
from e8s.core.json_types import JsonValue as JsonValue
