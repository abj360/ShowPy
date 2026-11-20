# ADR 0007: Stored format v2, read through a migration chain, with v1 read until 2.0

- Status: Accepted (2026-11-19)
- Date: 2026-11-18
- Deciders: the maintainer (abj360)

## Context

Every show stored by v0.1.0 to v0.3.0 is in one format, here called **v1**: the documents of a
repository tree (`/show/show.json`, `/show/sheet.json`, `/show/notes.json`,
`/time/timeline.json`, `/sets/<key>/set.json`, `forms.json`, `paths.json` and the rest) and the
single-file interchange document of `export_json`. Phase 2 changes the shape of stored models
(typed recipes, typed motion parameters, typed route curves, ID rules), so the library must tell
the two formats apart and turn v1 into v2. Today it cannot:

| Fact (measured 2026-11-18 on `main` at `3824263`)                    | Value                                                                                           |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Version marker in repository documents                               | none (UP-9.7-29 open)                                                                           |
| Version marker in `export_json`                                      | `{"schema": "showpy.show.v1", "show": {...}}`                                                   |
| A document from a newer library                                      | `SchemaError` from `extra="forbid"`, never `VersionError` (UP-9.7-92)                           |
| `json.loads` on `{"a": NaN, "a": Infinity}`                          | `{'a': inf}`: non-standard constants and duplicate keys both accepted                           |
| Stored fields typed `dict[str, Any]`                                 | 4: `Form.geometry`, `PathRow.motion_params`, `Route.curve`, `CanvasEvent.payload` (host-stored) |
| IDs in the v0.2.0 repository fixture that fail `check_id` (ADR 0006) | 16 of 50 checked: 15 form IDs such as `brass-2`, 1 selection `front-four`                       |
| Committed repository fixtures                                        | v0.2.0 only; none from v0.1.1 or v0.3.0                                                         |

Phase 1 left three stored-data problems for this format (release notes v0.3.0, "Data model"):

1. **Legacy set IDs.** A v1 set ID that fails ADR 0006 (`con`, a trailing `.`, over 64
   characters) or a v0.2 pair that differs only by case (`1a`, `1A`) loads, edits and saves, but
   `renumber` refuses the show. The reason is **key provenance**: `Set.key` is `None` in the
   document and defaults to the folder name, and the model treats "key equals ID" as "legacy key,
   not checked" (`showpy/choreo/models.py:148`, `showpy/show/models.py:121`). A renumber changes
   IDs, which turns those keys explicit, and two explicit keys equal ignoring case are refused.
   Provenance is inferred from a coincidence, not stored.
2. **`CountsPerMinute` positivity** (ruling R7) and the **`CoordinateSystem.precision`**
   constraint (ruling L-F1) were parked because Phase 1 could only add optional fields.
3. **Gapped slot orders** compact on `place`, so a later `rebuild` may shift spots (ruling L-G2,
   UP-9.6-62); the fix needs typed open slots in the stored form.

Audit decision 6 asks for format v2 and how long v1 is read.

## Decision

1. **One header, in `show.json`.** v2 writes `"format": "showpy.show"` and `"version": 2` as the
   first two keys of `/show/show.json` and of the `export_json` document, which becomes
   `{"format": "showpy.show", "version": 2, "show": {...}, "fonts": {...}}`. Other documents in a
   tree carry no header: a tree is committed atomically, so its `show.json` versions every
   document beside it. A tree whose `show.json` has no header is v1; an `export_json` document
   tagged `"schema": "showpy.show.v1"` is v1.
2. **Decoding is a chain, applied in `decode_show` and `import_json`.**
   1. Parse every JSON document strictly (decision 3).
   2. Read the version. A version above `CURRENT_VERSION` raises `VersionError` (code
      `VERSION`) naming the document's version and the newest this release reads. A missing or
      unknown `format` raises `SchemaError`.
   3. Apply `MIGRATIONS[v]` for each `v` from the stored version up to `CURRENT_VERSION`. Each
      step maps a document set to the next version's document set; there is exactly one step
      per version, `migrate_v1_to_v2`, composed of named sub-steps in a fixed order.
   4. Validate into models with ID checks deferred (decision 5), run the ID step, then validate
      again with every check on.
   5. Return the `Show`. `migrate_documents(docs) -> tuple[dict[str, bytes], MigrationReport]`
      is public, so a host can show the designer what changed before saving.
3. **Strict JSON from v2 on.** The decoder refuses `NaN`, `Infinity` and `-Infinity` tokens,
   duplicate object keys, a byte-order mark and lone surrogates, each with `SchemaError` naming
   the document and the position. v1 documents get the same parse: Phase 1 already refuses
   non-finite floats in every model, so a v1 document holding them never decoded since v0.3.0.
   Text values (titles, labels, note text) are stored NFC-normalized; IDs are ASCII by ADR 0006.
