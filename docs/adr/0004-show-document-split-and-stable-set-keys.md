# ADR 0004: Show metadata splits by owner, and set folders are keyed by a stable set key

- Status: Proposed
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

Two defects share one root cause: storage paths and ownership follow display data.

- **Set folders are keyed by the display set ID.** `encode_show` writes `/sets/<set id>/…`
  (`showpy/show/documents.py`). `renumber` changes display IDs (`1, 1A, 2` becomes `1, 2, 3`), so
  `save_show` deletes the folder of `1A`, writes a new folder `2`, and the build script
  `set.py`, which is not part of the `Show` model, is deleted for one set and handed to the
  wrong set for another (UP-9.7-01, audit P0 #1).
- **`/show/show.json` mixes owners.** It holds `id`, `title`, `pack`, `coordinates`, `cast`,
  `facing_overrides`, `zones`, `dependencies`, `pins` and the set order. Agents may write the
  file, so they can delete sets, empty the cast and change the show ID (UP-9.7-08, UP-9.7-09,
  UP-9.7-10). Only pins and locks are compared on agent writes.

Audit decision 5 asks whether to split `show.json` into designer-owned and agent-writable
documents, and whether to key set folders by a stable internal ID. ADR 0003 puts enforcement in
one policy; this ADR fixes what that policy enforces and where the bytes live.

## Decision

1. **Every set gets a stable `key`.** `Set.key` is assigned when a set is created (by
   `add_set`, `insert_subset` and every other constructor) and never changes afterwards:
   `renumber`, relabels and moves keep it. Its default is the set's display ID at creation, so
   every v0.1 and v0.2 set already has a key equal to its folder name. A new set whose display
   ID equals a live key gets the first free `<id>.<n>` (`2.2`, `2.3`, …). Keys follow the ID
   pattern of ADR 0006.
2. **Storage paths use keys; workspace paths keep display IDs.** The stored tree holds
   `/sets/<key>/set.json`, `/sets/<key>/forms.json`, `/sets/<key>/set.py` and
   `/transitions/<key a>-<key b>/paths.json`; the set order in `show.json` lists keys. The
   `Workspace` maps `/sets/<display id>/…` to `/sets/<key>/…` through the decoded show, so
   agents, the designer app and the documentation keep addressing `/sets/2/set.py`. `set.json`
   writes `key` only when it differs from `id`, so shows that were never renumbered keep their
   bytes and their revision history stays free of noise.
3. **Ownership is a table, by field and by path.** The table lives next to the policy of ADR
   0003 (`showpy/show/policy.py`):

   | Data                                                      | Owner                                |
   | --------------------------------------------------------- | ------------------------------------ |
   | show `id`, `title`, `pack`, `coordinates`, `dependencies` | designer                             |
   | `cast` (groups, spots, selections, label scheme)          | designer                             |
   | `pins`                                                    | designer                             |
   | set order                                                 | designer; agents insert only         |
   | `facing_overrides`, `zones`                               | agent-writable                       |
   | sets, forms, paths, scripts, timeline, notes, sheet       | agent-writable, minus pins and locks |
   | `/config/*`, `/canvas/selection.json`, `/publish/*`       | designer                             |
   | `/canvas/comments.json`                                   | each author owns their entries       |
   | `/renders/`, `/exports/`, `/lib/drafts/`                  | anyone                               |

   "Insert only" means an agent may add new set keys anywhere in the order, but every existing
   key must stay, in the same relative order, with the same display ID.

4. **Phase 1 enforces the table by field; the files split with format v2.** In Phase 1 the policy
   decodes `show.json` before and after an agent write and compares the designer-owned fields, so
   no stored bytes change. With stored format v2 (Phase 2, ADR 0007) `show.json` splits along the
   table:
   - `/show/show.json` (designer): format header, `id`, `title`, `pack`, `coordinates`,
     `dependencies`, set order;
   - `/show/cast.json` (designer);
   - `/show/pins.json` (designer);
   - `/show/overlays.json` (agent-writable): `facing_overrides`, `zones`.

   After the split, an agent write to a designer file fails on its path, before any decode.

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
- **Split `show.json` physically in Phase 1.** Rejected: it is a stored-format change without a
  format header, so Phase 2 would have to migrate three shapes (v1, a header-less split, v2)
  instead of two. Field-level enforcement fixes the P0 defects now without changing bytes.
- **Make the whole `show.json` designer-owned with no insert rule.** Rejected: agents that write
  drill must be able to add sets; deleting, reordering and renaming are what destroy designer
  work.

## Consequences

- `Set` gains a field. v0.2 readers fail on a `set.json` that carries `key`, which happens only
  after a renumber; downgrade is not supported before 1.0 and the release notes say so. The JSON
  schema stays `showpy.show.v1` because the field is optional; ADR 0007 decides the v2 header.
- `Repository.read(show_id, path)` returns stored paths, which differ from workspace paths after
  a renumber. Under ADR 0003 the raw repository is private, so callers read through a `Session`
  or `Workspace`; the UP-9.7-01 test reads through the workspace when it is fixed.
- The policy decodes `show.json` on agent writes until the Phase 2 split (cost in ADR 0003).
- Agents cannot change the cast. Adding performers goes through the designer or a `Proposal`.

## Numbers

| Measure                                                    | Today           | After Phase 1                | After Phase 2             |
| ---------------------------------------------------------- | --------------- | ---------------------------- | ------------------------- |
| Build scripts lost by `renumber` + `save_show` (UP-9.7-01) | 1 lost, 1 moved | 0                            | 0                         |
| `show.json` keys an agent can change                       | 10 of 10        | 2 of 10                      | 0 (file is designer-only) |
| Stored bytes changed for never-renumbered v0.2 shows       | n/a             | 0                            | format v2 migration       |
| P0 #1 and #5 ledger tests unblocked                        | 0               | 4 (UP-9.7-01, -08, -09, -10) | 4                         |

The 10 keys are `id`, `title`, `pack`, `coordinates`, `cast`, `facing_overrides`, `zones`,
`dependencies`, `pins` and `sets`; the 2 agent-writable keys are `facing_overrides` and `zones`.

## What would change our mind

- Consumers need agents to add or edit performers routinely: then `cast.json` gets an
  agent-append rule like the set order (add spots, never remove or rename).
- Phase 3's three-way merge shows frequent key collisions between branches: then keys gain a
  short branch-unique suffix.
- Designers want sets reordered by agents: the insert-only rule becomes "reorder allowed, delete
  never".

## References

- Audit `docs/upgrade-plan.md`: section 3 rows 1 and 5; section 9.7 items 1 and 5 and upgrade 5;
  section 10 decision 5.
- Ledger tests unblocked: UP-9.7-01, UP-9.7-08, UP-9.7-09, UP-9.7-10.
- Code: `showpy/show/documents.py`, `showpy/show/files.py`, `showpy/show/manage.py`
  (`renumber`), `showpy/choreo/models.py` (`Set`).
- Related: ADR 0003 (enforcement point), ADR 0006 (key pattern), ADR 0007 (format v2, Phase 2).
