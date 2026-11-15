# ADR 0017: A show keeps the sheet it was created on

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

`showpy.sheet.replace.replace_sheet(show, sheet, mapping, force)` moves a whole show onto a new
sheet: it fits one affine map from shared landmarks (or the origin, or a size ratio) and applies
it to every placement, form curve, path waypoint, route and zone.

The audit found it unsafe (P0 #11). A high-school to NFL field swap moves spots by up to 2.57 m
without a warning, because the hash marks share names but not meaning (UP-9.4-01, UP-9.4-43);
`scale` mapping divides by zero on a zero-size sheet (UP-9.4-07). The first Phase 1 plan fixed
it with a RANSAC fit on structural landmarks and a 0.25 m RMS refusal.

The maintainer ruled on 2026-11-15 that a show cannot move to a different field. Drill written
for one field is designed for that field; carrying it to another is a new design, not an edit.

## Decision

1. **A show's sheet is fixed for its lifetime.** Every set of a show is on the same sheet, as
   today (`Show.sheet`), and no public function swaps it.
2. **`replace_sheet` and `SheetReplaceReport` are removed from the public API** in v0.3.0
   (breaking, in the API snapshot diff and the release notes), with the module-level
   `SheetMapping` and `RESTRICTED_ZONE_KINDS`.
   `showpy/sheet/replace.py` keeps only `similarity_fit`, a general least-squares similarity fit
   between matched points that stays public.
3. **Sheet customizations stay editable.** Zones, landmarks and the coordinate display are part
   of the same field and can change; they never move stored positions (project rule 1).
4. **Moving drill to another field means a new show.** The designer creates a show on the new
   sheet and copies forms into it. ShowPy has no dedicated copy function today; a form is plain
   data, and its members must exist in the new show's cast. A helper that copies forms between
   shows is a separate feature, decided when it is needed.

## Alternatives

- **Fix `replace_sheet` (RANSAC on structural landmarks, refuse above 0.25 m RMS unless
  forced; the first Phase 1 plan).** Rejected by the maintainer: no show should change fields,
  so a better fit solves a problem the product does not have.
- **Keep `replace_sheet` but only for sheets of the same template family.** Rejected: that case
  moves nothing, so the function would do no work.
- **Per-set sheets.** Rejected: performers do not change fields between sets.

## Consequences

- Removes about 300 lines (`showpy/sheet/replace.py` minus `similarity_fit`), their tests, and the
  `landmarks`/`origin`/`scale` mapping modes.
- Deleted as out of scope, with this ADR as the reason: UP-9.4-01, UP-9.4-07, UP-9.4-43. Kept and
  fixed: UP-9.4-02 to UP-9.4-06 (`similarity_fit` refuses mirrored and degenerate input).
- The section 5.2.3 scenario drops its sheet-replacement step.
- Consumers calling `replace_sheet` must build a new show instead.

## Numbers

| Measure                                         | Today              | After   |
| ----------------------------------------------- | ------------------ | ------- |
| Worst silent spot shift on a field swap         | 2.57 m (HS to NFL) | no swap |
| Public names removed                            | —                  | 2       |
| Ledger tests: deleted, fixed (`similarity_fit`) | —                  | 3, 5    |

## What would change our mind

- Designers routinely re-staging one show at venues with different fields, with drill that should
  survive the move unchanged: then a copy-forms helper comes first, and a whole-show swap only if
  that proves too coarse.

## References

- Audit `docs/upgrade-plan.md`: section 3 row 11; section 9.4.
- Code: `showpy/sheet/replace.py`, `showpy/sheet/models.py` (`SheetReplaceReport`),
  `showpy/_api_table.py`.
- Supersedes ADR 0001 rows 6 and 22 (where `replace_sheet` lives and what it reports).
