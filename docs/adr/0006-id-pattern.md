# ADR 0006: One ID pattern, ASCII and grammar-safe, for every ID kind

- Status: Accepted (2026-11-15)
- Date: 2026-11-15
- Deciders: the maintainer (abj360)

## Context

IDs reach three places that each have rules: the selection grammar (`T1-T8`, `T*`, `all`,
operators `+ - & ( )`), workspace and repository paths, and file systems that fold case or
reserve names. Today there are four different rules and one kind with none:

| Where                                         | Rule today                            |
| --------------------------------------------- | ------------------------------------- |
| `SET_ID_PATTERN` (`show/manage.py:37`)        | `^[A-Za-z0-9][A-Za-z0-9_.]*$`         |
| `SHOW_ID_PATTERN` (`show/repositories.py:28`) | `^[A-Za-z0-9][A-Za-z0-9_.\-]*$`       |
| `SEGMENT_PATTERN` (`show/documents.py:39`)    | `^[A-Za-z0-9_][A-Za-z0-9_.\-]*$`      |
| `REF_PATTERN` ID part (`core/ids.py`)         | `[^@:\s]+`, which admits `..` and `/` |
| spot, form, route, zone, landmark, note IDs   | none                                  |

The consequences are confirmed defects: empty spot IDs, IDs with spaces or operators, and IDs
such as `all`, `T*` or `group:1` (which `relabel` can produce) cannot be selected (UP-9.3-69 to
UP-9.3-71); `parse_ref` accepts path separators (UP-9.3-31); `Show` and `show` share a folder
on macOS, `CON` and `abc.` are accepted and a 300-character ID leaks `OSError` (UP-9.7-14 to
UP-9.7-17); `T٣` parses as `T3` because `\d` matches Unicode digits (UP-9.3-26); `ID_PARTS`
backtracks quadratically, 1.3 s on a 20,000-character ID (UP-9.3-22). The ID aliases are PEP 695
`type` aliases of `str`, so mypy cannot tell a `SetId` from a `SpotId`.

Audit decision 4 asks for the pattern and the reserved words.

## Decision

1. **One pattern for every ID kind**: show, set (and set key, ADR 0004), spot, group key, form,
   route, zone, landmark, selection name, note, comment and publication.

   ```
   ^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$     and no ".." anywhere
   ```

   - ASCII letters, ASCII digits, `_` and `.`; 1 to 64 characters.
   - A leading digit is allowed: set IDs are `1`, `12A`, `2.5`.
   - No `-`: it is the range and difference operator (`T1-T8`, `A - B`) and the separator of
     transition folders (`/transitions/12-13/`).
   - No leading or trailing `.`: Windows strips a trailing dot, and a leading dot hides a file.

2. **Reserved words**, compared case-insensitively: `all` (the grammar keyword) and the Windows
   device names `con`, `prn`, `aux`, `nul`, `com1` to `com9`, `lpt1` to `lpt9` (IDs become path
   segments). `*` and `:` are reserved by being outside the character set, which keeps `T*`
   and `group:1` grammar-only.
3. **Case-sensitive, but unique ignoring case.** `T1` and `t1` are different IDs, and they cannot
   both exist in one namespace (spots of a cast, sets of a show, shows of a repository). This
   keeps every ID safe as a folder name on case-insensitive file systems without lowercasing
   anyone's labels.
4. **NFC first.** Input text is NFC-normalized before the check. With an ASCII pattern this
   changes no valid ID; it exists so the same normalization step serves IDs and free text
   (titles, labels, note text), which format v2 stores NFC-normalized.
5. **One checker, typed results.** `showpy.core.ids.check_id(text, kind)` returns the
   normalized ID or raises `SchemaError` naming the kind, the position of the first bad
   character and the pattern. In Phase 2 the ID aliases become `NewType`s (`SpotId`, `SetId`,
   `SetKey`, `FormId`, `RouteId`, `ZoneId`, `LandmarkId`, `ShowId`), `TransitionId` becomes a
   `NamedTuple(from_set, to_set)`, and model fields use annotated types that run `check_id`.
   Public functions that take user text keep accepting `str` and validate it; functions passing
   IDs between library calls take the `NewType`s, so mypy catches swapped IDs.
