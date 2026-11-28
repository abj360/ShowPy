# ADR 0015: Repository layout v2

- Status: Accepted (2026-11-24)
- Date: 2026-11-24
- Deciders: the maintainer (abj360)

## Context

A repository stores each show as a linear history of revisions. A revision names a tree (stored
path to blob digest), and blobs are content-addressed. Two adapters implement it:
`InMemoryRepository` (`e8s/show/repositories.py`) and `FileRepository`
(`e8s/show/file_repository.py`). The file layout has not changed since v0.1:

```
<root>/<show id>/objects/<sha256>        blobs, verified by hash on read
<root>/<show id>/trees/<revision>.json   the revision record and its whole tree
<root>/<show id>/next/root, next/<rev>   the history: each link file holds its child's ID
```

Creating `next/<parent>` with an exclusive hard link is the compare-and-swap that lands a commit;
Phase 1 proved one winner in every 8-process race. Everything else about the layout is the
audit's list of open problems (`docs/upgrade-plan.md`, section 9.7, item 10 and "Upgrades" 1, 3,
4, 6 and 7), and Phase 2 added more (its Appendix A rows 34, 52 to 62, 71 to 74, 93, 94 and 96 to
101):

- **History costs grow with history.** `head()` follows the `next/` chain and parses every tree
  record on the way, so it is O(revisions x tree size). `revisions_since`, `get_revision` and the
  conflict check call it again. `events_since` decodes two whole shows per revision.
- **A one-spot edit re-encodes the whole show.** `save_show` runs `encode_show` over all 181
  documents of a 300 x 60 show, then compares bytes; 84% of the save is that encode.
- **No version on the layout.** Nothing on disk says which layout a root uses, so a later layout
  cannot refuse an older reader or be refused by one.
- **Durability and hygiene.** No directory `fsync` after a link; a commit that loses the race
  leaves its tree record behind (UP-9.7-28); a crashed writer leaves `.tmp-*` files nothing removes
  (UP-9.7-27); there is no `fsck` and no garbage collection; deleting a `next/` file rolls the
  head back silently.
- **File systems.** On a case-insensitive file system (APFS and NTFS by default) `Show` and `show`
  share one folder (UP-9.7-14); a trailing dot is stripped by Windows (UP-9.7-16); a long ID leaks
  `NAME_TOO_LONG`; a file system without hard links (exFAT, many network shares) cannot commit at
  all (`NO_HARDLINKS`).
- **Merging is too coarse to be useful and too fine to be safe.** A whole transition is one
  overlap key, while counts and paths of the same transition are separate keys
  (UP-9.7-46). `diff_show` misses groups, the pack, set cues and labels, placement notes and
  selections (UP-9.7-38 to -43). Proposals are not stored at all, and `accept` never marks one
  accepted (UP-9.7-34).
- **Fonts.** A show that uses a user font reopens only when the caller passes a registry holding
  that font; otherwise it fails closed with `MissingFontError` (text stage 1 limit).

Three decisions bound this one. ADR 0007 decision 10 and ADR 0023 decision 4 version the folder
layout apart from the document format: v0.5.0 writes no new document format, and stored models
stay format v2. ADR 0003 keeps permissions with the host, so nothing here checks who writes.
ADR 0026 limits what storage may call on a model: attributes, `replace`, `to_json` and
`from_json`, plus `FrozenModel`'s private helpers where a validation context is needed. Notes left
the show in v0.4.0 (ADR 0021): the notes document is `/notes/notes.json`, a plain document of the
tree that storage carries and never interprets.

### Measured today

On an Apple M4 (10 cores, 16 GB, APFS, macOS 26.6.2), Python 3.14.8, `main` at `a6ef3ca`
(v0.4.0 plus release notes), load average 1.5 to 1.8, on 2026-11-23. The show is
`seeded_show(11, 300, 60)`: 181 stored documents, 14.35 MB, the largest 167 KB. Times are the
minimum of 5 rounds unless marked. Script:
`~/.cache/showpy-scratch/phase-3/measure_storage.py` and `measure_parts.py`.

| Operation                                                 | Memory   | Files   | Budget  |
| --------------------------------------------------------- | -------- | ------- | ------- |
| `head()`, 1,001 revisions (two-set show)                  | 0.001 ms | 62.8 ms | < 1 ms  |
| `head()`, 201 revisions, 300 x 60                         | 0.001 ms | 20.9 ms | < 1 ms  |
| `events_since`, 200 revisions, 300 x 60                   | 6.35 s   | 11.70 s | < 1 s   |
| One-spot edit saved, 300 x 60                             | 191 ms   | 195 ms  | < 50 ms |
| One-spot edit saved at 201 revisions, 300 x 60            | 201 ms   | 243 ms  | < 50 ms |
| First save of the whole show (cold caches)                | 364 ms   | 494 ms  | (none)  |
| `load_show` at 201 revisions, 300 x 60                    | 315 ms   | 257 ms  | (none)  |
| `validate_show`, 300 x 60                                 | 2.01 s   |         | < 1 s   |
| Load, edit and save, mean per revision while building 200 | 507 ms   | 605 ms  | (none)  |
| Load, edit and save, mean per revision, 1,000 (two-set)   | 1 ms     | 138 ms  | (none)  |

Where the one-spot save goes (memory backend, cProfile, 232 ms profiled): `show_changes` 194 ms,
of which `encode_show` 193 ms (120 model dumps, 184 `json.dumps` calls); the commit's validity
check 32 ms with a warm decode cache.

Where `events_since` goes (memory, 20 revisions, 120 ms per revision): decoding the two whole
shows of each revision 47 ms each, and comparing them model by model (`_edit_ops`, 378,180
`__eq__` calls) 71 ms. The parts a narrower path would use cost far less: `json.loads` of one
69 KB `set.json` 0.22 ms; `content_hash` of a revision body 0.008 ms; `validate_set` of one
300-spot set 0.19 ms; `Show.replace(sets=...)` after a one-set edit 2.4 ms. `validate_paths` of
one transition costs 27.3 ms (continuity runs the engine for all 300 spots), so re-checking the
two transitions around an edited set whole already takes 54.5 ms.

