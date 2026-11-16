# ADR 0014: Content hashes are SHA-256 over domain-tagged RFC 8785 canonical JSON

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

`content_hash` (`showpy/core/util.py`) is documented as canonical and is not. It runs
`json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)` over a model dump, hashes
strings and bytes raw, and is used for revision IDs (`DocumentRepository.transaction`), for the
publication revision of an unsaved show (`export/publish.py`), and by consumers for set-script
`source_hash` and staleness. The audit (section 9.3) confirmed nine failure classes, each now a
ledger test:

| Failure                                                         | Ledger    |
| --------------------------------------------------------------- | --------- |
| `0.0` and `-0.0` hash differently                               | UP-9.3-04 |
| `1` and `1.0` hash differently                                  | UP-9.3-05 |
| the string `'{"a":1}'` and the object `{"a": 1}` collide        | UP-9.3-06 |
| `{1: "a"}` and `{"1": "a"}` collide                             | UP-9.3-07 |
| mixed key types leak `TypeError`                                | UP-9.3-08 |
| two different 2,000-element arrays hash equal (`str` truncates) | UP-9.3-09 |
| nested models hash by their `str()` repr                        | UP-9.3-10 |
| NaN is written as non-standard JSON and hashed                  | UP-9.3-11 |
| a lone surrogate leaks `UnicodeEncodeError`                     | UP-9.3-12 |

Separately, `Spot.tags` and `Cast.selections` are `frozenset`s, so `content_hash(show)` changes
with `PYTHONHASHSEED` (UP-9.3-55; under seeds 1, 2 and 3 one selection dumps as `T3,T1,T2,T4`,
`T3,T1,T2,T4` and `T2,T4,T1,T3`). Stored documents (`dump_json` in `show/documents.py`) have a
second, slightly different canonicalization, and write `-0.0` and `0.0` as different bytes
(UP-9.7-31).

Every v0.1 and v0.2 repository already holds revision IDs computed the old way, and consumers
hold old `source_hash` values. The change must not lose or rewrite any of that.

## Decision

1. **One value normalization.** `showpy.core.canonical.canonical_value(obj)` turns any supported
   value into the JSON data model and rejects everything else with `SchemaError`:
   - pydantic models: `model_dump(mode="json")`, then recursively normalized;
   - `dict` and `Mapping` with only string keys: a JSON object;
   - a mapping with any non-string key: the type-tagged form
     `{"$showpy:map": [[key, value], …]}`, pairs sorted by the canonical bytes of the key, so
     `{1: "a"}` and `{"1": "a"}` differ and insertion order never matters; keys must themselves
     be canonical values;
   - a user mapping with a key that starts with `$showpy:` raises, so no input can forge a tag;
   - `list`, `tuple`: lists; `set`, `frozenset`: lists sorted by the canonical bytes of each
     element;
   - numpy arrays and scalars: their full content via `tolist()` and `item()`, never truncated;
   - `bool`, `None`, `str` (must be valid Unicode: lone surrogates raise), `int`, `float`;
   - floats: NaN and ±infinity raise; `-0.0` becomes `0.0`; integral floats stay floats in the
     value, and the encoder below prints them as integers.
2. **Hash bytes are RFC 8785 (JCS).** `showpy.core.canonical.jcs(obj)` serializes
   `canonical_value(obj)` per RFC 8785: UTF-8, no whitespace, object keys sorted by UTF-16 code
   units, strings with the minimal JSON escapes, numbers in the ECMAScript shortest round-trip
   form (`1.0` prints `1`, `1e21` prints `1e+21`, `-0.0` prints `0`).
3. **Hashes are domain-tagged.** `content_hash(obj, *, domain="showpy.content")` returns

   ```python
   sha256(b"showpy-hash/1\n" + domain + b"\n" + kind + b"\n" + payload).hexdigest()
   ```

   where `kind` is `bytes` (payload is the raw bytes), `str` (the UTF-8 text) or `json` (the JCS
   bytes). The output stays 64 lowercase hex characters, so every stored-ID check and path layout
   is unchanged. Revision IDs use `domain="showpy.revision"`.

