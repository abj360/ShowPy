# Text on the Field, Stage 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spell any text that the bundled single-line fonts cover, as an editable `text` form with an exact spot count (or one derived from a spacing between spots), placed anywhere on the sheet.

**Architecture:** A build script converts commit-pinned, checksummed Hershey and Relief SingleLine sources into one JSON font format (`showpy.font.v1`). The converted files are committed under `showpy/fonts/` with their licences. A frozen `FontRegistry` identifies each font by `asset:sha256:<hex>` of its exact bytes. `showpy.geo.text_layout` turns text into stroke curves: a per-character fallback chain, lines, alignment, anchor, spacing and rotation, with no shaping. `showpy.choreo.form_text` spreads exactly `n` slots over the strokes in reading order (with `spacing=`, `n` is derived from the total stroke length and stored), and the `text` form kind, `edit` and `export_json`/`import_fonts` build on those two modules.

**Tech Stack:** Python 3.12+, Pydantic v2 frozen models, numpy, the existing curve and SVG-path machinery (`showpy.geo.svg`, `SvgCurve`, `Polyline`), stdlib `json`/`base64`/`hashlib`/`unicodedata`/`importlib.resources`. The build script also uses stdlib `urllib` and `xml.etree`.

**Spec:** `docs/superpowers/specs/2026-11-14-text-on-field-design.md` (build stage 1 only).

## Global Constraints

- Scope is build stage 1 only. Stage 2 (user TTF/OTF/WOFF through fontTools, `outline`/`fill`, skeleton `centerline` with automatic fallback and a build report) and stage 3 (kerning, text `along` a curve, the optional HarfBuzz/bidi extra `showpy[shaping]`) get their own plans.
- No new runtime dependency. Stage 1 uses only the standard library and the current dependencies. fontTools arrives in stage 2. If a task seems to need a new package, stop and ask.
- The library never downloads anything and never looks a font up in the operating system. Fonts come from `showpy/fonts/` (via `importlib.resources`) or from bytes the caller passes.
- Font files get into the repository once, through `scripts/build_fonts.py`. The script downloads commit-pinned URLs and verifies each one's SHA-256 before converting. Its output is byte-deterministic (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)` plus `"\n"`) and is committed. CI never runs the script.
- Font identity is `asset:sha256:<64 hex>`: the SHA-256 of the exact font file bytes, the same URI `InMemoryAssetStore.put(data)` returns. A recipe stores URIs, never names.
- Error codes (rule 7, all `ShowPyError` subclasses with `code`, `message`, `context`, `hint`): `MISSING_GLYPH` (`GlyphError`), `MISSING_FONT` (`MissingFontError`), `BAD_FONT`, `FONT_NAME_TAKEN` and `FONT_HASH_MISMATCH` (`FontError`), `TOO_FEW_SPOTS` and `TOO_MANY_SPOTS` (`SpacingError`), `N_OR_SPACING` (`SchemaError`) and `COINCIDENT_SLOTS` (`GeometryError`). The legibility notice is a `LegibilityWarning` (a `UserWarning`), not an error. pytest runs with `filterwarnings = error`, so:
  - The dedicated warning tests assert it with `pytest.warns`.
  - Any other test that builds text at or near its minimum count is marked `@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")`. This covers the golden, the minimum-count tests and the tiny-font tests.
  - Doctests use counts that never trigger it. If one does, raise its `n`; never filter in a doctest.
- Per-stroke minimum spots: 2 for an open stroke, 3 for a closed one. Closed loops never place two spots on one point.
- Units: `height` is the cap height in meters. `rotation` is in degrees, counter-clockwise positive, and 0 reads left to right toward +x as the audience sees it. Glyph y-up maps to stage +y (upstage). `letter_spacing` and `line_spacing` are fractions of the first font's em, where `em_m = height * units_per_em / cap`.
- Slot order is reading order: lines top to bottom, glyphs in logical order, strokes in font order, each stroke from its start.
- `edit(form, text=...)` keeps the same members and the same count (maintainer decision of 2026-11-14). Member i stands on slot i. A text form gets exactly one of `n` (spots) or `spacing` (meters between spots along strokes). With `spacing`, the derived count is stored as the recipe's `n`. Only an explicit `n=` or `spacing=` edit changes the count (Task 10).
- Every `text_form` call passes `n=` (or, from Task 10, `spacing=`). Tests in Tasks 8 and 9 always pass `n=`.
- `simplex` keeps its glyph geometry, 4 × 6 grid, advance 5 and case folding. The one visible change is the fix for UP-9.1-28: loop strokes come out `closed=True` without a repeated end point. `simplex` is its own bundled font, not an alias for a Hershey face.
- The stored schema stays `showpy.show.v1`. Adding `"text"` to `FormKind` only widens the enum, so every old file still loads (`tests/unit/show/test_schema.py` rule: bump only when old files no longer load). Regenerate with `SHOWPY_UPDATE_SCHEMA=1`.
- `PathEngine` is untouched. Existing form kinds ignore the font registry, and their outputs do not change.
- Every function that takes the font registry spells it `font_registry: FontRegistry | None = None`, keyword-only; `None` means `bundled_fonts()`. The name `fonts` is taken by the recipe key.
- **API change procedure.** Use it in every task that adds or changes a public name:
  1. Add the new names to `showpy/_api_table.py` in sorted order, then run `uv run python scripts/gen_public_api.py`.
  2. Run `uv run git-cliff --config cliff.toml --with-commit "<the task's exact commit subject>" --output CHANGELOG.md`, then `PATH=/home/uapb-ai/.nvm/versions/node/v22.23.5/bin:$PATH npx --no-install prettier --write CHANGELOG.md`.
  3. Run `SHOWPY_UPDATE_API=1 uv run pytest tests/unit/test_api_snapshot.py`, then the same command without the variable. Expected: PASS.
  4. The commit subject names, in backticks, every name the snapshot diff lists as added or changed. Stage `CHANGELOG.md`, `tests/fixtures/api_snapshot.json`, `showpy/_api_table.py` and `showpy/__init__.py` with the code.
- **Gates** close every task: `set -o pipefail; uv run ruff format . && uv run ruff check . && uv run nox -s types && uv run pytest -q 2>&1 | tail -5`. Expected: no failures, and a `Ledger: N open` line (528 today, 523 after Task 5).
- Headers and docstrings follow `~/.claude/CLAUDE.md`. Library modules under `showpy/` have no shebang; tests and scripts keep theirs. Public functions get a runnable `>>>` example.
- Keep modules under about 500 lines. `showpy/choreo/forms.py` (467 lines today) gets only `text_form` and the new parameter. The text slot logic lives in `showpy/choreo/form_text.py`.
- Commits: Conventional Commits under `Oluwatoyosi Peter Abolaji` (check `git config user.name`), with no `Co-Authored-By` or "Generated with" trailer. Never push.
- **Sequencing with Phase 1.** This branch edits files that Phase 1 lanes own: lane C (`showpy/geo/text.py`, `shapes.py`, `spacing.py`, `models.py`), lane G (`showpy/choreo/form_layout.py`, `form_ops.py`; plus `forms.py` and `models.py`), lane E (`showpy/export/data.py`), lane A (two lines in `showpy/show/repositories.py`), L0/lane D (`showpy/core/errors.py`, `util.py`). Shared files: `showpy/_api_table.py`, `showpy/__init__.py`, the API snapshot, `CHANGELOG.md` and the schema. Start execution after lanes C and G merge, rebasing onto `main` first. New files (`showpy/geo/fonts.py`, `text_layout.py`, `showpy/choreo/form_text.py`, `showpy/fonts/**`, `scripts/build_fonts.py`) conflict with nothing.

## Review Focus

