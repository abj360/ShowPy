#!/usr/bin/env python3

"""
text.py --- a built-in single-stroke font so letters can be spelled with spots

Glyphs sit on a 4 x 6 grid (x right, y up, baseline at 0). Strokes are polylines, which
space evenly along their length, unlike filled outline fonts.

Contains:
    FONTS: maps font names to glyph tables
    glyph_strokes(): returns the strokes of one character on the unit grid
"""

from stage.core.errors import UnknownRefError

type Stroke = tuple[tuple[float, float], ...]
type Glyph = tuple[Stroke, ...]

GLYPH_WIDTH = 4.0
GLYPH_HEIGHT = 6.0
ADVANCE = 5.0

_O: Stroke = ((1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0))
_P: Stroke = ((0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3))

SIMPLEX: dict[str, Glyph] = {
    " ": (),
    "A": (((0, 0), (2, 6), (4, 0)), ((1, 3), (3, 3))),
    "B": (
        ((0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)),
        ((3, 3), (4, 2), (4, 1), (3, 0), (0, 0)),
    ),
    "C": (((4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)),),
    "D": (((0, 0), (0, 6), (3, 6), (4, 5), (4, 1), (3, 0), (0, 0)),),
    "E": (((4, 6), (0, 6), (0, 0), (4, 0)), ((0, 3), (3, 3))),
    "F": (((4, 6), (0, 6), (0, 0)), ((0, 3), (3, 3))),
    "G": (((4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)),),
    "H": (((0, 0), (0, 6)), ((4, 0), (4, 6)), ((0, 3), (4, 3))),
    "I": (((1, 6), (3, 6)), ((2, 6), (2, 0)), ((1, 0), (3, 0))),
    "J": (((4, 6), (4, 1), (3, 0), (1, 0), (0, 1)),),
    "K": (((0, 0), (0, 6)), ((4, 6), (0, 2)), ((1, 3), (4, 0))),
    "L": (((0, 6), (0, 0), (4, 0)),),
    "M": (((0, 0), (0, 6), (2, 3), (4, 6), (4, 0)),),
    "N": (((0, 0), (0, 6), (4, 0), (4, 6)),),
    "O": (_O,),
    "P": (_P,),
    "Q": (_O, ((2, 2), (4, 0))),
    "R": (_P, ((2, 3), (4, 0))),
    "S": (
        (
            (4, 5),
            (3, 6),
            (1, 6),
            (0, 5),
            (0, 4),
            (1, 3),
            (3, 3),
            (4, 2),
            (4, 1),
            (3, 0),
            (1, 0),
            (0, 1),
        ),
    ),
    "T": (((0, 6), (4, 6)), ((2, 6), (2, 0))),
    "U": (((0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)),),
    "V": (((0, 6), (2, 0), (4, 6)),),
    "W": (((0, 6), (1, 0), (2, 4), (3, 0), (4, 6)),),
    "X": (((0, 6), (4, 0)), ((0, 0), (4, 6))),
    "Y": (((0, 6), (2, 3), (4, 6)), ((2, 3), (2, 0))),
    "Z": (((0, 6), (4, 6), (0, 0), (4, 0)),),
    "0": (_O, ((0, 1), (4, 5))),
    "1": (((1, 5), (2, 6), (2, 0)), ((1, 0), (3, 0))),
    "2": (((0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)),),
    "3": (
        ((0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3)),
        ((3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)),
    ),
    "4": (((3, 0), (3, 6), (0, 2), (4, 2)),),
    "5": (((4, 6), (0, 6), (0, 3), (3, 3), (4, 2), (4, 1), (3, 0), (0, 0)),),
    "6": (
        (
            (4, 5),
            (3, 6),
            (1, 6),
            (0, 5),
            (0, 1),
            (1, 0),
            (3, 0),
            (4, 1),
            (4, 2),
            (3, 3),
            (0, 3),
        ),
    ),
    "7": (((0, 6), (4, 6), (1, 0)),),
    "8": (
        ((1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3)),
        ((1, 3), (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3)),
    ),
    "9": (((4, 3), (1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)),),
    "-": (((1, 3), (3, 3)),),
    ".": (((2, 0), (2, 0.5)),),
}

FONTS: dict[str, dict[str, Glyph]] = {"simplex": SIMPLEX}


def glyph_strokes(char: str, font: str) -> Glyph:
    """Returns the strokes of one character on the 4 x 6 grid; letters are case-insensitive.

    >>> len(glyph_strokes("h", "simplex"))
    3
    """
    if font not in FONTS:
        raise UnknownRefError(
            f"unknown font {font!r}", hint=f"use one of {sorted(FONTS)}", context={"font": font}
        )
    glyphs = FONTS[font]
    key = char.upper()
    if key not in glyphs:
        raise UnknownRefError(
            f"font {font!r} has no glyph for {char!r}",
            hint="use A-Z, 0-9, space, '-' or '.'",
            context={"char": char},
        )
    return glyphs[key]