After 201 revisions of the 300 x 60 show the file repository holds 588 files, 18.2 MB.

File-system primitives under the repository root (APFS, 4 KB files, median of 200 unless marked):

| Primitive                                 | Median   | p95      |
| ----------------------------------------- | -------- | -------- |
| `os.link` (today's compare-and-swap)      | 0.293 ms | 0.971 ms |
| `O_CREAT \| O_EXCL` create and 4 KB write | 0.069 ms | 0.227 ms |
| `fsync` of a 4 KB file                    | 0.038 ms | 0.088 ms |
| `fsync` of the directory                  | 0.001 ms | 0.003 ms |
| `F_BARRIERFSYNC` of a 4 KB file (of 100)  | 0.396 ms |          |
| `F_FULLFSYNC` of a 4 KB file              | 3.505 ms | 4.458 ms |
| `lstat` of one path (minimum of 1,000)    | 0.003 ms |          |
| Read 1,000 files of 200 bytes (minimum)   | 11.1 ms  |          |

The root is case-insensitive: after `mkdir Show`, `show` exists.

Earlier measurements at the caps (task X2 of Phase 2, 2026-11-23, an arm64 machine under load
average 5 to 10, so absolute times are inflated): a one-spot save takes 0.10 s at 300 x 60 and
5.5 s at 300 x 1,000 sets (16.7 times the sets, 55 times the time), 0.58 s at 5,000 x 2 and 3.9 s
at 5,000 x 60; `events_since` over 2 revisions takes 4.0 s at 300 x 1,000.

## Decision

The repository gets a second layout, written by every release from v0.5.0, and the history,
encoding, merging and proposal code is rebuilt on it. Each numbered decision below names the
problem it closes.

1. **A layout version file.** A layout-2 root holds `layout.json`:
   `{"format": "e8.repository", "layout": 2}`. It is created with an exclusive create when the
   first show is stored, and it is the last thing an upgrade writes (decision 15), so a root
   either has a complete layout or none. A root with `<id>/next/root` folders and no
   `layout.json` is layout 1. A higher `layout` raises `VersionError` (code `VERSION`) naming
   the layout and the newest this release reads. Any other content raises `StorageError` (code
   `CORRUPT`). The layout number changes only when the folder grammar changes. A new kind of
   document never changes it (decision 11).

2. **O(1) `head()`: numbered slots written once, as the compare-and-swap and the log.** Layout 2
   stores a show as:

   ```
   <root>/layout.json
   <root>/tmp/                               every writer's temp files (one file system)
   <root>/assets/<sha256>                    the file-backed asset store (decision 14)
   <root>/shows/<folder>/show.json           manifest: {"id": "<show id>"}, plus legacy_before
   <root>/shows/<folder>/log/<seq>           one revision per slot: 000000000001, 000000000002, ...
   <root>/shows/<folder>/records/<rev>.json  revision record, its sequence number and its tree
   <root>/shows/<folder>/objects/<sha256>    blobs
   ```

   - **Commit.** Write the blobs and the record, then claim slot `parent seq + 1` with an
     exclusive create. The slot holds the revision (`id`, `parent`, `at`, `changed`) as one line
     of canonical JSON. The writer that creates the slot lands; every other writer gets
     `ConflictError` listing what landed. This is the same one-winner rule as today's
     `next/<parent>` link, keyed by position instead of by parent.
   - **Head.** A process remembers the last sequence number it saw per show. `head()` checks
     that slot `s + 1` is absent (one `lstat`) and returns slot `s`. On a miss it gallops forward
     (`s + 1`, `s + 2`, `s + 4`, ...) and bisects back. A cold process starts at 0, so at most
     `2 log2(n) + 2` lookups: 22 at 1,000 revisions. There is no `HEAD` file to keep
     consistent: Windows refuses to replace a file a reader holds open, and a stale hint would be
     a second source of truth.
   - **Log.** `revisions(show_id)` reads the slots in order; they are small and never change.
     `revisions since r` reads `r`'s sequence number from its record and reads only the later
     slots, so the conflict check and `events_since` cost O(revisions since), not O(history).
   - **Checks on every read.** Each slot's `parent` must equal the previous slot's `id`, and its
     `id` must name a record whose `seq` is the slot's number; a gap, a fork or a mismatch raises
     `StorageError` (code `CORRUPT`) naming the slot. A commit that claims slot `n` also checks
     that slot `n + 1` is absent, so a deleted slot cannot be refilled silently.
   - **The memory backend** keeps a list per show under its lock, as today, and creates a show's
     log only when its first commit lands (today a failed first commit leaves an empty show
     listed).

3. **Verified reads.** Blobs are re-hashed on every read, as today. Records are verified too: the
   revision ID is recomputed from the record (parent, time and the digests of the changed paths,
   all held in the record) and must equal the record's name and its slot. Revisions written by
   v0.1 and v0.2 carry IDs of the old hashing scheme (ADR 0014 decision 5); an upgraded show
   stores `legacy_before`, the first sequence number written under the current scheme, and only
   slots before it may carry an old-scheme ID. Every read failure is `StorageError` with a code:
   `CORRUPT`, `IO`, `NAME_TOO_LONG` or `TOO_LARGE`.

4. **Durability: directory `fsync` and one full flush per commit.** Every created file is
   flushed before it is linked or named, and its directory is flushed after (0.001 ms on APFS).
   On macOS, where `fsync` does not order writes on the drive, blobs and the record get
   `F_BARRIERFSYNC` and the slot gets `F_FULLFSYNC` (3.5 ms), so a commit that has returned
   survives power loss and nothing a slot names can be lost before the slot. Windows has no
   directory flush; NTFS journals its metadata, and the file flushes stay. Decision needed D3.

5. **`fsck` and `gc`.**
   - `fsck(root, show_id=None) -> FsckReport` reads and changes nothing. It checks the layout
     file, every manifest, every slot (chain, gaps, torn slots), every record (ID, sequence,
     tree paths, digests), every reachable blob (present, re-hashed), and lists unreachable
     records and blobs, temp files and anything else under the root as findings with codes.
   - `gc(root, *, at, confirm) -> GcReport` removes unreachable records and blobs and stale temp
     files. Without `confirm=True` it reports what it would remove and removes nothing. It never
     touches a slot, a manifest or a reachable object.
   - `fsck(root, repair=True, confirm=True)` removes one thing only: a torn last slot, which a
     crashed writer without hard links can leave (decision 7). Nothing references it.

6. **Temp files.** Writers create temp files only under `<root>/tmp/`. A commit removes its own
   temp files on every path, and removes temp files whose modification time is more than
   `STALE_TEMP_AGE = timedelta(hours=24)` before the commit's `at` (UP-9.7-27). A commit that
   loses the race removes its record unless the winning slot names the same revision
   (UP-9.7-28); its blobs stay until `gc`, because another commit may share them. The library
   reads no clock: the cut-off comes from the caller's `at`. Decision needed D4.

7. **A hard-link-free fallback.** The first commit through a `FileRepository` probes a hard link
   inside `tmp/`. Where links work, every file is written to `tmp/` and linked into place, as
   today. Where they fail with `EPERM`, `EXDEV` or `ENOTSUP`, files are created in place with
   `O_CREAT | O_EXCL`, written in one call and flushed. A slot is at most 4 KB, so a reader that
   finds it empty or cut short retries the read three times and then raises `StorageError`
   (code `CORRUPT`) whose hint names `fsck(root, repair=True, confirm=True)`. Blobs are verified
   by hash, so a torn blob is found on read. `NO_HARDLINKS` is no longer raised by a commit.

8. **Encoded show folders on every file system.** A show's folder is
   `<slug>-<first 16 hex of sha256(b"e8s.show-folder\n" + show id)>`. The slug is the ID
   lower-cased, with every character outside `[a-z0-9_]` replaced by `_`, cut to 32 characters.
   So `Show` and `show` get different folders on a case-insensitive file system (UP-9.7-14), a
   trailing dot never reaches the file system (UP-9.7-16), no folder is a Windows device name,
   and no folder name passes 49 characters. The manifest holds the real ID; `shows()` reads the
   manifests. New show IDs follow ADR 0006 (`check_id`, so no `-`); IDs already stored in a
   layout-1 repository keep their spelling through the upgrade. The string
   `e8s.show-folder` is a frozen wire name.

9. **Per-document encoding and caches.**
   - Encoding is memoized by the identity of each document's source objects (a `Set`, the forms
     of one set, a `TransitionSpec`, the cast), held strongly in a bounded cache so an identity
     is never reused while cached. A one-spot edit builds one new `Set` and shares every other
     object, so `save_show` encodes the documents of that set only.
   - The commit check decodes only the documents whose digests changed (the decode cache keyed by
     path and digest exists today) and checks the model rules those documents touch, instead of
     assembling and validating the whole show.
   - Validation gets the same treatment: `revalidate(previous, before, after) -> ValidationReport`
     re-runs only the checks whose items changed (decision 11), and path continuity only for the
     changed spots' rows, because one whole transition costs 27.3 ms. Its result must equal
     `validate_show(after)`; a property test over random edits holds it to that.
   - Caches are bounded by bytes, not entries, and live on the repository, so every workspace on
     it shares them. A workspace keeps the tree and the migrated view of the revision it reads.
   - The cold first commit of a process decodes per document too; it has no budget of its own.

