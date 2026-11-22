# ADR 0023: Stored format versions: `e8.show` v2 at v0.4.0, v3 at v0.6.0

- Status: Accepted (2026-11-22)
- Date: 2026-11-22
- Deciders: the maintainer (abj360)

## Context

ADR 0007 defines stored format v2 and the migration chain that reads v1. On `main` at `8b92d66`
the chain is scaffolded: `FORMAT = "showpy.show"` and `CURRENT_VERSION = 2`
(`showpy/show/migrate.py:54-55`), the v2 schema is `showpy/schemas/showpy.show.v2.json`, and
`showpy/show/migrations/` holds nine sub-step modules (`cast`, `events`, `facings`, `forms`,
`ids`, `paths`, `selectors`, `sheet`, `timeline`), each filled by the Phase 2 task that changes
its model.

Three later decisions change what v2 holds and what follows it:

- The library is renamed e8Show at v0.4.0 (ADR 0020), so a format value that carries the old
  name would be written into every new show.
- Notes leave `Show` at v0.4.0 for a `notes.json` document of their own (ADR 0021).
- v0.6.0 brings many new stored shapes at once: the show's own copy of its sheet, prop spots,
  path segments, facing modes, time anchors, set titles, and performer names and heights. None
  of them exists yet, so none can be part of v2.

No release has ever written or read `"format": "showpy.show"`: the commits that add it
(`4f586380`, `8925ffc8`) are in no tag (`git tag --contains` prints nothing for either), and
`v0.3.0` has no `showpy/show/migrate.py`. Every released writer, v0.1.0 to v0.3.0, writes v1.

## Decision

1. **v2 ships at v0.4.0 as ADR 0007 scaffolds it**: typed payloads (form recipes, motion
   parameters, route curves, event payloads), IDs renamed once by rule with a report, and tempo
   sections sorted by count. One change from ADR 0007: v2 holds no notes. The migration moves
   them to a `notes.json` document with its own header,
   `{"format": "e8.notes", "version": 1, ...}` (ADR 0021), which adds a tenth sub-step, `notes`.
2. **The format value is `e8.show`.** v0.4.0 writes `"format": "e8.show"` and `"version": 2` as
   the first two keys of `show.json` and of the `export_json` document. Version 2 under the old
   value `showpy.show` exists only on `main`, never in a tag, so no reader for it is needed and
   none is written. Readers accept:
   - a tree whose `show.json` has no header, or an `export_json` document tagged
     `"schema": "showpy.show.v1"`: v1;
   - `"format": "e8.show"` with a version up to the release's `CURRENT_VERSION`.

   Any other `format` raises `SchemaError`; a newer version raises `VersionError`, as ADR 0007
   decision 2 says.

3. **v3 ships at v0.6.0 with every new stored shape of that release at once**: the sheet copy,
   prop spots, path segments, facing modes, time anchors, set titles, names and heights. The same
   step drops `Show.coordinates`, `Show.zones` and `Spot.claimable` and lists them in its report
   (ADR 0021). It is one more step in `MIGRATIONS`, as ADR 0007 decision 10 allows.
4. **At most one format version per release.** v0.5.0 changes no stored document shape; its
   repository layout 2 (ADR 0015) is versioned apart from the document format (ADR 0007,
   decision 10).
5. **v1 is read until 2.0.0, and at least until 2027-11-18** (ADR 0007, decision 8, unchanged).
   The committed fixtures of every released writer (`tests/fixtures/repos/v0.1.1/`, `v0.2.0/`,
   `v0.3.0/`) stay in the suite, and each later release adds its own.
6. **Frozen wire names are never renamed**: `showpy-hash/1`, `$showpy:map` and `showpy.font.v1`
   (ADR 0020, decision 4).
7. **Schema files.** The v2 schema is renamed `e8.show.v2.json` with the rename (ADR 0020). The
   v1 schema keeps its name, `showpy.show.v1.json`, and stays frozen.
8. **The single-file show package** is covered by a later addendum to this ADR.

### Who reads and writes what