4. **What `migrate_v1_to_v2` changes**, one sub-step each, in this order. Each sub-step is owned by
   the Phase 2 lane that changes the model, and each records what it did in the report.
   1. `forms`: `Form.geometry` (an untyped JSON object) becomes `Form.recipe`, a discriminated
      union on `kind` with `recipe_version: 1`, one member per form kind, the text recipe
      included; slot orders become typed open slots.
   2. `paths`: `PathRow.motion_params` becomes the typed parameters of its built-in motion, or a
      read-only JSON object for a registered custom motion; `Route.curve` becomes a typed curve.
   3. `selectors`: stored selector text (zone `applies_to`, facing-override `entities`) is rewritten
      to the v2 grammar of ADR 0008: glued operators get spaces, and mixed operators get the
      parentheses that keep their v1 left-to-right meaning (`A + B & C` is stored as
      `(A + B) & C`).
   4. `timeline`: tempo sections are sorted by count (stable); a show whose tempo map cannot be
      sorted without changing a count's meaning (two sections at one count) is refused.
   5. `facings`: stored placement and fixed facings are wrapped into `[0, 360)`.
   6. `ids`: IDs that fail ADR 0006 are renamed, and case pairs are split (decision 5).
5. **IDs are renamed once, by rule, with a report.** Validation in step 2.4 runs with a
   `legacy_ids` validation context that accepts any non-empty v1 ID. The `ids` sub-step then
   renames, through the same rewrite that `rename_spots` uses, so every reference follows:
   - Each `-` becomes `_`; leading and trailing `.` are stripped; the ID is cut to 60
     characters; an empty result becomes `id`; a reserved word (`all`, a device name) gets a
     trailing `_`.
   - A collision, including one ignoring case with an earlier ID in show order, gets the
     smallest free suffix `.2`, `.3`, ... The first ID in show order keeps its spelling.
   - This applies to spot, set, form, route, zone, selection-name and note IDs. Show IDs are not
     renamed: they name repository folders, and ADR 0006 moves them in Phase 3.
   - Every set's key is written explicitly in v2 and must pass `check_id` and be unique ignoring
     case. The "key equals ID means legacy" rule is deleted, which removes key provenance: no
     stored flag records where a key came from, because after migration no key needs one.
   - `MigrationReport` lists `renamed: dict[kind, tuple[tuple[old, new], ...]]` in show order,
     plus every repair from decision 4, so a host can update what it keeps outside ShowPy
     (claims, links, notes in its own database).
6. **Constraints that v1 could hold and v2 refuses** are refused at migration, never repaired by
   guessing: a tempo of 0 or less (`CountsPerMinute` becomes positive, ruling R7), a
   `CoordinateSystem.precision` of 0 or less (ruling L-F1), a dict key that differs from the
   contained ID. Each raises `SchemaError` naming the document, the field and the stored value.
   None of them could be written by a v0.3.0 edit function.
7. **Nothing is rewritten on read.** Loading migrates in memory. The first save after the upgrade
   writes v2 documents in one revision, which plays the role of ADR 0014's normalizing revision
   (it is that revision when both apply). Revision IDs already stored never change.
8. **v1 is read by every release before 2.0.0**, and at least until 2027-11-18 (12 months after
   v0.4.0), whichever is later. Removing the v1 reader is a major-version change with a
   release-notes entry. ShowPy never writes v1 after v0.4.0. A v0.3.0 reader refuses v2 documents
   with `SchemaError` (`extra="forbid"` on `Show`), so an old consumer fails closed.
9. **Fixtures.** The repository keeps one read-only repository fixture per released writer:
   `tests/fixtures/repos/v0.1.1/` (written by `stage` 0.1.1), the existing `v0.2.0/` and a new
   `v0.3.0/` holding a renumbered show with explicit keys, a text form, a legacy set ID and a
   v0.2 case pair. Each has an `expected.json` of its v2 migration and report. A test loads each
   one, migrates it, saves it, and checks that a second save writes nothing.
10. **Version 3 and later** add one step to `MIGRATIONS` each; a step never edits an earlier
    step's output format. Phase 3's repository layout v2 (ADR 0015) versions the folder layout
    separately from this document format.

## Alternatives

- **A header in every document.** Rejected: ten document kinds each carrying a version can
  disagree inside one atomically committed tree, and the decoder would need a rule for a mixed
  tree. Phase 3's per-document encoding may revisit this (see "What would change our mind").
- **Store key provenance** (`Set.key_origin: "legacy" | "assigned"`). Rejected: it keeps the
  case-pair and invalid IDs alive forever, adds a field every future reader must honour, and
  still leaves `renumber` with a special case. Renaming once removes the problem.