10. **`events_since` by digest.** A revision's `changed` paths and two trees give the digests that
    changed; only those documents are decoded (from the cache when warm), and events are derived
    from the item changes in them (decision 11). Whole shows are never decoded per revision.

11. **Item-level change tracking, three-way merge, and documents split by design role.**
    - **Items.** Every stored value has an item key: a placement role of one spot in one set
      (`placement/<set key>/<spot>/<role>`), a form, a path row of one spot in one transition, a
      set's counts, its labels, its order, a spot, a group, a selection, a pin, and one item each
      for the sheet, the timeline and the show's metadata. A table in the library maps each
      model field to its item; a test fails when a stored field has no row.
    - `changes(before, after) -> ChangeSet` compares the `to_json()` of items, skipping objects
      the two shows share by identity, so it costs what changed. `diff_show` becomes a rendering
      of a `ChangeSet` and is complete by construction (UP-9.7-38 to -43).
    - `merge(base, ours, theirs) -> MergeResult` applies every item changed on one side only,
      keeps items changed the same way on both, and reports every item changed differently on
      both as a `MergeConflict`. A merged show that the models refuse (a placement of a spot the
      other side deleted) is reported as a conflict on the items involved, never repaired.
      `blame(ws, item) -> Revision | None` and `id_changes(before, after) -> IdChanges` read the
      same item keys.
    - **Design roles.** A placement splits into roles: `drill` (`x`, `y`, `on_field`, `form`,
      `order`), `facing` (`facing`), `labels` (`note`) and `pin` (`pinned`); a set into `timing`
      (`counts`), `labels` (`label`, `cue`) and `build` (`source_hash`, `state`). Three agents
      that move, turn and annotate the same spots of one set merge with no conflict.
    - **Documents follow roles.** Layout 2 stores a set as `/sets/<key>/set.json` (ID, key,
      timing, build, form list), `drill.json`, `facing.json` and `labels.json`, and the cast as
      `/cast/cast.json`, out of `show.json`. The data inside is format v2 unchanged (ADR 0023
      decision 4): the split decides which file holds a field, not its shape, and
      `export_json` does not change. The decoder reads both document sets: a tree whose
      `set.json` holds `placements` is a layout-1 tree (older revisions and upgraded shows), and
      the first save re-encodes it as roles, in one revision that changes no content, as ADR 0014
      decision 5 does for non-canonical bytes. A tree that holds both forms of one set is refused.
    - **Room for documents that do not exist yet.** The layout fixes the grammar
      `/sets/<key>/<name>.json`, `/transitions/<a>-<b>/<name>.json`, `/cast/<name>.json` and the
      reserved roots `/props/` and `/tracks/`. Which names a tree may hold is set by the format
      version in `show.json`: a later format adds a document (a state track, prop spots) without
      a new layout, and a reader refuses a name its format does not define, so it fails closed.

