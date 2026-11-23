"""
fonts.py --- loads, validates and registers stroke fonts by SHA-256 asset URI

Contains:
    MAX_FONT_BYTES: bounds the size of one font file
    MAX_GLYPH_PIECES: bounds the path pieces of one glyph, over all its strokes
    MAX_STROKE_PIECES: bounds the path pieces of one stroke
    BUNDLED_FONTS: maps each bundled font's name to the URI of its exact file bytes
    FontRegistry: holds loaded fonts, their source bytes and their names, all read-only; checks
        on construction that every URI hashes its bytes, every font is what its bytes load to,
        and every name names its font
    FontRegistry.get(): returns the font stored under a URI
    FontRegistry.resolve(): turns a font name or URI into a URI
    FontRegistry.source(): returns the exact bytes a font was loaded from
    load_font(): validates font file bytes into a Font
    register_font(): returns a registry that also holds one more font, and its URI
    bundled_fonts(): returns the cached registry of the bundled fonts
"""

import functools
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Literal, Self

import numpy as np
from pydantic import model_validator

from e8s.core.caps import require_size
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    FontError,
    MissingFontError,
    SchemaError,
    field_errors,
)
from e8s.core.readonly import ReadOnly, ReadOnlyMap
from e8s.core.resources import font_path
from e8s.core.strict_json import parse_strict_json
from e8s.core.types import FrozenModel
from e8s.core.util import ASSET_PREFIX, asset_uri, close_matches
from e8s.geo.font_strokes import sample_subpaths, stroke_length
from e8s.geo.models import Font
from e8s.geo.svg import Subpath, parse_path

MAX_FONT_BYTES = 8 * 1024 * 1024
# The most complex bundled glyph, hershey-script "8", has 51 path pieces, and the longest bundled
# stroke, hershey-script "Z", has 38 (measured 2026-11-16); each cap is about 10 times that.
MAX_GLYPH_PIECES = 512
MAX_STROKE_PIECES = 384
BUNDLED_FONT_HASHES = MappingProxyType(
    {
        "relief-singleline": "fd67c5b12e38ab886258f5bc16611b3f61f0bcc2d5711dc5ddf8fd42bc8f4b55",
        "hershey-roman": "e1969f4711a74be259a5e602da900a65b94c5f45f69c33e32ab4eeb86a2c83cb",
        "hershey-script": "1f0a661303e872ffd3ac5c8abdd8804519c2174696c1f8f04ac1aaee37c5a9d0",
        "hershey-greek": "43f6945dde1eea1a5b51295a518f7cb8c1a12fd04d2df536ef4ab37cb2533cd1",
        "simplex": "7e345d1ae7a55abc2d6414c6b16d121bdaddba1ef1fb5d82dec483e4bac20e3e",
    }
)
BUNDLED_FONTS: Mapping[str, str] = MappingProxyType(
    {name: f"{ASSET_PREFIX}{digest}" for name, digest in BUNDLED_FONT_HASHES.items()}
)
MISSING_FONT_HINT = "register it with register_font(registry, data) or import_fonts(...)"

type NameTaken = Literal["raise", "keep"]