4. **Stored documents share the normalization, not the layout.** `dump_json` writes
   `canonical_value(obj)` with sorted keys and two-space indent, so documents stay readable for
   agents, while `-0.0`, sets, NaN and non-string keys are handled exactly as in hashes. Hashes
   are always computed on JCS bytes, never on document bytes. Stored documents never contain a
   type-tagged map: every stored model has string keys. Blob digests stay SHA-256 of the
   stored bytes, as today.
5. **Old hashes are read once as stale; nothing is rewritten.**
   - **Revision IDs** are names. Stored IDs are never recomputed or rewritten, the `next/` chain
     and trees stay as they are, and every old revision stays readable and revertible. New
     revisions get new-scheme IDs. Phase 3's `fsck` checks each revision ID against the new
     scheme and accepts the v0.2 scheme only for revisions before the first new-scheme one.
   - **Stored documents** written by v0.1 or v0.2 can differ from today's canonical bytes (`-0.0`,
     frozenset order). The first save after the upgrade commits a separate normalizing
     revision, holding only the re-encoded documents, and then the edit on top. Its decoded show
     equals the stored show, so `diff_show` is empty, and `events_since` and proposal overlap,
     which both compare content, see nothing in it. A second save adds none.
   - **Hashes held outside ShowPy** (consumer `source_hash`, publication `revision` of an unsaved
     show, golden fixtures) mismatch exactly once. Consumers treat a mismatch as stale and
     recompute: each scripted set rebuilds once, an unsaved-show publication reports the show as
     changed once. Golden fixtures that store hashes are regenerated in the same commit, with a
     human review (`SHOWPY_UPDATE_GOLDEN=1`).
6. **Encoder correctness is tested against the RFC.** Unit tests use the number vectors of RFC
   8785 Appendix B, and a Hypothesis differential test compares `jcs` with the `rfc8785`
   package, added as a dev-only dependency. ShowPy keeps no runtime dependency for this.

## Alternatives

- **Keep `json.dumps(sort_keys=True)` and patch the edge cases.** Rejected: `json.dumps` prints
  `1.0` and `1` differently, has no rule for key order beyond Python string order, and each patch
  would be one more ad-hoc rule; RFC 8785 is a specification with test vectors.
- **Use the `rfc8785` package at runtime.** Rejected: it would add a runtime dependency for about
  100 lines, while ShowPy still needs its own normalization of models, numpy values and sets
  before encoding. The package stays as the differential oracle in tests.
- **CBOR (RFC 8949 deterministic encoding) or a custom binary format.** Rejected: hashes would no
  longer be reproducible from the JSON documents agents and the designer app already read, and
  every consumer that recomputes a hash would need a CBOR encoder.
- **Hash stored document bytes directly.** Rejected: document bytes are a layout choice (indent,
  key order) that should be free to change; tying identity to them makes a formatting change a
  data migration.
- **Rewrite history to new-scheme revision IDs.** Rejected: revision IDs are referenced by
  consumers, proposals and undo targets; rewriting them is data loss in everything that holds an
  old ID.
- **Tag hashes with the model class name.** Rejected: module moves (the geo layering in Phase 2)
  would change every hash. An explicit `domain` string is stable and chosen by the caller.

## Consequences

- `content_hash` raises `SchemaError` for NaN, infinity, invalid Unicode, reserved `$showpy:`
  keys and unsupported types, where it used to return a hash or leak a builtin error. After
  `allow_inf_nan=False` (Phase 1, L0) models cannot hold
  NaN anyway.
- `content_hash(1) == content_hash(1.0)` and `content_hash(0.0) == content_hash(-0.0)`; booleans
  stay distinct from numbers.
- Every hash value changes once. Release notes for v0.3.0 say so and name the stale-once rule.
- The normalizing revision is an ordinary revision. Downgrade is not supported before 1.0
  (ADR 0003 also changes the revision record).
- Sets and frozensets leave stored models (lane D turns `Spot.tags` and `Cast.selections` into
  ordered, deduplicated tuples); the normalization still sorts sets passed in by callers.