12. **Persisted proposals.** A proposal is a document of the show's tree,
    `/proposals/<id>.json`, so it is versioned, verified and conflict-checked like every other
    document, and needs nothing new from a backend. Its states are `open`, `accepted` (with the
    revision that applied it), `rejected` (with a reason) and `withdrawn`; `stale` is derived (an
    open proposal whose items changed since its base), never stored. `accept` merges the proposal
    into the head (`merge(base, head, base + change)`) and writes the result and the `accepted`
    state in one revision, so a proposal is never applied without being marked. Accepting twice
    names the accepted state (UP-9.7-34); a proposal made before the first save is accepted
    against the root (UP-9.7-35); overlap is per item, so a rescale and a newer row of the same
    transition conflict (UP-9.7-46) and a deleted form is never resurrected (UP-9.7-91).
    `preview_proposal` shows the merge result on the head. `changes`, `diff_show` and
    `events_since` ignore `/proposals/`.

13. **The public backend protocol and its conformance kit.** The seam between the repository
    and its storage is one small protocol, `RepositoryBackend`, with two adapters in the library
    (memory, files) and room for more (SQLite, an object store):

    ```python
    class RepositoryBackend(Protocol):
        def shows(self) -> list[str]: ...
        def head(self, show_id: str) -> str: ...
        def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]: ...
        def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]: ...
        def blob(self, show_id: str, digest: str) -> bytes: ...
        def commit(
            self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
        ) -> None: ...
    ```

    Its interface is more than the signatures, and the kit checks all of it: exactly one winner
    per parent across threads and processes; a returned commit is visible to every later read; a
    failed commit changes nothing visible; `head()` is O(1) amortized; reads are verified; every
    failure is `StorageError` with a code or `ConflictError`; IDs and paths are never trusted from
    storage. `DocumentRepository(backend)` keeps every rule that is not storage (known paths,
    decodable documents, ID checks, caches), so an adapter cannot weaken them.
    `e8s.show.conformance.check_backend(make_backend) -> ConformanceReport` ships in the wheel
    and needs no test framework. The stateful, crash-injection and fuzz suites live in `tests/`.
    `DocumentRepository` drops the twelve single-document helpers (`get_set`, `put_set`,
    `list_sets`, `get_script`, `put_script`, `get_paths`, `put_paths`, `get_forms`, `put_forms`,
    `get_timeline`, `get_limits`, `get_waivers`): with role documents a set is four files, and
    `get_show`, `put_show`, `transaction` and the `Workspace` cover each use. Decision needed D7.

14. **A file-backed asset store, and fonts in the tree.** `FileAssetStore(root)` stores bytes
    under `<root>/assets/<sha256>` with the same exclusive-create, flush and verified-read rules
    as blobs, and returns `asset:sha256:<hex>` URIs like `InMemoryAssetStore`. It is the
    `AssetStore` for media a host keeps beside a show (audio). Font bytes go in the show's own
    tree instead, as `/assets/fonts/<sha256>.json`, written by `save_show` for every text form
    whose font is not bundled: they then land in the same revision as the form that needs them,
    are verified and garbage-collected with it, travel in an archive, and need no second store.
    `load_fonts(ws) -> FontRegistry` returns the bundled fonts plus the fonts of the tree, so a
    reopened show rebuilds its text forms with no caller registry. Decision needed D8.

15. **Layout 1 is read until 2.0.0, and upgraded by copy.**
    - `FileRepository(root)` on a layout-1 root opens it read-only: shows, revisions, trees,
      blobs and `load_show` work as in v0.4.0 (v1 and v2 documents both migrate in memory, as
      today), and every write raises `VersionError` (code `OLD_LAYOUT`) whose hint names
      `upgrade_repository`. Layout 1 is read for as long as format v1 is (ADR 0007 decision 8),
      by a frozen copy of today's reader.
    - `upgrade_repository(source, target) -> UpgradeReport` reads a layout-1 root and writes a
      layout-2 root at `target`, never touching `source`. Each revision keeps its ID, parent,
      time and tree (ADR 0014: revision IDs are names and are never rewritten); blobs are
      hard-linked where the two roots share a file system and copied otherwise; every blob and
      record is verified on the way; `layout.json` is written last. A crash leaves a target with
      no `layout.json`, which is refused and can be deleted and redone. The host swaps the
      folders, so rolling back is keeping `source`.
    - Committed fixtures: the v0.1.1, v0.2.0 and v0.3.0 repositories stay, and a v0.4.0
      repository written by the `v0.4.0` tag is added. Each one loads read-only, upgrades, loads
      with the same shows at every revision, and takes a write.

16. **A portable archive with history.** `export_archive(repo, show_id) -> bytes` writes one show's
    slots, records and reachable blobs as a ZIP with stored entries, sorted names and zeroed
    times, so equal histories give equal bytes. `import_archive(repo, data) -> str` verifies
    every entry as `fsck` would and refuses a show ID that exists. Decision needed D9.

### Decisions needed

Accepted by the maintainer on 2026-11-24: D1 to D10 as recommended below.

Each line: the question, the recommendation, and the cost if the recommendation is wrong.

- **D1. Role documents are layout, not format.** Recommendation: yes; the split of a set into
  `set.json`, `drill.json`, `facing.json` and `labels.json` and of the cast into `cast.json`
  ships in layout 2 at v0.5.0 with format v2 unchanged, reading ADR 0023 decision 4 as "no model
  or interchange shape changes". Cost if wrong: the split waits for format v3, and the
  three-agent merge works on decoded items only, with the per-set document still re-encoded
  whole (69 KB per one-spot save at 300 spots).
- **D2. Slots instead of a `HEAD` file.** Recommendation: numbered slots with a galloping
  search, no head file. Cost if wrong: a cold `head()` does up to 22 `lstat` calls at 1,000
  revisions instead of 1 read; a head file can be added later as a cache without a layout bump.
