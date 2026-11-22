# ADR 0020: The library is renamed e8Show, with no `showpy` alias

- Status: Accepted (2026-11-22)
- Date: 2026-11-22
- Deciders: the maintainer (abj360)

## Context

The maintainer decided on 2026-11-22 to rename the library from ShowPy to e8Show. The name is
everywhere: in the distribution name, the import name, every module path, the error base class,
four environment variables the test suite reads, the repository URL and the prose of every
document. Measured on `main` at `8b92d66` with `git grep`:

| Spelling                             | Occurrences | Files            |
| ------------------------------------ | ----------- | ---------------- |
| `showpy` in any case                 | 5,923       | 342 of 473       |
| `ShowPyError`                        | 432         | 73               |
| The four `SHOWPY_UPDATE_*` variables | 31          | 14 (6 read them) |

`git grep -i -o -h showpy | wc -l` gives the occurrences, `git grep -i -l showpy | wc -l` the
files, and `git ls-files | wc -l` the 473 tracked files. `git grep -i -c showpy` counts 5,342
lines, since some lines hold the name more than once. The variables are counted with
`git grep -o -h -E "SHOWPY_UPDATE_(API|GOLDEN|MIGRATION|SCHEMA)"`; six test modules read them.

Three strings carry the name inside stored data, not only in code:

| String           | Where                                                        | What it is part of                                  |
| ---------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| `showpy-hash/1`  | `HASH_PREFIX`, `showpy/core/util.py:48`                      | every content hash, and so every revision ID        |
| `$showpy:map`    | `MAP_TAG`, `showpy/core/canonical.py:26` (prefix at line 25) | the canonical bytes of every map with non-text keys |
| `showpy.font.v1` | `"format"` key inside each of the 5 bundled font files       | the bytes, and so the URI, of every font            |

Bundled fonts are identified by the SHA-256 of their bytes (ADR 0016): `relief-singleline`,
`hershey-roman`, `hershey-script`, `hershey-greek` and `simplex` are stored in text forms as
`asset:sha256:...` URIs. Changing one byte of a font file changes its URI, and every stored text
form that names the old URI then fails closed with `MissingFontError`.

ADR 0009 says a renamed or moved public name keeps working and warns for two minor releases. A
rename of the distribution and import name cannot follow that rule cheaply: every module path
would need a second package that re-exports the first.

## Decision

1. **The names change.**

   | Before                           | After                                             |
   | -------------------------------- | ------------------------------------------------- |
   | distribution and import `showpy` | `e8show`                                          |
   | `ShowPy` in prose                | `e8Show`                                          |
   | `ShowPyError`                    | `E8ShowError`                                     |
   | `SHOWPY_UPDATE_API`              | `E8SHOW_UPDATE_API`                               |
   | `SHOWPY_UPDATE_GOLDEN`           | `E8SHOW_UPDATE_GOLDEN`                            |
   | `SHOWPY_UPDATE_MIGRATION`        | `E8SHOW_UPDATE_MIGRATION`                         |
   | `SHOWPY_UPDATE_SCHEMA`           | `E8SHOW_UPDATE_SCHEMA`                            |
   | GitHub `abj360/ShowPy`           | `abj360/e8Show`; GitHub redirects the old address |

   Names derived from the old one follow the same rule (`only_showpy_errors` becomes
   `only_e8show_errors`; test names ending `raises_showpy_error` end `raises_e8show_error`).

2. **When.** The rename is the last step before the v0.4.0 tag, when no lane branch or worktree
   is open. One script, kept outside the repository, moves `showpy/` to `e8show/` and rewrites
   the spellings above. Every generated file is then regenerated (`e8show/__init__.py`, the API
   snapshot, the schemas, the changelog, the migration fixtures' expected output, the error-code
   list) and every gate runs, the Docker run included.
3. **No `showpy` alias or shim.** This is a written exemption from ADR 0009's two-release warning
   rule, for the distribution and import name only, and for the names that carry it: the error
   base class and the four environment variables. Every other renamed or moved public name
   still follows ADR 0009. Reasons:
   - v0.4.0 is already the breaking release: keyword-only flags (ADR 0009), format v2 (ADR 0007)
     and the narrowed scope (ADR 0021) all land in it. A consumer upgrades once.
   - A scripted rewrite of 5,923 occurrences in 342 files is safe only when nothing else is in
     flight; any branch open at the time would conflict with it.
   - A consumer changes one import line (`import showpy` to `import e8show`) and one exception
     name; type checkers and the first test run find both.