6. **Rollout.**
   - **Phase 1** adds `check_id`, the pattern and the reserved words in `core/ids.py`, and uses
     them for new set keys, storage path segments and ASCII-only digit splitting.
   - **Phase 2** enforces the pattern on every model and migrates stored v1 IDs that fail it
     (format v2, ADR 0007). The migration renames through `rewrite_spot_ids` and reports each
     rename.
   - **Phase 3** stores shows under encoded folder names (layout v2, ADR 0015), so existing show
     IDs with `-` keep loading. Until then the repository keeps accepting today's show IDs.

## Alternatives

- **The audit's draft `^[A-Za-z_][A-Za-z0-9_.-]{0,63}$`.** Rejected: it forbids a leading digit,
  which would reject every numbered set (`1`, `12A`), and it allows `-`, which collides with
  ranges (`flag-1-flag-3` cannot be read) and with transition folder names.
- **Allow Unicode letters (NFC plus `str.isidentifier` classes).** Rejected for now: confusables
  (Cyrillic `А` against Latin `A`) need a UTS #39 check, Unicode digits break natural sorting and
  ranges (UP-9.3-17, UP-9.3-26), and case folding outside ASCII differs between file systems.
  Labels and titles stay free Unicode text; only IDs are ASCII.
- **Per-kind patterns (keep `-` for show and publication IDs).** Rejected: four patterns are the
  problem this ADR removes, and show IDs appear in selection contexts (claims, links) too.
  Hyphenated slugs are the price; `_` and `.` remain.
- **Lowercase every ID.** Rejected: designers write `T1` and `B12`; forcing `t1` changes every
  printed label.
- **Keep PEP 695 aliases and rely on naming.** Rejected: swapped IDs are a silent wrong-result
  class mypy can catch for free.

## Consequences

- Shows created before Phase 2 with spot IDs outside the pattern are renamed once by the v1 to
  v2 migration, with a report. Hyphenated show IDs keep loading through Phase 3's encoded
  folders; new shows cannot use `-`.
- `relabel` can no longer produce `all` or a selector-looking ID; it raises with the bad label.
- Range parsing, `T*` and `label_prefix` share one linear split, `re.search(r"\d+\Z", s, re.ASCII)`:
  O(n) on any ID, and `T٣` is no longer `T3`.
- Typed consumers see `NewType`s from Phase 2: passing a `str` where a `SpotId` is required
  becomes a mypy error at internal call sites, not at the public text-taking entry points.

## Numbers

| Measure                           | Today                                 | After                                                              |
| --------------------------------- | ------------------------------------- | ------------------------------------------------------------------ |
| ID rules in the code              | 4, plus 6 kinds unchecked             | 1                                                                  |
| Longest ID accepted               | unbounded (300 chars leaks `OSError`) | 64                                                                 |
| Split of a 20,000-character ID    | 1.3 s (quadratic)                     | linear, under 1 ms                                                 |
| Ledger tests this pattern decides | —                                     | 12: UP-9.3-22, -26, -31, -69, -70, -71; UP-9.7-14 to -17, -81, -82 |

## What would change our mind

- Designers working in non-Latin scripts need native IDs: then Unicode letters behind NFC plus a
  UTS #39 confusable check, with digits still ASCII.
- Consumers need hyphenated show or publication slugs in URLs: then the link layer maps `_` to
  `-`, or show and publication IDs alone admit `-` because they never enter the grammar.
- A namespace needs more than 64 characters (generated IDs): raise the cap; nothing else depends
  on it.

## References

- Audit `docs/upgrade-plan.md`: section 4.1; section 9.3 "Selection grammar" and "API and design
  changes"; section 9.7 item 8; section 10 decision 4.
