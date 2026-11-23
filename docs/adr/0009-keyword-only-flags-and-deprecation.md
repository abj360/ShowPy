# ADR 0009: Boolean flags are keyword-only; renamed or moved names warn for two minor releases

- Status: Accepted (2026-11-19)
- Date: 2026-11-18
- Deciders: the maintainer (abj360)

## Context

**Positional booleans.** A call such as `edit(form, True)` or `rebuild(show, "1", True)` does not
say what `True` means, and swapping two flags is a silent wrong result. Ruff's FBT rules measure
the problem on `main` at `3824263`:

| Rule                                    | Findings in `showpy/` |
| --------------------------------------- | --------------------- |
| FBT001 (positional `bool` parameter)    | 107                   |
| FBT002 (positional `bool` default)      | 73                    |
| FBT003 (positional `True`/`False` call) | 4 (10 with `tests/`)  |

The 107 parameters sit in 97 functions; 61 of those functions are public names in
`showpy/_api_table.py` (among them `edit`, `rebuild`, `apply_match`, `add_set`, `remove_set`,
every path editor in `choreo/paths.py` and every transform in `geo/transforms.py`). 68 of the
107 parameters are `force`, the flag that overrides locks and pins (project rule 6): a misplaced
positional `force=True` is exactly the edit rule 6 exists to stop. Phase 1 already made `force`
keyword-only on the functions it touched (cast additions, `delete_spots`, `two_part`, the route
builders, `apply_event`). The audit counted 114 positional booleans on v0.1.1.

**Deprecation.** The audit's standard (section 2 item 8) says deprecations warn for two minor
releases. The library has the pieces but no policy:

- `DeprecatedWarning` exists and subclasses `UserWarning`; the library raises it nowhere.
- Tests run with `filterwarnings = ["error"]`, so a library call to a deprecated name fails the
  suite.
- `showpy.core.errors.ValidationError` shares its name with pydantic's. Five library modules
  import pydantic's as `PydanticValidationError` to tell them apart, and a consumer catching
  `ValidationError` must know which one it imported. ShowPy raises its own at 6 sites
  (`export/publish.py` 5, `export/positions.py` 1).
- Phase 2 moves public names: the 26 classes, aliases and constants that leave
  `showpy/choreo/models.py` when it is split, the `Set` handling of 28 functions that leave
  `showpy.geo`, `Form.geometry` (becoming `Form.recipe`, ADR 0007) and the renamed
  `ValidationError`.

Audit decision 7 asks whether flags become keyword-only; the master plan pairs it with the
deprecation policy.

## Decision

1. **Every boolean parameter in `showpy/` is keyword-only**, public or private. Ruff FBT001,
   FBT002 and FBT003 are enabled for `showpy/` and `scripts/` with no per-file exceptions.
   `tests/` keeps FBT001 and FBT002 off (test helpers are not API) and FBT003 on.
2. **The keyword-only change breaks at v0.4.0, with no positional fallback.** A positional call
   raises Python's `TypeError` ("takes 2 positional arguments but 3 were given"). Release notes
   list all 61 public functions and their flags. mypy and pyright report every such call in a
   consumer before it runs.
3. **Deprecation policy, before 1.0.** A public name that is renamed, moved or removed in minor
   release `0.N` keeps working through `0.N` and `0.N+1`, emits `DeprecatedWarning` on use, and is
   removed in `0.N+2`. Names deprecated in v0.4.0 are removed in v0.6.0.
4. **Deprecation policy, from 1.0.** A name deprecated in `1.N` warns for at least two minor
   releases and is removed only in the next major release.
5. **How a deprecation is written**:
   - A function or class alias uses `warnings.deprecated` (PEP 702) with
     `category=DeprecatedWarning`, so type checkers flag the use statically as well.
   - A moved module attribute (class, constant, type alias) is served by a module-level
     `__getattr__` that warns and returns the new object, built by one helper,
     `showpy.core.deprecation.moved(old_module, names, new_module, since, remove)`.
   - A renamed model field keeps a read-only property under the old name that warns, and the
     v1 reader (ADR 0007) maps the stored key; the model never accepts the old name on input.
   - Every deprecation is a row in `showpy/_api_table.py` with `since` and `remove` versions. A
     test fails when the package version reaches a row's `remove`, so removal cannot be forgotten,
     and the API snapshot marks deprecated names.
6. **`DeprecatedWarning` subclasses `DeprecationWarning`** instead of `UserWarning`. Python shows
   it by default in `__main__` and test runners, which is where a consumer's developer sees it.
