# ADR 0004: Set folders are keyed by a stable set key

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

Set folders are keyed by the display set ID. `encode_show` writes `/sets/<set id>/…`
(`showpy/show/documents.py`). `renumber` changes display IDs (`1, 1A, 2` becomes `1, 2, 3`), so
`save_show` deletes the folder of `1A`, writes a new folder `2`, and the build script `set.py`,
which is not part of the `Show` model, is deleted for one set and handed to the wrong set for
another (UP-9.7-01, audit P0 #1).

Audit decision 5 asks two things: whether to key set folders by a stable internal ID, and whether
to split `/show/show.json` into designer-owned and agent-writable documents. The split only served
permissions, which ADR 0003 leaves to the host, so this ADR settles the key alone.

## Decision

1. **Every set gets a stable `key`.** `Set.key` is assigned when a set is created (by `add_set`,
   `insert_subset` and every other constructor) and never changes afterwards: `renumber`,
   relabels and moves keep it. Its default is the set's display ID at creation, so every v0.1 and
   v0.2 set already has a key equal to its folder name. A new set whose display ID equals a live
   key gets the first free `<id>.<n>` (`2.2`, `2.3`, …). Keys follow the ID pattern of ADR 0006.
2. **Storage paths use keys; workspace paths keep display IDs.** The stored tree holds
   `/sets/<key>/set.json`, `/sets/<key>/forms.json`, `/sets/<key>/set.py` and
   `/transitions/<key a>-<key b>/paths.json`; the set order in `show.json` lists keys. The
   `Workspace` maps `/sets/<display id>/…` to `/sets/<key>/…` through the decoded show, so
   agents, the designer app and the documentation keep addressing `/sets/2/set.py`. `set.json`
   writes `key` only when it differs from `id`, so shows that were never renumbered keep their
   bytes and their revision history stays free of noise.
3. **`show.json` is not split by owner.** Who may change which field is the host's rule (ADR
   0003). Format v2 (Phase 2, ADR 0007) may still split the file for size or merge reasons, and
   decides that on its own merits.

## Alternatives

- **Have `renumber` return a path-rename plan that storage applies.** Rejected: every caller of
  `renumber` (and of any future edit that renames sets) must remember to pass the plan to
  storage, and a plain `save_show` after `renumber` (exactly UP-9.7-01) still loses the script.
  A key in the model makes the plain save correct.
- **Opaque counter keys (`k1`, `k2`, …) stored in `show.json`.** Rejected: every existing set
  would need a migration to get one, every stored path would change once, and two branches that
  each add a set collide on the next counter value in Phase 3 merges. Defaulting the key to the
  creation-time display ID needs no migration and makes collisions rare and readable.
- **Content-hash keys.** Rejected: a set's content changes on every edit, so the key would too.
- **Put the build script into the `Show` model.** Rejected: scripts belong to the consumer since
  the repository split of 2026-11-12, and per-set side files (renders, drafts) would hit the same
  bug.
- **Split `show.json` by owner (designer, agent) and enforce an ownership table (the first draft
  of this ADR).** Rejected by the maintainer, 2026-11-15, with ADR 0003: permissions live in the
  host.

## Consequences

- `Set` gains a field. v0.2 readers fail on a `set.json` that carries `key`, which happens only
  after a renumber; downgrade is not supported before 1.0 and the release notes say so. The JSON
  schema stays `showpy.show.v1` because the field is optional; ADR 0007 decides the v2 header.
- `Repository.read(show_id, path)` returns stored paths, which differ from workspace paths after
  a renumber. Callers that address sets by display ID read through a `Workspace`; the UP-9.7-01
  test reads through the workspace when it is fixed.
- UP-9.7-08, UP-9.7-09 and UP-9.7-10 (agents rewriting `show.json`) are deleted with ADR 0003.

## Numbers

| Measure                                                    | Today           | After |
| ---------------------------------------------------------- | --------------- | ----- |
| Build scripts lost by `renumber` + `save_show` (UP-9.7-01) | 1 lost, 1 moved | 0     |
| Stored bytes changed for never-renumbered v0.2 shows       | n/a             | 0     |
| P0 #1 ledger tests passing                                 | 0 of 1          | 1     |

## What would change our mind

- Phase 3's three-way merge shows frequent key collisions between branches: then keys gain a
  short branch-unique suffix.

## References

- Audit `docs/upgrade-plan.md`: section 3 row 1; section 9.7 item 1; section 10 decision 5.
- Ledger test fixed: UP-9.7-01.
- Code: `showpy/show/documents.py`, `showpy/show/files.py`, `showpy/show/manage.py`
  (`renumber`), `showpy/choreo/models.py` (`Set`).
- Related: ADR 0003 (permissions belong to the host), ADR 0006 (key pattern), ADR 0007 (format
  v2, Phase 2).
