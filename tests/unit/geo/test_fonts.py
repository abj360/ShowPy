#!/usr/bin/env python3

"""
test_fonts.py --- tests the font data classes, load_font and the frozen FontRegistry

Contains:
    test_*(): one behavior of showpy.geo.fonts each
    test_regression_font_json_nested_past_the_cap_is_a_font_error: refuses it before validation
    test_regression_bundled_registry_is_read_only: refuses writes through the cached registry's
        mappings and a font's glyph table
    test_regression_font_dumps_to_json: dumps and reloads Font and FontRegistry without warnings
    test_regression_font_registry_rejects_mismatched_sources: refuses a registry whose URIs, bytes
        or names disagree
    test_regression_font_cap_falls_back_to_h_then_ascender: reads the cap height from the field,
        then from the ink of H, then from the ascender
    test_regression_font_registry_rejects_fonts_that_differ_from_their_bytes: refuses a registry
        that serves glyphs its URI's bytes do not hold
    test_regression_load_font_caps_the_pieces_of_one_glyph: refuses a glyph whose strokes hold
        more than MAX_GLYPH_PIECES path pieces in all
    test_regression_load_font_caps_the_pieces_of_one_stroke: refuses a stroke over
        MAX_STROKE_PIECES path pieces even when its glyph is under MAX_GLYPH_PIECES
    test_regression_register_font_can_keep_a_taken_name: registers a font by URI only when its
        name is bound to other bytes, and the registry still validates from its dump
"""

import copy
import pickle
import warnings
from typing import Any

import pytest

from showpy.core.canonical import MAX_NESTING
from showpy.core.errors import FontError, MissingFontError, SchemaError
from showpy.core.resources import font_path
from showpy.core.util import asset_uri
from showpy.geo.fonts import (
    BUNDLED_FONTS,
    MAX_FONT_BYTES,
    MAX_GLYPH_PIECES,
    MAX_STROKE_PIECES,
    FontRegistry,
    bundled_fonts,
    load_font,
    register_font,
)
from showpy.geo.models import Font
from showpy.show.repositories import InMemoryAssetStore
from tests.fixtures.fonts import TINY, tiny_bytes

EMPTY = FontRegistry(fonts={}, sources={}, names={})


def _glyphs(**glyphs: Any) -> dict[str, Any]:
    return {"glyphs": glyphs}