- Ledger tests: Phase 1 unblocks UP-9.3-22 (linear split) and the set keys behind UP-9.7-01;
  Phase 2 closes UP-9.3-26, UP-9.3-31, UP-9.3-69, UP-9.3-70, UP-9.3-71, UP-9.7-81, UP-9.7-82;
  Phase 3 closes UP-9.7-14 to UP-9.7-16. UP-9.7-17 closes in Phase 1, when storage maps
  `ENAMETOOLONG` to `StorageError` (code `NAME_TOO_LONG`).
- Code: `showpy/core/ids.py`, `showpy/core/util.py` (`ID_PARTS`, `RANGE_PATTERN`),
  `showpy/show/manage.py`, `showpy/show/repositories.py`, `showpy/show/documents.py`.
- Related: ADR 0004 (set keys), ADR 0007 (format v2 migration), ADR 0008 (selection precedence).

## Addendum 2026-11-15: device names before an extension, COM0 and LPT0, and NFC

- **A reserved device name followed by an extension is reserved too.** Windows treats `NUL.txt`
  and `NUL.tar.gz` as `NUL`, so `con.5`, `AUX.txt` and `lpt1.2` cannot be folder names either.
  `check_id` now rejects an ID when the part before its first `.`, compared ignoring case, is a
  Windows device name. `all` stays a whole-ID rule: `all.1` is not the grammar keyword and names
  no device, so it is accepted. Names that only start with a device name, such as `cons.5` or
  `com10`, are accepted. The error position is 0, as for a bare reserved name.
- **COM0 and LPT0 are not reserved.** Microsoft's "Naming Files, Paths, and Namespaces" (Win32
  docs, `ms.date` 2024-08-28, read 2026-11-15) lists `CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`,
  `COM¹`, `COM²`, `COM³`, `LPT1` to `LPT9`, `LPT¹`, `LPT²` and `LPT³`. `COM0` appears there only
  as an example symlink in the NT `Global??` namespace, not in the reserved list, so
  `RESERVED_IDS` keeps its 23 names. The superscript forms need no entry: `¹`, `²` and `³` are
  outside the ASCII character set and already rejected.
- **NFC can turn non-ASCII input into a valid ID.** Decision 4 says NFC changes no valid ID; it
  can still change an invalid one into a valid one. `check_id("\u212a1")` (U+212A KELVIN SIGN,
  then `1`) returns `"K1"`, because the Kelvin sign's canonical decomposition is ASCII `K`.
  Callers must store the returned value, never the text they passed in.

## Addendum 2026-11-17: device-name show IDs refused for new shows

- **New shows cannot take a Windows device name, from Phase 1.** The maintainer moved this part
  of UP-9.7-15 from Phase 3 to Phase 1 on 2026-11-17. Windows CI showed the gap on PR #20:
  Windows refused the `CON` folder, while Linux and macOS stored it.
  `DocumentRepository.transaction` now refuses a show ID that `is_device_name` matches (`con`,
  `NUL`, `aux.1`, `com1`; the same device list and before-the-first-dot rule as `check_id`)
  whenever the caller starts a show (expected revision is the root). It raises `StorageError`
  with code `BAD_PATH`, the code for any show ID that cannot be stored, before the show's folder
  is even looked up: on Windows that lookup opens the device (`con` gave `IO` on CI). The
  refusal holds in memory and on disk, on every platform.
- **Existing shows keep loading.** A show already stored under a device name on Linux or macOS
  still reads, lists and commits on its head, as decision 6 requires; a write to it that
  claims to start the show gets `BAD_PATH` instead of `ConflictError`. Phase 3's encoded folder
  names (ADR 0015) migrate it. Only the device-name rule moves: hyphens and the rest of the
  pattern stay accepted for show IDs until Phase 3.
- **Ledger.** UP-9.7-15 is closed. UP-9.7-14 (case) and UP-9.7-16 (trailing dot) still close in
  Phase 3.
- **Code comments state the rule, not its source.** By the user's rule of 2026-11-17, docstrings
  and comments do not cite ADRs or ledger IDs; the refusal's docstring and hint name the device
  names themselves.
