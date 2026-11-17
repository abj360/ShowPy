# Bundled single-line fonts

Audience: whoever changes `scripts/build_fonts.py` or a bundled font. Scope: sources, checksums,
licence terms and what was checked by eye for the five fonts in `showpy/fonts/`.

Last updated: 2026-11-16

## Sources

| Font                | Source commit                                                             | Source file                                      | Source SHA-256                                                     | Output SHA-256                                                     | Glyphs | Output bytes |
| ------------------- | ------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------ | ------------------------------------------------------------------ | ------ | ------------ |
| `relief-singleline` | `isdat-type/Relief-SingleLine` `01dfc5779ec1e9e4b288d96c6c96c23bfccbaf9d` | `fonts/open_svg/ReliefSingleLineSVG-Regular.svg` | `75f05a5b64ed6039c9816628ee051d98e16c19148a8268c63f5eccf8382479e2` | `fd67c5b12e38ab886258f5bc16611b3f61f0bcc2d5711dc5ddf8fd42bc8f4b55` | 423    | 82,162       |
| `hershey-roman`     | `kamalmostafa/hershey-fonts` `1356bf2f83d380fcef68c887e88675eb9d445d86`   | `hershey-fonts/rowmans.jhf`                      | `8718fb129c0f6bce89c84fe41bc467e39534d215a6f7c3220cc5789a8a7d8618` | `e1969f4711a74be259a5e602da900a65b94c5f45f69c33e32ab4eeb86a2c83cb` | 95     | 14,766       |
| `hershey-script`    | same                                                                      | `hershey-fonts/scripts.jhf`                      | `6b391b2ea3a0771cf18caff0ed111db3d05681fa586d423a740cc0b2b155a873` | `1f0a661303e872ffd3ac5c8abdd8804519c2174696c1f8f04ac1aaee37c5a9d0` | 95     | 24,811       |
| `hershey-greek`     | same                                                                      | `hershey-fonts/greeks.jhf`                       | `309298debb0e472047f8329d31f849b14a98108a706d0f4f684e40d1eb023a76` | `43f6945dde1eea1a5b51295a518f7cb8c1a12fd04d2df536ef4ab37cb2533cd1` | 48     | 8,784        |
| `simplex`           | built in (`showpy/geo/text.py`)                                           | none                                             | none                                                               | `7e345d1ae7a55abc2d6414c6b16d121bdaddba1ef1fb5d82dec483e4bac20e3e` | 65     | 6,252        |

Licence texts: `OFL.txt` from the Relief commit (SHA-256
`005834fd32a01fb021a44167cafd4fa3df8058a7b0fb7319bd19b445d2fe24a3`) and `hershey.txt` from the
Hershey commit (SHA-256 `6ffb639e2d2799e987cec4f913473d4119e4270b65c13ceafe1cd502f904d855`).
`simplex` ships the repository MIT text as `licenses/MIT.txt`.

## Licence terms

- Relief SingleLine: SIL OFL 1.1 with no Reserved Font Name, so a derived JSON file may keep the
  family name. The licence text and copyright notice ship with the font.
- Hershey: the distribution notice requires the acknowledgement text to travel with the data.
  `licenses/HERSHEY.txt` is that text; `NOTICE.txt` names it per font.
- Combined expression in `pyproject.toml`: `MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts`.

## Left out

Hershey Cyrillic: the pinned `cyrillic.jhf` carries no Hershey numbers, so its glyph-to-letter
mapping cannot be verified. Greek uses the Hershey numbering (capitals from 527, smalls from 627).

## Eye check (2026-11-16)

Contact sheets from `scripts/build_fonts.py --sheets`, opened one by one; each drawn glyph was
compared with the Unicode name under it.

- `hershey-greek`: all 48 match. Theta (U+0398, Hershey 534) is a circle with a bar. Psi
  (U+03A8) and Omega (U+03A9) end the capital row and are right. Small psi and omega end the
  sheet and are right. Capital upsilon (U+03A5) is drawn with a curled left arm, the form of
  the source glyph; it is legible as upsilon.
- `hershey-roman`, `hershey-script`: ASCII 32 to 126, every glyph matches its label.
- `simplex`: digits, capitals and folded smalls match; no accents (65 glyphs).
- `relief-singleline`: Latin, Latin-1, Latin Extended-A, accents, Greek smalls, arrows and
  symbols all match their labels.

## Sizes

Every font file is under 1,024 KB; the five total 136,775 bytes. Each Hershey `.jhf` source is
3,470 to 5,528 bytes.