7. **`ValidationError` becomes `ShowValidationError`**, with code `VALIDATION` unchanged so code
   that switches on `error.code` keeps working. `showpy.ValidationError` and
   `showpy.core.errors.ValidationError` stay as deprecated aliases of the same class, so
   `except ValidationError` still catches it through v0.5.x.
8. **Signatures that change type, not name** (a positional flag made keyword-only, a parameter
   that now rejects NaN) are breaking changes recorded in release notes and the changelog, not
   deprecations.

## Alternatives

- **A decorator that accepts positional booleans with a warning for two releases.** Rejected:
  61 wrapped public functions, signatures that differ between runtime and the type checker for
  two releases, and a positional `force=True` keeps working during the window, which is the
  hazard. The 10 positional boolean literals in this repository's own tests suggest consumers
  hold few such calls, and type checkers find them all.
- **Keyword-only for `force` only (68 of 107).** Rejected: `ordered`, `keep_aspect`, `closed` and
  `flip_y` are as unreadable at a call site, and two rules are harder to keep than one.
- **Keep `UserWarning`.** Rejected: `UserWarning` shows to end users of an app built on ShowPy,
  who cannot act on it; `DeprecationWarning` shows to the developer who can.
- **Rename to `IntegrityError`.** Rejected: SQLAlchemy and `sqlite3` export `IntegrityError`, and
  a host app that stores shows in a database would meet the same clash this rename removes.
- **No aliases before 1.0** (semantic versioning allows any break in `0.x`). Rejected: consumers
  pin tags and upgrade between phases; one release of warnings turns a hard break into a
  to-do list, at the cost of one table row per name.

## Consequences

- Every public call site with a positional boolean in a consumer breaks once, at v0.4.0, with a
  clear `TypeError`; the release notes table is the migration guide.
- `showpy/core/deprecation.py` is new and small; the moved-name aliases of Phase 2 add about 60
  rows to the API table, all removed in v0.6.0.
- `warnings.deprecated` exists from Python 3.13 (verified: absent in 3.12.14, present in
  3.13.15). With the 3.13 floor of ADR 0011 the standard library provides it; if the
  maintainer keeps 3.12, the helper uses `typing_extensions.deprecated` and `typing-extensions`
  becomes a declared runtime dependency (it is installed today only through pydantic).

## Numbers

| Measure                                       | Today | After v0.4.0                       |
| --------------------------------------------- | ----- | ---------------------------------- |
| Positional boolean parameters in `showpy/`    | 107   | 0, enforced by FBT001/FBT002       |
| Public functions whose signature changes      | —     | 61                                 |
| `force` parameters still positional           | 68    | 0                                  |
| Library uses of `DeprecatedWarning`           | 0     | one per deprecated name (about 60) |
| Modules aliasing pydantic's `ValidationError` | 5     | 0 required (the clash is gone)     |

## What would change our mind

- A consumer reports a large body of positional boolean calls that type checking cannot reach
  (untyped scripts, notebooks): then a v0.4.x patch adds the warning decorator to the named
  functions only, for one release.
- Python adds a standard way to deprecate a parameter's positional use: adopt it for later
  changes.

## References

- Audit `docs/upgrade-plan.md`: section 2 item 8, section 4.2, section 4.5, section 6
  ("Policies"), section 9.3 "API and design changes", section 10 decision 7.
- PEP 702 (`warnings.deprecated`), PEP 565 (showing `DeprecationWarning` in `__main__`).
- Code: `showpy/core/errors.py` (`ValidationError`, `DeprecatedWarning`), `showpy/_api_table.py`,
  `scripts/gen_public_api.py`, `pyproject.toml` (`[tool.ruff.lint]`, `filterwarnings`).
- Related: ADR 0007 (format v2 field renames), ADR 0011 (Python floor).

## Addendum 2026-11-19: deprecating a class alias

A renamed class keeps a same-class alias. It warns at runtime through a module `__getattr__`
and is visible to type checkers as a plain alias (`ValidationError = ShowValidationError` under
`TYPE_CHECKING`). `warnings.deprecated` on an alias would create a subclass, and `except OldName`
would stop catching the renamed class. Functions and new wrapper classes still use
`warnings.deprecated`.

## Addendum 2026-11-23: waived for the names that leave the library

The library has no users, so the two-release warning of this ADR is waived for the names on the
leaving timeline of ADR 0021 and for the deprecated aliases, all of which go in v0.4.0 (ADR 0021,
addendum 2026-11-23); the rule stands for later releases once there are users.
