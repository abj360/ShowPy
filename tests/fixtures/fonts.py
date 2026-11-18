#!/usr/bin/env python3

"""
fonts.py --- a tiny test font shared by the font and text layout tests

Contains:
    TINY: a showpy.font.v1 document named "tiny" with one glyph, I, on a 10-unit em
    tiny_bytes(): encodes TINY, with any top-level fields replaced, as font file bytes
"""

import json
from typing import Any

TINY: dict[str, Any] = {
    "format": "showpy.font.v1",
    "name": "tiny",
    "family": "Tiny",
    "license": "MIT",
    "source": "tests",
    "units_per_em": 10,
    "cap_height": 10,
    "ascender": 10,
    "descender": 0,
    "glyphs": {"I": {"advance": 4, "strokes": ["M2 0 L2 10"]}},
}


def tiny_bytes(**changes: Any) -> bytes:
    """Encodes TINY, with the given top-level fields replaced, as font file bytes."""
    return json.dumps({**TINY, **changes}).encode()