- **D3. Full flush on macOS.** Recommendation: `F_BARRIERFSYNC` for blobs and records,
  `F_FULLFSYNC` for the slot, always on (about 3.5 ms per commit, inside the 50 ms save budget).
  Cost if wrong: either 3.5 ms per commit spent for safety a host did not need, or, without it, a
  commit reported durable that power loss rolls back.
- **D4. Stale temp age from the caller's time.** Recommendation: 24 hours before the commit's
  `at`. Cost if wrong: a host whose `at` runs more than 24 hours ahead of the file system's clock
  deletes a concurrent writer's temp file; that writer fails with `StorageError` (code `IO`) and
  retries, and no data is lost because nothing it wrote had landed.
- **D5. Show IDs.** Recommendation: new shows follow ADR 0006 (`check_id`, no `-`, 64
  characters); stored layout-1 IDs keep their spelling. Cost if wrong: hosts that name shows with
  hyphens must rename them; the library's own examples change `show-1` to `show_1`.
- **D6. Spot renames between revisions.** Recommendation: `id_changes` reports set renames
  exactly (set keys never change) and spot renames only when one spot disappears and one appears
  with equal placements in every set; anything else is a removal and an addition. A stable spot
  key is a format change and waits for format v3. Cost if wrong: a host that follows spots
  across a rename-and-move sees a removal and an addition until v3.
- **D7. The single-document repository helpers go.** Recommendation: delete the twelve helpers;
  there are no users. Cost if wrong: thin wrappers come back, additive, in a later release.
- **D8. Fonts in the tree, media in the asset store.** Recommendation: as decision 14. Cost if
  wrong: fonts move to the asset store later, which needs a pass over every stored text form.
- **D9. The archive is a ZIP.** Recommendation: ZIP with stored entries (`zipfile`, opens on
  every desktop); not tar, not CBOR. Cost if wrong: a second archive reader later; the 1.0 show
  file is a separate format either way.
- **D10. Release.** Recommendation: layout 2 ships as v0.5.0, the storage release ADR 0023's
  table names. Cost if wrong: none to the code; only the tag moves.

## Alternatives

- **Keep layout 1 and cache the head in memory.** Rejected: every new process still pays O(history)
  once per show (62.8 ms at 1,000 revisions of a two-set show), two processes disagree until
  each rewalks, and none of the hygiene, file-system or durability problems goes away.
- **A `HEAD` file replaced by compare-and-swap.** Rejected: there is no portable atomic
  compare-and-swap on a file's contents. A lock file (`O_EXCL`) plus `os.replace` is two steps a
  crash can split, and Windows refuses to replace a file a reader holds open.
- **One append-only log file (`log.jsonl`, `O_APPEND`).** Rejected: appends are atomic only up to
  the pipe buffer on some systems and not at all on network shares, two appenders on one parent
  both "win", and the compare-and-swap would still need a second mechanism.
- **SQLite as the file backend.** Rejected for the default: one file is opaque to agents that read
  the repository with plain tools, locking on network shares is unreliable, and a single
  corrupted page loses every show. The backend protocol and the kit (decision 13) keep it open as
  an adapter.
- **Text-level three-way merge of JSON documents.** Rejected: canonical JSON with two-space indent
  puts one field per line, but two edits of one placement's `x` and `facing` touch adjacent lines
  and conflict, and a merge of bytes can produce JSON the models refuse. Items are the unit
  designers and agents think in.
- **Split documents by agent or owner.** Rejected: ownership is the host's (ADR 0003). Roles are
  what a person or agent changes in a set, whoever they are.
- **Proposals in a side store with their own history.** Rejected: a second compare-and-swap, a
  second history and a second conformance surface, for data that fits in the tree.
- **Rewrite layout-1 repositories in place.** Rejected: a crash halfway leaves a root that neither
  release reads, and rolling back needs a copy anyway.
- **Hash-only show folders.** Rejected: folders become unreadable to a person or agent browsing
  the root; the slug costs nothing and the hash keeps them distinct.

## Consequences

- **Breaking, with no deprecation period** (there are no users): layout 2 is the only layout
  written; the twelve single-document helpers are deleted (D7); new show IDs follow ADR 0006
  (D5); `RepositoryBackend` gains `head` and `log(since=)`; `revisions_since` is deleted
  (`log(since=)` replaces it).
- A host upgrades each repository once with `upgrade_repository`, keeps the source until it is
  satisfied, and then deletes it. A v0.4.0 reader does not see a layout-2 root's shows: it finds
  no `<id>/next/root` folders. Release notes for v0.5.0 say to copy first, as ADR 0023 asks.
- An upgraded show's first save writes one extra revision that re-encodes its documents as roles
  and changes no content.
- `events_since`, `diff_show`, proposal overlap, `blame` and `id_changes` share one item table;
  a stored field added later must get a row, and a test enforces it.
- A refused migration still makes every read of that v1 revision raise: it fails closed, as in
  v0.4.0.
- The library still offers no protection against a caller that holds a backend (ADR 0003): a
  raw `backend.commit` skips `DocumentRepository`'s checks. `fsck` finds what that breaks.
- **Limitations, stated plainly.** Deleting files under a root by hand can still lose history:
  `fsck` detects a missing slot, record or blob but cannot restore it. Timelines, sheets and the
  show's metadata merge as one item each until later formats split them. Whole-show
  `validate_show` keeps its cost (2.01 s at 300 x 60); this ADR makes only the incremental path
  fast.

## Numbers

Targets are the phase plan's exit criteria, measured with the same script on the same machine.

