# ADR 0005: Cast edits may rename pinned spots, but never change a pinned picture without force

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

Project rule 6 says functions that edit sets, forms or paths raise `LockedError` on pinned items
unless the caller passes `force=True`. The cast edits in `showpy/cast/spots.py` ignore that rule:

- `add_spot`, `add_spots_along`, `fill_spots` and `duplicate_spots` write placements into pinned
  sets (UP-9.3-88 to UP-9.3-91).
- `regroup` and `relabel` change the IDs of pinned spots and of locked form members
  (UP-9.3-92, UP-9.3-93).
- Of the cast edits, only `delete_spots` takes `force` (UP-9.3-94).
- The rename machinery (`rewrite_spot_ids`) skips `Show.pins`, so after a relabel a pin names a
  spot that no longer exists (UP-9.3-79), and a forced delete leaves the pin behind (UP-9.3-80).

Audit decision 11 asks for the policy and suggests "renames allowed, positions protected". The
ledger tests for regroup and relabel were written policy-neutral: each accepts either a
`LockedError` or a successful rename with the pin intact.

## Decision

1. **A pin protects positions and pictures, not labels.**
   - A **pinned spot** keeps its placement in every set: point, facing, `on_field`, form slot.
   - A **pinned set** keeps its on-field picture: no placement may be added on-field, moved,
     removed or switched on or off field, and its counts stay.
   - A **locked form** keeps its geometry and the order of its members.
2. **Renames are allowed through pins and locks.** `relabel`, `regroup` and any future
   `rename_spots` may change the ID of a pinned spot or of a locked form member. Every reference
   follows through the one rename choke point, `rewrite_spot_ids`: pins, form members, path
   rows, facing overrides, zones (show and sheet), selections, notes, comments and published
   claims. The lock check runs on `before` rewritten through the rename mapping, so a rename on
   its own never counts as touching a locked item.
3. **Additions that leave pinned pictures alone are allowed.** A new spot is placed off-field in
   every set before its entrance. If its first on-field set comes after every pinned set, or it
   is off-field in every pinned set, no pinned picture changes and no `force` is needed. A spot
   that would stand on-field in a pinned set raises `LockedError` naming the set.
4. **Every cast edit that can change a placement takes keyword-only `force: bool = False`:**
   `add_spot`, `add_spots_along`, `fill_spots`, `duplicate_spots` and `delete_spots`. Edits that
   cannot change a placement (`relabel`, `regroup`, `edit_group`, `set_shape`,
   `set_claimable`, `define_selection`) take no `force`, because there is nothing to force.
   `delete_spots(force=True)` also drops the pins on the deleted spots.
5. **Who may call these is the host's business (ADR 0003).** ShowPy enforces no permissions; a
   host that keeps agents away from the cast refuses their cast edits itself. This ADR governs the
   pure edit functions, which apply to every caller alike.
6. **The ledger tests tighten once this is accepted.** UP-9.3-92 and UP-9.3-93 keep their names;
   the fix drops the `except LockedError: return` branch, so they assert the rename-with-pin
   outcome only.

## Alternatives

- **Pins freeze labels too (renames need `force`).** Rejected: inserting a section and
  relabelling is routine. Designers would learn to pass `force=True`, which also lifts position
  protection, so the pins they rely on would stop protecting anything.
- **Cast edits ignore pins entirely.** Rejected: that is today's behavior, and it silently adds
  performers to finished sets (UP-9.3-88 to UP-9.3-91), against rule 6.
- **Refuse every addition while any set is pinned.** Rejected: one pinned opener would block
  adding a performer who enters in the closer. The picture rule refuses exactly the additions
  that change a pinned set.
- **Give every cast edit a `force` flag, including renames.** Rejected: a flag that does nothing
  misleads; a reader assumes it lifts something.

## Consequences

- Five public signatures gain or keep a keyword-only `force` (four new). Release notes for
  v0.3.0 list them.
- `rewrite_spot_ids` must reach pins and sheet zones (UP-9.3-79, UP-9.3-81); a missed reference
  now breaks a lock check instead of leaving a silent dangling ID.
- Footprint changes (`set_shape`) stay allowed on pinned spots. A rectangle footprint can create
  a collision in a pinned set; validation and the external checker report it, the lock does not.
- Performers memorize labels. A rename after publishing is carried by claims (UP-9.5-19, Phase
  2); if designers want labels frozen after publishing, that is a new pin target, not this rule.

## Numbers

| Measure                                                  | Today | After  |
| -------------------------------------------------------- | ----- | ------ |
| Cast edits that bypass pins (audit 9.3)                  | 6     | 0      |
| Cast edits with a `force` parameter                      | 1     | 5      |
| References `rewrite_spot_ids` misses (pins, sheet zones) | 2     | 0      |
| P0 #13 cast ledger tests unblocked                       | 0     | 6 of 6 |

## What would change our mind

- Designers report pinned labels changing under them after publication: add a `labels` pin
  target that refuses renames without `force`.
- A real show needs to add an on-field performer to a pinned set routinely: then
  `add_spot(force=True)` is the path, and we document it rather than loosening the rule.
- Footprint-induced collisions in pinned sets turn up in practice: then `set_shape` joins the
  `force` list.

## References

- Audit `docs/upgrade-plan.md`: section 3 row 13; section 9.3 "Locks and pins are not enforced on
  cast edits" and "Rename and delete do not reach every reference"; section 10 decision 11.
- Ledger tests unblocked: UP-9.3-88, UP-9.3-89, UP-9.3-90, UP-9.3-91, UP-9.3-92, UP-9.3-93,
  UP-9.3-94; with UP-9.3-79 and UP-9.3-80 as the rename and delete companions.
- Code: `showpy/cast/spots.py`, `showpy/cast/rewrite.py`, `showpy/show/locks.py`.
- Project `CLAUDE.md` rule 6.