BAD_FONT_BYTES = [
    b"not json",
    b"\xff\xfe",
    b"[]",
    b"[" * 100_000,
    tiny_bytes(format="other"),
    tiny_bytes(name="Bad Name"),
    tiny_bytes(name="x" * 65),
    tiny_bytes(**_glyphs(AB={"advance": 1, "strokes": []})),
    tiny_bytes(**_glyphs(**{"": {"advance": 1, "strokes": []}})),
    tiny_bytes(**_glyphs(A={"advance": -1, "strokes": []})),
    tiny_bytes(**_glyphs(A={"advance": 1, "strokes": ["M0 0"]})),
    tiny_bytes(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L0 0"]})),
    tiny_bytes(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L1 0 M2 0 L3 0"]})),
    tiny_bytes(**_glyphs(A={"advance": 1, "strokes": ["bogus"]})),
    tiny_bytes(units_per_em=0),
    tiny_bytes(cap_height=0),
    b" " * (MAX_FONT_BYTES + 1),
]


def test_bundled_font_uris_are_the_file_hashes() -> None:
    for name, uri in BUNDLED_FONTS.items():
        assert uri == asset_uri(font_path(f"{name}.json").read_bytes())


def test_bundled_registry_holds_every_bundled_font() -> None:
    registry = bundled_fonts()
    assert set(registry.names) == set(BUNDLED_FONTS)
    assert registry.get(BUNDLED_FONTS["simplex"]).name == "simplex"
    assert registry.source(BUNDLED_FONTS["simplex"]) == font_path("simplex.json").read_bytes()
    assert bundled_fonts() is bundled_fonts()


def test_resolve_accepts_names_and_uris() -> None:
    uri = BUNDLED_FONTS["relief-singleline"]
    assert bundled_fonts().resolve("relief-singleline") == uri
    assert bundled_fonts().resolve(uri) == uri


def test_resolve_unknown_name_suggests_close_names() -> None:
    with pytest.raises(MissingFontError) as raised:
        bundled_fonts().resolve("relief-singlelin")
    assert "relief-singleline" in raised.value.hint
    assert "register_font" in raised.value.hint
    assert "import_fonts" in raised.value.hint
    assert raised.value.context["font"] == "relief-singlelin"
    known = raised.value.context["known"]
    assert isinstance(known, list)
    assert "simplex" in known


def test_resolve_unknown_uri_names_the_hash() -> None:
    uri = "asset:sha256:" + "0" * 64
    with pytest.raises(MissingFontError) as raised:
        bundled_fonts().resolve(uri)
    assert raised.value.context["font"] == uri


def test_get_unknown_uri_names_the_hash() -> None:
    uri = "asset:sha256:" + "0" * 64
    with pytest.raises(MissingFontError) as raised:
        bundled_fonts().get(uri)
    assert raised.value.context["font"] == uri
    with pytest.raises(MissingFontError):
        bundled_fonts().source(uri)


def test_register_font_returns_a_new_registry_and_the_store_uri() -> None:
    data = tiny_bytes()
    registry, uri = register_font(EMPTY, data)
    assert uri == InMemoryAssetStore().put(data)
    assert registry.resolve("tiny") == uri
    assert registry.source(uri) == data
    assert registry.get(uri).name == "tiny"
    assert EMPTY.fonts == {}
    assert EMPTY.sources == {}
    assert EMPTY.names == {}


def test_register_font_is_idempotent() -> None:
    once, uri = register_font(EMPTY, tiny_bytes())
    assert register_font(once, tiny_bytes()) == (once, uri)


def test_register_font_rejects_a_taken_name() -> None:
    once, _ = register_font(EMPTY, tiny_bytes())
    other = tiny_bytes(**_glyphs(L={"advance": 4, "strokes": ["M0 0 L0 10"]}))
    with pytest.raises(FontError) as raised:
        register_font(once, other)
    assert raised.value.code == "FONT_NAME_TAKEN"
    assert raised.value.context["font"] == "tiny"


def test_register_font_cannot_shadow_a_bundled_name() -> None:
    with pytest.raises(FontError) as raised:
        register_font(bundled_fonts(), tiny_bytes(name="simplex"))
    assert raised.value.code == "FONT_NAME_TAKEN"


def test_load_font_reads_the_bundled_files() -> None:
    for name in BUNDLED_FONTS:
        assert load_font(font_path(f"{name}.json").read_bytes()).name == name


@pytest.mark.parametrize("data", BAD_FONT_BYTES)
def test_load_font_rejects_bad_bytes(data: bytes) -> None:
    with pytest.raises(FontError) as raised:
        load_font(data)
    assert raised.value.code == "BAD_FONT"


def test_load_font_names_the_failing_field() -> None:
    with pytest.raises(FontError) as raised:
        load_font(tiny_bytes(units_per_em=0))
    assert raised.value.context["field"] == "units_per_em"
    assert isinstance(raised.value.__cause__, SchemaError)


def test_load_font_names_the_failing_stroke() -> None:
    with pytest.raises(FontError) as raised:
        load_font(tiny_bytes(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L1 0", "M0 0"]})))
    assert raised.value.context["glyph"] == "A"
    assert raised.value.context["stroke"] == 1


def test_font_registry_survives_pickling() -> None:
    registry, uri = register_font(EMPTY, tiny_bytes())
    assert pickle.loads(pickle.dumps(registry)).get(uri) == registry.get(uri)


def test_regression_font_cap_falls_back_to_h_then_ascender() -> None:
    glyphs = {"H": {"advance": 5, "strokes": ["M0 1 L0 8", "M4 1 L4 8"]}}
    with_cap = Font.model_validate({**TINY, "cap_height": 7, "glyphs": glyphs})
    assert with_cap.cap() == 7
    ink = Font.model_validate({**TINY, "cap_height": None, "glyphs": glyphs})
    assert ink.cap() == pytest.approx(7.0)
    bare = Font.model_validate({**TINY, "cap_height": None, "ascender": 9})
    assert bare.cap() == 9


def test_regression_bundled_registry_is_read_only() -> None:
    registry = bundled_fonts()
    with pytest.raises(TypeError):
        registry.fonts["x"] = registry.get(BUNDLED_FONTS["simplex"])  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.sources["x"] = b""  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.names["x"] = "y"  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.get(BUNDLED_FONTS["simplex"]).glyphs["x"] = None  # type: ignore[index]


def test_regression_font_dumps_to_json() -> None:
    font = load_font(tiny_bytes())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = font.model_dump()
        text = font.model_dump_json()
        registry, uri = register_font(EMPTY, tiny_bytes())
        registry_dump = registry.model_dump()
        registry_text = registry.model_dump_json()
    assert dumped["glyphs"]["I"]["advance"] == 4
    assert Font.model_validate_json(text) == font
    assert registry_dump["fonts"][uri]["name"] == "tiny"
    assert FontRegistry.model_validate_json(registry_text) == registry
    assert FontRegistry.model_validate(registry_dump) == registry
    with pytest.raises(TypeError):
        Font.model_validate_json(text).glyphs["x"] = None  # type: ignore[index]


def test_font_registry_survives_deepcopy() -> None:
    registry, uri = register_font(EMPTY, tiny_bytes())
    assert copy.deepcopy(registry).get(uri) == registry.get(uri)


def _tiny_parts() -> tuple[str, Font, bytes]:
    registry, uri = register_font(EMPTY, tiny_bytes())
    return uri, registry.get(uri), registry.source(uri)


def test_regression_font_registry_rejects_mismatched_sources() -> None:
    uri, font, data = _tiny_parts()
    other_uri = asset_uri(b"other")
    with pytest.raises(FontError) as swapped:
        FontRegistry.model_validate({"fonts": {uri: font}, "sources": {uri: b"x"}, "names": {}})
    assert swapped.value.code == "FONT_HASH_MISMATCH"
    assert swapped.value.context["uri"] == uri
    cases = [
        {"fonts": {uri: font}, "sources": {}, "names": {"tiny": uri}},
        {"fonts": {uri: font}, "sources": {uri: data}, "names": {"tiny": other_uri}},
        {"fonts": {uri: font}, "sources": {uri: data}, "names": {"tiny": uri, "extra": uri}},
        {"fonts": {}, "sources": {}, "names": {"tiny": uri}},
    ]
    for case in cases:
        with pytest.raises(FontError) as info:
            FontRegistry.model_validate(case)
        assert info.value.code == "BAD_FONT", case
    good = {"fonts": {uri: font}, "sources": {uri: data}, "names": {"tiny": uri}}
    assert FontRegistry.model_validate(good).get(uri) == font
    assert FontRegistry(**good).source(uri) == data


def test_regression_register_font_can_keep_a_taken_name() -> None:
    once, uri = register_font(EMPTY, tiny_bytes())
    other = tiny_bytes(family="Tiny, another release")
    kept, other_uri = register_font(once, other, if_name_taken="keep")
    assert kept.names == {"tiny": uri}
    assert kept.get(other_uri).family == "Tiny, another release"
    assert FontRegistry.model_validate(kept.model_dump()) == kept
    assert register_font(EMPTY, other, if_name_taken="keep")[0].names == {"tiny": other_uri}
    with pytest.raises(FontError) as raised:
        register_font(once, other)
    assert raised.value.code == "FONT_NAME_TAKEN"


def test_regression_font_registry_rejects_fonts_that_differ_from_their_bytes() -> None:
    bundled = bundled_fonts()
    simplex = bundled.resolve("simplex")
    impostor = bundled.get(bundled.resolve("hershey-roman")).replace(name="simplex")
    forged = {
        "fonts": {simplex: impostor},
        "sources": {simplex: bundled.source(simplex)},
        "names": {"simplex": simplex},
    }
    with pytest.raises(FontError) as info:
        FontRegistry.model_validate(forged)
    assert info.value.code == "FONT_HASH_MISMATCH"
    assert info.value.context["uri"] == simplex
    assert FontRegistry.model_validate(bundled.model_dump()) == bundled


def _zigzag(pieces: int) -> str:
    return "M0 0 " + " ".join(f"L{i % 2} {i + 1}" for i in range(pieces))


def _strokes(pieces: int) -> list[str]:
    """Splits `pieces` into zigzag strokes of at most MAX_STROKE_PIECES each."""
    sizes = [MAX_STROKE_PIECES] * (pieces // MAX_STROKE_PIECES) + [pieces % MAX_STROKE_PIECES]
    return [_zigzag(size) for size in sizes if size]


def test_regression_load_font_caps_the_pieces_of_one_glyph() -> None:
    whole = _glyphs(I={"advance": 4, "strokes": _strokes(MAX_GLYPH_PIECES)})
    assert len(load_font(tiny_bytes(**whole)).glyphs["I"].strokes) == len(
        _strokes(MAX_GLYPH_PIECES)
    )
    half = MAX_GLYPH_PIECES // 2 + 1
    for strokes in (_strokes(MAX_GLYPH_PIECES + 1), [_zigzag(half), _zigzag(half)]):
        with pytest.raises(FontError) as info:
            load_font(tiny_bytes(**_glyphs(I={"advance": 4, "strokes": strokes})))
        assert info.value.code == "BAD_FONT"
        assert info.value.context["glyph"] == "I"
        assert info.value.context["cap"] == MAX_GLYPH_PIECES
        assert "simplify glyph 'I'" in info.value.hint


def test_regression_load_font_caps_the_pieces_of_one_stroke() -> None:
    whole = _glyphs(I={"advance": 4, "strokes": [_zigzag(MAX_STROKE_PIECES)]})
    assert len(load_font(tiny_bytes(**whole)).glyphs["I"].strokes) == 1
    strokes = ["M5 0 L5 1", _zigzag(MAX_STROKE_PIECES + 1)]
    assert MAX_STROKE_PIECES + 2 <= MAX_GLYPH_PIECES
    with pytest.raises(FontError) as info:
        load_font(tiny_bytes(**_glyphs(I={"advance": 4, "strokes": strokes})))
    assert info.value.code == "BAD_FONT"
    assert info.value.context["glyph"] == "I"
    assert info.value.context["stroke"] == 1
    assert info.value.context["cap"] == MAX_STROKE_PIECES
    assert f"split stroke 1 of glyph 'I' into strokes of at most {MAX_STROKE_PIECES}" in (
        info.value.hint
    )


def test_regression_font_json_nested_past_the_cap_is_a_font_error() -> None:
    deep = b'{"name": ' + b"[" * (MAX_NESTING + 1) + b"]" * (MAX_NESTING + 1) + b"}"
    with pytest.raises(FontError, match="nests deeper than"):
        load_font(deep)
