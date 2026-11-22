#!/usr/bin/env python3

"""
test_build_fonts.py --- tests the one-time font build script that writes showpy/fonts

Contains:
    test_parse_jhf_reads_bounds_and_pen_up: checks bounds, numbering and pen lifts of a glyph
    test_parse_jhf_joins_wrapped_lines: checks a glyph wrapped over two lines parses the same
    test_jhf_font_flips_y_and_takes_baseline_and_cap_from_h: checks metrics come from H
    test_jhf_closed_loop_ends_in_z_without_repeating_its_start: checks loops close with Z
    test_svg_font_snaps_a_nearly_closed_stroke: checks a near-closed outline gets a Z
    test_svg_font_reads_metrics_and_skips_ligatures_and_unnamed_glyphs: checks glyph filtering
    test_serialize_font_is_canonical_ascii_with_a_newline: checks byte-deterministic output
    test_simplex_font_folds_case_on_the_four_by_six_grid: checks the built-in font conversion
    test_greek_table_follows_hershey_numbering: checks the Greek letter table
    test_verified_rejects_a_checksum_mismatch: checks a bad checksum stops the build
    test_regression_notice_is_written_with_lf_line_ends: checks NOTICE.txt has no CR bytes
    test_regression_every_named_license_file_is_written: checks NOTICE names files that ship
"""

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_fonts.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_fonts", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_fonts = _load_script()
META = build_fonts.FontMeta(name="test", family="Test", license="MIT", source="memory")