| Release  | Reads                              | Writes       | Single-file form                    |
| -------- | ---------------------------------- | ------------ | ----------------------------------- |
| `v0.3.0` | v1 (no header)                     | v1           | `export_json`, tag `showpy.show.v1` |
| `v0.4.0` | v1, `e8.show` v2                   | v2           | `export_json` with the v2 header    |
| `v0.5.0` | v1, v2; repository layouts 1 and 2 | v2; layout 2 | The same                            |
| `v0.6.0` | v1, v2, v3                         | v3           | The same, v3                        |

### Rollback and mixed versions

- **Reading is safe.** Loading migrates in memory; nothing on disk changes until the first save
  (ADR 0007, decision 7). A host can open a repository with a new release and decide not to
  write.
- **Writing is one step.** The first save writes the new format in one revision. An older
  release then refuses the repository with `SchemaError` (`extra="forbid"` on `Show`), so it
  fails closed and never half-reads.
- **Going back.** Use the new release to `revert` to the last revision the old release wrote,
  or restore a copy made before the first write. The release notes of v0.4.0, v0.5.0 (layout 2)
  and v0.6.0 tell hosts to copy a repository before its first write.
- **Two releases on one repository at once is not supported.** The older one stops at the first
  revision in the newer format. ADR 0007 names what would change that.
- **Retries.** Every migration step is a pure function of its input documents: running it twice
  gives the same bytes. The fixtures prove it for each released writer.

## Alternatives

- **Hold v2 back until the v0.6.0 shapes exist.** Rejected: v2's typed payloads and ID renames
  are ready and needed now: ADR 0006's ID checks on every model and ADR 0007's strict JSON
  arrive with v2.
- **One format for everything at v0.4.0.** Rejected: it cannot be built. The v3 shapes (prop
  spots, segments, the sheet copy) do not exist yet.
- **Keep the value `showpy.show`.** Rejected: every show written from v0.4.0 would carry the old
  name for as long as v2 is read, and changing it later would need one more accepted value.
- **A new format version in every release.** Rejected: each version is a migration step kept
  until 2.0.0 and a fixture kept for good; v0.5.0 has no shape change to record.

## Consequences

- Shows written by v0.4.0 and v0.5.0 carry `"format": "e8.show", "version": 2`; shows written by
  v0.6.0 carry version 3. A v0.4.0 or v0.5.0 reader refuses version 3 with `VersionError`.
- The migration chain has two steps at v0.6.0 (`v1_to_v2`, `v2_to_v3`), each composed of named
  sub-steps.
- Hosts upgrading over two releases at once (v0.3.0 to v0.6.0) migrate through both steps in one
  load; the report lists the changes of each.
- The old name stays inside stored data only in the three frozen wire names and the v1 tag.

## Numbers

| Measure                                       | Value                                                 |
| --------------------------------------------- | ----------------------------------------------------- |
| Released writers of `"format": "showpy.show"` | 0 (no tag contains `4f586380` or `8925ffc8`)          |
| Header forms a v0.4.0 reader accepts          | 3: no header, `"schema": "showpy.show.v1"`, `e8.show` |
| v1-to-v2 sub-steps                            | 10 (9 modules at `8b92d66`, plus `notes`)             |
| Format versions written, v0.4.0 to v0.6.0     | 2 (v2 from v0.4.0, v3 from v0.6.0)                    |
| Committed released-writer fixtures            | 3 (v0.1.1, v0.2.0, v0.3.0)                            |
| Minimum v1 read window                        | until 2.0.0, and at least until 2027-11-18            |

## What would change our mind

- A host needs one of the v0.6.0 shapes before v0.6.0: then that shape ships as v3 in the
  earlier release, and the rest take the next version, still one version per release.
- A v0.6.0 shape slips past v0.6.0: then it arrives in a later format version (v4), never by
  editing the v3 step after a release writes it.

## References

- ADR 0006 (ID pattern), ADR 0007 (stored format v2 and the migration chain), ADR 0015
  (repository layout v2), ADR 0020 (rename and frozen wire names), ADR 0021 (scope; notes leave
  `Show`).
- Code: `showpy/show/migrate.py` (`FORMAT`, `CURRENT_VERSION`), `showpy/show/migrations/`,
  `showpy/export/data.py` (`SCHEMA_TAG`), `showpy/schemas/`, `tests/fixtures/repos/`.
