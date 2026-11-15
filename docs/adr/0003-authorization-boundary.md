# ADR 0003: Authorization runs in one policy, reached through a host-issued Session

- Status: Proposed
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

Agents get ShowPy handles from their host: a `Workspace` today, sometimes a `Repository`. The
principal rules (designer-only files, pins, locks) are checked in exactly one place,
`Workspace.commit` (`showpy/show/storage.py:306-331`), and even that check is partial:

- `DocumentRepository` exposes six write methods (`transaction`, `put_show`, `put_set`,
  `put_script`, `put_paths`, `put_forms`) that take `actor: Principal` as a plain label and check
  nothing. `put_set(..., actor="agent")` moves a pinned spot; `transaction(..., actor="agent")`
  writes `/config/limits.yaml`, which project rule 4 reserves for the designer (UP-9.7-12,
  UP-9.7-13).
- `Workspace.repo` is a public attribute, so any agent holding a workspace also holds the raw
  repository and can write as `actor="designer"`.
- `open_workspace(repo, show_id, principal)` trusts whatever principal the caller names.
- Through `/show/show.json` an agent can delete sets, empty the cast and change the show ID; only
  pins and locks are compared (UP-9.7-08, UP-9.7-09, UP-9.7-10).
- `/canvas/comments.json` is agent-writable and `Comment.author` is free text, so an agent can
  post an "approved" comment signed `designer` (UP-9.7-11).
- No principal's commit is validated: `{"/show/show.json": b"garbage"}` lands and makes the show
  unloadable, and files under unknown roots are stored but never readable (UP-9.7-06,
  UP-9.7-07).

Audit decision 12 asks whether authorization belongs inside ShowPy or only at the host boundary.
Standard 6 of the audit (section 2) requires locks, pins and principal rules "enforced in one
place that every write path goes through".

## Decision

1. **One policy function owns every principal rule.**
   `showpy.show.policy.authorize(principal, before, after)` receives the stored tree before the
   write and the tree the write would leave,
   and raises `LockedError`, `SchemaError` or `StorageError`. Nothing else in ShowPy decides who
   may write what.
2. **It runs in the single write funnel.** Every write, whether it comes from `Workspace.commit`,
   `save_show` or a typed `put_*` method, reaches `DocumentRepository.transaction`, and
   `transaction` calls `authorize` before it touches the backend. Backends (memory, file and the
   Phase 3 protocol) only store bytes and never see principals.
3. **Principals come from a host-issued `Session`, not from an argument.** The host builds a
   `SessionIssuer` around its repository and calls `issuer.open(show_id, principal)`. The
   resulting `Session` is the capability: it carries the show ID and principal, exposes reads,
   `commit`, the typed `put_*` writes and `workspace()`, and holds no public reference to the
   repository or the issuer. Agents receive a `Session` or a `Workspace`, never a repository.
4. **The raw repository becomes private.** `Workspace.repo` is removed. The `actor=` keyword on
   the six repository writes and the `principal` argument of `open_workspace` are deprecated in
   v0.3.0: they still work, they now run the same policy for the named actor (so
   `actor="agent"` is refused exactly as a session would be), and they warn. They are removed in
   v0.5.0, after which the write methods exist only on `Session`.
5. **What the policy enforces** (the ownership table itself is ADR 0004):
   - **Every principal:** paths must be under a known root (`/show`, `/time`, `/sets`,
     `/transitions`, `/canvas`, `/config`, `/publish`, `/renders`, `/exports`, `/lib/drafts`);
     the tree after the write must decode into a valid `Show` when it holds show documents.
   - **Agents:** may not write designer paths (`/config/limits.yaml`, `/config/waivers.yaml`,
     `/canvas/selection.json`, `/publish/claims.json`); may not change designer-owned show
     fields (show ID, title, pack, coordinates, cast, dependencies, pins); may insert new sets
     but may not remove, reorder or rename existing ones; may not touch pinned or locked items
     (`locks.require_unlocked`).
   - **Comments:** a comment added or changed by an agent must have `author == "agent"`; comments
     by anyone else must come through unchanged. The session stamps the author; agents cannot
     choose it.
   - **Designer and system:** the validity rules only. `force=True` stays a designer-only
     argument of the pure edit functions, not a storage bypass.

