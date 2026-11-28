"""
strict_json.py --- parses stored and imported JSON strictly, so two readers never disagree on it

Standard JSON has no NaN or infinity, and a document with a repeated key or a lone surrogate
reads differently in different parsers; a byte-order mark hides the UTF-8 encoding. All four
are refused, each with SchemaError code STRICT_JSON naming the document. A number too large for
a float, such as 1e400, parses to infinity and is refused when the model validates, not here.

Contains:
    UTF8_BOM: the byte-order mark a document may not start with
    STRICT_HINT: the hint of a STRICT_JSON error
    SURROGATE_HINT: the hint of the STRICT_JSON error for a lone surrogate
    json_nesting(): measures how deep arrays and objects nest in JSON bytes, without recursing
    require_nesting(): refuses JSON bytes nested deeper than MAX_NESTING levels
    parse_strict_json(): parses UTF-8 JSON bytes, refusing non-standard tokens, repeated keys,
        a byte-order mark and lone surrogates
    strict_json_copy(): copies an already parsed JSON value through the strict parser
"""

from e8s._core.core import STRICT_HINT as STRICT_HINT
from e8s._core.core import SURROGATE_HINT as SURROGATE_HINT
from e8s._core.core import UTF8_BOM as UTF8_BOM
from e8s._core.core import json_nesting as json_nesting
from e8s._core.core import parse_strict_json as parse_strict_json
from e8s._core.core import require_nesting as require_nesting
from e8s._core.core import strict_json_copy as strict_json_copy