class FontRegistry(FrozenModel):
    """Holds loaded fonts, their source bytes and their names, all read-only.

    A URI is a font's identity; a name is a convenience. A font whose name is bound to other
    bytes, such as an older release imported from a document, is reachable by URI only.
    Validating a registry from data loads every font from its bytes once and compares;
    `register_font` skips that, since it loads the one font it adds.

    Attributes:
        fonts: Loaded fonts keyed by `asset:sha256:<hex>` URI.
        sources: Exact bytes of each font file keyed by URI.
        names: URI of a font keyed by its short name; at most one font per name.
    """

    fonts: Annotated[Mapping[str, Font], ReadOnly()]
    sources: Annotated[Mapping[str, bytes], ReadOnly()]
    names: Annotated[Mapping[str, str], ReadOnly()]

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if set(self.fonts) != set(self.sources):
            raise _bad_font(
                "registry fonts and sources hold different URIs",
                hint="give every font its source bytes, and every source its font",
                fonts_only=sorted(set(self.fonts) - set(self.sources)),
                sources_only=sorted(set(self.sources) - set(self.fonts)),
            )
        for uri, data in self.sources.items():
            if asset_uri(data) != uri:
                raise FontError(
                    f"font bytes do not hash to {uri}",
                    hint="key each font by asset_uri(its bytes), or use register_font",
                    context={"uri": uri, "actual": asset_uri(data)},
                    code=ErrorCode.FONT_HASH_MISMATCH,
                )
            if self.fonts[uri] != load_font(data):
                raise FontError(
                    f"the font stored at {uri} is not the font its bytes load to",
                    hint="build registries with register_font, or store load_font(bytes)",
                    context={"uri": uri, "font": self.fonts[uri].name},
                    code=ErrorCode.FONT_HASH_MISMATCH,
                )
        self._check_names()
        return self

    def _check_names(self) -> None:
        for name, uri in self.names.items():
            if uri not in self.fonts or self.fonts[uri].name != name:
                raise _bad_font(
                    f"registry name {name!r} does not name the font at {uri}",
                    hint="map each font's own name to its URI",
                    font=name,
                    uri=uri,
                )

    def get(self, uri: str) -> Font:
        """Returns the font stored under a URI.

        Args:
            uri: Font identity, `asset:sha256:<hex>` of the font file bytes.

        Returns:
            font: The loaded font.

        Examples:
            >>> registry = bundled_fonts()
            >>> registry.get(registry.resolve("simplex")).name
            'simplex'
        """
        if uri not in self.fonts:
            raise MissingFontError(
                f"no font {uri} in the registry",
                hint=MISSING_FONT_HINT,
                context={"font": uri},
            )
        return self.fonts[uri]

    def resolve(self, ref: str) -> str:
        """Turns a font name or URI into the URI of a registered font.

        Args:
            ref: Short font name, or a font URI.

        Returns:
            uri: The font's `asset:sha256:<hex>` URI.

        Examples:
            >>> bundled_fonts().resolve("simplex").startswith("asset:sha256:")
            True
        """
        if ref.startswith(ASSET_PREFIX):
            self.get(ref)
            return ref
        if ref in self.names:
            return self.names[ref]
        near = close_matches(ref, self.names)
        didyoumean = f"did you mean {', '.join(near)}? " if near else ""
        raise MissingFontError(
            f"no font named {ref!r} in the registry",
            hint=f"{didyoumean}{MISSING_FONT_HINT}",
            context={"font": ref, "known": sorted(self.names)},
        )

    def source(self, uri: str) -> bytes:
        """Returns the exact bytes a font was loaded from.

        Args:
            uri: Font identity, `asset:sha256:<hex>` of the font file bytes.

        Returns:
            data: The font file bytes.

        Examples:
            >>> registry = bundled_fonts()
            >>> registry.source(registry.resolve("simplex")).startswith(b"{")
            True
        """
        if uri not in self.sources:
            raise MissingFontError(
                f"no font bytes for {uri} in the registry",
                hint=MISSING_FONT_HINT,
                context={"font": uri},
            )
        return self.sources[uri]


def _bad_font(message: str, *, hint: str, **context: object) -> FontError:
    return FontError(message, hint=hint, context=context)


def _check_strokes(font: Font) -> None:
    for char, glyph in font.glyphs.items():
        parsed = _parse_glyph(font.name, char, glyph.strokes)
        for index, subpaths in enumerate(parsed):
            _check_stroke(font.name, char, index, subpaths)


def _parse_glyph(name: str, char: str, strokes: Sequence[str]) -> list[list[Subpath]]:
    """Parses a glyph's strokes, rejecting one that does not parse or too many pieces in all."""
    parsed = []
    pieces = 0
    for index, stroke in enumerate(strokes):
        try:
            subpaths = parse_path(stroke)
        except SchemaError as exc:
            raise _bad_font(
                f"glyph {char!r} stroke {index} does not parse: {exc.message}",
                hint="write each stroke as SVG path data",
                **_where(name, char, index),
            ) from exc
        stroke_pieces = sum(len(subpath.pieces) for subpath in subpaths)
        _require_stroke_pieces(name, char, index, stroke_pieces)
        pieces += stroke_pieces
        try:
            require_size(pieces, MAX_GLYPH_PIECES, what="glyph path pieces", error=FontError)
        except FontError as exc:
            raise _bad_font(
                f"glyph {char!r} has over {MAX_GLYPH_PIECES} path pieces",
                hint=f"simplify glyph {char!r} to at most {MAX_GLYPH_PIECES} path pieces in all",
                **exc.context,
                **_where(name, char, index),
            ) from exc
        parsed.append(subpaths)
    return parsed


def _require_stroke_pieces(name: str, char: str, index: int, pieces: int) -> None:
    try:
        require_size(pieces, MAX_STROKE_PIECES, what="stroke path pieces", error=FontError)
    except FontError as exc:
        raise _bad_font(
            f"glyph {char!r} stroke {index} has over {MAX_STROKE_PIECES} path pieces",
            hint=f"split stroke {index} of glyph {char!r} into strokes of at most "
            f"{MAX_STROKE_PIECES} path pieces",
            **exc.context,
            **_where(name, char, index),
        ) from exc


