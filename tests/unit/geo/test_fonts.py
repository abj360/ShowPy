#!/usr/bin/env python3

"""
test_fonts.py --- tests the font data classes, load_font and the frozen FontRegistry

Contains:
    test_*(): one behavior of showpy.geo.fonts each
    test_regression_bundled_registry_is_read_only: refuses writes through the cached registry's
        mappings and a font's glyph table
    test_regression_font_cap_falls_back_to_h_then_ascender: reads the cap height from the field,
        then from the ink of H, then from the ascender
"""

import json
import pickle
from typing import Any

import pytest

from showpy.core.errors import FontError, MissingFontError, SchemaError
from showpy.core.resources import font_path
from showpy.core.util import asset_uri
from showpy.geo.fonts import (
    BUNDLED_FONTS,
    MAX_FONT_BYTES,
    FontRegistry,
    bundled_fonts,
    load_font,
    register_font,
)
from showpy.geo.models import Font
from showpy.show.repositories import InMemoryAssetStore

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
EMPTY = FontRegistry(fonts={}, sources={}, names={})


def _tiny(**changes: Any) -> bytes:
    return json.dumps({**TINY, **changes}).encode()


def _glyphs(**glyphs: Any) -> dict[str, Any]:
    return {"glyphs": glyphs}


BAD_FONT_BYTES = [
    b"not json",
    b"\xff\xfe",
    b"[]",
    b"[" * 100_000,
    _tiny(format="other"),
    _tiny(name="Bad Name"),
    _tiny(name="x" * 65),
    _tiny(**_glyphs(AB={"advance": 1, "strokes": []})),
    _tiny(**_glyphs(**{"": {"advance": 1, "strokes": []}})),
    _tiny(**_glyphs(A={"advance": -1, "strokes": []})),
    _tiny(**_glyphs(A={"advance": 1, "strokes": ["M0 0"]})),
    _tiny(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L0 0"]})),
    _tiny(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L1 0 M2 0 L3 0"]})),
    _tiny(**_glyphs(A={"advance": 1, "strokes": ["bogus"]})),
    _tiny(units_per_em=0),
    _tiny(cap_height=0),
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
    assert "simplex" in raised.value.context["known"]


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
    data = _tiny()
    registry, uri = register_font(EMPTY, data)
    assert uri == InMemoryAssetStore().put(data)
    assert registry.resolve("tiny") == uri
    assert registry.source(uri) == data
    assert registry.get(uri).name == "tiny"
    assert EMPTY.fonts == {}
    assert EMPTY.sources == {}
    assert EMPTY.names == {}


def test_register_font_is_idempotent() -> None:
    once, uri = register_font(EMPTY, _tiny())
    assert register_font(once, _tiny()) == (once, uri)


def test_register_font_rejects_a_taken_name() -> None:
    once, _ = register_font(EMPTY, _tiny())
    other = _tiny(**_glyphs(L={"advance": 4, "strokes": ["M0 0 L0 10"]}))
    with pytest.raises(FontError) as raised:
        register_font(once, other)
    assert raised.value.code == "FONT_NAME_TAKEN"
    assert raised.value.context["font"] == "tiny"


def test_register_font_cannot_shadow_a_bundled_name() -> None:
    with pytest.raises(FontError) as raised:
        register_font(bundled_fonts(), _tiny(name="simplex"))
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
        load_font(_tiny(units_per_em=0))
    assert raised.value.context["field"] == "units_per_em"
    assert isinstance(raised.value.__cause__, SchemaError)


def test_load_font_names_the_failing_stroke() -> None:
    with pytest.raises(FontError) as raised:
        load_font(_tiny(**_glyphs(A={"advance": 1, "strokes": ["M0 0 L1 0", "M0 0"]})))
    assert raised.value.context["glyph"] == "A"
    assert raised.value.context["stroke"] == 1


def test_font_registry_survives_pickling() -> None:
    registry, uri = register_font(EMPTY, _tiny())
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