```
host ── SessionIssuer(repo) ── open(show_id, principal) ──► Session ──► Workspace
                                                              │
                                   save_show / put_* / commit │
                                                              ▼
                         DocumentRepository.transaction ── authorize(principal, before, after)
                                                              │
                                                              ▼
                                       RepositoryBackend (memory, file): bytes only
```

## Alternatives

- **Enforce only at the host boundary.** Rejected: agents already hold library handles, every
  consumer would re-implement the same rules, and the audit found six bypasses inside ShowPy's own
  public API. Project rule 6 asks for one enforcement point, and the host cannot see pins or locks
  without decoding the show the way ShowPy does.
- **Keep enforcement in `Workspace` only (today).** Rejected: the six repository writes skip it
  (UP-9.7-12, UP-9.7-13), and `Workspace.repo` hands any agent the unchecked path.
- **Enforce inside each storage backend.** Rejected: pins and locks need the decoded show, which
  lives above storage. Every new backend (SQLite, object store in Phase 3) would re-implement
  authorization, and the conformance kit would have to test it per backend.
- **Signed principal tokens (HMAC) on each call.** Rejected: inside one process the signing key
  is as reachable as the repository, so a token adds key management and no security. Revisit when
  sessions cross a process boundary.
- **Agents never write; every agent change is a `Proposal`.** Rejected for Phase 1: proposals are
  not persisted until Phase 3, and a designer accept step per agent edit stalls the agent loop.
  The policy above keeps proposals as the route for designer-owned changes only.
- **Remove the `actor=` writes in v0.3.0 without a deprecation window.** Rejected: consumers pin
  tags, and the master plan promises two minor releases of warnings. The window does not reopen
  the hole, because the deprecated path runs the same policy; only a caller that lies with
  `actor="designer"` gains anything, and that caller must already hold the raw repository.

## Consequences

- One function to review, test and fuzz for every authorization question. The stateful tests of
  Phase 3 drive both `Workspace` and `Session` against it.
- Public API changes (v0.3.0, called out in the release notes): new `Session`, `SessionIssuer`
  and `Session.workspace()`; `Workspace.repo` removed; `actor=` and `open_workspace(principal)`
  deprecated.
- Commits that touch show documents decode the before and after trees: about 2 x 70 ms at 300
  spots x 60 sets (audit 9.7 measurements) until Phase 3 decodes per document. Writes outside
  show documents (renders, exports, drafts) skip the decode.
- **Limitation, stated plainly:** CPython cannot hide an attribute from code running in the same
  process. A hostile agent with arbitrary Python in the host process can reach the repository
  through private attributes or the garbage collector. ShowPy's guarantee is that no public API
  call can bypass the policy; isolating untrusted agent code (the consumer's bubblewrap sandbox,
  which also blocks importing `showpy.show.repositories`) remains the host's job.

## Numbers

| Measure                                                | Today            | After            |
| ------------------------------------------------------ | ---------------- | ---------------- |
| Write paths that check principal rules                 | 1 of 7           | 7 of 7           |
| Repository writes that trust a self-declared `actor`   | 6                | 0 (after v0.5.0) |
| P0 #4 and #5 ledger tests passing                      | 0 of 8           | 8 of 8           |
| Show-document commit decode cost, 300 spots x 60 sets  | 0 ms (unchecked) | about 140 ms     |
| Designer-only paths enforced for agents on every write | 4 of 4 in 1 path | 4 of 4 in 7      |

## What would change our mind

- Sessions served over RPC or HTTP: then a signed, expiring session token is worth its key
  management, and `authorize` takes the verified token instead of a `Session` object.
- Teams with several designers needing different rights: the principal becomes a role plus a
  user ID, and the ownership table gains per-user grants.
- The policy decode measured above 20 ms per edit after Phase 3's per-document decoding: then
  `authorize` works on changed documents only.

## References

- Audit `docs/upgrade-plan.md`: section 2 standard 6; section 3 rows 4 and 5; section 9.7 items 4
  to 7 and upgrade 5; section 10 decision 12.
- Ledger tests unblocked: UP-9.7-06, UP-9.7-07, UP-9.7-08, UP-9.7-09, UP-9.7-10, UP-9.7-11,
  UP-9.7-12, UP-9.7-13.
- Code: `showpy/show/storage.py`, `showpy/show/repositories.py`, `showpy/show/files.py`,
  `showpy/show/locks.py`.
- Related: ADR 0004 (ownership table and document split).
