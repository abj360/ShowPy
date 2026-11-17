"""
fonts.py --- loads, validates and registers stroke fonts by SHA-256 asset URI

Contains:
    MAX_FONT_BYTES: bounds the size of one font file
    BUNDLED_FONTS: maps each bundled font's name to the URI of its exact file bytes
    FontRegistry: holds loaded fonts, their source bytes and their names, all read-only
    FontRegistry.get(): returns the font stored under a URI
    FontRegistry.resolve(): turns a font name or URI into a URI
    FontRegistry.source(): returns the exact bytes a font was loaded from
    load_font(): validates font file bytes into a Font
    register_font(): returns a registry that also holds one more font, and its URI
    bundled_fonts(): returns the cached registry of the bundled fonts
"""

import functools
import json
from collections.abc import Mapping
from types import MappingProxyType

import numpy as np
from pydantic import field_validator

from showpy.core.caps import require_size
from showpy.core.errors import (
    FontError,
    MissingFontError,
    SchemaError,
    field_errors,
)
from showpy.core.readonly import read_only
from showpy.core.resources import font_path
from showpy.core.types import FrozenModel
from showpy.core.util import ASSET_PREFIX, asset_uri, close_matches
from showpy.geo.font_strokes import sample_stroke, stroke_length
from showpy.geo.models import Font

MAX_FONT_BYTES = 8 * 1024 * 1024
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


class FontRegistry(FrozenModel):
    """Holds loaded fonts, their source bytes and their names, all read-only.

    Attributes:
        fonts: Loaded fonts keyed by `asset:sha256:<hex>` URI.
        sources: Exact bytes of each font file keyed by URI.
        names: URI of each font keyed by its short name.
    """

    fonts: Mapping[str, Font]
    sources: Mapping[str, bytes]
    names: Mapping[str, str]

    @field_validator("fonts", "sources", "names")
    @classmethod
    def _freeze[V](cls, table: Mapping[str, V]) -> Mapping[str, V]:
        return read_only(table)

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
        for index, stroke in enumerate(glyph.strokes):
            _check_stroke(font.name, char, index, stroke)


def _check_stroke(name: str, char: str, index: int, stroke: str) -> None:
    where = {"font": name, "glyph": char, "stroke": index, "field": "glyphs"}
    try:
        subpaths = sample_stroke(stroke)
    except SchemaError as exc:
        raise _bad_font(
            f"glyph {char!r} stroke {index} does not parse: {exc.message}",
            hint="write each stroke as SVG path data",
            **where,
        ) from exc
    if len(subpaths) != 1:
        raise _bad_font(
            f"glyph {char!r} stroke {index} has {len(subpaths)} subpaths",
            hint="write each stroke as one subpath starting with M",
            **where,
        )
    length = stroke_length(subpaths[0])
    if not np.isfinite(subpaths[0]).all() or not 0 < length < float("inf"):
        raise _bad_font(
            f"glyph {char!r} stroke {index} has no finite positive length",
            hint="give each stroke at least two distinct points",
            **where,
        )


def load_font(data: bytes) -> Font:
    """Validates font file bytes into a Font.

    Args:
        data: UTF-8 JSON of a `showpy.font.v1` file, at most `MAX_FONT_BYTES` long.

    Returns:
        font: The validated font, every stroke one subpath of positive length.

    Examples:
        >>> from showpy.core.resources import font_path
        >>> load_font(font_path("simplex.json").read_bytes()).name
        'simplex'
    """
    require_size(len(data), MAX_FONT_BYTES, what="font bytes", error=FontError)
    try:
        document = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise _bad_font(
            f"font bytes are not UTF-8 JSON: {type(exc).__name__}",
            hint="pass the bytes of a showpy.font.v1 JSON file",
            field="(root)",
        ) from exc
    try:
        font = Font.model_validate(document)
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


def register_font(registry: FontRegistry, data: bytes) -> tuple[FontRegistry, str]:
    """Returns a registry that also holds the font in `data`, and the font's URI.

    Args:
        registry: Registry to extend; it is not changed.
        data: Bytes of a `showpy.font.v1` JSON file.

    Returns:
        registered: The extended registry, or `registry` itself when it already holds these
            bytes, and the font's `asset:sha256:<hex>` URI.

    Examples:
        >>> from showpy.core.resources import font_path
        >>> registry, uri = register_font(bundled_fonts(), font_path("simplex.json").read_bytes())
        >>> registry is bundled_fonts()
        True
    """
    font = load_font(data)
    uri = asset_uri(data)
    taken = registry.names.get(font.name)
    if taken == uri:
        return registry, uri
    if taken is not None:
        raise FontError(
            f"font name {font.name!r} is already bound to {taken}",
            hint="rename the font in its file, or use the registered one",
            context={"font": font.name, "taken_by": taken, "uri": uri},
            code="FONT_NAME_TAKEN",
        )
    extended = FontRegistry(
        fonts={**registry.fonts, uri: font},
        sources={**registry.sources, uri: data},
        names={**registry.names, font.name: uri},
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
                hint="reinstall showpy; the font file was modified",
                context={"font": name, "expected": expected, "actual": asset_uri(data)},
                code="FONT_HASH_MISMATCH",
            )
        registry, _ = register_font(registry, data)
    return registry