4. **Frozen legacy wire names, never renamed**, in this release or any later one:
   - the hash domain `showpy-hash/1`: renaming it changes every content hash and every revision
     ID;
   - the map tag `$showpy:map` and its reserved key prefix `$showpy:`: renaming them changes the
     hash of every value that holds a map with a non-text key;
   - `"format": "showpy.font.v1"` inside every bundled font file. Renaming it changes the bytes,
     and so the identity, of every bundled font, which breaks every stored text form.

5. **Also unchanged by the script:**
   - the frozen v1 schema `showpy/schemas/showpy.show.v1.json` (moved with its folder, name
     kept) and its tag `"schema": "showpy.show.v1"`, which the reader keeps accepting;
   - the stored trees under `tests/fixtures/repos/*/repo/`, written by released versions, and
     the fixtures' `README.md` files, which name the release that wrote each one. The
     `expected_v2.json` files beside them are generated output: each holds
     `"format": "showpy.show"` once, and the rename regenerates them with `e8.show`;
   - the font files themselves;
   - `CHANGELOG.md` entries and release notes up to and including v0.3.0;
   - the text of ADRs 0001 to 0019. History is not rewritten.
6. **How to read older ADRs.** In ADRs 0001 to 0019, in older release notes and in older changelog
   entries, `showpy` and ShowPy name the same package now called `e8show` and e8Show. Paths such
   as `showpy/show/storage.py` there mean `e8show/show/storage.py`.

## Alternatives

- **Rename in its own release after v0.4.0.** Rejected: a second breaking upgrade for every
  consumer, one release after the first.
- **Rename in v0.4.0 and ship a `showpy` shim package that warns until v0.6.0.** Rejected: it
  keeps two import names alive for two releases, and every module path would need a forwarding
  module. If a consumer ever needs it, it stays a small package (about 20 lines) that re-exports
  `e8show` and warns, which can be added in a patch release.
- **Rename the three wire names too.** Rejected: it changes every content hash, revision ID and
  bundled font URI, so every stored show and text form written before would need a migration for
  no gain in behaviour.

## Consequences

- From v0.4.0, `import showpy` raises `ModuleNotFoundError`. Consumers pinned to v0.3.0 keep
  working; they move by changing the pin, the import and `ShowPyError`.
- The first paragraph of the v0.4.0 release notes says so, with the table in decision 1.
- The old name stays visible in three stored strings, one schema file name and its tag, the
  committed fixtures, and the history (old ADRs, changelog entries, release notes).
- A test asserts that the five bundled font URIs equal their v0.3.0 values, so a stray rewrite of
  a font file fails the suite.
- The old repository address keeps redirecting only while no repository takes the old name.

## Numbers

| Measure                                                               | Value                                                |
| --------------------------------------------------------------------- | ---------------------------------------------------- |
| Occurrences of `showpy` in any case, `8b92d66`                        | 5,923 in 342 of 473 tracked files (5,342 lines)      |
| `ShowPyError` occurrences                                             | 432 in 73 files                                      |
| Environment variables renamed                                         | 4                                                    |
| Frozen wire strings                                                   | 3 (`showpy-hash/1`, `$showpy:map`, `showpy.font.v1`) |
| Bundled font URIs that must not change                                | 5                                                    |
| `git diff --stat v0.3.0 -- showpy/fonts` at `8b92d66`                 | empty                                                |
| Fixture occurrences left (READMEs) and rewritten (`expected_v2.json`) | 3 and 3 (measured at `8b92d66`)                      |
| Occurrences left by design in fonts and the v1 schema                 | 7 and 1 (measured at `8b92d66`)                      |

## What would change our mind

- A consumer that cannot change its import in one step (a long-lived deployment pinned by
  package name, not by tag): then the shim of the second alternative ships in a v0.4.x patch.
- A later stored format that already rewrites every hash for another reason: then the two
  hashing strings could be renamed in that same step at no extra cost.

## References

- ADR 0007 (stored format v2), ADR 0009 (deprecation policy), ADR 0014 (canonical hashing), ADR
  0016 (fonts as content-hashed assets), ADR 0021 (scope), ADR 0023 (format versions).
- Code: `showpy/core/util.py` (`HASH_PREFIX`), `showpy/core/canonical.py` (`TAG_PREFIX`,
  `MAP_TAG`), `showpy/geo/models.py` (`FONT_FORMAT`), `showpy/fonts/`, `showpy/export/data.py`
  (`SCHEMA_TAG`), `pyproject.toml` (`[project]`, `[project.urls]`).
- Readers of the environment variables: `tests/unit/test_api_snapshot.py`,
  `tests/unit/show/test_schema.py`, `tests/unit/show/test_migrate.py`,
  `tests/golden/test_golden_large.py`, `tests/golden/test_golden_opener.py`,
  `tests/golden/test_golden_text.py`.