| Measure                                                  | Today                | After              |
| -------------------------------------------------------- | -------------------- | ------------------ |
| `head()`, files, 1,000 revisions                         | 62.8 ms              | < 1 ms             |
| `head()`, files, cold process, 1,000 revisions (lookups) | 1,000 record parses  | at most 22 `lstat` |
| `events_since`, 200 revisions, 300 x 60, memory / files  | 6.35 s / 11.70 s     | < 1 s each         |
| One-spot save, 300 x 60, memory / files                  | 191 ms / 195 ms      | < 50 ms each       |
| One-spot save at 300 x 1,000 and at 5,000 x 60           | 5.5 s / 3.9 s (X2)   | < 100 ms each      |
| `events_since`, 2 revisions, 300 x 1,000                 | 4.0 s (X2)           | < 100 ms           |
| Incremental validation of a one-spot edit, 300 x 60      | none (2.01 s whole)  | < 50 ms            |
| Documents encoded by a one-spot save, 300 x 60           | 181                  | the edited set's   |
| Layout version markers on disk                           | 0                    | 1 (`layout.json`)  |
| File systems a commit works on                           | with hard links only | any                |
| Ledger IDs this layout closes directly (decisions 6, 8)  | 4 open               | 0 open             |
| Show-layer ledger IDs the phase closes                   | 42 open              | 1 left (Phase 5)   |
| Released writers with a committed fixture                | 3                    | 4 (adds v0.4.0)    |

## What would change our mind

- A backend that cannot offer a create-if-absent primitive (some object stores): then the
  protocol takes an explicit `compare_and_set(show_id, seq, revision)` and the file adapter
  implements it with today's slots.
- Merge conflicts between roles turn out common in practice (two roles edited from one gesture):
  then the role table moves fields, which needs no layout change.
- Measured `head()` on a network share passes 1 ms because `lstat` round-trips are slow: then a
  head hint file is added as a cache, still checked by one slot probe.
- A host needs v0.4.0 and v0.5.0 to write one repository at once: then layout 2 keeps a layout-1
  `next/` chain in step, for one release.

## References

- Audit `docs/upgrade-plan.md`: section 2 standard 6; section 9.7 items 8 to 11, "History and
  collaboration defects", "Measured performance", "Upgrades" 1, 3, 4, 6 and 7, "Missing
  capabilities"; section 10 "Phase 3".
- Phase 2 plan `docs/superpowers/plans/2026-11-18-phase-2-foundations.md`: Appendix A rows 34,
  52 to 62, 71 to 74, 79, 82, 93, 94, 96 to 99 and 101; Appendix B, Phase 3 rows.
- Ledger tests: UP-9.7-14, UP-9.7-16, UP-9.7-27, UP-9.7-28 (this layout); UP-9.7-32 to -35,
  UP-9.7-38 to -43, UP-9.7-46, UP-9.7-91 (items, merge and proposals).
- Code: `e8s/show/repositories.py`, `e8s/show/file_repository.py`, `e8s/show/storage.py`,
  `e8s/show/integrity.py`, `e8s/show/documents.py`, `e8s/show/document_reading.py`,
  `e8s/show/decode_cache.py`, `e8s/show/history.py`, `e8s/show/collab.py`,
  `e8s/show/changes.py`, `e8s/show/models.py` (`Revision`, `Proposal`), `e8s/geo/fonts.py`.
- Related: ADR 0003 (permissions belong to the host), ADR 0004 (stable set keys), ADR 0006 (ID
  pattern; show IDs move here), ADR 0007 (format v2; layout versioned apart), ADR 0014
  (canonical hashing; revision IDs are never rewritten), ADR 0016 (fonts as content-hashed
  assets), ADR 0021 (scope; notes leave the show), ADR 0023 (format versions), ADR 0026 (the
  public model interface).
- Plan: `docs/superpowers/plans/2026-11-24-phase-3-storage.md`.

## Addenda

### Addendum 2026-11-24: the v2 tree schema accepts both layouts

The maintainer decided on 2026-11-24 that `e8s/schemas/e8.show.v2.json`, which describes a stored
tree as a map from document path to document, is widened with no version bump. Until layout 1
retires at 2.0.0, it accepts the layout-1 documents (`set.json` holding `placements`, `cast` in
`show.json`) and the role documents (`set.json` with `placement_order`, `drill.json`,
`facing.json`, `labels.json` and `/cast/cast.json`), so older trees still validate. A tree that
holds both forms of one set still fails to decode. Model and interchange shapes are unchanged:
the schema's model definitions stay as they are, and `export_json` writes the same bytes.

### Addendum 2026-11-27: `events_since` decodes the show documents each revision changed

Decision 10 ships in part. `events_since` finds what each revision changed by comparing the blob
digests of its tree and its parent's tree, never from the revision record's `changed` list. A
revision that changed a show document gets its show from `Workspace.show_at`. The decode cache
decodes only the documents that revision changed, but the show is still assembled and validated
whole. Events come from `changes()` between that show and the one before it. A revision that
changed no show document, such as a comment-only revision, decodes nothing. Its comment events
come from the two comment documents.

Measured on 2026-11-27 on an Apple M4, Python 3.14.8, load average 3.2 to 3.8. The show is
`seeded_show(11, 300, 60)` with 200 one-spot revisions, each moving one spot in one set. Times
are the minimum of 3 rounds, with the base revision's show already decoded. That first decode
adds 0.20 s.

| Operation                                       | Before (4f4cda58) | After  | Budget |
| ----------------------------------------------- | ----------------- | ------ | ------ |
| `events_since`, 200 revisions, memory           | 4.99 s            | 1.32 s | < 1 s  |
| `events_since`, 200 revisions, files            |                   | 1.54 s | < 1 s  |
| Documents decoded (decode cache misses), memory | 200               | 200    |        |

Where the 1.32 s goes: `show_at` assembling and validating each whole show takes 0.80 s, and
`changes()` between two whole shows takes 0.50 s. The budget is not met. Meeting it means
deriving events from the item values of the changed documents alone, without assembling either
show. That needs a document-level item reader, which belongs with the item table. It is
follow-up work, to start once the item reader of the Phase 3 diff and merge work (lane C) is on
`main`.

### Addendum 2026-11-27: `events_since` reads only the documents each revision changed