def _where(name: str, char: str, index: int) -> dict[str, object]:
    return {"font": name, "glyph": char, "stroke": index, "field": "glyphs"}


def _check_stroke(name: str, char: str, index: int, subpaths: Sequence[Subpath]) -> None:
    if len(subpaths) != 1:
        raise _bad_font(
            f"glyph {char!r} stroke {index} has {len(subpaths)} subpaths",
            hint="write each stroke as one subpath starting with M",
            **_where(name, char, index),
        )
    (points,) = sample_subpaths(subpaths)
    length = stroke_length(points)
    if not np.isfinite(points).all() or not 0 < length < float("inf"):
        raise _bad_font(
            f"glyph {char!r} stroke {index} has no finite positive length",
            hint="give each stroke at least two distinct points",
            **_where(name, char, index),
        )


def load_font(data: bytes) -> Font:
    """Validates font file bytes into a Font.

    Args:
        data: UTF-8 JSON of a `showpy.font.v1` file, at most `MAX_FONT_BYTES` long.

    Returns:
        font: The validated font, every stroke one subpath of positive length with at most
            `MAX_STROKE_PIECES` path pieces, and no glyph with more than `MAX_GLYPH_PIECES`;
            each count is checked before the glyph is sampled.

    Examples:
        >>> from e8s.core.resources import font_path
        >>> load_font(font_path("simplex.json").read_bytes()).name
        'simplex'
    """
    require_size(len(data), MAX_FONT_BYTES, what="font bytes", error=FontError)
    try:
        document = parse_strict_json(data, document="font")
    except SchemaError as exc:
        raise _bad_font(exc.message, hint=exc.hint, field="(root)", **exc.context) from exc
    try:
        font = Font._validated(document)
    except SchemaError as exc:
        first = field_errors(exc)[0] if "errors" in exc.context else {}
        raise _bad_font(
            f"font fails its schema: {exc.message}",
            hint="fix the field named in context",
            field=first.get("loc", "(root)"),
            errors=exc.context.get("errors", []),
        ) from exc
    _check_strokes(font)
    return font


def register_font(
    registry: FontRegistry, data: bytes, *, if_name_taken: NameTaken = "raise"
) -> tuple[FontRegistry, str]:
    """Returns a registry that also holds the font in `data`, and the font's URI.

    Args:
        registry: Registry to extend; it is not changed.
        data: Bytes of a `showpy.font.v1` JSON file.
        if_name_taken: What to do when the font's name is bound to other bytes: `"raise"`
            FONT_NAME_TAKEN, or `"keep"` that binding and register the font by URI only.

    Returns:
        registered: The extended registry, or `registry` itself when it already holds these
            bytes, and the font's `asset:sha256:<hex>` URI.

    Examples:
        >>> from e8s.core.resources import font_path
        >>> registry, uri = register_font(bundled_fonts(), font_path("simplex.json").read_bytes())
        >>> registry is bundled_fonts()
        True
    """
    font = load_font(data)
    uri = asset_uri(data)
    if uri in registry.fonts:
        return registry, uri
    taken = registry.names.get(font.name)
    if taken is not None and if_name_taken != "keep":
        raise FontError(
            f"font name {font.name!r} is already bound to {taken}",
            hint="rename the font in its file, or use the registered one",
            context={"font": font.name, "taken_by": taken, "uri": uri},
            code=ErrorCode.FONT_NAME_TAKEN,
        )
    names = registry.names if taken is not None else {**registry.names, font.name: uri}
    # model_construct skips the consistency validator: this font was just hashed and loaded, and
    # re-hashing the rest on every call would be quadratic in the registry size.
    extended = FontRegistry.model_construct(
        fonts=ReadOnlyMap({**registry.fonts, uri: font}),
        sources=ReadOnlyMap({**registry.sources, uri: data}),
        names=ReadOnlyMap(names),
    )
    return extended, uri


@functools.cache
def bundled_fonts() -> FontRegistry:
    """Returns the cached registry of the five bundled fonts.

    Returns:
        registry: A read-only registry holding every font named in `BUNDLED_FONTS`.

    Examples:
        >>> sorted(bundled_fonts().names)[0]
        'hershey-greek'
    """
    registry = FontRegistry(fonts={}, sources={}, names={})
    for name, expected in BUNDLED_FONTS.items():
        data = font_path(f"{name}.json").read_bytes()
        if asset_uri(data) != expected:
            raise FontError(
                f"bundled font {name!r} does not match its pinned hash",
                hint="reinstall e8s; the font file was modified",
                context={"font": name, "expected": expected, "actual": asset_uri(data)},
                code=ErrorCode.FONT_HASH_MISMATCH,
            )
        registry, _ = register_font(registry, data)
    return registry
