# ADR 0019: License ShowPy under PolyForm Noncommercial 1.0.0

- Status: Accepted (2026-11-22)
- Date: 2026-11-22
- Deciders: the maintainer (abj360)

## Context

ShowPy has been public under the MIT License since 2026-11-15, and every release so far (v0.1.0,
v0.1.1, v0.2.0, v0.3.0) shipped under MIT. MIT lets anyone use the library in a commercial
product with no obligation beyond keeping the notice. On 2026-11-22 the maintainer decided that
the repository stays public but that commercial use needs a separate licence.

Three facts bound the decision:

- **Who can relicense.** Every commit in the history up to `8b92d66` is authored by the
  maintainer, except seven Dependabot commits that change version pins only (see Numbers). One
  person can therefore change the licence of the code that follows.
- **What cannot change.** A licence already granted is not withdrawn. Anyone who received v0.3.0
  or any earlier commit under MIT keeps every MIT right to that code.
- **What ships besides code.** The wheel carries third-party fonts under their own licences
  (SIL OFL 1.1, the Hershey font notice) and the `simplex` font and JSON schemas under MIT.
  Consumers read show files through those schemas.

## Decision

1. **PolyForm Noncommercial 1.0.0** governs ShowPy from the commit
   `chore(licence)!: relicense under PolyForm Noncommercial 1.0.0` on. `LICENSE` holds the
   official text unchanged, under the licence's own `Required Notice:` line:
   `Required Notice: Copyright 2026 abj360 (https://github.com/abj360/ShowPy)`.
2. **v0.1.0 to v0.3.0, and every commit before the licence change, remain MIT, forever.** This
   ADR does not and cannot revoke that grant.
3. **The JSON schemas stay MIT.** `showpy/schemas/LICENSE-MIT.txt` holds the MIT text and
   copyright line from the former `LICENSE`, and the generated `showpy.show.v2.json` states it in
   a top-level `$comment`. `showpy.show.v1.json` stays frozen byte for byte, covered by the
   notice file. Any tool may read and validate show files.
4. **The fonts keep their own licences:** OFL-1.1 for `relief-singleline`, the Hershey notice
   (`LicenseRef-Hershey-Fonts`) for the `hershey-*` fonts and MIT for `simplex`. Every file
   under `showpy/fonts/` is unchanged. The non-commercial terms do not cover the font files.
5. **Package metadata** states the licence expression
   `PolyForm-Noncommercial-1.0.0 AND MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts`, lists every
   licence text the expression names as a `License-File`, and carries no licence classifier
   (PEP 639 deprecates licence classifiers beside an expression). The `wheel` gate asserts the
   exact `License-Expression`.
6. **Outside contributions need a relicensing grant.** `CONTRIBUTING.md` accepts an outside
   contribution only with a written grant that lets the maintainer relicense it, including under
   commercial terms. Without it, the first merged outside patch ends sole ownership.
7. **Commercial licences on request,** through the maintainer's GitHub profile
   (`https://github.com/abj360`).

## Alternatives

| Licence                      | Written for software | What it allows                                                | Patent grant                     | Promise about the future                         | SPDX identifier                |
| ---------------------------- | -------------------- | ------------------------------------------------------------- | -------------------------------- | ------------------------------------------------ | ------------------------------ |
| PolyForm Noncommercial 1.0.0 | yes                  | any non-commercial purpose, including non-profit institutions | yes, ends on a patent claim      | none                                             | `PolyForm-Noncommercial-1.0.0` |
| Business Source License 1.1  | yes                  | everything except production use beyond an extra use grant    | none stated                      | each release turns open source within four years | `BUSL-1.1`                     |
| CC BY-NC 4.0                 | no                   | non-commercial sharing and adaptation, with attribution       | none; patent rights are excluded | none                                             | `CC-BY-NC-4.0`                 |
| MIT (status quo)             | yes                  | any use, keeping the notice                                   | none stated                      | none                                             | `MIT`                          |

- **Business Source License 1.1.** Rejected: it limits production use, not commercial use, and
  commits every release to turning open source within four years, which nobody asked for.
- **CC BY-NC 4.0.** Rejected: it is not written for software, Creative Commons advises against
  using its licences for code, and it grants no patent licence.
- **Stay MIT.** Rejected: the maintainer decided on non-commercial terms.

## Consequences

- Consumers pinned to v0.3.0 or earlier keep MIT. Upgrading past the licence change means
  accepting the PolyForm Noncommercial terms, or obtaining a commercial licence.
- Anyone may keep a public fork of any MIT commit, under MIT.
- Commercial users of later versions need a licence from the maintainer.
- PolyForm Noncommercial is not OSI-approved, so ShowPy is no longer open source in the OSI
  sense; the classifier `License :: OSI Approved :: MIT License` is gone.
- Distribution stays a wheel plus sdist on GitHub releases.
- Tools that only read or validate show files depend on the MIT schemas, not on the library.
- `scripts/build_fonts.py` copies the `simplex` font's `licenses/MIT.txt` from
  `showpy/schemas/LICENSE-MIT.txt` (identical bytes to the former `LICENSE`), so a font rebuild
  keeps that file MIT.

## Numbers

| Measure                                                    | Value                                                                           |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Commits reachable from `8b92d66` (`git shortlog -sne`)     | 711                                                                             |
| Commits by the maintainer, under three author identities   | 704 (667, 34 GitHub web merges, 3)                                              |
| Commits by Dependabot (version pin bumps only)             | 7                                                                               |
| Commits with a `Co-Authored-By` trailer                    | 0                                                                               |
| SPDX check, list data release 2026-11-18                   | `PolyForm-Noncommercial-1.0.0` listed, not deprecated, not OSI-approved         |
| Hatchling 1.33.0                                           | accepts the SPDX identifier; no `LicenseRef-` fallback needed                   |
| Wheel `License-Expression`                                 | `PolyForm-Noncommercial-1.0.0 AND MIT AND OFL-1.1 AND LicenseRef-Hershey-Fonts` |
| Wheel `License-File` entries                               | 5 (`LICENSE`, the schemas' MIT notice, the three font licences)                 |
| Licence text SHA-256 (fetched file, the body of `LICENSE`) | `c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5`              |
| Word diff against the official page                        | 681 words, 0 differences                                                        |

## What would change our mind

- An outside contribution that is wanted but comes without a relicensing grant: then the
  maintainer either declines it or the licence model is reopened.
- A need for an OSI-approved licence, for example a package index or a distribution that
  requires one: then a dual licence or a different licence is reconsidered.

## References

- PolyForm Noncommercial License 1.0.0:
  <https://polyformproject.org/licenses/noncommercial/1.0.0>; source text
  `PolyForm-Noncommercial-1.0.0.md` at tag `1.0.0` of
  <https://github.com/polyformproject/polyform-licenses>.
- PEP 639, licence expressions in core metadata: <https://peps.python.org/pep-0639/>.
- SPDX entry: <https://spdx.org/licenses/PolyForm-Noncommercial-1.0.0.html>.
- `LICENSE`, `showpy/schemas/LICENSE-MIT.txt`, `showpy/fonts/NOTICE.txt`, `CONTRIBUTING.md`.