Decision 10 now ships for the revisions an editing session makes. A revision that changed set,
role, forms, paths, sheet or timeline documents, and kept every set's ID, is read part by part
through the workspace's decode cache. Each changed document is decoded and checked as a
whole-show decode checks it. The items the revision changed are listed from those documents
alone (`tree_changes`, which a property test holds equal to `changes()` of the two whole shows),
and the events come from those items. The payloads read only the sets, paths and forms the events
name. The one wider read is the stored paths into a set whose counts changed, since they take the
new counts. No show is assembled or validated for such a revision.

A revision that changed show.json, the cast document or a set's ID is still compared as two
whole shows, and so is a tree of an older format or of layout 1. Those changes reach items stored
in other documents: the set order, the pins, the transitions and forms that name a set. A
revision whose parent holds no show is decoded whole once, as before. A parity property test
compares `events_since` with a frozen copy of the whole-show derivation over random edits of
every kind, renumbers, pins, removed sets and cast edits included.

Limitation: a revision read part by part does not rerun the `Show` model's cross-document rules.
None can break from the documents such a revision changes. Form keys follow form IDs, set IDs and
keys are unchanged, and the set count and extensions live in show.json. The tree-wide path checks
of a whole-show decode do run on every such revision. A future `Show` rule that reads a set,
forms, paths, sheet or timeline document together with another document must also be checked by
the part reader, or such revisions must be read whole.

Measured on 2026-11-27 on an Apple M4, Python 3.14.8, with other lanes running (load average 4 to
8). The probe is the one of the previous addendum: `seeded_show(11, 300, 60)`, 200 one-spot
revisions moving one spot in one set to two alternating positions, minimum of 3 rounds, base
revision's show already decoded. The last two rows give every revision new bytes, so each one
misses the decode cache once.

| Operation                                        | Before (686d2abc) | After  | Budget |
| ------------------------------------------------ | ----------------- | ------ | ------ |
| `events_since`, 200 revisions, memory            | 1.81 s            | 0.11 s | < 1 s  |
| `events_since`, 200 revisions, files             | 1.29 s            | 0.35 s | < 1 s  |
| `events_since`, 200 revisions, new bytes, memory | 1.18 s            | 0.40 s | < 1 s  |
| `events_since`, 200 revisions, new bytes, files  | 2.43 s            | 0.62 s | < 1 s  |
| Shows assembled and validated, 200 revisions     | 200               | 0      |        |

The new-bytes Before figures were measured in review, on the same probe. The table gives the
fastest round. Repeated calls in one process cost more. Over six new-bytes calls in memory with a
collection forced before each, the calls after the first took 0.36 to 0.38 s. Without forced
collections, three of six calls took 0.68 to 0.69 s: the garbage collector's full pass over the
cached decodes ran inside them. The review measured about 0.75 s with collections and up to about
1.0 s on repeated calls without them. The margin to the 1 s budget is thin there. Profiled, about
two thirds of that case is decoding the changed set: a set decodes from set.json and its three role
documents together, so the unchanged facing and labels documents are parsed again. About a fifth
is comparing the set's 300 placements. Caching each role document's parsed rows by digest would
remove the first part.

### Addendum 2026-11-27: what lane A built differs from decisions 3, 5, 7 and 15

Decisions 3, 5, 7 and 15 are kept. Building them changed these details; the code is the source
of truth, and this addendum records where it departs from the text above.

- **Decision 5, repair.** `fsck(root, repair=True, confirm=True, at=...)` needs the caller's
  time `at`, keyword-only and with a time zone; a repair without it, or `confirm` without
  `repair`, raises `StorageError` (code `NOT_CONFIRMED`). A torn file is removed only once it was
  last modified more than `STALE_TEMP_AGE` (24 hours) before `at`, so a writer that is only
  paused keeps its file. Besides a torn last slot, a repair removes a torn `show.json` of a show
  with no slot and a torn `layout.json` of a root with no show folder: the only states those
  files reach are a first write that stopped part way.
- **Decision 7, torn slots and links.** A slot that stays empty or cut short after three rereads
  (pauses of 1, 10 and 100 ms) raises `StorageError` with code `TORN_SLOT`, not `CORRUPT`, and
  the hint names the repair call. A slot holds the revision's `changed` paths, so it can pass
  4 KB; a torn slot is one that is not one finished line of canonical JSON, found by parsing,
  never by size. A commit never raises `NO_HARDLINKS`: a link refused with `EPERM`, `EXDEV` or
  `ENOTSUP`, by the first probe or by a later link, switches that writer to exclusive creates.
- **Decision 5, gc.** `gc` moves an unreachable record or blob aside in its own folder as
  `.gc-<name>` and removes it only if it is still the file it moved and no slot landed
  meanwhile; otherwise the file goes back. A gc that stops part way leaves `.gc-` files, which
  `fsck` reports and the next confirmed `gc` puts back, freshened. A stored file counts as the
  same file across time only when its device, inode, size, modification time and change time
  are all unchanged (`durable_reads.file_identity`); an inode of 0 never proves it. Whether two
  files hold the same content is decided by their bytes alone, never by metadata.
- **Decision 3, `legacy_before`.** An upgrade writes `legacy_before` as one past the last copied
  slot whose revision ID does not recompute under the current scheme, so every later copied
  record is checked like a native one; a history with no such slot gets no `legacy_before` at
  all. The v0.4.0 fixture gets 3 (only its second revision is old-scheme), v0.3.0 gets 3, v0.1.1
  and v0.2.0 get 4.
- **Decision 3, the legacy prefix in the manifest.** `show.json` of an upgraded show holds three
  keys together, or none: `legacy_before`, `legacy_count` (`legacy_before - 1`) and
  `legacy_digest`, the SHA-256 of the canonical JSON list of the legacy slots' revision IDs in
  order. A native show's manifest holds only `id`. A manifest with some of the keys, or keys
  that disagree with each other, is `CORRUPT` on every read. `head` and `commit` check only
  that, and that `legacy_before` is at most the slot after the last, so a cold `head` stays at
  `2 log2(n) + 2` lookups. `log` (whenever it reads a legacy slot) and `tree` of a legacy
  revision check the digest once per backend; `fsck` checks it over the slots its walk reads and
  reports `BAD_RECORD` on `show.json`. A `legacy_before` moved within the legacy range or onto
  the slot after the last, or a legacy record swapped for another revision, is refused. Layout 2
  is unreleased, so the keys need no migration.