1. **Strokes that cross or touch** (`X`, `+`, `T`, `B`, `8`, script loops): two strokes can each put a spot on the shared point, which puts two performers on one dot. Expected: no two slots closer than `COINCIDENT_TOL_M`, for every glyph of every bundled font. Test in Task 7.
2. **Text as people type it**: an accent typed as `e` + U+0301, Windows `\r\n` line ends, a trailing newline, whitespace-only text. Expected: `é` resolves to the precomposed glyph; `\r\n` counts as one line break; whitespace-only text raises a teaching `SchemaError` instead of dividing by zero. Tests in Tasks 4 and 7.
3. **A user-font text form reaching code that does not take `font_registry`** (`gate`, `reorder`, `match`, `form_place`). Expected: `MissingFontError` naming the hash. Never a substitute font, never a `KeyError`. Test in Task 8.
4. **Transforming a text form** (`transform_form`, `gate`, `pinwheel`, mirror). These store a `"transform"` matrix in the recipe. Expected: slots and tangents both map through it, so `along` facings turn with the text. Test in Task 8.
5. **Mixing fonts in one string through the fallback chain.** Each font has its own `units_per_em` and cap height. Expected: every glyph's cap height equals `height`, and each glyph advances by its own font's advance. Test in Task 4.
6. **A `spacing` far from the letter size.** A spacing wider than a letter would leave a stroke with fewer spots than its minimum; a microscopic one would ask for millions of spots. Expected: the count is clamped up to the per-stroke minimums, and the `LegibilityWarning` fires as it does for a small `n`. Below the cap, the count raises `TOO_MANY_SPOTS` (`MAX_TEXT_SPOTS = 10_000`) before anything is allocated. Tests in Task 10.

---

## Files

| File                                                      | Responsibility                                                                                     |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `docs/adr/0003-fonts-as-content-hashed-assets.md`         | Font format and identity decision                                                                  |
| `scripts/build_fonts.py`                                  | One-time fetch, checksum, conversion and contact sheets                                            |
| `showpy/fonts/*.json`, `licenses/*.txt`, `NOTICE.txt`     | Bundled font data and licences (shipped in the wheel)                                              |
| `docs/research/2026-11-14-bundled-fonts.md`               | Sources, commits, checksums, licences, coverage                                                    |
| `showpy/core/errors.py`, `util.py`, `resources.py`        | Font errors and warning, `asset_uri`, `font_path`                                                  |
| `showpy/geo/models.py`                                    | `Font`, `FontGlyph`, `LaidGlyph`, `TextAlign`, `TextAnchor`, `DEFAULT_LINE_SPACING`                |
| `showpy/geo/fonts.py`                                     | `FontRegistry`, `load_font`, `register_font`, `bundled_fonts`, `BUNDLED_FONTS`                     |
| `showpy/geo/text_layout.py`                               | `layout_text`: fallback chain, lines, align, anchor, spacing, rotation                             |
| `showpy/geo/text.py`, `shapes.py`                         | Frozen `simplex` tables; `text_outline` on the registry                                            |
| `showpy/geo/spacing.py`                                   | `apportion` with per-curve minimums                                                                |
| `showpy/choreo/models.py`                                 | `FormKind` gains `"text"`; `TextRecipe`                                                            |
| `showpy/choreo/form_text.py`                              | Exact-`n` slots on strokes, spacing-derived counts, minimums, coincidence rule, legibility warning |
| `showpy/choreo/form_layout.py`, `forms.py`, `form_ops.py` | `text` dispatch, `text_form`, `font_registry` threading, `edit`                                    |
| `showpy/export/data.py`                                   | Fonts embedded by `export_json`; `import_fonts`                                                    |

---

### Task 1: Font format decision and the build script

**Files:**

- Create: `docs/adr/0003-fonts-as-content-hashed-assets.md`, `scripts/build_fonts.py`
- Test: `tests/unit/test_build_fonts.py` (load the script with `importlib.util.spec_from_file_location`, as `tests/unit/test_public_api.py` does)

**Interfaces:**

- Produces (in `scripts/build_fonts.py`):
  - `FONT_FORMAT = "showpy.font.v1"`, `HERSHEY_UNITS_PER_EM = 32.0`, `CLOSE_TOL_EM = 0.005`
  - `JhfGlyph` (frozen dataclass): `number: int`, `left: int`, `right: int`, `strokes: tuple[tuple[tuple[int, int], ...], ...]`
  - `parse_jhf(text: str) -> list[JhfGlyph]`
  - `jhf_font(meta: FontMeta, glyphs: Sequence[JhfGlyph], chars: Mapping[int, str]) -> dict[str, object]`. `chars` maps a glyph's index in the file to its character.
  - `GREEK_BY_HERSHEY: Mapping[int, str]`
  - `svg_font(meta: FontMeta, svg_text: str) -> dict[str, object]`
  - `simplex_font(meta: FontMeta) -> dict[str, object]`
  - `serialize_font(font: Mapping[str, object]) -> bytes`
  - `verified(data: bytes, sha256: str, url: str) -> bytes`
  - `main(argv: Sequence[str] | None = None) -> int`, with options `--cache DIR`, `--out DIR` and `--sheets DIR`
  - `FontMeta` (frozen dataclass): `name`, `family`, `license`, `source`.
- The `showpy.font.v1` document (Task 3 loads it):
  `{"format", "name", "family", "license", "source", "units_per_em", "cap_height", "ascender", "descender", "glyphs": {"<one character>": {"advance": float, "strokes": ["<one absolute SVG subpath>", ...]}}}`.
  Coordinates are font units, y up, baseline at 0, x measured from the glyph origin. Each stroke is written by `showpy.geo.svg.Subpath.to_d()` after rounding coordinates with `round(v, 6) + 0.0`. A closed stroke ends in `Z` and does not repeat its start point.

- [ ] **Step 1: Write the ADR.** `docs/adr/0003-fonts-as-content-hashed-assets.md`, with the ADR sections from `~/.claude/CLAUDE.md`. Decision: one JSON stroke format for all bundled fonts; identity is the SHA-256 of the file bytes, as an asset URI; data is built once from pinned sources and committed; fonts never come from the OS; licences ship inside the package. Alternatives: "**Ship the original .jhf/.svg files and parse them at runtime.** Rejected: two parsers in the library and an XML parser on untrusted input"; "**Look fonts up by name.** Rejected: anti-goal, shows rebuild differently per machine". Numbers: the source sizes from Task 2 (Relief SVG 176,600 bytes; each Hershey .jhf about 3.5 KB).

- [ ] **Step 2: Write the failing tests**

```python
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
        1000.0, 680.0, 800.0, -200.0,
    )
    assert sorted(font["glyphs"]) == ["A"]


def test_serialize_font_is_canonical_ascii_with_a_newline() -> None:
    a = build_fonts.serialize_font({"b": 1, "a": "É"})
    assert a == build_fonts.serialize_font({"a": "É", "b": 1})
    assert a.endswith(b"\n") and a.isascii()


def test_simplex_font_folds_case_on_the_four_by_six_grid() -> None:
    font = build_fonts.simplex_font(META)
    assert font["glyphs"]["h"] == font["glyphs"]["H"]
    assert font["glyphs"]["H"]["advance"] == 5.0 and font["cap_height"] == 6.0


def test_greek_table_follows_hershey_numbering() -> None:
    assert build_fonts.GREEK_BY_HERSHEY[527] == "Α"
    assert build_fonts.GREEK_BY_HERSHEY[534] == "Θ"
    assert build_fonts.GREEK_BY_HERSHEY[650] == "ω"
    assert 699 not in build_fonts.GREEK_BY_HERSHEY


def test_verified_rejects_a_checksum_mismatch() -> None:
    with pytest.raises(SystemExit, match="E: checksum mismatch"):
        build_fonts.verified(b"x", "0" * 64, "https://example.invalid/font")
```

`svg_font_text(d_attr)` is a test helper. It builds a minimal SVG font: `<font horiz-adv-x="500">`, `<font-face units-per-em="1000" ascent="800" descent="-200" cap-height="680"/>`, and one `<glyph unicode="A" {d_attr}/>`. `SVG_WITH_LIGATURE_AND_NOTDEF` adds a `unicode="fi"` glyph and a `glyph-name=".notdef"` glyph that has no `unicode`.

- [ ] **Step 3: Run the tests to see them fail.** Run: `uv run pytest tests/unit/test_build_fonts.py -q`. Expected: FAIL, because `scripts/build_fonts.py` does not exist.

