# ADR 0003: Authorization and authorship belong to the host; ShowPy validates

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

Agents get ShowPy handles from their host: a `Workspace` today, sometimes a `Repository`. ShowPy
carries a half-built permission model. A `Principal` (`designer`, `agent`, `system`) travels with
every write, and some places act on it:

- `Workspace.commit` refuses agent writes to four designer files (`DESIGNER_PATHS` in
  `showpy/show/files.py`) and agent changes to pinned items (`_require_allowed` and
  `_require_pins_kept`, `showpy/show/storage.py:306-331`).
- `showpy/show/collab.py` refuses agent canvas events that change pins, refuses agents accepting
  proposals, and applies designer canvas events with `force=True`.
- The six `DocumentRepository` writes (`transaction`, `put_show`, `put_set`, `put_script`,
  `put_paths`, `put_forms`) take `actor: Principal` as a plain label and check nothing, and
  `Workspace.repo` is public, so any holder can write as `actor="designer"`.

The audit counted six bypasses (UP-9.7-08 to UP-9.7-13): through `/show/show.json` an agent can
delete sets, empty the cast and change the show ID; it can sign a comment as the designer; and the
repository writes skip every rule. It asked (decision 12) whether authorization belongs inside
ShowPy or only at the host boundary.

Separately, no commit is validated, whoever makes it: `{"/show/show.json": b"garbage"}` lands
and makes the show unloadable, and files under unknown roots are stored but never readable
(UP-9.7-06, UP-9.7-07). That is a data-integrity defect, not a permission one.

Project rule 8 keeps code specific to one consumer (agent sandboxes, prompts, UI) out of ShowPy.
Which principal may change which file is a decision of the product that runs agents, and the
maintainer will build it there (2026-11-15).

## Decision

1. **ShowPy enforces no permissions.** It does not decide who may write what. The host
   application (the agent product, a designer app, a script) owns every permission rule and must
   not hand untrusted code a writable repository or workspace.
2. **The partial permission code is removed, not left half-working.** In v0.3.0:
   - `DESIGNER_PATHS`, `DESIGNER_PRINCIPALS`, `Workspace._require_allowed` and
     `Workspace._require_pins_kept` are deleted;
   - `collab.py` drops its agent checks (`_require_designer`, the agent refusal in
     `accept_proposal`) and the implicit `force=event.actor != "agent"`: applying a canvas event
     takes an explicit keyword `force: bool = False` from the caller, like every other edit.
3. **ShowPy does not record who made a change** (maintainer's decision, 2026-11-15). The host
   app keeps authorship, keyed by revision ID.
   - Removed: the `Principal` type, `actor=` on the six repository writes, the `principal`
     argument of `open_workspace`, `Workspace.principal`, `Revision.actor` and
     `CanvasEvent.actor`.
   - A `Revision` keeps `id`, `parent`, `at` (date and time), `message`, `changed` (the paths it
     altered), `kind` and `target`.
   - Undo and redo become one shared stack per show: `undo` reverses the latest undoable
     revision, whoever made it. A host that wants per-user undo calls `revert(ws, revision_id, at)`
     on the revisions it knows its user made.
   - Reading a v0.1 or v0.2 repository drops the stored `actor` key from revision records and
     events; nothing on disk is rewritten.
   - Comment and note `author` fields stay: they are part of what the comment says, not a record
     of who saved it.
4. **ShowPy still guards the data, for every caller.**
   - Every commit is validated in `DocumentRepository.transaction`, the one write funnel: paths
     must sit under a known root (`/show`, `/time`, `/sets`, `/transitions`, `/canvas`,
     `/config`, `/publish`, `/renders`, `/exports`, `/lib/drafts`), and a tree that holds show
     documents must decode into a valid `Show`. This fixes UP-9.7-06 and UP-9.7-07.
   - Pins and locks stay a design rule (project rule 6): edit functions raise `LockedError` on
     pinned or locked items unless the caller passes `force=True`. They protect the designer's
     work from every caller, the designer included; they are not permissions.
5. **Tools a host can build permissions on:** `diff_show(before, after)` reports every changed
   set, spot, form and path; `Revision.changed` lists the touched paths. A host compares an
   agent's proposed show with the current one and decides.

## Alternatives

- **One policy function inside ShowPy, reached through a host-issued `Session` (the first draft
  of this ADR).** Rejected by the maintainer, 2026-11-15: the ownership rules belong to the agent
  product, which will enforce them itself, and rule 8 keeps consumer logic out of the library.
- **Keep today's partial checks.** Rejected: they look like protection and are bypassed six ways
  (UP-9.7-08 to UP-9.7-13). A half-enforced rule is worse than none, because a host may trust it.
- **Keep `actor` as a recorded label (the second draft of this ADR).** Rejected by the
  maintainer, 2026-11-15: three fixed values (`designer`, `agent`, `system`) cannot name a person
  or an agent run, ShowPy cannot verify them, and the app already knows who is acting.
- **Make every permission check opt-in through a policy hook.** Rejected for now: no second
  consumer needs it. Revisit if two hosts end up writing the same rules.

## Consequences

- Breaking for consumers (release notes, v0.3.0): agent writes that used to be refused now land
  if the data is valid, so the host must refuse them first; every write and `open_workspace`
  lose their actor argument; undo is shared per show.
- Canvas events no longer get `force` from their actor; callers pass `force=True` explicitly.
- Conflict messages and `changelog` lines name the revision, time, message and paths, not a
  person.
- The six permission ledger tests (UP-9.7-08 to UP-9.7-13) are deleted as out of scope, with this
  ADR as the reason. UP-9.7-06 and UP-9.7-07 stay and are fixed.
- Commits that touch show documents decode the after tree once: about 70 ms at 300 spots x 60
  sets (audit 9.7 measurements) until Phase 3 decodes per document. Writes outside show documents
  skip the decode.
- **Limitation, stated plainly:** ShowPy offers no protection against a caller that holds a
  repository. Isolating untrusted agent code (the consumer's bubblewrap sandbox) and deciding what
  each agent may change is entirely the host's job.

## Numbers

| Measure                                               | Today                   | After       |
| ----------------------------------------------------- | ----------------------- | ----------- |
| Write paths that branch on the principal              | 3 (workspace, 2 collab) | 0           |
| Stored fields naming who saved (`Revision`, event)    | 2                       | 0           |
| Write paths that validate the committed tree          | 0 of 7                  | 7 of 7      |
| Permission ledger tests                               | 6 open                  | 0 (deleted) |
| Validity ledger tests (UP-9.7-06, UP-9.7-07) passing  | 0 of 2                  | 2 of 2      |
| Show-document commit decode cost, 300 spots x 60 sets | 0 ms (unchecked)        | about 70 ms |

## What would change our mind

- A second host that needs the same ownership rules: then a shared, opt-in policy module earns
  its place, still outside the core write path.
- Sessions served by ShowPy itself over RPC or HTTP: the library would then own a boundary and
  must authorize at it.

## References

- Audit `docs/upgrade-plan.md`: section 2 standard 6; section 3 rows 4 and 5; section 9.7 items 4
  to 7 and upgrade 5; section 10 decision 12.
- Ledger tests fixed: UP-9.7-06, UP-9.7-07. Deleted as out of scope: UP-9.7-08 to UP-9.7-13.
- Code: `showpy/show/storage.py`, `showpy/show/repositories.py`, `showpy/show/files.py`,
  `showpy/show/collab.py`, `showpy/show/locks.py`.
- Related: ADR 0004 (stable set keys), ADR 0005 (what pins and locks protect).
