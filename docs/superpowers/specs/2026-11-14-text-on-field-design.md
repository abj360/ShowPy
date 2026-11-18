# Text on the field: design

Audience: the builder who plans and implements this feature. Scope: spelling arbitrary text
with spots, in any font, anywhere on the sheet.

Last updated: 2026-11-14

## Intent

A designer or an agent types any text (upper and lowercase, digits, punctuation, accented and
non-Latin letters, several lines), picks any font, places it anywhere on the field at any size
and angle, and decides how many spots spell it. The result stays editable as text: change
"GO" to "GO TEAM" and the letters regenerate.

Today `showpy.geo.shapes.text_outline` spells only the 40 hard-coded glyphs of the built-in
`simplex` font (A–Z, 0–9, space, `-`, `.`) as polylines; turning them into spots is manual,
stroke by stroke, and there is no text form. The maintainer's words: "I don't want to be
restricted to just a group of hardcoded spots … upper or lowercase, of any font … decide how many
spots … just full stop and - won't cut it."

## Goals

1. Any Unicode text the chosen fonts cover, with several lines.
2. Any font: bundled single-line fonts and user-supplied TTF, OTF and WOFF files.
3. Placement anywhere: position, size (cap height in meters), rotation, alignment, letter and
   line spacing, kerning, and text along a curve.
4. Exact spot count chosen by the caller, spread across strokes so every letter stays legible.
5. A `text` form kind whose recipe regenerates the letters; editing the text, font, size or
   count regenerates slots, and matching reassigns spots.
6. Robust by design: every stage has a fallback, and every failure teaches (rule 7).
7. Deterministic: the same recipe gives byte-identical slots on any machine (rule 3).

## Non-goals