- [ ] **Step 4: Implement `scripts/build_fonts.py`.** Write a shebang, the module docstring with a `Contains:` section, and the interfaces above.
  - **jhf files.** Columns 0–4 hold the number and 5–7 the vertex count, which counts the bounds pair. Keep consuming continuation lines until that many pairs are read. Each coordinate is `ord(c) - ord("R")`, and `" R"` lifts the pen. The baseline is the largest y of `H` (the bottom in jhf's y-down frame); `cap_height` is H's height. Map each point to `(x - left, baseline - y)`, and set `advance = right - left`, `units_per_em = HERSHEY_UNITS_PER_EM`, `ascender` = the highest point, `descender` = the lowest. A stroke whose last point equals its first loses that point and gains `Z`. Single-point strokes are dropped.
  - **Greek.** `GREEK_BY_HERSHEY` maps 527+i to `"ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"[i]` and 627+i to `"αβγδεζηθικλμνξοπρστυφχψω"[i]`. Hershey number 699 means "no glyph" and is skipped.
  - **SVG fonts.** Parse with `xml.etree.ElementTree`; the input is verified by checksum first. Read the metrics from `<font-face>`, and use `<font horiz-adv-x>` as the default advance. Keep only glyphs whose `unicode` is exactly one character. Parse each `d` with `showpy.geo.svg.parse_path`: one stroke per subpath, dropping zero-length ones. When an open subpath ends within `CLOSE_TOL_EM * units_per_em` of its start, close it: drop a last `LinePiece` (Z redraws it), or move a last curve piece's `end` onto the start.
  - **simplex.** `simplex_font` reads `showpy.geo.text.SIMPLEX`, adds lowercase keys that point to the uppercase glyphs, and uses `units_per_em = cap_height = ascender = 6.0`, `descender = 0.0`, `license = "MIT"`.
  - **`main`.**
    - Fetch each source in `SOURCES` into `--cache` (reuse a cached file whose checksum matches). Pass the bytes through `verified`.
    - Write `<out>/<name>.json`. Copy Relief's `OFL.txt` verbatim to `<out>/licenses/OFL-1.1.txt` and `hershey.txt` verbatim to `<out>/licenses/HERSHEY.txt`.
    - Write `<out>/NOTICE.txt`, one block per font: name, family, source URL, source SHA-256, licence file, output SHA-256.
    - With `--sheets`, draw one PNG contact sheet per font with Pillow. Each glyph is drawn with its `U+XXXX` and `unicodedata.name` under it.
    - Print terse lines such as `Wrote showpy/fonts/relief-singleline.json sha256:<hex>`.
  - `SOURCES` holds these pins (verified on 2026-11-14):

| File                                                                                                                                                     | SHA-256                                                            |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `https://raw.githubusercontent.com/isdat-type/Relief-SingleLine/01dfc5779ec1e9e4b288d96c6c96c23bfccbaf9d/fonts/open_svg/ReliefSingleLineSVG-Regular.svg` | `75f05a5b64ed6039c9816628ee051d98e16c19148a8268c63f5eccf8382479e2` |
| `https://raw.githubusercontent.com/isdat-type/Relief-SingleLine/01dfc5779ec1e9e4b288d96c6c96c23bfccbaf9d/OFL.txt`                                        | `005834fd32a01fb021a44167cafd4fa3df8058a7b0fb7319bd19b445d2fe24a3` |
| `https://raw.githubusercontent.com/kamalmostafa/hershey-fonts/1356bf2f83d380fcef68c887e88675eb9d445d86/hershey-fonts/rowmans.jhf`                        | `8718fb129c0f6bce89c84fe41bc467e39534d215a6f7c3220cc5789a8a7d8618` |
| `https://raw.githubusercontent.com/kamalmostafa/hershey-fonts/1356bf2f83d380fcef68c887e88675eb9d445d86/hershey-fonts/scripts.jhf`                        | `6b391b2ea3a0771cf18caff0ed111db3d05681fa586d423a740cc0b2b155a873` |
| `https://raw.githubusercontent.com/kamalmostafa/hershey-fonts/1356bf2f83d380fcef68c887e88675eb9d445d86/hershey-fonts/greeks.jhf`                         | `309298debb0e472047f8329d31f849b14a98108a706d0f4f684e40d1eb023a76` |
| `https://raw.githubusercontent.com/kamalmostafa/hershey-fonts/1356bf2f83d380fcef68c887e88675eb9d445d86/hershey-fonts/hershey.txt`                        | `6ffb639e2d2799e987cec4f913473d4119e4270b65c13ceafe1cd502f904d855` |

Outputs and their sources: `relief-singleline` (Relief SVG, `OFL-1.1`); `hershey-roman` (rowmans) and `hershey-script` (scripts), where file index i is `chr(32 + i)` for i in 0..94; `hershey-greek` (greeks, letters through `GREEK_BY_HERSHEY` only, so punctuation falls back to other fonts); and `simplex`. All three Hershey faces use `LicenseRef-Hershey-Fonts`. Take only the `.jhf` data and `hershey.txt` from the Hershey repository; its C code is GPL-2 and stays out.

- [ ] **Step 5: Run the tests to see them pass.** Run: `uv run pytest tests/unit/test_build_fonts.py -q`. Expected: PASS.
- [ ] **Step 6: Run the gates, then commit** `docs(adr): record fonts as content-hashed assets` (the ADR) and `build: add the one-time font build script` (the script and its tests).

### Task 2: Build and commit the bundled fonts and their licences

**Files:**

- Create: `showpy/fonts/{simplex,hershey-roman,hershey-script,hershey-greek,relief-singleline}.json`, `showpy/fonts/licenses/OFL-1.1.txt`, `showpy/fonts/licenses/HERSHEY.txt`, `showpy/fonts/NOTICE.txt`, `docs/research/2026-11-14-bundled-fonts.md`
- Modify: `.prettierignore` (add `showpy/fonts/`), `_typos.toml` (`extend-exclude` adds `"showpy/fonts"`), `pyproject.toml` (`license`, `license-files`), `noxfile.py` (`WHEEL_CHECK`)
- Test: `tests/unit/test_packaging.py`

**Interfaces:**

- Produces the five font files and the SHA-256 of each, printed by the script. Task 3 copies those hashes into `BUNDLED_FONTS`.

- [ ] **Step 1: Write the failing test**

```python
BUNDLED = ("simplex", "hershey-roman", "hershey-script", "hershey-greek", "relief-singleline")
LICENCES = ("licenses/OFL-1.1.txt", "licenses/HERSHEY.txt", "NOTICE.txt")


def test_bundled_fonts_ship_with_their_licences() -> None:
    fonts = files("showpy").joinpath("fonts")
    for name in BUNDLED:
        font = json.loads(fonts.joinpath(f"{name}.json").read_text())
        assert font["format"] == "showpy.font.v1"
        assert font["license"] in {"MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts"}
    assert all(fonts.joinpath(path).is_file() for path in LICENCES)
```

- [ ] **Step 2: Run the test to see it fail.** Run: `uv run pytest tests/unit/test_packaging.py -q`. Expected: FAIL, because `fonts` is missing.
- [ ] **Step 3: Run the script once.** Run: `uv run python scripts/build_fonts.py --cache "$SCRATCH/font-sources" --out showpy/fonts --sheets "$SCRATCH/font-sheets"`, where `$SCRATCH` is the session scratchpad. Expected: one `Wrote … sha256:<hex>` line per font, and every file under 1,024 KB (the pre-commit large-file limit).
- [ ] **Step 4: Check the contact sheets by eye.** Open each PNG. Every drawn glyph must match the Unicode name printed under it. This matters most for `hershey-greek`: Θ at 534, Ψ and Ω at the end. If a Greek glyph does not match, fix `GREEK_BY_HERSHEY` and its Task 1 test, then rerun Step 3. If the table cannot be made right, drop `hershey-greek` everywhere in this plan and record that in the research note.
- [ ] **Step 5: Packaging.**
  - In `pyproject.toml`, set `license = "MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts"` and `license-files = ["LICENSE", "showpy/fonts/licenses/*.txt"]`.
  - Extend `WHEEL_CHECK` with `assert resources.files("showpy").joinpath("fonts", "relief-singleline.json").is_file(), "fonts missing"` and the same check for `fonts/licenses/OFL-1.1.txt`.
  - Write the research note: sources, commits, both checksums per file, licence terms (the Hershey acknowledgement duty; OFL with no Reserved Font Name), glyph counts per font, and why Hershey Cyrillic is left out (the pinned `cyrillic.jhf` carries no Hershey numbers, so its letter mapping cannot be verified).
- [ ] **Step 6: Run the test and the wheel.** Run: `uv run pytest tests/unit/test_packaging.py -q && uv run nox -s wheel`. Expected: PASS, and the wheel session prints `Found showpy`.
- [ ] **Step 7: Run the gates, then commit** `feat(geo): bundle Hershey and Relief SingleLine stroke fonts with their licences`.

### Task 3: Font errors, asset URIs and the frozen `FontRegistry`

**Files:**

- Modify: `showpy/core/errors.py`, `showpy/core/util.py`, `showpy/core/resources.py`, `showpy/show/repositories.py` (import `ASSET_PREFIX` and `asset_uri` from `showpy.core.util`; `InMemoryAssetStore.put` uses `asset_uri`), `showpy/geo/models.py`, `showpy/geo/text.py` (wrap `SIMPLEX` and `FONTS` in `types.MappingProxyType`)
- Create: `showpy/geo/fonts.py`
- Test: `tests/unit/core/test_errors.py`, `tests/unit/core/test_util.py`, `tests/unit/geo/test_fonts.py`, `tests/unit/geo/test_svg_text.py`

**Interfaces:**

- Consumes: the font files and hashes from Task 2.
- Produces:
  - `showpy.core.errors`: `FontError(ShowPyError)` with code `BAD_FONT`, `MissingFontError(UnknownRefError)` with `MISSING_FONT`, `GlyphError(UnknownRefError)` with `MISSING_GLYPH`, and `LegibilityWarning(UserWarning)`.
  - `showpy.core.util`: `ASSET_PREFIX = "asset:sha256:"` and `asset_uri(data: bytes) -> str`.
  - `showpy.core.resources`: `font_path(name: str) -> Traversable`, the path to `showpy/fonts/<name>`.
  - `showpy.geo.models`:
    - `FontGlyph(FrozenModel)`: `advance: float`, `strokes: tuple[str, ...] = ()`.
    - `Font(FrozenModel)`: `format: Literal["showpy.font.v1"]`, `name: str` (pattern `^[a-z0-9][a-z0-9-]*$`), `family: str`, `license: str`, `source: str`, `units_per_em: float`, `cap_height: float | None = None`, `ascender: float`, `descender: float`, `glyphs: dict[str, FontGlyph]`.
    - `Font.cap() -> float`: `cap_height`, else the ink height of `H`, else `ascender`.
  - `showpy.geo.fonts`:
    - `MAX_FONT_BYTES = 8 * 1024 * 1024`
    - `BUNDLED_FONTS: Mapping[str, str]`, a `MappingProxyType` from name to URI holding the literal hashes from Task 2
    - `FontRegistry(FrozenModel)` with fields `fonts: dict[str, Font]`, `sources: dict[str, bytes]` and `names: dict[str, str]`, and the methods:
      - `get(uri: str) -> Font`
      - `resolve(ref: str) -> str`, which accepts a name or a URI
      - `source(uri: str) -> bytes`
    - `load_font(data: bytes) -> Font`
    - `register_font(registry: FontRegistry, data: bytes) -> tuple[FontRegistry, str]`
    - `bundled_fonts() -> FontRegistry`, cached with `functools.cache`

- [ ] **Step 1: Write the failing tests.** `TINY` is a test constant: a `showpy.font.v1` dict named `"tiny"` with `units_per_em = cap_height = ascender = 10`, `descender = 0`, and one glyph `"I": {"advance": 4, "strokes": ["M2 0 L2 10"]}`. `EMPTY = FontRegistry(fonts={}, sources={}, names={})`.

```python
def test_font_errors_carry_their_codes() -> None:
    assert GlyphError("m", hint="h").code == "MISSING_GLYPH"
    assert MissingFontError("m", hint="h").code == "MISSING_FONT"
    assert FontError("m", hint="h").code == "BAD_FONT"
    assert issubclass(GlyphError, UnknownRefError) and issubclass(LegibilityWarning, UserWarning)


def test_asset_uri_matches_the_asset_store() -> None:
    assert asset_uri(b"x") == InMemoryAssetStore().put(b"x")


def test_bundled_font_uris_are_the_file_hashes() -> None:
    for name, uri in BUNDLED_FONTS.items():
        assert uri == asset_uri(font_path(f"{name}.json").read_bytes())


def test_resolve_accepts_names_and_uris() -> None:
    uri = BUNDLED_FONTS["relief-singleline"]
    assert bundled_fonts().resolve("relief-singleline") == uri
    assert bundled_fonts().resolve(uri) == uri


def test_resolve_unknown_name_suggests_close_names() -> None:
    with pytest.raises(MissingFontError) as raised:
        bundled_fonts().resolve("relief-singlelin")
    assert "relief-singleline" in raised.value.hint


def test_get_unknown_uri_names_the_hash() -> None:
    uri = "asset:sha256:" + "0" * 64
    with pytest.raises(MissingFontError) as raised:
        bundled_fonts().get(uri)
    assert raised.value.context["font"] == uri


def test_register_font_returns_a_new_registry_and_the_store_uri() -> None:
    data = json.dumps(TINY).encode()
    registry, uri = register_font(EMPTY, data)
    assert uri == InMemoryAssetStore().put(data)
    assert registry.resolve("tiny") == uri and registry.source(uri) == data
    assert EMPTY.fonts == {}


def test_register_font_is_idempotent() -> None:
    data = json.dumps(TINY).encode()
    once, uri = register_font(EMPTY, data)
    assert register_font(once, data) == (once, uri)


def test_register_font_rejects_a_taken_name() -> None:
    once, _ = register_font(EMPTY, json.dumps(TINY).encode())
    other = {**TINY, "glyphs": {"L": {"advance": 4, "strokes": ["M0 0 L0 10"]}}}
    with pytest.raises(FontError) as raised:
        register_font(once, json.dumps(other).encode())
    assert raised.value.code == "FONT_NAME_TAKEN"


@pytest.mark.parametrize("data", BAD_FONT_BYTES)
def test_load_font_rejects_bad_bytes(data: bytes) -> None:
    with pytest.raises(FontError) as raised:
        load_font(data)
    assert raised.value.code == "BAD_FONT"


def test_font_cap_falls_back_to_h_then_ascender() -> None: ...


def test_regression_simplex_tables_are_read_only() -> None:
    with pytest.raises(TypeError):
        FONTS["other"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        SIMPLEX["?"] = ()  # type: ignore[index]
```

`BAD_FONT_BYTES` holds:

- `b"not json"`
- `TINY` with `"format": "other"`
- `TINY` with a glyph key `"AB"`
- `TINY` with the stroke `"M0 0"` (no length)
- `TINY` with the stroke `"M0 0 L1 0 M2 0 L3 0"` (two subpaths)
- `TINY` with `units_per_em: 0`
- `b" " * (MAX_FONT_BYTES + 1)`

The cap-fallback test checks `Font.cap()` three ways: with `cap_height` set; without it, giving the ink height of `H`; and without `H`, giving the ascender.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/core tests/unit/geo/test_fonts.py tests/unit/geo/test_svg_text.py -q`. Expected: FAIL with import errors.
- [ ] **Step 3: Implement.**
  - `load_font` rejects data over `MAX_FONT_BYTES`, bad JSON and a Pydantic `ValidationError` with `FontError` (`BAD_FONT`); the context names the failing field. It checks that every stroke parses to exactly one subpath with positive length.
  - `register_font`: equal bytes return the same registry. A name already bound to another URI raises `FONT_NAME_TAKEN`. The result is a new `FontRegistry`.
  - `resolve` and `get` raise `MissingFontError`. The context holds `font` (and `known`, for names). The hint names `register_font` and `import_fonts`, plus close names from `close_matches`.
  - `bundled_fonts` registers the five files named in `BUNDLED_FONTS` through `font_path`.
- [ ] **Step 4: Run the tests to see them pass.** Same command as Step 2. Expected: PASS.
- [ ] **Step 5: API change procedure, then commit both commits.** Commit ``feat(core): add `FontError`, `MissingFontError`, `GlyphError`, `LegibilityWarning` and `asset_uri` `` (errors, util, repositories). Then commit ``feat(geo): add `FontRegistry`, `Font`, `FontGlyph`, `BUNDLED_FONTS`, `bundled_fonts`, `load_font`, `register_font` and `font_path` ``. Run the procedure once per commit.

### Task 4: Text layout with a per-character fallback chain

**Files:**

- Create: `showpy/geo/text_layout.py`
- Modify: `showpy/geo/models.py`
- Test: `tests/unit/geo/test_text_layout.py`

**Interfaces:**

- Consumes: `FontRegistry`, `bundled_fonts()` and `GlyphError` from Task 3.
- Produces:
  - In `showpy.geo.models`:
    - `type TextAlign = Literal["left", "center", "right"]`
    - `type TextAnchor = Literal["baseline", "middle", "top"]`
    - `DEFAULT_LINE_SPACING = 1.2`
    - `LaidGlyph(FrozenModel)`: `char: str`, `font: str` (the URI), `line: int`, `strokes: tuple[Polyline | SvgCurve, ...]`. A space has no strokes.
  - In `showpy.geo.text_layout`: `layout_text(text: str, fonts: Sequence[str], height: Meters, at: Point, *, rotation: Degrees = 0.0, align: TextAlign = "left", anchor: TextAnchor = "baseline", letter_spacing: float = 0.0, line_spacing: float = DEFAULT_LINE_SPACING, font_registry: FontRegistry | None = None) -> tuple[LaidGlyph, ...]`
  - Strokes are in meters on the stage plane, in reading order. A stroke made only of `LinePiece`s becomes a `Polyline` (points mapped explicitly, `closed` kept). Any other stroke becomes `SvgCurve(d=stroke, transform=matrix, closed=…)`.

- [ ] **Step 1: Write the failing tests.** Expected values follow the rules in Step 3. `TINY_REGISTRY` is `bundled_fonts()` plus Task 3's `TINY`.
  - `test_layout_puts_baseline_left_on_at`: `"H"`, simplex, height 2.0, at (1, 5). Stroke x min is 1.0, y min 5.0, y max 7.0 (`approx_equal`, tol 1e-12).
  - `test_layout_matches_cap_heights_across_fonts` (Review Focus 5): `"IH"` with fonts `["tiny", "simplex"]`. The I comes from tiny and the H from simplex, both tops equal at.y + height, and the H starts at `tiny advance × (height / 10)`.
  - `test_layout_falls_back_per_character`: `"a!"` with `["simplex", "hershey-roman"]`. The glyph fonts are the simplex URI, then the hershey-roman URI.
  - `test_layout_reports_a_missing_glyph`: `"A☃"` with `["simplex"]` raises `GlyphError`. Its code is `MISSING_GLYPH`, `context["codepoint"] == "U+2603"`, `context["fonts"] == ["simplex"]`, and `"U+2603"` is in the hint.
  - `test_layout_normalizes_decomposed_accents` (Review Focus 2): `"é"` with relief gives one glyph whose `char` is `"é"`.
  - `test_layout_treats_crlf_as_one_line_break` (Review Focus 2): the `line` values of `"A\r\nB"` equal those of `"A\nB"`, which are `[0, 1]`.
  - `test_layout_aligns_each_line`: `"HH\nH"`. Center puts both lines' advance midpoints on at.x; right puts both lines' advance ends on at.x.
  - `test_layout_anchor_top_and_middle`: `top` puts the first line's cap line on at.y. `middle` on `"H\nH"` puts the midpoint between the first cap line and the last baseline on at.y.
  - `test_layout_spacing_uses_the_first_fonts_em`: simplex, height 6, where em = 6 m. With `letter_spacing=0.5`, the second H's left edge sits at 5 + 3 = 8 m. With `line_spacing=2.0`, line 2's baseline is at y = −12.
  - `test_layout_rotation_turns_counter_clockwise`: `"H"` simplex, height 6, at (0, 0), rotation 90. All points have x in [−6, 0] and y in [0, 4].
  - `test_layout_curved_strokes_are_svg_curves`: relief `"O"` gives one `SvgCurve` with `closed`; simplex `"O"` gives one `Polyline` with `closed`.
  - `test_layout_rejects_bad_sizes`: height 0, height NaN and `line_spacing` 0 each raise `GeometryError`; `fonts=[]` raises `SchemaError`.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/geo/test_text_layout.py -q`. Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `layout_text`.**
  - Normalize with `unicodedata.normalize("NFC", …)`, map `\r\n` and `\r` to `\n`, and split on `\n`.
  - Resolve every font in the chain. Each character takes the first font that has it. If none does, raise `GlyphError`, whose hint says to add a font covering `U+XXXX`, for example with `register_font()`.
  - Glyph scale is `height / font.cap()`. `em_m` comes from the first font.
  - Line widths come from advances: the cursor adds `advance × scale + letter_spacing × em_m` between glyphs, with no trailing spacing. Align offsets are 0, −w/2 and −w.
  - Line k's baseline sits at `−k × line_spacing × em_m`.
  - Anchor offsets: `baseline` 0, `top` −height, `middle` −(height + last baseline)/2.
  - Rotate about the origin, then translate by `at`.
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS.
- [ ] **Step 5: API change procedure, then commit** ``feat(geo): add `layout_text` and `LaidGlyph` for multi-line text with font fallback``. Also list `TextAlign`, `TextAnchor` and `DEFAULT_LINE_SPACING` if the snapshot does.

### Task 5: `text_outline` on the registry; closed glyph loops (UP-9.1-28)

**Files:**

- Modify: `showpy/geo/shapes.py`
- Test: `tests/unit/geo/test_shapes.py` (remove the `defect` and `xfail` markers from `test_regression_text_outline_loops_are_closed_without_repeated_start`), `tests/unit/geo/test_svg_text.py`

**Interfaces:**

- Consumes: `layout_text` (Task 4).
- Produces: `text_outline(text: str, font: str | Sequence[str], height: Meters, at: Point, *, font_registry: FontRegistry | None = None) -> list[Polyline | SvgCurve]`. The result is every `LaidGlyph`'s strokes, flattened in reading order. `at` is still the baseline-left point.

- [ ] **Step 1: Remove the UP-9.1-28 markers** (all 5 parametrized cases). Then add these tests:
  - `test_regression_simplex_text_outline_keeps_its_geometry`: for every key in `SIMPLEX`, plus its lowercase form, check `text_outline(c, "simplex", 2.0, (1.0, 3.0))`. The expected value is the old formula `(at.x + x·s, at.y + y·s)` with `s = 2.0 / 6`, except that a stroke whose first point equals its last is expected `closed=True` without that last point. Stroke count, order and `closed` flags must match exactly, and points within 1e-12.
  - `test_text_outline_spells_lowercase_and_punctuation`: `text_outline("hi!", "hershey-roman", 1.0, (0, 0))` is non-empty and all its strokes are `Polyline`.
  - `test_text_outline_takes_a_fallback_chain`: `text_outline("A€", ["simplex", "relief-singleline"], 1.0, (0, 0))` is non-empty; `text_outline("A€", "simplex", 1.0, (0, 0))` raises `GlyphError`.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/geo/test_shapes.py tests/unit/geo/test_svg_text.py -q`. Expected: the five UP-9.1-28 cases and the new tests FAIL. The existing `test_text_outline_unknown_font_or_glyph` passes before and after, since both new errors are `UnknownRefError`s.
- [ ] **Step 3: Rewrite `text_outline` on top of `layout_text`.** Drop its import of `ADVANCE`, `GLYPH_HEIGHT` and `glyph_strokes`. Update its doctest so it counts `HI` strokes as 6.
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS. The gates report `Ledger: 523 open`.
- [ ] **Step 5: API change procedure, then commit** ``fix(geo): close glyph loops and spell `text_outline` from the font registry``.

### Task 6: Apportion with per-curve minimums

**Files:**

- Modify: `showpy/geo/spacing.py` (replace the private `_apportion`, which `space_proportional` calls today)
- Test: `tests/unit/geo/test_spacing.py`

**Interfaces:**

- Produces: `apportion(lengths: Sequence[float], n: int, minimums: Sequence[int] | None = None) -> list[int]`. `minimums` defaults to 1 per curve, which keeps `space_proportional` unchanged.
- Rule: `count_i = m_i + floor(q_i)`, where `q_i = (n − Σm) · L_i / ΣL`, or an equal share when ΣL = 0. Leftover spots go to the largest fractional parts; ties go to the lower index.
- Errors (`SpacingError`): mismatched lengths; a negative or non-finite length or minimum; `n < Σm`. The last has `context["min_n"] == Σm` and the hint `use n >= {Σm}`.

- [ ] **Step 1: Write the failing tests:**
  - `apportion([4.0, 12.0], 6) == [2, 4]`
  - `apportion([1.0, 100.0], 6, [3, 2]) == [3, 3]`
  - `apportion([0.0, 0.0], 4, [0, 0]) == [2, 2]`
  - `apportion([1.0, 1.0], 3, [2, 2])` raises with `min_n == 4`
  - The Hypothesis property `test_apportion_sums_exactly_and_respects_minimums`. Lengths are 1–30 floats in [0, 100], minimums are in 0..3, and n runs from Σm to Σm + 200. The property checks: the sum is n; every count is ≥ its minimum; two calls give equal results; and with equal minimums, a longer curve never gets fewer spots.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/geo/test_spacing.py -q`. Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `apportion`.** Have `space_proportional` call it.
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS, and the existing `space_proportional` doctest still prints `[2, 4]`.
- [ ] **Step 5: API change procedure, then commit** ``feat(geo): add `apportion` with per-curve minimums``.

### Task 7: The text recipe and exact-`n` slots on strokes

**Files:**

- Modify: `showpy/choreo/models.py` (`TextRecipe`, placed after `FacingSpec`; `FormKind` stays unchanged until Task 8)
- Create: `showpy/choreo/form_text.py`
- Test: `tests/unit/choreo/test_form_text.py`

**Interfaces:**

- Consumes: `layout_text`, `TextAlign`, `TextAnchor`, `DEFAULT_LINE_SPACING` (Task 4) and `apportion` (Task 6).
- Produces:
  - `MAX_TEXT_CHARS = 1000`
  - `TextRecipe(FrozenModel)` with these fields:
    - `text: str`, 1..`MAX_TEXT_CHARS` characters
    - `fonts: tuple[str, ...]`, at least one, each matching `^asset:sha256:[0-9a-f]{64}$`
    - `mode: Literal["centerline"] = "centerline"`
    - `height: Meters`, > 0
    - `at: Point`
    - `rotation: Degrees = 0.0`
    - `align: TextAlign = "left"`
    - `anchor: TextAnchor = "baseline"`
    - `letter_spacing: float = 0.0`
    - `line_spacing: float = DEFAULT_LINE_SPACING`, > 0
  - In `showpy.choreo.form_text`:
    - `STROKE_MINIMUM_OPEN = 2`, `STROKE_MINIMUM_CLOSED = 3`
    - `LEGIBLE_SPACING_SHARE = 0.5`, with a comment explaining it: a vertical stroke reads as a line only with spots at top, middle and bottom, so a spacing above half the cap height loses that.
    - `COINCIDENT_TOL_M = 1e-6`
    - `text_recipe(geometry: Mapping[str, Any]) -> TextRecipe`. It ignores the shared keys `n` and `transform`. A Pydantic error becomes a `SchemaError` naming the field and the allowed values.
    - `text_strokes(recipe: TextRecipe, font_registry: FontRegistry | None = None) -> list[Polyline | SvgCurve]`
    - `min_text_spots(strokes: Sequence[Curve]) -> int`
    - `text_slots(recipe: TextRecipe, n: int, font_registry: FontRegistry | None = None) -> tuple[Points, Points]`, which returns points and unit tangents before any stored transform.

- [ ] **Step 1: Write the failing tests.** `recipe(text, *fonts, **kw)` builds a `TextRecipe` with fonts resolved through `bundled_fonts()`, `height=2.0` and `at=(0.0, 10.0)`. `min_gap(pts)` returns the smallest pairwise distance.
  - `test_text_slots_count_is_exact`: for `"HI"` simplex, `len(text_slots(r, n)[0]) == n` for n in (12, 13, 37).
  - `test_text_slots_follow_reading_order`: for `"HI"` simplex with n = 12, slot 0 equals the H's first stroke start, and the last 6 slots all have x ≥ the I's left edge.
  - `test_regression_closed_loops_never_double_a_spot`: for `"O0D8Q6B"` in simplex and in relief, at n = 60, `min_gap > COINCIDENT_TOL_M`. This is the UP-9.1-28 defect class, now in slots.
  - `test_regression_crossing_strokes_never_stack_spots` (Review Focus 1): every glyph of every bundled font that has strokes, at n = `min_text_spots` and at 3 × that, has `min_gap ≥ COINCIDENT_TOL_M`.
  - `test_too_few_spots_names_the_minimum`: `"HI"` simplex at n = 5 raises `SpacingError` with code `TOO_FEW_SPOTS`, `context["min_n"] == 12`, and `"12"` in the hint.
  - `test_whitespace_only_text_is_rejected` (Review Focus 2): `" \n "` raises `SchemaError`.
  - `test_sparse_text_warns_about_legibility`: relief `"I"` at its minimum n gives `pytest.warns(LegibilityWarning)`.
  - `test_dense_text_does_not_warn`: `"HI"` simplex at n = 36 issues no warning (`filterwarnings = error` enforces this).
  - `test_text_slots_are_byte_identical_across_calls`: the `tobytes()` of two runs are equal.
  - `test_text_recipe_rejects_bad_fields`: parametrize over `height=0`, `fonts=[]`, `fonts=["relief-singleline"]` (a name, not a URI), `mode="outline"`, `align="justify"`, `text=""` and `text="x" * 1001`. Each raises `SchemaError` whose `context["field"]` names the field.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/choreo/test_form_text.py -q`. Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `form_text.py`.**
  - Strokes are the non-empty strokes of `layout_text(...)`, in reading order. With no strokes, raise `SchemaError`, hint "give text with at least one visible character". If `n < min_text_spots`, raise `SpacingError(code="TOO_FEW_SPOTS")` with context `n`, `min_n` and `text`, and the hint `use n >= {min_n} for this text, or shorten it`.
  - Counts come from `apportion(lengths, n, minimums)`.
  - Station candidates for a stroke of length L with k spots, j = 0..k−1. Take the first candidate whose points are all at least `COINCIDENT_TOL_M` from each other and from every earlier slot.
    - Open stroke: `j·L/(k−1)`; `(j+1)·L/k`; `j·L/k`; `(j+1)·L/(k+1)`; `(j+0.5)·L/k`.
    - Closed stroke: `j·L/k`; `(j+0.5)·L/k`.
  - When no candidate fits, raise `GeometryError(code="COINCIDENT_SLOTS")` with the stroke's character in the context and the hint "change n or the text".
  - Tangents come from `curve.tangent_at(stations)`.
  - Warn with `LegibilityWarning` when the largest per-stroke spacing (L/(k−1) for an open stroke, L/k for a closed one) exceeds `LEGIBLE_SPACING_SHARE × height`. The message is `W: text spots are up to {s:.2f} m apart on {h:.2f} m letters; raise n`.
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS.
- [ ] **Step 5: API change procedure, then commit** ``feat(choreo): add `TextRecipe` and exact-count text slots on strokes``. Leave `form_text` functions out of `_api_table.py`: they are internal, like `form_layout`.

### Task 8: The `text` form kind, `text_form` and `font_registry` threading

**Files:**

- Modify:
  - `showpy/choreo/models.py`: `FormKind` gains `"text"`
  - `showpy/choreo/form_layout.py`: `layout(form, registry=DEFAULT_FORM_FNS, *, font_registry=None)`, plus a text branch
  - `showpy/choreo/forms.py`: `text_form`; `font_registry` on `slots`, `slot_poses` and `placements`; the module docstring's recipe contract gains `text: TextRecipe fields`
  - `showpy/choreo/form_ops.py`: `form_templates` gains `"text"`
  - `showpy/schemas/showpy.show.v1.json`
  - `tests/strategies/forms.py`
- Create: `tests/golden/test_golden_text.py`, `tests/golden/fixtures/text_slots.json`
- Test: `tests/unit/choreo/test_forms.py`, `tests/unit/choreo/test_form_ops.py` (`test_form_templates_cover_every_kind` gains `"text"`), `tests/property/test_strategy_coverage.py` (unchanged, must pass)

**Interfaces:**

- Consumes: `text_recipe` and `text_slots` (Task 7).
- Produces:
  - `text_form(form_id: FormId, members: Sequence[SpotId], text: str, *, fonts: Sequence[str], height: Meters, at: Point, rotation: Degrees = 0.0, align: TextAlign = "left", anchor: TextAnchor = "baseline", letter_spacing: float = 0.0, line_spacing: float = DEFAULT_LINE_SPACING, facing: FacingSpec | None = None, set_id: SetId = "", n: int, font_registry: FontRegistry | None = None) -> Form`. `n` is a required keyword until Task 10 adds `spacing` as its alternative. It resolves font names to URIs, stores `TextRecipe(...).model_dump(mode="json")` plus `"n"`, and validates by building the layout, as `generated_form` does.
  - `slots`, `slot_poses` and `placements` each gain `*, font_registry: FontRegistry | None = None`.
  - The text branch of `layout`: `n = _required_count(form)`; `pts, tangents = text_slots(text_recipe(form.geometry), n, font_registry)`. A stored `"transform"` maps points through `transform_points` and tangents through its 2 × 2 linear part. It returns `FormLayout(pts, tangents)`.

- [ ] **Step 1: Write the failing tests.**
  - The `text_form` doctest: `len(slots(text_form("go", [f"S{i}" for i in range(24)], "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=24)))` prints `24`.
  - `test_text_form_stores_uris_not_names`: `geometry["fonts"] == [BUNDLED_FONTS["hershey-roman"]]`.
  - `test_text_form_placements_follow_reading_order`: members `A…` stand on slots in order.
  - `test_existing_kinds_ignore_the_font_registry`: `slots(line_form(...), font_registry=EMPTY)` equals `slots(line_form(...))`.
  - `test_text_form_with_a_user_font_needs_its_registry`: with `TINY` registered, `slots(f, font_registry=reg)` works. `gate(f, "start", 90.0)` (Review Focus 3) raises `MissingFontError` whose `context["font"]` is the tiny URI.
  - `test_transformed_text_moves_slots_and_facings` (Review Focus 4): `transform_form(f, translation_matrix(0, 3))` shifts every slot by (0, 3) within 1e-9. `transform_form(f, rotation_matrix(90.0, (0, 0)))` with `FacingSpec(mode="along")` turns every facing by 90° (mod 360) within 1e-9.
  - `tests/strategies/forms.py` gets a `_text` builder: `text_form(d.form_id, d.members, "-", fonts=["simplex"], height=draw(sizes), at=d.center, facing=d.facing, set_id=d.set_id, n=len(d.members))`. Simplex `-` is one open stroke (minimum 2), which fits `MIN_MEMBERS = 2`, and it never reaches the legibility threshold. Add `"text"` to `FORM_KINDS` and `BUILDERS`.
  - Golden `test_text_slots_match_reviewed_fixture`, marked `golden`. Every entry uses height 4.0 at (0, 10). It holds:
    - `"GO TEAM"` in hershey-roman with n = 60
    - `"Équipe 2026!"` in relief with n = 90, rotation 15, `align="center"`, `anchor="middle"`
    - `"ΑΒΓ"` in hershey-greek with n = 30

    It stores slots rounded to 9 digits and compares with tol 1e-9. `SHOWPY_UPDATE_GOLDEN=1` rewrites the fixture, which then needs human review.

- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/choreo tests/property/test_strategy_coverage.py tests/golden/test_golden_text.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `SHOWPY_UPDATE_SCHEMA=1 uv run pytest tests/unit/show/test_schema.py`. Check that the schema diff is only `"text"` added to the form kind enum, and keep `showpy.show.v1`. Run `SHOWPY_UPDATE_GOLDEN=1 uv run pytest tests/golden/test_golden_text.py` once, and look over the fixture: 180 rows, no NaN.
- [ ] **Step 4: Run the tests to see them pass.** Same command, plus `uv run pytest tests/property -q`. Expected: PASS.
- [ ] **Step 5: API change procedure, then commit** ``feat(choreo): add the `text` form kind with `text_form`; `slots`, `slot_poses` and `placements` take `font_registry` ``. If the snapshot lists `Form` or `form_templates`, name them as well.

### Task 9: `edit(form, text=...)` keeps the same members

**Files:**

- Modify: `showpy/choreo/form_ops.py` (`RECIPE_KEYS["text"] = frozenset(TextRecipe.model_fields)`; `edit` gains `font_registry`; when a text form's change set has `fonts`, each one is resolved to its URI)
- Test: `tests/unit/choreo/test_form_ops.py`

**Interfaces:**

- Consumes: `text_form` (Task 8).
- Produces: `edit(form: Form, force: bool = False, registry: FormFnRegistry = DEFAULT_FORM_FNS, *, font_registry: FontRegistry | None = None, **changes: Any) -> Form`. A text, font or layout edit never changes the members or `n`. Count edits arrive in Task 10.

- [ ] **Step 1: Write the failing tests.**
  - `test_edit_text_keeps_members_in_reading_order`: `f = text_form("t", members_60, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=60)`, then `g = edit(f, text="GO TEAM")`. Then `g.members == f.members`, `len(slots(g)) == 60`, and `placements(g)[0]` stands on the first stroke start of the `G`.
  - `test_edit_text_too_long_for_the_count_names_the_minimum`: a 12-member simplex `"HI"` with `n=12`, edited to `"HIHI"`, raises `SpacingError` with code `TOO_FEW_SPOTS` and `context["min_n"] == 24`.
  - `test_edit_text_resolves_font_names`: `edit(f, fonts=["relief-singleline"]).geometry["fonts"] == [BUNDLED_FONTS["relief-singleline"]]`.
  - `test_edit_text_suggests_the_field_for_a_typo`: `edit(f, txt="A")` raises `SchemaError`, and `"text"` is in the hint.
  - `test_edit_locked_text_form_needs_force`: raises `LockedError`.
  - `test_edit_text_with_a_user_font_takes_the_registry`: with the tiny registry, `edit(f, text="II", font_registry=reg)` works, and without the registry it raises `MissingFontError`.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/choreo/test_form_ops.py -q`. Expected: FAIL. The `text` key is missing from `RECIPE_KEYS`, and `edit` has no `font_registry`.
- [ ] **Step 3: Implement.** `edit` passes `font_registry` to `layout`.
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS.
- [ ] **Step 5: API change procedure, then commit** ``feat(choreo): regenerate text forms through `edit` with the same members``.

### Task 10: Spot counts from a spacing along strokes

**Files:**

- Modify: `showpy/choreo/models.py` (`TextRecipe.spacing`), `showpy/choreo/form_text.py` (`MAX_TEXT_SPOTS`, `spacing_count`), `showpy/choreo/forms.py` (`text_form` takes `n` or `spacing`), `showpy/choreo/form_ops.py` (count edits on text forms)
- Test: `tests/unit/choreo/test_form_text.py`, `tests/unit/choreo/test_forms.py`, `tests/unit/choreo/test_form_ops.py`

**Interfaces:**

- Consumes: `text_strokes`, `min_text_spots` and `text_slots` (Task 7), `apportion` (Task 6), `text_form` (Task 8) and `edit` (Task 9).
- Produces:
  - `TextRecipe.spacing: Meters | None = None`, > 0 and finite when set. It records the spacing that last set the count; `text_recipe` still ignores `n`.
  - In `showpy.choreo.form_text`:
    - `MAX_TEXT_SPOTS = 10_000`
    - `spacing_count(strokes: Sequence[Curve], spacing: Meters) -> int`
  - `text_form(..., n: int | None = None, spacing: Meters | None = None, font_registry: FontRegistry | None = None) -> Form`. Exactly one of `n` and `spacing` must be given. With `spacing`, the recipe stores `spacing` and `"n" = spacing_count(text_strokes(recipe), spacing)`.
  - `edit` on a text form:
    - `text=`, `fonts=` and the layout fields keep `n` and `spacing` as stored.
    - `spacing=s` re-derives `n` from the recipe after this call's other changes, and stores both.
    - `n=k` stores `k` and sets `spacing` to `None`.
    - `n=` and `spacing=` together raise `SchemaError(code="N_OR_SPACING")`.
    - Members never change.
- The count rule. Each open stroke spans L/s gaps, so it holds L/s + 1 spots; each closed stroke holds L/s. So `ideal = ΣL / s + open_strokes`, and `n = max(min_text_spots(strokes), floor(ideal + 0.5))`. Half rounds up, which is deterministic. Per-stroke counts then come from the same `apportion(lengths, n, minimums)` call as the `n` path, so the minimums, the coincidence rule and the legibility warning apply unchanged. If `n > MAX_TEXT_SPOTS`, raise `SpacingError(code="TOO_MANY_SPOTS")` with context `n`, `max_n` and `spacing`, and the hint `use spacing >= {ΣL / (MAX_TEXT_SPOTS - open_strokes):.3f} m`. Raise it before any slot is placed.

- [ ] **Step 1: Write the failing tests.** Simplex `"L"` at height 6 is one open stroke of 10 m (6 m down, 4 m across). Simplex `"O"` at height 6 is one closed stroke of 12 + 4√2 ≈ 17.657 m.
  - `test_spacing_count_follows_total_stroke_length`: `"L"` with spacing 1.0 gives 11. `text_form(..., spacing=1.0)` stores `geometry["n"] == 11` and `geometry["spacing"] == 1.0`.
  - `test_spacing_count_rounds_half_up`: `"L"` with spacing 4.0 gives `floor(2.5 + 1 + 0.5) == 4`. Mark it with the `LegibilityWarning` filter, since 10/3 m > 3 m.
  - `test_spacing_count_respects_stroke_minimums` (Review Focus 6): `"O"` with spacing 100.0 gives 3, the closed minimum, and slot building warns: `pytest.warns(LegibilityWarning)`.
  - `test_tiny_spacing_hits_the_spot_cap` (Review Focus 6): `"L"` with spacing 1e-6 raises `SpacingError` with code `TOO_MANY_SPOTS` and `context["max_n"] == 10_000`.
  - `test_regression_spacing_never_doubles_a_spot_on_closed_loops`: `"O0D8Q6B"` in simplex and in relief, height 2.0, spacing 0.3: `min_gap > COINCIDENT_TOL_M`.
  - `test_text_form_needs_exactly_one_of_n_and_spacing`: both missing, and both given, each raise `SchemaError` with code `N_OR_SPACING`. The hint names `n=` and `spacing=`.
  - `test_text_recipe_rejects_bad_spacing`: spacing 0, −1 and NaN each raise `SchemaError` with `context["field"] == "spacing"`.
  - `test_edit_text_keeps_the_derived_count`: `f = text_form("t", [], "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), spacing=0.5)`, then `edit(f, text="GOGO")` keeps `geometry["n"]` and `geometry["spacing"]`, and its members equal `f.members`.
  - `test_edit_spacing_rederives_the_count`: `edit(f, spacing=1.0).geometry["n"]` equals `spacing_count(text_strokes(text_recipe(f.geometry)), 1.0)`. `edit(f, text="GO TEAM", spacing=1.0)` derives the count from `"GO TEAM"`.
  - `test_edit_n_clears_spacing`: `edit(f, n=30)` gives `geometry["n"] == 30` and `geometry["spacing"] is None`.
  - `test_edit_n_and_spacing_together_is_rejected`: raises `SchemaError` with code `N_OR_SPACING`.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/choreo -q`. Expected: the new tests FAIL. `spacing_count` is missing, and `text_form` has no `spacing`.
- [ ] **Step 3: Implement.**
  - `text_form` and `edit` share one helper in `form_text.py`, `count_recipe(geometry: Mapping[str, Any], n: int | None, spacing: Meters | None, font_registry: FontRegistry | None) -> dict[str, Any]`. It applies the exactly-one rule and returns the geometry with `n` and `spacing` set.
  - Update the `text_form` doctest to `n=24` or `spacing=`, whichever keeps it free of warnings.
  - Update the recipe contract in the `forms.py` module docstring: `spacing` is a request, and `n` is the stored count.
- [ ] **Step 4: Run the tests to see them pass.** Run: `uv run pytest tests/unit/choreo tests/property tests/golden/test_golden_text.py -q`. Expected: PASS. The golden is unchanged, since it uses `n`.
- [ ] **Step 5: API change procedure, then commit** ``feat(choreo): derive text spot counts from `spacing` in `text_form` and `edit` ``. Also name `TextRecipe` if the snapshot lists it.

### Task 11: Fonts travel with exported shows

**Files:**

- Modify: `showpy/export/data.py`
- Test: `tests/unit/export/test_data.py`

**Interfaces:**

- Consumes: `FontRegistry.source`, `register_font` and `asset_uri`.
- Produces:
  - `FONTS_KEY = "fonts"`
  - `export_json(show: Show, include: Iterable[str] | None = None, *, font_registry: FontRegistry | None = None) -> dict[str, Any]`. It adds `{"fonts": {uri: base64}}`, sorted by URI, holding every font listed by any `text` form in the exported `forms`. Bundled fonts are included, so the document does not depend on the library version. A show with no exported text form gets no `fonts` key.
  - `import_fonts(d: Mapping[str, Any], font_registry: FontRegistry | None = None) -> FontRegistry`. It decodes each entry (`binascii.Error` becomes `FontError` `BAD_FONT`), checks `asset_uri(data) == uri` (`FontError(code="FONT_HASH_MISMATCH")`, with `uri` in the context) and registers the font.
  - `import_json` is unchanged and still returns `Show`.

- [ ] **Step 1: Write the failing tests.** `text_show(fonts)` is `two_set_show()` with one text form added to `forms`.
  - `test_export_embeds_every_font_a_text_form_uses`: the `fonts` keys are the tiny and simplex URIs, and `b64decode` of the tiny entry equals its bytes.
  - `test_export_without_text_forms_has_no_fonts_key`: `set(export_json(two_set_show())) == {"schema", "show"}`.
  - `test_import_fonts_round_trips_slots`: `slots(form, font_registry=import_fonts(doc))` is `np.array_equal` to the original slots.
  - `test_import_fonts_rejects_tampered_data`: raises `FontError` with code `FONT_HASH_MISMATCH`.
  - `test_import_fonts_rejects_bad_base64`: raises `FontError` with code `BAD_FONT`.
  - `test_export_with_a_missing_font_fails_closed`: raises `MissingFontError`.
  - `test_export_include_without_forms_embeds_no_fonts`.
- [ ] **Step 2: Run the tests to see them fail.** Run: `uv run pytest tests/unit/export/test_data.py tests/property/test_export_properties.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests to see them pass.** Same command. Expected: PASS, with the export property tests still green.
- [ ] **Step 5: API change procedure, then commit** ``feat(export): embed text fonts in `export_json` and add `import_fonts` ``.

### Task 12: Documents, changelog and full gate

**Files:**

- Modify: `docs/showpy-api-reference.md` (the `showpy.geo` text rows, plus a "Text forms" subsection under `showpy.choreo.forms`), `docs/HANDOFF.md` (module map, a status line, deviations), `CHANGELOG.md`

- [ ] **Step 1: Update the docs.**
  - **Reference.** Document `text_outline`, `layout_text`, `FontRegistry`, `register_font`, `bundled_fonts`, `text_form` (`n=` or `spacing=`), `TextRecipe`, `edit(text=)`, the count edits (`edit(n=)`, `edit(spacing=)`) and `import_fonts`, with an error-code table.
  - **HANDOFF deviations.** `simplex` is its own font, not an alias; fonts are imported with `import_fonts`, not inside `import_json`; Hershey Cyrillic is deferred; `spacing=` stores its derived count as `n`, so a text edit keeps the count.
  - **HANDOFF next steps.** Plans for stage 2 and stage 3, and for threading `font_registry` through `match`, `reorder`, `gate`, `split_form`, `merge_forms` and `form_place`.
  - Run Prettier on both files.
- [ ] **Step 2: Regenerate the changelog.** Run `uv run nox -s changelog`, which replaces the `()` placeholders with real SHAs. Then run `uv run pytest tests/unit/test_api_snapshot.py`. Expected: PASS.
- [ ] **Step 3: Run the full gate.** Run: `set -o pipefail; uv run nox && uv run nox -s wheel && docker compose -f docker/docker-compose.yml up --build --exit-code-from showpy`. Expected: all green, `Ledger: 523 open`, and the wheel holds the fonts and licences.
- [ ] **Step 4: Commit** `docs: describe text on the field, stage 1` and `docs: regenerate the changelog`.

## Follow-up plans (out of scope here)

- **Stage 2.** User TTF/OTF/WOFF/WOFF2 through fontTools (a new runtime dependency, pinned in the lock and ranged in `pyproject.toml`). `register_font` dispatches on the magic bytes. Adds the `outline` and `fill` modes, the skeleton `centerline` with automatic fallback to `outline`, the build report, and the research note on skeleton thresholds.
- **Stage 3.** Kerning (`GPOS`/`kern`), text `along` a curve, and the `showpy[shaping]` extra (HarfBuzz and bidi).
- **Follow-ups found while planning.** Thread `font_registry` through every function that rebuilds slots. Add a verified Cyrillic single-line source. Consider one spot for dot-sized strokes (`.`, `i` dots), which today take 2 or 3 spots within centimeters.