## Numbers

| Measure                                             | Today  | After     |
| --------------------------------------------------- | ------ | --------- |
| Confirmed `content_hash` failure classes            | 9      | 0         |
| Hash-seed values under which a show hashes the same | 1 of 3 | 3 of 3    |
| Canonical forms in the library                      | 2      | 1         |
| Stored revisions rewritten by the upgrade           | —      | 0         |
| Extra revisions written per upgraded show           | —      | at most 1 |
| Times each consumer-held hash mismatches            | —      | exactly 1 |
| Hash output length                                  | 64 hex | 64 hex    |

## What would change our mind

- A consumer needs hashes that a non-Python service recomputes from documents alone: then the
  documents themselves move to JCS bytes, and the indent goes.
- `canonical_value` shows up above 10% of save time at 300 spots x 60 sets: then hashing works
  per document and caches by blob digest.
- RFC 8785 is superseded, or `rfc8785` and our encoder disagree on a vector we cannot resolve:
  then we pin to the reference implementation's behavior and record it in an addendum.

## References

- RFC 8785, JSON Canonicalization Scheme (JCS), including Appendix B number test vectors.
- Audit `docs/upgrade-plan.md`: section 3 row 9; section 4.3; section 9.3 "Determinism" and
  "`content_hash` is neither canonical nor collision-free"; section 9.7 item 11.
- Ledger tests unblocked: UP-9.3-04 to UP-9.3-12; with UP-9.3-55, UP-9.7-30 and UP-9.7-31 as
  companions.
- Code: `showpy/core/util.py`, `showpy/show/documents.py`, `showpy/show/repositories.py`,
  `showpy/export/publish.py`.
- Master plan review focus 1 (stored shows from v0.1.x and v0.2.x).

## Addendum 2026-11-15: caps, the value tie-break, exact ints and set order

- **Two caps.** `canonical_value` accepts containers nested at most 100 levels: 100 levels of
  lists, or of tagged maps such as `{1: {1: ...}}`, normalize and encode, and 101 raise
  `SchemaError`. One input level can become three output levels (tagged map, pair list, pair)
  and the encoder recurses once per output level, so 100 stays under CPython's default
  recursion limit of 1000; a reference cycle hits the same cap. Ints must satisfy
  `abs(n) < 10**640`: `10**640 - 1` is accepted, `10**640` raises. The environment can lower
  `sys.int_max_str_digits` to 640 and no further, so every environment prints the same bytes or
  raises the same error.
- **Tagged pairs tie-break on value bytes.** Pairs sort by the canonical bytes of the key, then
  of the value. Distinct keys can share canonical bytes (the tuple `(1,)` and the frozenset
  `{1}` both become `[1]`), so the value decides and insertion order never does.
- **Ints stay exact beyond ±2\*\*53.** `jcs` writes every digit of an int (`jcs(2**64 + 1)` is
  `18446744073709551617`). This departs from RFC 8785 and I-JSON (RFC 7493), which limit numbers
  to IEEE 754 doubles; a JavaScript verifier parsing these bytes would round them. Distinct ints
  never collide, at a price: `content_hash(n) == content_hash(float(n))` holds only for
  `abs(n) <= 2**53`, and `jcs(2**60)` (`1152921504606846976`) differs from `jcs(float(2**60))`
  (`1152921504606847000`). The `rfc8785` differential test draws ints within ±(2\*\*53 − 1).
- **Sets held by model fields dump in iteration order.** `model_dump(mode="json")` turns a `set`
  or `frozenset` field into a list before `canonical_value` sees it, so that list is not sorted
  and, for strings, depends on `PYTHONHASHSEED`. Sets passed directly are still sorted. This
  lasts until lane D turns `Spot.tags` and `Cast.selections` into ordered tuples (UP-9.3-55).
- **Dump failures raise `SchemaError`.** A model whose field serializer fails
  (`PydanticSerializationError`) or whose fields hold a reference cycle (pydantic's
  circular-reference `ValueError`) raises `SchemaError` naming the model.