- **Decision 15, the upgrade.** `upgrade_repository` refuses a target that names the source in
  another case, Unicode normalization, through a symlink or `..`, by comparing files, not
  spellings. It refuses a symlink or special file anywhere it lists the source. It lists the
  source again and reads every head again just before it places `layout.json`, and raises
  `StorageError` (code `CONFLICT`) when a writer changed anything. It carries `<root>/assets/`.
  It links only blobs and assets the current user owns, since a later commit that reuses a blob
  sets its modification time; it copies the others. It lists the revision fields it drops (the
  v0.1 and v0.2 authorship keys) in `ShowUpgrade.dropped`. On a layout-1 root, `FileRepository`
  writes, `FileAssetStore.put`, `fsck` and `gc` all raise `VersionError` (code `OLD_LAYOUT`)
  whose hint is the runnable upgrade call.
- **Correction 2026-11-27, decision 15.** Comparing files covers only the folders of the target
  that exist. A target that spells `..` or `.` below a folder that does not exist yet, or runs
  through a symlink to nothing, is refused by its spelling (`StorageError`, code `BAD_PATH`)
  before anything is written, even where it would land outside the source: creating the missing
  folders would let the file system follow `..` into the source.
- **Correction 2026-11-27, decision 15.** Only `..` is refused below a missing folder. A `.`
  never reaches the check: `x/./y` names `x/y`, and the upgrade creates and writes `x/y`.

### Addendum 2026-11-27: what the archive built differs from decisions 6 and 16 and D9

Decisions 6 and 16 and D9 are kept. Building the archive changed these details; the code is the
source of truth.

- **D9, the ZIP reader and writer.** The archive is a ZIP, but `e8s.show.archive_zip` writes and
  reads it by hand instead of through `zipfile`. `zipfile` reads the whole central directory into
  objects before any cap applies, walks it by size rather than by the entry count, and accepts
  overlapping entries, bytes before the first entry, comments and ZIP64. The reader accepts the
  one canonical form the writer emits: stored entries in ascending name order, the ZIP epoch as
  every time, no flag, no extra field, no comment, entries that tile with no gap or overlap. It
  checks the end record and the entry count before it walks the central directory, each entry's
  size cap before it reads the entry, and every CRC. The bytes equal what `zipfile` writes given
  the same fixed fields, and `zipfile` opens every archive.
- **Decision 16, limits.** An archive holds at most 65,534 entries and 2,147,483,647 bytes, so
  no field needs ZIP64. A revision adds a slot and a record, so at most 32,766 revisions fit and
  about 20,000 to 30,000 in practice, as blobs add entries: below `MAX_REVISIONS` (100,000).
  Export refuses a longer history before it reads it. An archive holds only a history whose
  every revision ID recomputes, under a show ID a new show may take; a show with a hyphenated ID
  or older-scheme IDs is refused at export with a message saying to save its head as a new show.
  None of the four released fixtures (v0.1.1, v0.2.0, v0.3.0, v0.4.0) exports after its upgrade.
- **Decision 16, import.** `import_archive` checks every entry before it writes anything, then
  commits each revision through the backend. It is resumable and idempotent, not atomic: a
  backend has no commit of several revisions. An import that stops leaves the revisions that
  landed, a complete shorter history; importing the same archive again finishes it, and an
  import into a show that holds the whole history writes nothing. Any other show under the ID is
  refused with `ConflictError`. The checks prove an archive consistent, not authentic: a
  self-consistent forged history imports, and no digest covers the show ID in the manifest.
- **Decision 6, the stale-temp cut-off.** A commit removes temp files modified more than
  `STALE_TEMP_AGE` before the earlier of its own time and the file system's now, clamped to the
  earliest time a datetime holds. The library reads no clock: the file system's now is the
  modification time of an empty temp file the commit writes, only when some temp file is old
  enough by the caller's time. A time far ahead of the file system's clock, as an imported
  archive may carry, no longer removes other writers' fresh temp files, and a time within a day
  of year one no longer overflows.

### Addendum 2026-11-28: torn-file rereads wait up to 3.6 s

Decision 7 is kept. The rereads the 2026-11-27 addendum lists (three, pausing 1, 10 and 100 ms)
were likely too short: on a loaded `windows-2022` runner a concurrent commit of a blob failed
with `CORRUPT` ("blob bytes do not match their digest") instead of a `ConflictError` naming the
winner (`test_one_winner_without_hard_links`, trial 98 of 200). The code allows one cause: the
writer without hard links that created the blob was held back for more than 0.111 s before
writing it, which reproduces the failure on macOS with a 0.3 s stall. The same gap failed a
commit meeting another writer's empty layout file, manifest or record with `CORRUPT`, and its
empty slot with `TORN_SLOT`.

- A file a writer may still be filling is now reread six times while it looks torn, pausing
  1 ms, 10 ms, 100 ms, 0.5 s, 1 s and 2 s, 3.611 s in all: the layout file, manifests, blobs and
  records a commit checks, and the last slot wherever it is read (commits, `head`, `tree`, `log`
  and `fsck`).
- `log` and `fsck` read a slot before the last once: a later slot lands only after its writer
  read that one whole, so no writer can still be filling it. They report a damaged earlier slot
  without a pause, so `fsck` over many damaged slots pays the pauses at most once per show for
  slots. `tree` and the legacy-prefix check still reread any slot they read; each stops at the
  first damaged one.
- A file still torn after the last pause is reported as before, so a stopped writer's file fails
  closed with the same code and hint until `fsck` or `gc` removes it. Such a read now takes
  3.6 s instead of 0.1 s before it fails.
- A writer held back for longer than 3.6 s still fails a concurrent commit closed. Without hard
  links no reader can tell a paused writer from a stopped one; a lock or a liveness signal would
  close the gap, and neither is part of this layout.
