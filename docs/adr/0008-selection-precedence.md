# ADR 0008: `&` never mixes with `+` or `-` without parentheses; named selections store ordered IDs

- Status: Proposed
- Date: 2026-11-18
- Deciders: the maintainer (abj360)

## Context

The selection grammar (`showpy/cast/select.py`) is external text: designers type it, agents write
it, and two stored models keep it (`Zone.applies_to` in the sheet, `FacingOverride.entities` in
the show). Its three operators have one precedence and combine left to right. Measured on
`main` at `3824263` with the four-spot example show:

| Expression      | Result today | Python set reading (`&` above `\|`, `-` above both) | SQL reading (`INTERSECT` first)      |
| --------------- | ------------ | --------------------------------------------------- | ------------------------------------ |
| `T1 + T2 & T2`  | `T2`         | `T1, T2`                                            | `T1, T2`                             |
| `T2 & T2 + T1`  | `T2, T1`     | `T2, T1`                                            | `T2, T1`                             |
| `T1 + T2 - T1`  | `T2`         | `T1, T2` (`T1 \| (T2 - T1)`)                        | `T2`                                 |
| `all - T1 & T2` | `T2`         | `T2` (`(all - T1) & T2`)                            | `T1, T2, T3, T4` (`all - (T1 & T2)`) |

So the same text means different things under the three conventions a reader is likely to bring.
The audit (section 9.3, algorithm upgrade 3) asks for conventional precedence or for rejecting
mixed operators. Phase 1 already removed one misreading: an operator glued to a word (`T1+T2`)
raises `SchemaError` with its position, because `T1-T2` is a range (decided 2026-11-12). Spaces
around operators are now required, which leaves only the precedence question.

Named selections (`define_selection`, `Cast.selections`) store the resolved IDs as an ordered
tuple since Phase 1, replace an existing name silently, and accept `-` in names, which ADR 0006
forbids. The audit asks for "a choice between frozen lists and live expressions" and a
`replace=False` guard.

Audit decision 3 asks for the precedence.

## Decision

1. **`+` and `-` share one precedence and combine left to right**, as in arithmetic:
   `T* + B* - T3` is `(T* + B*) - T3`. This is the idiom designers already write, and arithmetic is
   the reading every user brings to `+` and `-`.
2. **`&` may chain with itself** (`tag:a & T* & front`), **but an expression that mixes `&` with
   `+` or `-` at one parenthesis level is refused** with `SchemaError` (code `MIXED_OPERATORS`),
   naming the position of the second operator kind and a hint showing both parenthesized
   readings: `write (T1 + T2) & T2 or T1 + (T2 & T2)`.
3. **Stored selectors keep their meaning.** Format v2's `selectors` migration step (ADR 0007)
   rewrites every stored v1 selector to the v2 grammar by adding the parentheses that encode the
   v1 left-to-right reading, and glued operators get spaces. The rewrite is reported. No stored
   show changes which spots it selects.
4. **The parser becomes iterative**, so depth is bounded by `MAX_DEPTH` without recursion, and it
   evaluates on integer index sets over the universe instead of building a `Selection` per
   operator. Results keep today's order rule: the order of the left operand, then new IDs from
   the right operand in their order.
5. **Named selections store an ordered, deduplicated tuple of spot IDs** (frozen lists). The
   expression that made them is kept only as display text, `Selection.description`.
   `define_selection(show, name, sel, *, replace=False)` raises `SchemaError` (code
   `NAME_TAKEN`) for an existing name unless `replace=True`. Names follow ADR 0006 (no `-`);
   format v2 renames stored names that fail it (`front-four` becomes `front_four`).
6. **`Selection` behaves like a value.** Equality and hashing use `ids` only, not `description`;
   it stops overriding `BaseModel.__iter__` (`selection.ids` is the iterator), which removes one
   `type: ignore`; its operators accept any iterable of IDs; constructing one from a bare string
   raises instead of splitting it into characters.

## Alternatives

- **Conventional precedence, `&` above `+` and `-`.** Rejected: it silently changes the result of
  every stored mixed expression (`T1 + T2 & T2` goes from `T2` to `T1, T2`), and readers still
  disagree about `-` against `+` (Python binds `-` tighter; arithmetic does not). A wrong
  selection moves real performers; an error costs one pair of parentheses.
- **Reject every mix of two different operators, `+` with `-` included.** Rejected: it refuses
  `T* + B* - T3`, the most common designer idiom, and arithmetic already gives it one reading.
- **Keep left to right.** Rejected: `T1 + T2 & T2` reads as `T1, T2` to anyone who knows Python
  or SQL, and the library answers `T2` without warning.
- **Live named selections** (store the expression, resolve on use). Rejected: a stored
  expression changes meaning when the cast changes (a new `T9` joins `T*` in every saved
  selection), references between names can form cycles, and rename rewriting would have to
  parse and reprint expressions. A host that wants live behaviour stores the expression itself
  and calls `select` on use.

## Consequences

- Code or prompts that send mixed expressions without parentheses get `MIXED_OPERATORS` after
  v0.4.0. The hint shows the fix; agents can be prompted with the rule in one line.
- `define_selection` callers that relied on silent replacement pass `replace=True`.
- The grammar fuzz target (`tests/fuzz/test_fuzz_select.py`) gains the property that every
  accepted expression parses identically after adding redundant parentheses, and that the
  iterative and the old recursive evaluators agree on every expression the new grammar accepts.

## Numbers

| Measure                                                                                  | Value                                                                |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Operators and levels today                                                               | 3 operators, 1 level, left to right                                  |
| Expressions in the library, tests and docs that mix `&` with `+`/`-` without parentheses | 1 (`tests/unit/cast/test_select.py:162`, which pins today's reading) |
| Stored selector fields migrated                                                          | 2 (`Zone.applies_to`, `FacingOverride.entities`)                     |
| `type: ignore` removed (`Selection.__iter__`)                                            | 1 of 8 in the library                                                |
| Ledger tests closed by this decision and its tasks                                       | UP-9.3-34, UP-9.3-35, UP-9.3-83                                      |

## What would change our mind

- Designers or agents write `&` mixed with `+` often enough that the error becomes friction
  (measured from host telemetry or support reports): then adopt `&` above `+`/`-` in a later
  format version, with a migration step that parenthesizes stored text the same way.
- Phase 5 adds operators (complement, symmetric difference): each new operator joins the
  "must be parenthesized" class unless it is `+`/`-`-like.

## References

- Audit `docs/upgrade-plan.md`: section 9.3 "Selection grammar", "Algorithm upgrades" item 3,
  "API and design changes"; section 10 decision 3.
- Release notes `docs/releases/v0.3.0.md`: glued operators refused (study-guide bug 10).
- Code: `showpy/cast/select.py` (`_Parser`), `showpy/core/selection.py`,
  `showpy/cast/spots.py` (`define_selection`), `showpy/sheet/models.py` (`Zone`),
  `showpy/choreo/models.py` (`FacingOverride`).
- Related: ADR 0006 (ID pattern), ADR 0007 (format v2 migration).
