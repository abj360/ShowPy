# ADR 0016: Fonts are one JSON stroke format, identified by the SHA-256 of their bytes

- Status: Accepted (2026-11-14)
- Date: 2026-11-14
- Deciders: the maintainer (approved the design and plan on 2026-11-14), Claude (drafter)

## Context

Text on the field (spec `docs/superpowers/specs/2026-11-14-text-on-field-design.md`) places spots
along letter strokes. Strokes come from single-line fonts, not outline fonts: an outline has a
filled area and no centerline, so spots cannot be spaced along it.

Three facts drive the format:

- The sources differ. Relief SingleLine ships as an SVG font (path data with curves). The
  Hershey fonts ship as `.jhf` files (integer polylines, pen-up marks, a bounds pair per glyph).
  The built-in `simplex` font is a Python table (`showpy/geo/text.py`).
- A show must rebuild the same dots on every machine. A recipe that says "font Relief" and
  resolves it through the operating system, or through whatever file a package happens to hold,
  can change meaning without any edit to the show.
- Fonts are untrusted input once a caller registers one. A parser reading XML or a bespoke
  binary format on caller bytes is attack surface.

## Decision

1. **One stroke format for every font.** `showpy.font.v1` is a JSON document:
   `{"format", "name", "family", "license", "source", "units_per_em", "cap_height", "ascender",
"descender", "glyphs": {"<one character>": {"advance", "strokes": ["<absolute SVG subpath>"]}}}`.
   Coordinates are font units, y up, baseline at 0. Each stroke is one absolute subpath written
   by `showpy.geo.svg.Subpath.to_d()` after rounding to 6 decimals; a closed stroke ends in `Z`
   and does not repeat its start point. The runtime parser is `parse_path`, which already exists.
2. **Identity is the SHA-256 of the exact file bytes**, written as the asset URI
   `asset:sha256:<64 hex>`: the string `InMemoryAssetStore.put(data)` returns for the same bytes.
   Recipes store that URI, never a name. A name is a label for people and registries.
3. **Not `content_hash`.** ADR 0014's `content_hash` hashes the canonical value of a Python
   object under a domain tag. A font file is an opaque blob that travels as bytes, so its
   identity must be computable by any tool that can hash a file (`sha256sum`), must equal the
   asset-store key with no tag or re-encoding, and must change if a single byte changes. Raw
   SHA-256 gives all three; a canonical-JSON hash would equate files that differ in whitespace
   and would need the JSON parsed to verify.
4. **Built once, committed.** `scripts/build_fonts.py` downloads pinned URLs (commit-hash URLs),
   verifies each source SHA-256 before parsing, converts to `showpy.font.v1` with deterministic
   bytes (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)` plus a newline)
   and writes `showpy/fonts/`. CI never runs it; the library never downloads anything.
5. **Fonts never come from the OS.** The library loads bundled fonts through
   `importlib.resources` and caller-supplied bytes only.
6. **Licences ship inside the package.** `showpy/fonts/licenses/` holds the OFL 1.1 text and the
   Hershey notice, and `NOTICE.txt` lists each font with its source URL, source SHA-256 and
   output SHA-256. Only the Hershey `.jhf` data and `hershey.txt` are taken; the repository's C
   code is GPL-2 and stays out.

## Alternatives

- **Ship the original `.jhf` and `.svg` files and parse them at runtime.** Rejected: two parsers
  in the library, and an XML parser on untrusted input. The build script parses XML once, on
  checksum-verified bytes.
- **Look fonts up by name.** Rejected: anti-goal. Shows rebuild differently per machine, and a
  renamed or upgraded font changes dots with no edit to the show.
- **Use `content_hash` (ADR 0014) as font identity.** Rejected: see Decision 3.
- **Outline fonts (TTF/OTF) as the only source.** Rejected for stage 1: outlines have no
  centerline. Reading TTF/OTF files from callers is stage 2.
- **Bundle one font.** Rejected: one single-line face cannot cover Latin print, script and Greek.

## Consequences

- Adding or upgrading a font changes its bytes, hence its URI; old shows keep naming the old
  bytes. Removing a bundled font breaks shows that name it, so removals need a release note.
- Every font is parsed by the same code, so one fuzz target covers all of them.
- Bundled data is committed output of a script that needs the network. Reproducing it needs the
  pinned URLs to stay up; the source checksums make a changed upstream fail loudly.
- The library carries font data (a few hundred KB) in the wheel.

## Numbers

| Measure                                         | Value                         |
| ----------------------------------------------- | ----------------------------- |
| Relief SingleLine SVG source                    | 176,600 bytes                 |
| Each Hershey `.jhf` source                      | about 3.5 KB                  |
| Parsers in the library for font data            | 1 (`parse_path`), 0 XML       |
| Pinned sources (data and licence texts)         | 6, each verified by SHA-256   |
| Bytes of the built JSON that depend on the host | 0 (byte-deterministic output) |
| Identity length                                 | 64 hex, equals the asset URI  |

## What would change our mind

- A caller needs to register fonts by a name that survives byte changes: then a name to URI
  table lives in the show, and identity stays the hash.
- The bundled JSON exceeds 1 MB in the wheel: then glyph tables load lazily per font, with the
  same bytes.
- Stage 2 outline import needs hinting or variable axes that strokes cannot express: then a
  second format gets its own ADR; it does not widen `showpy.font.v1`.

## References

- Spec `docs/superpowers/specs/2026-11-14-text-on-field-design.md`.
- ADR 0014 (canonical hashing), `showpy/show/repositories.py` (`ASSET_PREFIX`,
  `InMemoryAssetStore`), `showpy/geo/svg.py`, `showpy/geo/text.py`.
- Relief SingleLine (SIL OFL 1.1), Hershey fonts via `kamalmostafa/hershey-fonts`.