- **Migrate by decoding v1 into the v1 models, then converting models.** Rejected: it means
  keeping a frozen copy of every stored v1 model class inside the library (the library defines
  104 pydantic models today).
  Shape changes on JSON and ID renames on models keep only the step functions.
- **Rewrite stored repositories on read (eager migration).** Rejected: reading would write, which
  breaks read-only checkouts and the fixture tests, and races with concurrent writers.
- **Drop v1 at 1.0.** Rejected: shows outlive library versions, and a designer opening last
  season's show after 1.0 would be refused. The cost of keeping the reader is one module.
- **Keep lenient JSON** (NaN tokens and duplicate keys). Rejected: duplicate keys make two
  readers disagree about one file, and the canonical hash of ADR 0014 already refuses NaN.

## Consequences

- Hosts that store IDs outside ShowPy (claims, deep links, their own notes) must apply the
  report's `renamed` mapping once per show; ShowPy cannot see those stores. Release notes for
  v0.4.0 carry the rename rule and a host checklist.
- A v1 show whose stored IDs all pass ADR 0006 migrates with no renames; its first save still
  writes new bytes (header, typed recipes), so `Set.source_hash` and other consumer-held hashes
  mismatch exactly once.
- `renumber` works on every migrated show; the v0.3.0 refusal for legacy IDs and case pairs goes.
- Bundled sheet templates hold 335 item IDs with `-` (for example `front-sideline`). ADR 0006
  lists landmark and zone IDs, so either the templates and every stored sheet rename them, or
  ADR 0006 gets a dated addendum for sheet items. The phase plan asks the maintainer (decision 1
  there); this ADR's rename rule covers show zones either way.
- `migrate_v1_to_v2` is deleted at 2.0 at the earliest, so its sub-steps stay small and pure.

## Numbers

| Measure                                         | Today (v1)      | After (v2)                                                                                    |
| ----------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------- |
| Version markers in a stored tree                | 0               | 1 (`show.json`)                                                                               |
| Stored `dict[str, Any]` fields                  | 4               | 0                                                                                             |
| Ledger tests this format closes directly        | —               | UP-9.7-29, UP-9.7-92, UP-9.6-62                                                               |
| IDs renamed in the v0.2.0 fixture               | —               | 16 (15 form IDs, 1 selection name)                                                            |
| Released writers covered by a committed fixture | 1 of 4 (v0.2.0) | 3 of 4 (v0.1.1, v0.2.0, v0.3.0; v0.1.0's `show` package and schema are identical to v0.1.1's) |
| Minimum v1 read window                          | —               | until 2.0.0, and at least until 2027-11-18                                                    |

## What would change our mind

- Phase 3 chooses per-document encoding with independent document versions: then each document
  gets its own header, and this ADR gets a dated addendum mapping the single version to them.
- A host needs v0.3.0 and v0.4.0 to edit one repository at the same time (a staged rollout):
  then v0.4.x writes v1 behind an explicit flag until the host finishes, instead of failing the
  old reader closed.
- The rename rule produces IDs designers reject (for example on printed charts): then the
  migration refuses invalid IDs and asks the host to supply a mapping.

## References

- Audit `docs/upgrade-plan.md`: section 2 item 5, section 4.1, section 9.7 item 11, section 10
  ("Phase 2" and decision 6).
- Release notes `docs/releases/v0.3.0.md`: "Migrating from v0.2" item 13, "Rulings" R7, L-F1,
  L-G2, A6-R1, A6-R4, X-R15; "Known limitations", "Data model".
- Ledger tests: UP-9.7-29, UP-9.7-92 (`tests/unit/show/test_documents.py`), UP-9.6-62.
- Code: `showpy/show/documents.py` (`_parse`, `decode_show`), `showpy/export/data.py`
  (`SCHEMA_TAG`), `showpy/choreo/models.py` (`Set.key`), `showpy/show/models.py`
  (`Show._unique_set_ids`), `tests/fixtures/repos/v0.2.0/`.
- Related: ADR 0004 (stable set keys), ADR 0006 (ID pattern), ADR 0008 (selection precedence),
  ADR 0009 (deprecation), ADR 0014 (canonical hashing), ADR 0015 (repository layout v2, Phase 3).

## Addendum 2026-11-19: strict JSON errors name the token or key

Decision 3 says each refusal names the document and the position. The parser
(`showpy.core.strict_json`, task L0.9) names the document and, instead of a position, the
offending token (`NaN`, `Infinity`, `-Infinity`) or the repeated key: the standard library's
`parse_constant` and `object_pairs_hook` hooks receive no offset. A leading byte-order mark
reports position 0, and invalid JSON keeps the line and column of `json.JSONDecodeError` in its
message. Every refusal is `SchemaError` code `STRICT_JSON`.

A parser that tracks offsets (a second scan of the bytes for the token, or a parser other than
the standard library's) would restore positions; one more pass over every stored document was
not worth it for errors that name the token or key already.
