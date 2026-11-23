# ADR 0026: The public model interface

- Status: Accepted (2026-11-23), recording the maintainer's decision of 2026-11-22 as revised
  on 2026-11-23
- Date: 2026-11-23
- Deciders: the maintainer (abj360)

## Context

Every e8s data class is a frozen pydantic v2 model that inherits `FrozenModel`
(`e8s/core/types.py`). A caller therefore inherits all of pydantic's `BaseModel` methods as well
as the library's own: `model_validate`, `model_validate_json`, `model_validate_strings`,
`model_dump`, `model_dump_json`, `model_copy` and the `model_fields` lookup. Nothing says which
of them the library promises to keep.

Three problems follow:

- **The library cannot change how models are implemented.** Any change to the model layer,
  even a pydantic major version, would break every caller that reached for a pydantic method.
  pydantic itself changes these methods between minor releases: 2.11 deprecated reading
  `model_fields` from an instance.
- **Some of the methods break library rules.** `model_copy(update=...)` skips every validator,
  so it can build a `Set`, `Form` or `Publication` the library would refuse to construct.
  `FrozenModel.replace(**changes)` exists for edits, and a library rule test already bans
  `model_copy(update=)` inside `e8s/`; callers have no such guard.
- **The library itself calls them everywhere.** A caller reading the code sees `model_validate`
  and `model_dump` used freely and copies the pattern. The counts are under "Numbers".

The library already gives every model what a caller needs: attribute reads, equality, a hash
(models are frozen) and `replace(**changes)`. Converting to and from JSON is the one need it
does not cover with a name of its own.

ADR 0009 sets the deprecation policy for public names: a name deprecated in `0.N` warns through
`0.N+1` and is removed in `0.N+2`.

## Decision

1. **The public model interface is**:
   - attribute reads of the documented fields and properties;
   - `==` and `hash()`;
   - `replace(**changes)`, which returns a revalidated copy;
   - `to_json()`, which returns a JSON-safe dict;
   - `from_json(data)`, a classmethod that validates such a dict into the model and raises
     `SchemaError` on bad data.

   `to_json` and `from_json` are added to `FrozenModel`, so every model has them. They encode
   and decode the way the stored show documents already do: `to_json` is pydantic's JSON-mode
   dump, and `from_json` validates the parsed JSON.

2. **pydantic's methods are not part of the interface, and callers must not use them.** That
   covers the seven above and every other pydantic attribute (`model_config`, `model_construct`,
   `model_fields_set`, `model_json_schema` and the rest). They are not public names in the sense
   of ADR 0009, so they get no deprecation: no warning is emitted, and they go away whenever the
   model implementation changes, with no further notice. Callers replace them as follows:

   | Method                      | Use instead                                                     |
   | --------------------------- | --------------------------------------------------------------- |
   | `model_validate(data)`      | `Model.from_json(data)`; the constructor for typed values       |
   | `model_validate_json(text)` | `Model.from_json(json.loads(text))`                             |
   | `model_validate_strings`    | none                                                            |
   | `model_dump()`              | `to_json()`; attribute reads for typed values                   |
   | `model_dump_json()`         | `json.dumps(model.to_json())`                                   |
   | `model_copy(update=...)`    | `replace(**changes)`; a model is immutable, so it needs no copy |
   | `model_fields`              | none                                                            |

3. **The library never calls them on its own models.** Library code uses the interface above,
   or private helpers on `FrozenModel` where it needs a validation context, a Python-mode dump or
   an unvalidated copy. Only `e8s/core/types.py` calls pydantic's model methods on an e8s model;
   two boundary helpers that accept any pydantic model, including a caller's own
   (`canonical_value` and `validate_or_schema_error`), call them on that model. A library rule
   test scans `e8s/`, docstring examples included, and fails on any other call.
4. **The library's tests follow the same rule.** A test whose subject is not a pydantic method
   uses the interface, or the private helpers where it tests library internals.
5. **`ValidationReport.to_json()` returns the dict**, like every other model. It returned JSON
   text before; a caller that wants text calls `json.dumps(report.to_json())`.

## Alternatives

- **Warn for two releases, then remove at v0.6.0** (the decision of 2026-11-22). Rejected on
  2026-11-23: the library has no users yet, so shims, a deprecation table and warning tests
  would cost code and review for no caller.
- **Keep pydantic's methods as the interface.** Rejected: the library could then never change
  its model layer without a breaking release, and `model_copy(update=)` would stay a supported
  way to skip validation.
- **Name the JSON pair `dump` and `load`.** Rejected: `to_json` and `from_json` already name
  the same pair for timelines (`e8s.to_json`, `e8s.from_json`), and say what the format is.
- **Make `to_json()` return text.** Rejected: the stored documents, `export_json` and canonical
  hashing all work on the parsed dict; text is one `json.dumps` away, while a dict from text
  costs a parse.

## Consequences

- A caller that uses a pydantic method keeps working until the model implementation changes,
  then breaks with no warning. The interface above is the only promise.
- Every library call of the seven moves onto the interface or the private helpers, with no
  change in behavior, and the library rule test keeps it that way.
- `ValidationReport.to_json()` changes its return type: a breaking change listed in the release
  notes.
- ADR 0009's deprecation policy is unchanged; it applies to public names, which pydantic's
  methods are not.

## Numbers

Counted with `git grep -P '\.(model_validate|model_validate_json|model_validate_strings|model_dump|model_dump_json|model_copy|model_fields)\b'`
on `main` at `64e24f4`. The library counts include `FrozenModel`'s own overrides and docstring
examples.

| Method                   | Uses in `e8s/` | Uses in `tests/` |
| ------------------------ | -------------- | ---------------- |
| `model_validate`         | 77             | 119              |
| `model_validate_json`    | 3              | 39               |
| `model_validate_strings` | 1              | 3                |
| `model_dump`             | 43             | 136              |
| `model_dump_json`        | 2              | 56               |
| `model_copy`             | 5              | 294              |
| `model_fields`           | 12             | 8                |
| **Total**                | **143**        | **655**          |
| Files                    | 53             | 97               |

| Measure                                  | Value                                        |
| ---------------------------------------- | -------------------------------------------- |
| `FrozenModel` subclasses in `e8s/`       | 138                                          |
| Library calls of the seven after it      | 0 outside the three allowed places, by test  |
| Warnings and shims added                 | 0                                            |
| Public methods whose return type changes | 1 (`ValidationReport.to_json`, text to dict) |

## What would change our mind

- The library gains users before the model implementation changes: then a change that removes a
  pydantic method gets a release-notes entry, as any breaking change does.
- Callers report a real need that only `model_validate_strings` or `model_fields` meets: then
  the interface gains a named replacement.
- A host needs to build models that fail validation, for instance to repair stored data: then the
  library adds a named, documented way to do that repair, never an unvalidated copy.

## References

- ADR 0009 (keyword-only flags and the deprecation policy), ADR 0018 (tokenless publication
  links; `replace` instead of `model_copy(update=)`).
- Code: `e8s/core/types.py` (`FrozenModel`), `e8s/core/validation.py` (`ValidationReport`,
  `validate_or_schema_error`), `e8s/core/canonical.py` (`canonical_value`), `e8s/time/timeline.py`
  (`to_json`, `from_json`), `tests/unit/test_library_rules.py` (the library rule tests).