- Rendering text as labels or annotations on printouts (that is the renderer's job).
- Field wording such as "2 outside the 35" (pack language, deferred).
- Hand-editing individual glyph shapes inside the library (users supply a font instead).

## Anti-goals (a failure even if it technically works)

- A missing character silently replaced by `?`, a box or nothing.
- A show that rebuilds differently on another machine because a font was found by name in the
  operating system.
- Letters that read as blobs or hollow outlines by default.
- Text that can only be built once and then edited dot by dot.

## Approaches considered

**A. Two glyph sources with fallbacks (chosen by the maintainer, 2026-11-14).** Bundled
single-line (stroke) fonts give exact centerlines with no geometry processing; user-supplied
outline fonts go through a skeleton step. A per-character fallback chain covers missing
glyphs, and the spot mode falls back from centerline to outline when a skeleton fails.

**B. Outline fonts only, skeleton for everything.** Rejected: every glyph pays for a
skeleton step, results depend on skeleton quality even for plain drill text, and the bundled
single-line fonts already cover most needs exactly.

**C. Single-line fonts only.** Rejected: the maintainer wants "any font".

## Design

### 1. Glyph sources

- **Bundled single-line fonts** (data files inside the package, loaded with
  `importlib.resources`, frozen registry): Hershey (public-domain stroke fonts with lowercase,
  punctuation, Greek and Cyrillic; keep its attribution notice) and Relief SingleLine (OFL,
  broad Unicode). The existing `simplex` stays as an alias for the matching Hershey font so
  current callers keep working. Strokes are polylines (or Bézier paths) on a unit em.
- **User fonts**: TTF, OTF and WOFF/WOFF2 bytes parsed with fontTools (new runtime dependency,
  MIT, pinned in the lock, ranged in `pyproject.toml`). Outlines come from the `glyf` or `CFF`
  table as quadratic or cubic Bézier contours, flattened by the existing curve machinery.
- **Font identity**: every font is referenced by its content hash `asset:sha256:<hex>`, stored
  with the show through the existing asset store (`showpy.show.repositories`, `ASSET_PREFIX`).
  Bundled fonts get fixed hashes too. Never look a font up by name in the operating system.
- **Fallback chain**: the recipe holds an ordered list of font hashes. Each character comes from
  the first font that has it. If none has it, raise a `ShowPyError` subclass (for example
  `GlyphError`, code `MISSING_GLYPH`) naming the character, its code point and the fonts tried,
  with a hint to add a font that covers it.

### 2. Shaping and layout

- Map text to glyphs with the font's `cmap`; apply kerning (`GPOS` pair kerning or `kern`).
  Complex scripts (Arabic, Hebrew, Indic) shape through HarfBuzz (`uharfbuzz`) as an optional
  extra `showpy[shaping]`; without it, such text raises a teaching error naming the extra.
  Right-to-left runs apply the Unicode bidi algorithm under the same extra.
- Layout options: `at` (anchor point), `height` (cap height, meters), `rotation` (degrees,
  library convention), `align` (`left`, `center`, `right`), `anchor` (`baseline`, `middle`,
  `top`), `letter_spacing` and `line_spacing` (em fractions), lines split on `\n`, and
  optional `along` (a curve the baseline follows, for text on an arc).
- Output of layout: an ordered list of glyph instances, each with its geometry in meters.

### 3. Turning glyphs into spot paths (modes)

- `centerline` (default): single-line fonts give their strokes directly. Outline fonts are
  skeletonized: rasterize the glyph at a resolution derived from the cap height and the target
  spot spacing, thin it (Zhang–Suen or medial axis via scikit-image if the builder adds it, else
  an in-house thinning on numpy), vectorize the skeleton into polylines, prune spurs shorter than
  a fraction of the stroke width, and simplify. Deterministic: fixed resolution rule, no
  randomness.
- `outline`: glyph contours as closed curves.
- `fill`: the glyph region (with holes) filled with the existing `fill` spacing.
- Automatic fallback: if a centerline skeleton is degenerate (empty, or covering less than a set
  share of the glyph's height), that glyph falls back to `outline`. The form's build report lists
  every fallback; nothing falls back silently.

### 4. Spot count

- The caller gives `n` (exact total) or `spacing` (meters between spots along strokes).
- `n` is apportioned across all strokes of all glyphs by length with largest-remainder rounding
  (the library's existing apportionment), so the total is exact and deterministic.
- Every stroke gets at least a minimum (2 for an open stroke, 3 for a closed one) when `n`
  allows; when `n` is too small to give every stroke its minimum, raise a teaching error with
  the minimum `n` for this text. A warning (not an error) reports when the resulting spacing is
  below a legibility threshold relative to the cap height.
- Closed loops (O, 0, D, …) never place two spots on the same point (fixes the class of defect
  in ledger UP-9.1-28).

### 5. The `text` form kind

- New `FormKind` member `"text"` with a typed recipe: `text`, `fonts` (ordered hashes), `mode`,
  `height`, `at`, `rotation`, `align`, `anchor`, `letter_spacing`, `line_spacing`, optional
  `along`, and `n` or `spacing`. JSON-serializable; the schema is regenerated and its version
  handled per the project rules.
- Constructor `text_form(form_id, members, text, *, fonts, height, at, ...)` in
  `showpy.choreo.forms`, with a runnable doctest using a bundled font.
- `edit(form, text=...)` (and any other recipe field) regenerates slots; existing members are
  reassigned to the new slots with the existing matching (`match`), and the slot count follows
  `n`.
- Facing follows the existing `FacingSpec` modes (for example `along` the stroke).
- `text_outline` stays as a lower-level public function, extended to the new font sources, so
  callers who only want curves keep a simple path.

### 6. Errors (rule 7)

Every failure is a `ShowPyError` subclass with `code`, `message`, `context` and `hint`:
missing glyph, unreadable or unsupported font file, missing font asset (names the hash, never
substitutes), `n` too small, complex script without the shaping extra, degenerate geometry.

## Decisions made after the builder check (2026-11-14, Claude's calls unless marked)

- **Fonts reach slot generation through a `FontRegistry`** (frozen, like `FormFnRegistry`),
  passed next to the form-function registry to `slots`, `placements`, `layout` and `edit`, with
  a default that holds the bundled fonts. User fonts are added with
  `register_font(registry, data: bytes) -> (FontRegistry, asset_uri)`. A recipe naming a hash the
  registry lacks raises `MISSING_FONT` naming the hash. Existing form kinds ignore the registry,
  so their behavior is unchanged. (Claude's call: mirrors the existing registry pattern and keeps
  slot generation pure.)
- **Font bytes travel with the show.** Repositories store fonts in the asset store; `export_json`
  embeds every font a show's text forms use (base64, keyed by hash) and `import_json` registers
  them, so a show moved between repositories keeps its fonts. `Show` gains no new field. (Claude's
  call.)
- **Slot order** is reading order: lines top to bottom, glyphs in logical order, strokes in font
  order, each stroke from its start. (Claude's call.)
- **No scikit-image.** Thinning uses numpy and `scipy.ndimage`, both already dependencies.
  fontTools (pure Python) becomes a runtime dependency; `uharfbuzz` is the optional
  `showpy[shaping]` extra. (Claude's call: keeps the dependency set light.)
- **`simplex` keeps its exact current output** (uppercase only, lowercase mapped to uppercase, same
  4 × 6 grid and advance), so existing callers see no change. Lowercase comes from the new fonts.
  `height` means cap height; for a font without a cap height, the height of `H`, else the em
  ascender. (Claude's call.)
- **Licences** are checked before bundling: only Hershey data distributed with a redistribution
  notice and Relief SingleLine under OFL, each with its licence text in the package; the builder
  records the source and licence in a short note. (Claude's call.)
- **Text along a curve** starts where `align` puts it on the curve; text longer than the curve
  raises a teaching error. (Claude's call; build stage 3.)
- **Changing the text** (`edit(form, text=...)`, maintainer's decision 2026-11-14): the spot
  count stays the same and the same members spread over the new letters, in reading order.
  Changing the count is a separate, explicit edit, `edit_text_count(form, *, add, remove, n, spacing)`, that names the spots to add or remove: the resulting member count must equal the new slot count, adding a member or removing a non-member is an error, and `edit(form, n=...)` or `edit(form, spacing=...)` on a text form raises a teaching error that points to it. If the
  kept count cannot give every stroke its minimum, the edit raises the `n`-too-small error with
  the minimum count for the new text.

## Constraints

- Project rules in `CLAUDE.md`: frozen Pydantic models, pure functions, determinism, meters and
  degrees, `ShowPyError` subclasses, `PathEngine` untouched (this feature only creates forms).
- Public API changes go through `showpy/_api_table.py`, the API snapshot and the CHANGELOG.
- New dependencies are pinned in the lock and ranged in `pyproject.toml`; heavy or native ones
  (HarfBuzz) are optional extras.
- Bundled font files must carry their licence texts in the package.
- Timing: this is its own branch (`feat/text-on-field`), started after Phase 0 merges. It touches
  `showpy/geo` and `showpy/choreo` forms, which Phase 1 lanes also edit; the plan must sequence
  around Phase 1's lane C (geo) and lane G (forms) to avoid conflicts.

## Build order

1. Bundled single-line fonts, font registry and asset hashing, fallback chain, layout without
   shaping, `centerline` mode for stroke fonts, exact `n`, the `text` form kind and `edit`.
2. User outline fonts through fontTools: `outline` and `fill` modes, then `centerline` via the
   skeleton step with the automatic fallback to `outline`.
3. Kerning, text along a curve, and the optional HarfBuzz shaping and bidi extra.

## Left to the builder

- Exact Hershey and Relief SingleLine source files and their packaging format.
- The skeleton algorithm details and thresholds (resolution rule, spur pruning, degeneracy
  share), chosen and justified in a short research note before coding.
- The legibility threshold value, with its reasoning.
- Whether `along` reuses the existing curve offset machinery or a new baseline mapping.