def svg_font_text(d_attr: str) -> str:
    """Builds a minimal SVG font with one glyph named A."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg"><defs><font horiz-adv-x="500">'
        '<font-face units-per-em="1000" ascent="800" descent="-200" cap-height="680"/>'
        f'<glyph unicode="A" {d_attr}/></font></defs></svg>'
    )


SVG_WITH_LIGATURE_AND_NOTDEF = (
    '<svg xmlns="http://www.w3.org/2000/svg"><defs><font horiz-adv-x="500">'
    '<font-face units-per-em="1000" ascent="800" descent="-200" cap-height="680"/>'
    '<glyph glyph-name=".notdef" d="M0 0 L5 5"/>'
    '<glyph unicode="fi" d="M0 0 L5 5"/>'
    '<glyph unicode="A" d="M0 0 L5 5"/></font></defs></svg>'
)


def test_parse_jhf_reads_bounds_and_pen_up() -> None:
    (glyph,) = build_fonts.parse_jhf("  501  9I[RFJ[ RRFZ[ RMTWT\n")
    assert (glyph.number, glyph.left, glyph.right) == (501, -9, 9)
    assert glyph.strokes == (((0, -12), (-8, 9)), ((0, -12), (8, 9)), ((-5, 2), (5, 2)))


def test_parse_jhf_joins_wrapped_lines() -> None:
    wrapped = "  501  9I[RFJ[ RRF\nZ[ RMTWT\n"
    assert build_fonts.parse_jhf(wrapped) == build_fonts.parse_jhf("  501  9I[RFJ[ RRFZ[ RMTWT\n")


def test_jhf_font_flips_y_and_takes_baseline_and_cap_from_h() -> None:
    glyphs = build_fonts.parse_jhf("  501  9I[RFJ[ RRFZ[ RMTWT\n  508  9G]KFK[ RYFY[ RKPYP\n")
    font = build_fonts.jhf_font(META, glyphs, {0: "A", 1: "H"})
    assert font["cap_height"] == 21.0
    assert font["glyphs"]["A"]["advance"] == 18.0
    assert font["glyphs"]["A"]["strokes"][0] == "M9.0 21.0 L1.0 0.0"


def test_jhf_closed_loop_ends_in_z_without_repeating_its_start() -> None:
    glyphs = build_fonts.parse_jhf("    1  5I[RFVJRNRF\n  508  9G]KFK[ RYFY[ RKPYP\n")
    stroke = build_fonts.jhf_font(META, glyphs, {0: "o", 1: "H"})["glyphs"]["o"]["strokes"][0]
    assert stroke.endswith(" Z")
    assert stroke.count("L") == 2


def test_svg_font_snaps_a_nearly_closed_stroke() -> None:
    font = build_fonts.svg_font(META, svg_font_text('d="M0 0 L10 0 L10 10 L0.3 0"'))
    assert font["glyphs"]["A"]["strokes"] == ["M0.0 0.0 L10.0 0.0 L10.0 10.0 Z"]


def test_svg_font_reads_metrics_and_skips_ligatures_and_unnamed_glyphs() -> None:
    font = build_fonts.svg_font(META, SVG_WITH_LIGATURE_AND_NOTDEF)
    assert (font["units_per_em"], font["cap_height"], font["ascender"], font["descender"]) == (
        1000.0,
        680.0,
        800.0,
        -200.0,
    )
    assert sorted(font["glyphs"]) == ["A"]


def test_serialize_font_is_canonical_ascii_with_a_newline() -> None:
    a = build_fonts.serialize_font({"b": 1, "a": "É"})
    assert a == build_fonts.serialize_font({"a": "É", "b": 1})
    assert a.endswith(b"\n")
    assert a.isascii()


def test_simplex_font_folds_case_on_the_four_by_six_grid() -> None:
    font = build_fonts.simplex_font(META)
    assert font["glyphs"]["h"] == font["glyphs"]["H"]
    assert font["glyphs"]["H"]["advance"] == 5.0
    assert font["cap_height"] == 6.0


def test_greek_table_follows_hershey_numbering() -> None:
    assert build_fonts.GREEK_BY_HERSHEY[527] == "\u0391"
    assert build_fonts.GREEK_BY_HERSHEY[534] == "Θ"
    assert build_fonts.GREEK_BY_HERSHEY[650] == "ω"
    assert 699 not in build_fonts.GREEK_BY_HERSHEY


def test_verified_rejects_a_checksum_mismatch() -> None:
    with pytest.raises(SystemExit, match="E: checksum mismatch"):
        build_fonts.verified(b"x", "0" * 64, "https://example.invalid/font")


H_JHF = "  508  9G]KFK[ RYFY[ RKPYP\n"


def test_regression_short_open_line_inside_the_close_tolerance_stays_open() -> None:
    font = build_fonts.svg_font(META, svg_font_text('d="M0 0 L3 0"'))
    assert font["glyphs"]["A"]["strokes"] == ["M0.0 0.0 L3.0 0.0"]


def test_svg_font_snaps_a_nearly_closed_curve_by_moving_its_end() -> None:
    font = build_fonts.svg_font(META, svg_font_text('d="M0 0 L10 0 C10 5 5 10 0.3 0.1"'))
    assert font["glyphs"]["A"]["strokes"] == ["M0.0 0.0 L10.0 0.0 C10.0 5.0 5.0 10.0 0.0 0.0 Z"]


def test_svg_font_drops_zero_length_subpaths_and_keeps_the_rest() -> None:
    font = build_fonts.svg_font(META, svg_font_text('d="M0 0 L0 0 M1 1 L50 50"'))
    assert font["glyphs"]["A"]["strokes"] == ["M1.0 1.0 L50.0 50.0"]


def test_jhf_font_drops_strokes_that_never_leave_their_start() -> None:
    glyphs = build_fonts.parse_jhf("  501  3I[RFRF\n" + H_JHF)
    font = build_fonts.jhf_font(META, glyphs, {0: "A", 1: "H"})
    assert font["glyphs"]["A"]["strokes"] == []


def test_jhf_font_falls_back_to_greek_eta_for_the_baseline_and_cap() -> None:
    glyphs = build_fonts.parse_jhf("  501  9I[RFJ[ RRFZ[ RMTWT\n" + H_JHF)
    font = build_fonts.jhf_font(META, glyphs, {0: "A", 1: "\u0397"})
    assert font["cap_height"] == 21.0
    assert font["glyphs"]["A"]["strokes"][0] == "M9.0 21.0 L1.0 0.0"


def test_contact_sheet_draws_the_closing_edge_of_a_closed_stroke() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (30, 30), "white")
    build_fonts._draw_glyph(
        ImageDraw.Draw(image), ["M0.0 0.0 L10.0 0.0 L10.0 10.0 Z"], (5.0, 25.0), 1.0
    )
    assert image.getpixel((10, 20)) == (0, 0, 0)


def test_regression_notice_is_written_with_lf_line_ends(tmp_path: Path) -> None:
    build_fonts._write_notice(tmp_path, ["a\nb\n", "c\n"])
    data = (tmp_path / "NOTICE.txt").read_bytes()
    assert data == b"a\nb\n\nc\n"


def test_regression_every_named_license_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    sources = {}
    for key in ("relief.ofl", "hershey.txt"):
        body = b"licence text " + key.encode()
        (cache / key).write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        sources[key] = build_fonts.Source(build_fonts.SOURCES[key].url, digest)
    monkeypatch.setattr(build_fonts, "SOURCES", {**build_fonts.SOURCES, **sources})
    out = tmp_path / "out"
    build_fonts._write_licenses(out, cache)
    plans = build_fonts._plans()
    assert all((out / plan.license_file).is_file() for plan in plans)
    simplex = plans[-1]
    assert simplex.license_file == "licenses/MIT.txt"
    assert (out / simplex.license_file).read_bytes() == (
        SCRIPT_PATH.parents[1] / "showpy" / "schemas" / "LICENSE-MIT.txt"
    ).read_bytes()
    assert "(licenses/MIT.txt)" in build_fonts._notice_block(simplex, "0")
