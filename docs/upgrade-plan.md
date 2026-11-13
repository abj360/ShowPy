# Upgrade plan: `stage` to a state-of-the-art library

Audience: the maintainer and any engineer or agent who picks up this work. Scope: everything
needed to take `stage` from "well-tested internal library" to a library that meets the standard
of the best open-source scientific Python projects, in packaging, correctness, algorithms,
robustness, performance, tooling and documentation.

Status: written 2026-11-13 from a line-by-line audit of every module, the test suite, the
tooling and the documentation. Every item marked "confirmed" was reproduced against `main` at
`e64f464` (tag `v0.1.1`), including on stress shows of 150 to 300 spots and 20 to 60 sets with
every path style, tempo ramps and holds, entrances and exits, and long edit histories. Line
numbers refer to that commit. Nothing in the library was changed by the audit.

## Contents

1. Where the library stands
2. The standard we are aiming for
3. Priority 0: defects that must be fixed before anything else
4. Cross-cutting standards (validation, errors, determinism, immutability, types, performance)
5. Testing
6. Packaging and release engineering
7. CI, tooling and repository settings
8. Documentation
9. Workstreams by package (geometry; engine and paths; foundations, cast, notes and time;
   sheets; export and publishing; forms and matching; show layer and storage)
10. Roadmap: phases, exit criteria and decisions needed

## 1. Where the library stands

The foundations are better than most libraries of this size: 1,468 tests (410 doctests) in 8 s,
96% branch coverage, `mypy --strict`, warnings as errors, a hashed lockfile with `pip-audit`, no
order dependence, exact matching verified by brute force, an analytically exact tempo clock,
byte-exact undo and redo, a spec-correct PDF writer and music-clock video timing. Keep all of it.

What separates it from a state-of-the-art library is not polish. The audit found:

- **Broken guarantees.** The engine's collision segments, documented as within 0.02 m, are off by
  up to 1.0 m on custom motions; `detect_form` breaks its own tolerance by 40x; `transform_form`
  is documented as exact and is not; `content_hash` is documented as canonical and is not; show
  serialization depends on `PYTHONHASHSEED`.
- **Data loss and security holes** in storage and collaboration: a renumber deletes a set's
  script, the in-memory repository loses updates under threads, a tampered repository can make
  reads escape its root or loop forever, an agent can delete sets or edit designer-only files
  through public APIs, publication links are guessable and claims unauthenticated.
- **Inputs that hang or exhaust memory** (`to_polyline(tol=0)`, `scatter(min_gap<0)`,
  `SpiralCurve(turns=1e7)`, `select("T1-T999999999")`, `qr_code(size=100000)`).
- **Hundreds of boundary gaps**: NaN and infinity accepted almost everywhere, builtin and
  third-party exceptions leaking from public functions (against project rule 7), mutable dicts
  inside frozen models, locks and pins bypassed by several edit paths (against rule 6).
- **Scale limits**: `min_gaps` takes 21 to 90 s for one 300-spot transition; `events_since` takes
  95 s over 200 revisions; `sample()` runs at 53 frames per second for 300 spots.
- **Algorithms below the state of the art** in the places that matter most to designers:
  matching that optimizes spatial crossings rather than collisions in time, local pairwise
  collision fixes, sampled arc length, single-seed scatter, non-robust form fitting.
- **Release engineering of an internal project**: exact runtime pins, no extras, a version that
  disagrees with its tag, no schema or format versioning, no migrations, no changelog, CI on one
  Python and one OS, a hand-maintained API reference that has drifted.

## 2. The standard we are aiming for

A release of `stage` meets the standard when all of these hold, each checked by CI:

1. **Every documented guarantee is tested at its boundary.** Tolerances, exactness and determinism
   claims have property tests that fail when the claim is broken (today 6 of 13 hand-made mutants
   survive).
2. **No public function lets a non-`StageError` exception escape** for any input of the declared
   types, and none hangs or allocates without bound. Fuzzed nightly.
3. **Every model rejects invalid data at construction**: finite numbers, ranges, sizes, ID
   patterns, cross-field invariants. `validate_show` is for cross-object consistency only.
4. **Determinism is total**: identical inputs give byte-identical JSON, hashes, PDFs and videos
   under any hash seed, platform and supported Python.
5. **Stored data is versioned and migratable**, with old-version fixtures loaded in tests.
6. **Locks, pins and principal rules are enforced in one place** that every write path goes
   through.
7. **Performance budgets** (section 5) are measured on every pull request and fail on a 20%
   regression.
8. **The public API is snapshotted**; any change to a public name or signature needs a version
   bump and a changelog entry, and deprecations warn for two minor releases.
9. **Installable and supported like a real package**: version ranges, extras, wheels tested in a
   clean environment on Linux, macOS and Windows for every supported Python, signed provenance.
10. **Documentation is generated where it can be and tested where it cannot**: reference from
    docstrings, every example executed in CI, an error-code catalogue, a migration guide.

## 3. Priority 0: defects to fix before anything else

Each needs a failing `test_regression_` test first. Details and line numbers are in the
workstreams.

| #   | Defect                                                                                                                                                                   | Workstream                 |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| 1   | `renumber` + save deletes a set script and reassigns another                                                                                                             | show                       |
| 2   | `InMemoryRepository` loses updates under threads                                                                                                                         | show                       |
| 3   | Tampered `next/` or tree files escape the repository root or loop forever                                                                                                | show                       |
| 4   | `Workspace.commit` lands garbage that makes the show unloadable                                                                                                          | show                       |
| 5   | Agents can delete sets, empty the cast, change the show ID, forge comments, move pinned spots and write `/config/limits.yaml` through public APIs                        | show                       |
| 6   | Collision segments exceed the 0.02 m bound by up to 1.0 m                                                                                                                | engine                     |
| 7   | Hangs: `to_polyline(tol<=0 or nan)`, `scatter(min_gap<0)`, `scatter_form(min_gap=-1)`, cycles in history                                                                 | geo, forms, show           |
| 8   | Unbounded allocation: spiral turns, `fill`, `space_by_interval`, `density_map`, range expansion, `split(k)`, `qr_code(size)`, video canvas, quadratic SVG and ID regexes | geo, cast, export          |
| 9   | Hash-seed-dependent serialization and hashes (frozensets, set inputs)                                                                                                    | foundations                |
| 10  | Publication links guessable, claims unauthenticated, contact data inside the snapshot, CSV injection                                                                     | export                     |
| 11  | Football template swaps distort the drill; mirrored landmark fits are silent                                                                                             | sheets                     |
| 12  | `apply_match` and `place` leave forms and placements inconsistent                                                                                                        | forms                      |
| 13  | Locks and pins bypassed by `two_part`, cast edits, `rebuild(force=True)`                                                                                                 | engine, foundations, forms |

## 4. Cross-cutting standards

These are single decisions applied everywhere. Doing them once removes most of the confirmed
defects in the workstreams.

### 4.1 Validation at the model boundary

- Set `allow_inf_nan=False` on `FrozenModel` (`core/types.py:37`), then audit the few fields that
  legitimately need infinity.
- Introduce constrained annotated types in `core.types` and use them in every model:
  `Finite`, `NonNegFinite`, `PositiveFinite`, `Count`, `Seconds`, `Meters`, `PositiveMeters`,
  `Degrees` (normalized on input), `CountsPerMinute`.
- One ID pattern (for example `^[A-Za-z_][A-Za-z0-9_.-]{0,63}$`, NFC-normalized, with `all` and
  `*` reserved) enforced on spot, set, form, route, zone, landmark, show, note and publication
  IDs. `NewType`s so mypy catches swapped IDs; a `NamedTuple` for `TransitionId`.
- Cross-field invariants move into `model_validator`s: sorted unique tempo sections, ramp pairs,
  dict keys equal to the contained ID (`Show.forms`, `TransitionSpec.routes`, `Cast.groups`),
  `Region` with at least three distinct points and a valid ring, `Sheet` size and outline,
  `CoordinateSystem` precision, `Box2.min <= max`.
- Every string option goes through the existing `pick()` helper, so typos raise
  `UnknownRefError` with suggestions instead of silently falling back.
- Output-size limits as named constants with errors that report the size requested.

### 4.2 Errors that teach, and only `StageError`s

- A single boundary helper `validate_or_schema_error(Model, data)` that converts pydantic errors
  into `SchemaError` with the field path; use it in every constructor-like function.
- Wrap third-party failures where they occur: Shapely (`GEOSException`, `ValueError`), numpy
  (`LinAlgError`, `AxisError`, `RuntimeWarning`), Pillow, segno, ffmpeg, `decimal`, `json`, `os`.
- Make `StageError` picklable and deep-copyable (`__reduce__`), add `to_dict()`, a `repr` with
  code and context, and an **error-code catalogue** (an enum, documented) so agents and the app
  can switch on codes.
- Rename `stage.core.errors.ValidationError` (it clashes with pydantic's) behind a deprecated
  alias; make `DeprecatedWarning` a `DeprecationWarning` and use `warnings.deprecated`
  (PEP 702).
- Truncate echoed input in messages to about 80 characters; cap context lists (a stale save at
  1,000 revisions builds a 48 KB error today).

### 4.3 Determinism

- No sets or frozensets in stored models: ordered, deduplicated tuples.
- `content_hash` becomes an RFC 8785 (JCS) canonical encoder with type tags, `-0.0` normalized,
  NaN rejected and no `default=str`. One canonicalization for both revision IDs and blobs.
- `seeded_rng` requires a non-negative `int` and pins `PCG64`.
- No wall clock anywhere: render PDFs through `export/pdf.py` (Pillow embeds the clock); encode
  video with `-fflags +bitexact`.
- CI runs the suite under at least three `PYTHONHASHSEED` values and checks byte-identical
  outputs across two runs and two platforms.

### 4.4 Immutability and purity

- No mutable containers in frozen models: `MappingProxyType`-backed mappings with a JSON
  serializer, or tuples of pairs. Registries (`DEFAULT_UNITS`, form functions, motions, fonts)
  become truly read-only.
- `FrozenModel.replace(**changes)` that revalidates; ban `model_copy(update=...)` in library code
  with a lint rule (it skips validation and is used in about a dozen edit paths).
- `as_points` returns a copy (it returns a view of the caller's array today).
- A generic property test asserts that no public function mutates its inputs.

### 4.5 Types

- Replace `dict[str, Any]` payloads with typed models: form recipes (discriminated union with
  `recipe_version`), route curves, motion and generator parameters, canvas event payloads,
  `Fix.params`, facing values (`FixedFacing | PointFacing | EntityFacing`), match weights.
- Typed patch models (or `TypedDict` with `Unpack`) instead of `**changes: Any` in edit functions.
- Add pyright (basic) as a second type gate: it found 13 real errors that `mypy --strict` misses,
  including a reachable `IndexError` in `routes.py:98`.
- Keyword-only `force` and other boolean flags before 1.0 (114 positional booleans today).

### 4.6 Performance foundations

- Cache indexes on frozen models (`Set.get`, `Cast.spot`, `ShowIndex`, compiled clock) instead of
  rebuilding them per call; several loops are quadratic only because of this.
- Import pandas lazily (235 ms of the 368 ms it costs to import `stage.choreo.models`), and cache
  resolved names in `stage.__getattr__`.
- Arrays for bulk results; pydantic models only for single records.
- Generate the three copies of the public name list in `stage/__init__.py` from one table, with an
  `ast`-based test that they agree.

## 5. Testing

### 5.1 What to keep

Fast, order-independent, xdist-safe suite; brute-force matching properties; the engine segment
property; error tests that assert hints and positions; deterministic fixtures; the schema drift
guard; parametrized in-memory and file repositories.

### 5.2 What to add

1. **Show generators and a scenario suite.** Today almost every test uses a 4-spot, 2-set show;
   the largest moving test has 8 spots and 3 sets, and no test combines ramps, holds, measures,
   audio, entrances, pins, proposals and claims. Build Hypothesis strategies through the public
   builders (casts, sheets, timelines with measures and cues, placements with entrances and exits,
   path rows of all seven styles with easings, windows, waypoints, facing modes and overrides,
   forms of all twelve kinds, whole shows, edit scripts), plus a deterministic
   `seeded_show(seed, spots, sets)` factory for benchmarks.
2. **Scale tiers**: S (1–20 spots, every pull request), M (100 x 20, every pull request under a
   time budget), L (300 x 64 with a 500-revision history, nightly), XL (600 x 120, 2,000
   revisions, 30 s of video, weekly).
3. **Feature-mix scenarios**, each asserting `validate_show` ok, schema validity, continuity at
   every set boundary, segments within tolerance, JSON and document round trips, `events_since`
   replay equal to head, undo and redo as inverses, byte-identical repeated output and no input
   mutation:
   a 300-spot opener through block, company front, arc and scatter with pins; counter-march,
   follow-the-leader file of 40 and pinwheel gates; mid-show entrances and exits with claims;
   ramps, holds, measure changes and audio offset with split and join at every set; NFHS to NCAA
   sheet replacement with pins, zones and routes; 200 interleaved designer and agent edits with
   proposals, conflicts and undo; publish, claim, regroup, relabel, renumber, republish and notify;
   a full export bundle (JSON, frames CSV, positions re-import, 300-sheet PDF book, 30 s video).
4. **Stateful tests** (`RuleBasedStateMachine`) for the repository, workspace, history, pins,
   proposals and the claim state machine. None exist today.
5. **Missing properties**: selection algebra laws and order rules; clock derivative, additivity
   and hold boundaries; storage linearizability with real concurrent writers and crash
   injection; matching invariance under start and slot permutation and constraint respect for
   random constraints; transform group laws including facings under skew; for all eleven curve
   kinds, monotone arc length, `project` against dense sampling, substring additivity, reversal
   involution, chord error of `to_polyline`, construction round trip; engine continuity at set
   boundaries for all styles, velocity against position differences, `positions` against
   per-spot `position`; `undo(redo(x)) == x` over random scripts; export round trips; every
   generated show validates against the JSON schema; purity.
6. **Mutation testing** on core modules (`mutmut` or `cosmic-ray`, nightly), starting with the
   surviving mutants found by hand: the 1e-6 continuity tolerance, Poisson-disk spacing, zone
   window end, after-window facing blend, collinear and touching crossings.
7. **Fuzzing** of every parser and importer (selection grammar, SVG paths, refs, transition IDs,
   ranges, coordinate wording, positions CSV, JSON import): Hypothesis grammars on pull requests,
   HypoFuzz or Atheris nightly. Ad-hoc fuzzing already found leaks in `parse_path` and
   `import_json`.
8. **Golden tests beyond one 8-spot show**: a 100–300 spot real show using every style; goldens for
   segments, `min_gaps`, speeds, coordinate text, CSV, PDF text, SVG and PNG renders (image
   tolerance), video frame hashes, validation reports and match results; a visual diff artifact and
   a recorded reviewer on every fixture update.
9. **Strong assertions** in place of the weak ones listed by the audit (always-true timers and RNG
   checks, frame counts without frame content, unread QR codes, loose length inequalities, "does
   not raise" tests); decode QR codes and compare against the link; compare frames against engine
   positions.
10. **Packaging tests**: build the wheel, install it into a clean environment, import, check
    `py.typed` and the schema resource, run the README example; a lowest-dependency job; a
    consumer typing test with `assert_type` under mypy and pyright; an import-time budget; a memory
    budget.
11. **Hypothesis profiles**: `ci` (derandomized, `print_blob`, explicit deadlines), `nightly`
    (many examples), `dev`; a shared example database in CI artifacts; `@example` pins for every
    regression.
12. **One `conftest.py`** with shared fixtures (`_set`, `_session`, `_pin` and `_with_rows` are
    duplicated across files today).

### 5.3 Performance budgets (300 spots unless noted)

| Operation                             | Budget                     | Today                  |
| ------------------------------------- | -------------------------- | ---------------------- |
| `min_gaps`, one transition            | < 1 s                      | 21–90 s                |
| `sample()`                            | > 300 frames/s             | 53 frames/s            |
| `compile_all`, 64 sets                | < 2 s                      | 1.5 s                  |
| `match`, default objective, 500 spots | < 0.2 s                    | 0.15 s                 |
| `validate_show`, 60 sets              | < 1 s, incremental < 50 ms | 2.4 s, not incremental |
| one-spot edit saved                   | < 50 ms                    | 198 ms                 |
| `events_since`, 200 revisions         | < 1 s                      | 95 s                   |
| `head()`, 1,000 revisions             | O(1), < 1 ms               | 62 ms                  |
| two-minute video at 1080p             | < 60 s                     | 70 s at 672 x 306      |
| `import stage` plus first name        | < 0.3 s                    | 0.54 s                 |
| peak memory, 300 x 64                 | < 500 MB                   | 150–193 MB             |

Run with `pytest-benchmark` or `asv` on a fixed runner; fail on a 20% regression.

## 6. Packaging and release engineering

- **Dependency ranges, not pins.** Exact `==` runtime pins break every consumer's resolver. Use
  tested lower bounds and a lowest-direct CI job; keep exact versions only in `uv.lock`.
- **Extras.** Core: numpy, scipy, shapely, pydantic. `stage[video]` (imageio-ffmpeg, which bundles
  a large ffmpeg binary; drop `imageio`, which is unused), `stage[qr]` (segno), `stage[render]`
  (Pillow), `stage[tables]` (pandas). Missing extras raise a `StageError` naming the extra.
- **Version from the tag** (`hatch-vcs`) and `stage.__version__`. Today `pyproject.toml` says 0.1.0
  while HEAD is tagged v0.1.1.
- **Distribution name.** `stage` is very likely taken on PyPI (not checked); choose a distinct
  distribution name and keep `import stage`.
- **Metadata**: authors, keywords, classifiers (Python versions, `Typing :: Typed`, licence, OS),
  project URLs, `license-files`.
- **Wheel and sdist contents**: ship the JSON schemas inside the package and load them with
  `importlib.resources`; exclude docs, tests, the 2.6 MB guide PDF and agent files from the sdist.
- **Reproducible builds** (`SOURCE_DATE_EPOCH`, compare hashes of two builds), build provenance
  attestations (PEP 740 or GitHub attestations), a CycloneDX SBOM.
- **Distribution**: a private index (GitHub Packages or CodeArtifact) or PyPI trusted publishing,
  instead of consumers pinning git tags.
- **Release workflow on tags**: build, test the wheel on the full matrix in clean environments,
  publish, generate release notes.
- **Changelog** from Conventional Commits (git-cliff, release-please or commitizen), plus a
  release checklist.
- **Policies**: semantic versioning with a public API snapshot; deprecation (warn for two minor
  releases, enforced by `filterwarnings=error`); support window following SPEC 0 for Python and
  core dependencies; a security policy with a real private contact.

## 7. CI, tooling and repository settings

- **Matrix**: Python 3.12, 3.13 and 3.14 on Linux, macOS and Windows (3.12 passes locally today but
  is never tested in CI); a lowest-direct job; a pre-release dependencies job; several
  `PYTHONHASHSEED` values.
- **One task runner** (nox or just) that CI, pre-commit, Docker and the docs all call. The gate is
  defined four times today and the definitions differ (compose omits the format check and
  Prettier).
- **New gates**: coverage floor (95% branch, never decreasing), pyright basic, the wheel smoke
  test, guide exercises and examples, README and guide snippets as doctests, benchmarks, schema
  validation of generated documents, API snapshot diff, docs build with link checking.
- **Ruff**: add ARG, PERF, a tuned PL subset, TRY (002, 004, 300), FBT for public APIs, S with
  per-file ignores, DTZ, PTH, C90 at 12, TID (ban imports of `tests` from `stage`), PIE, PGH, RSE,
  FURB, LOG, ISC. Fix the contradiction that every module has a shebang but none is executable
  (EXE001 on 94 files): drop the shebangs from library modules.
- **Complexity**: refactor the 20 functions with cyclomatic complexity above 10 (worst
  `_resolve_spot_kind` 18, `_edit_ops` 17, `preview_proposal` 16) and split modules over 500
  lines (`choreo/models.py`, `geo/curves.py`, `show/manage.py`, `cast/spots.py`,
  `cast/select.py`).
- **Dependency hygiene** (`deptry`): `imageio` unused; `pydantic_core` imported but undeclared;
  guide tooling (matplotlib, weasyprint, markdown, pygments) in no dependency group.
- **Workflow hardening**: actions pinned by SHA, `permissions: contents: read`, `concurrency` with
  cancel-in-progress, `timeout-minutes`, uv cache, zizmor for workflow linting, CodeQL, OpenSSF
  Scorecard, Dependabot or Renovate for uv, Actions and Docker.
- **Repository settings**: branch protection with required checks, CODEOWNERS, issue and pull
  request templates, SECURITY.md.
- **Pre-commit**: move the full test suite to `pre-push`; vendor or pin Prettier instead of
  `npx --yes` (non-hermetic); add check-yaml, check-toml, end-of-file, trailing whitespace,
  check-added-large-files, check-merge-conflict, `uv lock --check`, and typos or codespell.
- **Docker**: base image pinned by digest, a non-root user, multi-stage build, test the built
  wheel rather than an editable install (ADR 0001 claims the image installs the package; it does
  not), actually run the image in CI (today it is only built), a `.dockerignore` that excludes
  `.env`, `docs/`, `dist/` and caches, and no hard failure when `.env` is missing.

## 8. Documentation

- **Generated reference**: MkDocs Material with mkdocstrings (or Sphinx with autodoc), versioned
  per release, replacing the hand-kept 1,155-line reference. Its signatures already disagree with
  the code in places (for example `Workspace.commit`, `save_show`), it documents four names that
  are not exported, omits 24 that are, mentions a 3D world and "height" that do not exist, and
  holds about 175 lines of unbuilt `stagecheck`, packs and index designs, which should move to a
  "planned" page.
- **Diátaxis structure**: tutorials (the guide's tutorial and capstone on the site), how-to guides
  (import a field CSV, build a 300-spot opener, fix gaps, swap sheets, publish), reference
  (generated), explanation (architecture with diagrams of the module graph, engine pipeline and
  storage model).
- **New documents**: error-code catalogue, performance guide with the budgets above, migration and
  schema-versioning guide, CHANGELOG, deprecation, support and security policies, FAQ, published
  glossary.
- **Fix the stale and contradictory statements** listed by the audit: test and export counts in
  README, HANDOFF, ADR 0001 and guide chapter 16; the missing `docs/project-overview.md` link;
  CLAUDE.md calling the reference the source of truth while the reference and HANDOFF say the
  code wins; two different machine-specific Node paths; CONTRIBUTING titled "Contributing to
  Eight" and claiming Python 3.14 only; HANDOFF's stale in-flight work, module paths and "each fix
  has a regression test" (8 exist for 10 listed fixes); ADR 0002's hotspot forecast (`min_gaps`,
  not `segments`, dominates); 15 public functions without the `>>>` example the README promises.
  Write a superseding ADR for the layout instead of chained addenda.
- **Run the guide in CI**: all 65 exercises pass and the tutorial outputs match today, but nothing
  runs them; two chapter snippets disagree on macOS because of signed zero (fixed by normalizing
  `-0.0`).

## 9. Workstreams by package

### 9.1 Geometry kernel (`stage.geo`)

#### Confirmed defects to fix first

Every item below was reproduced. Each fix starts with a `test_regression_` test (project rule).

**Hangs and memory exhaustion (fix before anything else; untrusted input reaches these).**

| Where                                                                | Input                     | What happens                                                               |
| -------------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------- |
| `curve_base.py:205-211` `to_polyline`                                | `tol <= 0` or `tol = nan` | Never terminates (recurses to depth 24 on every interval)                  |
| `spacing.py:275-291` `scatter`                                       | `min_gap < 0`             | Infinite loop; the active list grows forever                               |
| `curves.py:410-418` `SpiralCurve`                                    | `turns = 1e7`             | Table of 1e10 samples, process killed; `turns = inf` leaks `OverflowError` |
| `analysis.py:54-81` `density_map`                                    | `cell = 1e-5`             | Allocates 2e10 cells                                                       |
| `spacing.py:172-207` `fill`, `spacing.py:89-101` `space_by_interval` | tiny interval             | 4M points in 23.6 s; 1e8 points in 4.2 s; no cap                           |
| `svg.py:375` arc flag unpacking                                      | many arcs                 | Quadratic: 8,000 arcs take 0.68 s, 80,000 take about 70 s                  |

Fix: validate every tolerance, gap, interval and turn count as finite and positive at the
boundary; add explicit output-size limits (`MAX_POINTS`, `MAX_TABLE`, `MAX_GRID_CELLS`) that raise
`GeometryError` or `SpacingError` with the size that would have been produced in `context`.

**Wrong results.**

- `to_polyline` aliasing (`curve_base.py:196-213`): five symmetric probes on eight even splits
  collapse periodic curves. `arc(..., sweep=32*360).to_polyline()` returns 9 identical points
  (length 0 instead of 201 m). This corrupts `region`, `offset`, polyline-fallback transforms,
  `bounds` and `centroid` for such curves.
- `project` picks the wrong local minimum (`curve_base.py:172-182`): on a 40-turn spiral it
  returns distance 1.55 m where the true closest point is 0.30 m away; on a 2,000-vertex zigzag
  0.112 m versus 0.0027 m. The 512-sample scan does not scale with length or turns.
- `offset` ignores `CircleCurve.ccw` (`shapes.py:337-351`): the outward offset of a clockwise
  circle shrinks it. `side` is unchecked; any value other than `"left"` means right. An outward
  offset of a closed curve comes back open (`shapes.py:355-362`).
- `arc_through` loses 4.8 cm of radius 1 km from the origin (`shapes.py:91-108`) because the
  determinant uses absolute coordinates; `COLLINEAR_TOL` is an absolute m² value.
- `is_similarity` uses an absolute tolerance (`affine.py:106-114`): a rotation times scale 1e5 is
  rejected (arc degraded to a polyline) and a non-uniform 1e-5 scale is accepted (arc kept,
  geometrically wrong). The singular test (`transforms.py:405-419`) has the same flaw.
- `TrimmedCurve` on an open base accepts `s0`, `s1` outside `[0, L]`: length 15 m on a 1 m line,
  while points clamp (`curves.py:474-506`).
- `CompositeCurve` checks neither continuity nor closure (`curves.py:427-442`); spacing 500 spots
  on a gapped composite produced a 59 m jump. `SvgCurve.closed` is not checked against the data
  and an open path wraps (`curves.py:346-368`). `SvgCurve.transform` shape is unchecked; a 1-row
  matrix gives a wrong length and wrong point shape.
- Closed-curve seams duplicate a spot: `space_by_interval(circle, L/10)` returns 11 spots with the
  first and last coincident (`spacing.py:89-101`); `space_with_gaps` repeats its start; glyph
  loops in `text_outline` (`O`, `0`, `B`, `8`, `D`, `Q`, `6`) are open polylines that repeat their
  start point, so spacing on them stacks two spots.
- `space_along` on a closed curve with `start > 0` does not wrap (one 1 m gap, the rest 10 m);
  `end < start` silently reverses; `end > L` duplicates the clamped end (`spacing.py:50-71`).
- `scatter` is not organic (`spacing.py:297-337`): single-seed Bridson never reaches a second
  component of a `MultiPolygon` (0 of 10 spots in the second square), starves thin regions (1 spot
  in a 100 x 0.05 m strip that `fill` fills with 101), and farthest-point down-sampling puts 12 of
  20 spots within 1 m of the edge.
- Symmetry measures match many-to-one (`measure.py:232-244`, `analysis.py:138-156`): ten spots on
  the left and one on the right score 0.993.
- `svg_outline` does not stretch a degenerate span (`shapes.py:252-256`), extracts `d` with a
  regex that also matches `data-d`, and ignores `transform`, `viewBox`, `<use>` and basic shapes
  (`shapes.py:57, 241-244`).
- SVG parser: `parse_path("M0 0 L1 Z")` leaks `ValueError`; valid packed flags followed by a
  fraction (`a1 1 0 01.5.5`) are rejected; flag values `2` or `0.5` are accepted; `1e400`
  becomes `inf`; Unicode digits (`٣`) parse; a path not starting with `M` is accepted
  (`svg.py:29, 288, 344-377`).

**Non-`StageError` exceptions leaking from public functions** (breaks project rule 7):
pydantic `ValidationError` (`model_copy` updates, `curve_from_construction({"kind": "nope"})`,
`Polyline(points="abc")`), `KeyError` (`space_by_interval(align="middle")`,
`block(anchor="middle")`), `IndexError` and `ValueError` (`svg_outline` on empty data or a bad
`path_index`), `ZeroDivisionError` (`_apportion` with zero lengths, `fill(interval=0)`,
`scatter(min_gap=0)`), `TypeError` (`regular_polygon(n=3.5)`, `scatter(seed=1.5)`),
`GEOSException` (`fill` on an empty area, `coverage(radius=inf)`), and numpy `RuntimeWarning`
from NaN or inf inputs (Bezier degree above 1,030, `arc(radius=inf)`, `rect(nan, ...)`).

Root cause for most NaN and inf leaks: `FrozenModel` (`core/types.py:37`) does not set
`allow_inf_nan=False`. Fix it there once, then add per-field constraints.

#### Numerical accuracy (measured)

| Curve            | Length error | Tangent error | Note                                                        |
| ---------------- | ------------ | ------------- | ----------------------------------------------------------- |
| ellipse 2 x 1    | 3.8e-6 m     | 4.4e-4°       | fine                                                        |
| ellipse 20 x 0.5 | 3.2e-5 m     | 7.2e-2°       | slender shapes degrade tangents                             |
| spiral 5 turns   | 2.5e-4 m     | 1.4e-3°       |                                                             |
| spiral 40 turns  | 9.9e-3 m     |               | grows with turns                                            |
| spline, 5 points | 1.3e-4 m     | 1.0e-2°       |                                                             |
| SVG cubic + arc  | rel 6.1e-6   | 6.0e-3°       | 256 samples per piece regardless of length                  |
| Bezier degree 40 | 2.9e-14      |               | Bernstein is accurate to about degree 1,000, then overflows |

Millimetre error is acceptable for drill, but the method costs memory proportional to turns,
gives tangents that are not C1, and cannot report curvature.

#### Algorithm upgrades

1. **Arc length by quadrature, not tables.** Per piece, adaptive Gauss–Kronrod (or 16-point
   Gauss–Legendre on subintervals) of the analytic speed `|C'(t)|`; invert `s -> t` with
   safeguarded Newton (bisection fallback) using a coarse cumulative table only as the starting
   guess. Closed forms where they exist: ellipse perimeter and partial arcs with
   `scipy.special.ellipe`/`ellipeinc`; Archimedean spiral arc length in closed form. Target:
   1e-10 relative error, memory independent of turns.
2. **Analytic derivatives on every curve kind** (`_d1`, `_d2`): exact unit tangents, exact signed
   curvature `(x'y'' - y'x'') / |C'|^3`, and a public `curvature_at(s)`. Bezier hodograph,
   Catmull–Rom (Barry–Goldman) derivative, SVG pieces in closed form.
3. **Robust projection.** Candidates from the adaptive polyline (exact segment projection through
   an STRtree over segments), then Newton on `(C(s) - p) · T(s)` for every candidate within the
   best distance plus chord tolerance. Return a result model `(s, point, distance)`.
4. **Feature-aware flattening.** Breaks at quadrant angles for arcs, ellipses and spirals, and at
   Bezier extrema and inflections (hodograph roots); flatness from the control-polygon bound for
   Beziers and `L² κ / 8 <= tol` elsewhere; one jittered probe; a hard vertex cap.
5. **Vectorized evaluation.** Convert Catmull–Rom and SVG pieces to per-segment cubic
   coefficients once, then evaluate with `searchsorted` and a Horner gather. Today a 3,000-point
   spline takes 3.9 s and a 5,000-piece SVG 5.9 s to tabulate. De Casteljau (or split into cubic
   pieces) above degree 20.
6. **Exact offsets.** Closed curves through polygon buffering with round joins, keeping the ring
   closed; composites piece by piece with trimming and fillets; report a split offset explicitly.
7. **Scatter with exact counts and blue-noise quality.** Seed every component; reach exactly `n`
   by Yuksel's weighted sample elimination (2015) over a dense Poisson set, or by bisection on the
   radius; optional Lloyd relaxation (centroidal Voronoi) clipped to the region; vectorize the
   containment test with `shapely.contains_xy`.
8. **`fill` with a target count** (solve the interval by bisection) and vectorized containment.
9. **Symmetry done properly.** One-to-one matching with `linear_sum_assignment` plus an unmatched
   penalty; search the best mirror axis at any angle (principal axes, then refine); rotational
   symmetry order `C_n` detection.
10. **Menger curvature** (`4·Area / abc`) for curvature from points: exact on circles, signed.
11. **Relative tolerances everywhere**: `arc_through` translated to the first point;
    `is_similarity` and the singular test normalized by the matrix norm.
12. **Ellipses stay ellipses under any affine map** (SVD of `M · diag(rx, ry) · R`), with a start
    angle and direction on `EllipseCurve`; spirals get a direction so mirroring keeps the family.
13. **A streaming SVG tokenizer** with grammar state, so a flag is exactly one `[01]` character
    and parsing is linear.

#### API and design changes

- Move set-aware wrappers out of `geo`: `transform_base`, `measure`, `analysis`, `transforms` and
  `snapping` import `stage.choreo.models.Set`, and `snapping`/`analysis` import `stage.sheet`.
  The README's layer diagram says lower layers never import higher ones. Keep `geo` pure
  geometry; put Set-aware versions in `choreo` (or accept a small protocol).
- Decide and document how facings map under skew and non-uniform scale. Today they map as
  tangent vectors, so skewing a front-facing block by 20° turns every performer 20°. Offer
  `facing="keep" | "vector" | "normal"`.
- Rename mirror axes: `"x"` means the fixed line x = 0 while `"y"` means a box-relative
  horizontal line; the names read backwards and mix fixed with relative.
- `centroid` of an array (vertex mean) and of a polyline (length-weighted) differ for the same
  geometry; make the weighting an explicit argument.
- Extend the `Curve` protocol with `substring`, `reversed`, `curvature_at`, `bounds` and a public
  `breaks()` (the engine calls the private `_breaks`); `offset` should return `AnyCurve`.
- `closed` must not be a free field on families whose closure is defined by the data (`SvgCurve`,
  `CompositeCurve`); derive it or validate it.
- `circle()` gets `ccw`; `expand`/`contract` on round curves change the radius instead of
  degrading to a polyline.
- Replace silent clamping and wrapping on open curves with errors (or an explicit
  `clamp=True`).
- Use the existing `pick()` helper for every string option (`align`, `anchor`, `pattern`,
  `method`, `side`) so typos raise `UnknownRefError` with suggestions instead of being ignored.
- `text.FONTS` and `SIMPLEX` are mutable module-level dicts, against the "no global mutable
  state" rule: freeze them (`MappingProxyType`) and make fonts an immutable registry.

#### Missing capabilities to add

Exact curvature and derivatives; per-kind exact `bounds()`; curve–curve intersection,
self-intersection test and `is_simple`; closest points between two curves; `split(s)` and
`extend(d)`; fillets and chamfers on polylines; convex hull and minimum bounding rectangle;
regions with holes from the constructor plus `area` and `perimeter`; SVG export of any curve; SVG
basic shapes, nested transforms and `viewBox` (parse with `defusedxml`); text with lowercase,
punctuation, non-ASCII fallbacks, multi-line layout, alignment and proportional advance (only 40
glyphs exist today: A–Z, 0–9, space, `-`, `.`); spacing with per-spot weights, exact-count
`fill`, hole- and corner-aware spacing, Lloyd relaxation; general-axis and rotational symmetry.

#### Keep

One `CurveBase` with arc-length parameterization; exact segments, polylines, arcs and circles;
deterministic `seeded_rng` use; cache-safe `model_copy`; correct SVG reflection, relative
commands, zero and out-of-range radii; mirror and rotation identities to 1e-14; optimal
assignment in `symmetrize`; largest-remainder apportionment; `pick()` with close-match hints.

### 9.2 Motion engine and paths (`stage.choreo` engine, paths, routes, facing, avoid, motion_stats)

#### Confirmed defects to fix first

Reproduced on in-memory stress shows (150 spots, 30 sets, 242 counts, every path style, tempo
ramps and holds inside transitions, entrances, exits, rectangle footprints, overlapping facing
overrides) and on synthetic crossing shows of 100 to 300 spots.

**The collision guarantee does not hold.** `segments()` claims every time-linear piece stays
within 0.02 m of the true path. Fixed probe shares (`engine.py:63`, `engine.py:315-329`) alias on
periodic custom motions: `weave` with 4 cycles compiles to one segment with 1.0 m error, and
`speed()` reports 4.0 m of travel for a 16.75 m path. In the 150-spot mixed show, 83 of 4,350
plans exceed the bound, all custom motions. The depth cap accepts silently. ADR 0002 decision 5
("exact to within `tol`") is false for these paths. This is the single most important defect:
the checker, `avoid` and `speed` all trust these segments.

Fix: seed time edges with every curve break mapped through the inverse time map (polyline
vertices, spline knots, arc ends, easing breaks); bound chord error per piece with a
second-derivative bound (`h² / 8 · max|p''|`, from curvature, speed and easing acceleration,
all closed-form for built-in kinds); use probes only for opaque curves, with seeded jittered
shares; raise (do not accept) at the depth cap. STRAIGHT moves with linear easing get exactly two
stations.

**Crashes and wrong answers on reachable inputs.**

- `IndexError` at compile when a timed waypoint survives a style change to HOLD, ROTATE, FOLLOW or
  CUSTOM (`engine_compile.py:191`). Reached through public API: `set_style` keeps old waypoints
  (`paths.py:131`), and `follow`, `follow_and_set_targets` and `rotate_path` call it.
- Switching a POLYLINE row to STRAIGHT keeps the bend (`paths.py:131`); the two
  `WAYPOINT_STYLES` constants disagree (`engine_compile.py:45` includes STRAIGHT, `paths.py:64`
  does not). `add_waypoint` silently turns ROTATE, FOLLOW, CUSTOM and HOLD rows into POLYLINE and
  leaves stale `route`, `pivot` and `motion_fn`. Fix: a style change clears parameters the new
  style does not use; one shared constant.
- Facing overrides are ignored for holding spots (`engine.py:223`) and are evaluated at the
  row's window time instead of the query time (`engine.py:221`), so TOWARD_ENTITY faces where
  the other spot was, not where it is.
- A custom motion that never leaves its start teleports 4 m at the set boundary: continuity is
  checked only when a curve exists (`engine_compile.py:141, 227`); ADR 0002 decision 4 requires it
  always.
- Custom motions are not validated per call: wrong output shape leaks numpy `AxisError`
  (`engine_compile.py:134-135`); NaN samples are dropped silently.
- `Easing.strength` is unvalidated (`models.py:374-402`): NaN gives NaN positions with no error;
  `inf` with `steps` leaks `OverflowError`; values below 1 are clamped silently
  (`motion.py:78`); `steps` with strength 1e4 builds 10,000 edges with no cap.
- Timed waypoints miss their `at` count whenever easing is non-linear (`engine_compile.py:262`):
  easing is applied before the knot map, so a waypoint "at 2" is reached at a different count.
  `suggest_waypoint` relies on `at` and therefore misplaces its bends on eased rows.
- `delay`, `stagger` and `ripple` on FOLLOW rows are silent no-ops (`engine_compile.py:160-162`
  reads only the route window).
- Path edits accept waypoints that a later delay or ripple pushes outside the window; the failure
  appears only at compile (`paths.py:89-96`). `stagger` and `ripple` fail halfway through with an
  error naming one spot and no hint about step times index.
- `two_part` bypasses locks (`routes.py:238-253`), breaking project rule 6.
- `invalidate()` consumes a generator on the first key and keeps stale entries (`engine.py:383`);
  `_override_cache` is never cleared.
- A show with no sets leaks `IndexError` (`engine.py:145-149`); a 0-count set (validation only
  warns) makes `compile_all()` and `segments()` raise `PathError` while `position()` works
  (`engine_compile.py:165`). A cast spot missing from one set's placements fails the whole
  transition.
- `make_route` with a single-vertex route leaks `IndexError` (`routes.py:97-99`).
- `min_gaps(top=0)` leaks `IndexError` (`avoid.py:122`); `min_gaps` measures spots before their
  entrance as if on field (`avoid.py:97-102`) and reports a collision that cannot happen.
- `suggest_waypoint` returns fixes that make the gap smaller (`avoid.py:206` never filters).
- Every helper in `avoid` and `motion_stats` builds `PathEngine(show)` with the default motion
  registry (`motion_stats.py:45, 57, 67, 85, 98, 128, 155`; `avoid.py:116, 149, 164, 191`), so any
  show using a custom-registered motion fails in them.
- Facing snaps up to 180° at window end even with `turn_counts > 0`
  (`engine_facing.py:121`: the outgoing span is 0 when the move ends at the transition end). In
  the stress show 3,151 of 4,200 boundary crossings jump more than 1°.
- `velocity_at` reports half speed at window kinks (central difference across the kink,
  `engine.py:231-235`).
- `closest_approach([], ...)` leaks `IndexError`; `energy_curve(per_count=-1)` leaks numpy
  `ValueError`.

#### Measured performance

| Measure                             | 100 spots | 200 spots | 300 spots |
| ----------------------------------- | --------- | --------- | --------- |
| `min_gaps`, one 16-count transition | 1.84 s    | 8.18 s    | 21.44 s   |
| `sample()` frames per second        | 276       | 125       | 53        |
| `compile()`, one transition         | 7 ms      | 17 ms     | 48 ms     |

`min_gaps` is slightly worse than O(N²) because `_pair_gap` rebuilds both spots' segments for
every pair. `sample` loops over spots in Python with three or more curve evaluations each, and
`locate` rebuilds `transition_ids()` on every call (14 µs with 200 sets). A 30 fps video of 300
spots is at the edge of real time. `compile_all` for 150 spots x 29 mixed transitions takes about
1 s, which is good. Positions are continuous at every set boundary (worst 3e-14 m) and
`closest_approach` matches brute force exactly; keep both.

#### Algorithm upgrades

1. **Guaranteed linearization** (above).
2. **Vectorized batch evaluation.** Compile each transition to a structure of arrays (knot times
   and arc lengths per spot, easing code, polyline vertices with cumulative lengths in CSR
   layout); `positions_grid(tid, taus) -> (T, N, 2)` evaluates all spots at once. Expected 50x to
   100x on `sample`, `frames`, video and `energy_curve`.
3. **Analytic velocity**: `ds/dτ` from the time map and the easing derivative, times the tangent.
   Fixes the kink error and removes two evaluations per spot per frame.
4. **Space-time broad phase for gaps.** Compute each spot's segments once; sweep-and-prune over
   swept boxes in x and time (or `shapely.STRtree` over segment boxes, or a uniform spatial hash),
   then a vectorized narrow phase. Target under 1 s at 300 spots (21 s today).
5. **Exact footprints.** Oriented-rectangle distance by the separating axis theorem on
   facing-rotated rectangles; circle–OBB for mixed pairs. Today a 0.6 x 1.2 m rectangle uses its
   0.671 m enclosing circle: two side-by-side rectangles 0.8 m apart (true gap 0.2 m) are reported
   at −0.542 m. Return every conflict interval (roots of `|d(t)|² = r²` per merged interval), not
   only the minimum, and clip tracks to their on-field intervals.
6. **Global conflict resolution.** Current fixes are per pair and can create new conflicts with
   third spots. Build the conflict graph from item 4; resolve with prioritized planning (order by
   slack, plan against reserved space-time) or conflict-based search with delay and waypoint
   branches; for pure timing, an integer program or CP-SAT over per-spot start and end offsets
   with disjunctive constraints, minimizing total shift plus a speed penalty. Recompile only the
   changed spot (`compile_entity` exists).
7. **Speed, acceleration and step size.** Add `accel_at`, `max_accel` and jerk; a speed- and
   acceleration-limited easing (trapezoidal or S-curve profile, or TOPP-RA for curved paths);
   `min_counts(entity, tid, vmax)`; `step_size(entity, tid)` in steps per 5 yards. `PathRow.pace`
   is stored but never read.
8. **Easing per knot interval** so timed waypoints hold their counts, plus analytic inverses
   (needed for "when does the spot reach s"). Rename or fix `steps`: it is a smoothstep per step
   and never pauses.
9. **Facing that plans ahead.** Start the outgoing turn before the window ends; let turns span
   set boundaries; add turn amount (full spins), flank direction for the 180° ambiguity, override
   turn direction and override IDs; ROTATE paths rotate facing with the sweep; rectangles rotate
   with facing in collision checks.
10. **Set-boundary continuity report**: per spot, the speed jump, direction change and facing jump
    at each set, for the checker and the designer.
11. **Instantaneous 0-count transitions** compile to a step with no segments.

#### API and design changes

- Split global and local time: `at(t)` and `at_local(tid, tau)` (or a `Moment(tid, tau)` value)
  instead of `local=tid`, which reads like a bool.
- Arrays for bulk results, models for single records: `segments()` returns 85,000 validated
  pydantic objects for one show today. Add `positions_grid`, `trajectories(tid)`, per-transition
  bounds and swept areas, and segments as arrays.
- `avoid` and `motion_stats` accept an engine (or `motions=`) instead of building one per call;
  `position_at(show, ...)` compiles a whole transition for one point.
- Remove or use dead fields: `MotionPlan.pieces`, `MotionPiece.kind`,
  `CompiledTransition.warnings` and `PathRow.pace` are never read.
- Make `PathEngine.show` read-only and make `CompiledTransition.plans` immutable (it is a mutable
  dict in a cached, shared, frozen model).
- `apply_fix` and `two_part` take `force`; add `rotate_and_set_targets`; give `FacingOverride` an
  ID so it can be edited or removed; drop the duplicate `turn` re-export from `facing.py`.
- Make facing values a tagged union (`FixedFacing`, `PointFacing`, `EntityFacing`) instead of
  `Degrees | Point | SpotId`, and `Fix.params` a discriminated union per fix kind. This also
  removes the `type: ignore` comments in `engine_facing.py`.
- `compile` collects every bad spot into one report instead of failing on the first.
- Bound the compile caches (LRU) and make them safe for free-threaded Python.

#### Keep

Clean separation of curve and time map; exact `closest_approach`; monotone easings exact at both
ends; lock handling through one `_edit` helper; immutable motion registry; deterministic outputs;
fast compiles.

### 9.3 Foundations (`stage.core`), cast, notes and time

#### Confirmed defects to fix first

**Determinism is broken by hash order** (project rule 3).

- `Spot.tags` and `Cast.selections` are `frozenset`s (`cast/models.py:118, 158`) and serialize in
  hash-seed order. Under `PYTHONHASHSEED` 1, 2 and 3 the same show dumps its selection as
  `T3,T1,T2,T4`, `T3,T1,T2,T4` and `T2,T4,T1,T3`, and `content_hash(show)` changes. That breaks
  `source_hash`, staleness, golden fixtures and file diffs. Store ordered, deduplicated tuples.
- `format_ranges` ties (`T02` against `T2`) keep set order (`core/util.py:102`), and
  `select(show, {...})` on a Python `set` returns four different orders under four seeds
  (`cast/select.py:328-329`). Make the natural-sort key total and reject or sort set inputs.
- `seeded_rng(None)` returns an unseeded generator (`core/util.py:131`). Require a non-negative
  `int` and pin the bit generator (`Generator(PCG64(seed))`) so results survive a numpy default
  change.
- CI must run the suite under several `PYTHONHASHSEED` values to catch this class of bug.

**`content_hash` is neither canonical nor collision-free** (`core/util.py:166-174`): `0.0` and
`-0.0` hash differently, as do `1` and `1.0`; the string `'{"a":1}'` and the object `{"a": 1}`
collide; `{1: "a"}` and `{"1": "a"}` collide; mixed key types leak `TypeError`; two different
2,000-element numpy arrays hash equal because `default=str` truncates them with `...`; nested
models are hashed by their `str()` repr; NaN is written as non-standard JSON; a lone surrogate
leaks `UnicodeEncodeError`. Replace it with an RFC 8785 (JCS) canonical encoder: recurse into
models, sets (sorted), tuples and arrays; normalize `-0.0`; reject NaN, inf and unknown types;
prefix a type tag for domain separation.

**`StageError` cannot be pickled or deep-copied** (`core/errors.py:44-56`): `pickle.loads(
pickle.dumps(UnknownRefError("x", hint="h")))` raises `TypeError` for the missing keyword-only
`hint`. That breaks multiprocessing and process isolation. Add `__reduce__`; test every subclass.

**Rename and delete do not reach every reference.**

- `rewrite_spot_ids` (`cast/rewrite.py:56-74`) skips `Show.pins` and `Show.sheet.zones`: after
  `relabel`, a pin still names `T1`, which no longer exists; `delete_spots(force=True)` leaves the
  dangling pin; a sheet zone keeps `applies_to='T1 + T2'`. It also skips `PathRow.motion_params`,
  `Form.geometry` and `meta`, comments, and published claims.
- A stale stored selector anywhere (for example a zone naming a deleted spot) blocks every
  unrelated delete or relabel with `UnknownRefError` (`cast/rewrite.py:125-132`); a selector in
  the old glued syntax (`T1+T2`, valid before 2026-11-12) blocks every relabel with
  `SchemaError`. Resolve stored selectors leniently during rewrite and report; migrate old syntax
  on load.
- Deletion leaves empty references: routes with no members, facing overrides with no entities,
  notes anchored to no spots (`cast/rewrite.py:90-142`). Drop or flag them and return a
  `RewriteReport`.
- A many-to-one rename fails with the wrong class (`UnknownRefError: duplicate spot id X`, no
  context), and `_rewrite_spec` silently overwrites one row first. Check injectivity up front.

**Locks and pins are not enforced on cast edits** (rule 6): `add_spot`, `add_spots_along`,
`fill_spots` and `duplicate_spots` write unpinned placements into pinned sets; `regroup` and
`relabel` change pinned spots and locked form members; none takes `force`
(`cast/spots.py:96-105`). Decide the policy (for example, renames allowed, positions protected)
and apply it to every edit.

**Timeline correctness.**

- Tempo sections are not required to be sorted (`time/clock.py:68-83, 140-147`): out-of-order
  sections give `count_to_seconds(16) = 8.0` instead of 6.0 with no error. Only
  `validate_timeline` notices.
- NaN and inf pass every edit: `add_tempo(at_count=nan)`, `add_hold(seconds=nan)`,
  `add_marker(at_count=nan)`, `new_timeline(total=nan)`, `counts_per_minute=inf`; a negative
  `ramp_to` and a `ramp_counts` without `ramp_to` are accepted. `seconds_to_count(nan)` returns
  `0.0` (`max(0.0, nan)`); `tempo_at(nan)` leaks `IndexError`.
- `remove_tempo(tl, 0.0)` leaves a timeline every conversion rejects.
- Duplicate cue `order` makes `cue_to_seconds` start both cues at 0; validation reports ok.
- Measures: `counts_per_measure=0` leaks `ZeroDivisionError`; NaN length leaks `ValueError`;
  negative length returns measure −1; duplicate change counts resolve by length, not input
  order (`time/measures.py:28-61`); fractional measure numbers are accepted.
- `validate_timeline` passes negative `total_counts`, NaN holds and tempos, unsorted holds, holds
  past the end, duplicate cue orders, NaN gain and infinite sync offsets. Its hold-overlap rule
  is an error, while the clock sums overlapping holds and `add_hold` replaces them: three
  policies for one concept.
- `describe_count(1234567.5)` prints `1.23457e+06`.

Fix all of this at the model boundary: constrained types in `time/models.py`
(`Field(ge=0, allow_inf_nan=False)` for counts and seconds, `gt=0` for tempos), an after-validator
on `Timeline` for order and uniqueness, and on `TempoSection` for the ramp pair. Keep
`validate_timeline` for cross-field warnings only.

**Core helpers.**

- `as_points` scrambles 3D Shapely coordinates (`LineString((0,0,5),(1,1,7))` gives
  `[[0,0],[5,1],[1,7]]`), crashes on 3D points and multi-part geometry, and returns a _view_ of the
  caller's array, so later in-place math corrupts caller data (`core/util.py:140-157`).
- `round_to` leaks `decimal.InvalidOperation` on numpy scalars (numpy 2 `repr` is
  `np.float64(...)`), bools, inf and zero precision (`core/units.py:156`); returns `-0.0`.
- `DEFAULT_UNITS` can be mutated process-wide through its dict
  (`DEFAULT_UNITS.meters_per_unit["hack"] = 9.0` works); a directly built registry with a 0 factor
  leaks `ZeroDivisionError`; `register_unit` silently redefines `m`, accepts `inf` and an empty
  name, and raises `UnknownRefError` for a bad size.
- `normalize_angle(-0.0)` returns `-0.0`, `inf` leaks `ValueError`, NaN passes.
- `natural_sort` crashes on Unicode digits (`'²'.isdigit()` is true) and on numbers over 4,300
  digits.
- `approx_equal(inf, inf)` is false and emits a `RuntimeWarning` (warnings are errors in tests).
- `chunk_counts` does not keep its "sums exactly to total" promise and accepts NaN totals.
- `parse_ref` accepts `..` and `/` in IDs (`core/ids.py:26`): a path-traversal risk the moment a
  reference reaches a file path. `format_tid` silently drops a third element.
- `Selection(ids="T12")` gives `('T','1','2')`; `model_copy` keeps duplicates (and `rewrite.py`
  uses exactly that call); equality depends on the display `description`.
- Mutable containers inside frozen models (`Group.limits`, `Cast.groups`, `Cast.selections`,
  `LabelScheme.prefixes`, `UnitRegistry.meters_per_unit`): `show.cast.groups.pop(...)` mutates a
  frozen show.

**Selection grammar (takes external text).**

- `select(show, generator)` silently returns an empty selection (the iterator is consumed by
  validation, `cast/select.py:291-301`).
- Bad selector types leak `TypeError` and `AttributeError`.
- 500 nested parentheses leak `RecursionError` (`cast/select.py:265-288`).
- Ranges expand before their endpoints are checked: `select(show, "T1-T4000000")` spends 0.6 s
  building 4 million strings before failing; `T1-T999999999` tries 1e9
  (`cast/select.py:155-161`, `core/util.py:92`).
- `ID_PARTS` backtracks quadratically (`core/util.py:39`): a 20,000-character ID takes 1.3 s,
  and it runs per universe ID per `T*` token.
- Ranges work only for purely alphabetic ASCII prefixes; `P1x1-P1x10` and `flag-1-flag-3` fail
  while `T*` works for the same prefixes; `\d` accepts Unicode digits, so `T٣` is read as `T3`.
  `format_ranges` writes an en dash that `parse_range` cannot read back; zero-padding width comes
  only from the first endpoint.
- Spot IDs are unconstrained: empty IDs, IDs with spaces or parentheses, and IDs such as `all`,
  `T*` or `group:1` (which `relabel` can produce from a prefix) cannot be selected.
- `mirror_pairs` returns fewer pairs than possible because it filters by tolerance after a
  min-sum solve (`cast/select.py:503-508`).
- `split(['a'], 10**7)` takes 35 s and builds 10 million empty selections.

**Notes.** A degenerate area anchor (`Region` with two points) is accepted and later leaks
`ValueError` from Shapely in `notes_near`; `Region` itself accepts fewer than three points, NaN and
self-intersection (`core/types.py:82-94`). `accept_draft` cannot accept a hidden draft already in
the show and skips the blank-text check (`notes/notes.py:222-233`). Naive datetimes are accepted
for `created_at`, so comparing notes can leak `TypeError`.

**Model constraints missing in `cast`.** Negative radii, invalid colors, NaN limits, negative
sizes on the inactive shape kind, `LabelScheme(pad=-3)` (leaks `ValueError`) and `pad=10**7`
(10 MB labels) are all accepted. `Cast` does not check that a group's key matches its dict key,
that selections name existing spots, or that prefixes name real groups.

#### Algorithm upgrades

1. **Compiled clock.** Today `_pieces(tl)` is rebuilt on every call and holds re-scan all pieces
   (26 ms per call with 1,500 holds). Compile once per timeline into cumulative tables (piece
   starts, cumulative seconds including holds, slopes), cache it on the frozen model, answer with
   `bisect` in O(log n), and add vectorized array conversions for the engine and video. Keep the
   analytic `log1p`/`expm1` ramp formulas: they round-trip within 7e-10 counts at a million counts.
2. **Tempo curve shapes.** Offer ramps linear in time and exponential ramps in addition to linear
   in counts, as DAW and MIDI tempo maps do; all have closed forms.
3. **Selection parser.** Replace recursive descent with an iterative Pratt parser and conventional
   precedence (`&` above `+` and `-`), or reject mixed operators without parentheses; today
   `T1 + T2 & T2` gives `T2` while `T2 & T2 + T1` gives `T2, T1`. Evaluate on integer index sets,
   not a new pydantic `Selection` per operator. Resolve ranges by filtering parsed
   `(prefix, number)` pairs, never by expanding them. Cap nesting depth and input length.
4. **`mirror_pairs` as maximum-cardinality matching** within tolerance (big-M costs, or a sparse
   KD-tree graph with `min_weight_full_bipartite_matching`).
5. **`fill_spots(n=...)` by centroidal Voronoi tessellation** (Lloyd's algorithm, seeded) instead
   of about 70 lattice fills cropped to the centroid; hexagonal packing fits about 15% more spots
   at the same spacing.
6. **Exact units and chunks.** Store unit factors as `Fraction` (`convert(1, "ft", "in")` returns
   `11.999999999999998` today); compute `chunk_counts` from boundary differences.
7. **Linear ID splitting** (`re.search(r"\d+\Z", s, re.ASCII)`) shared by ranges, `T*` and
   `label_prefix`.

#### API and design changes

- Constrained annotated types in `core.types` (`FiniteFloat`, `NonNegCount`, `PositiveCpm`,
  `Meters`) used by every model, and `allow_inf_nan=False` on `FrozenModel`.
- One `SpotId` pattern enforced on `Spot`, label prefixes and group prefixes, with `all` and `*`
  reserved. `NewType` for `SpotId`, `SetId`, `FormId` and the other IDs, and a `NamedTuple` for
  `TransitionId`, so mypy catches swapped IDs.
- Public `rename_spots(show, mapping)` (the machinery is private today) with an injectivity check
  and a `RewriteReport`.
- Named selections become ordered `Selection`s, with a choice between frozen lists and live
  expressions; `define_selection` gets `replace=False`.
- `Selection` stops overriding `BaseModel.__iter__` (it breaks `dict(model)` and needs a
  `type: ignore`), excludes `description` from equality, accepts iterables in its operators and
  rejects a bare string.
- Typed patch models (or `TypedDict` with `Unpack`) instead of `**changes: Any` in `edit_group`,
  `edit_note` and timeline edits.
- Timeline identity rules made consistent (by label everywhere), and the missing edits added:
  `edit_tempo`, `edit_hold`, `move_marker`, `edit_cue`. Float `==` lookups in `remove_tempo` and
  `remove_hold` replaced by ID or tolerance.
- `ValidationReport` gains `errors`, `warnings`, `raise_for_errors()`, `codes`, `to_json()` and a
  per-issue `context`.
- Rename `stage.core.errors.ValidationError` (it clashes with pydantic's and forces aliasing in
  several modules), keeping a deprecated alias. Make `DeprecatedWarning` subclass
  `DeprecationWarning` and use `warnings.deprecated` (PEP 702).
- An error-code catalogue (an enum or `Literal` of codes) so agents can switch on codes, plus
  `StageError.to_dict()`, a `repr` with code and context, and messages that truncate echoed input
  to about 80 characters.
- `Set.get` and `Cast.spot` rebuild or scan on every call, making `ordered_along`,
  `mirror_pairs`, `regroup` and `duplicate_spots` quadratic (4x the spots costs 15x the time).
  Cache an index on the frozen model.
- `log` writes unquoted `key=value` fields, so a value containing a newline forges log lines;
  quote values and format lazily.

#### Missing capabilities

Grammar: complement, symmetric difference, comma lists, case-insensitive keywords, stepped and
reversed ranges, `first:N`, spatial words (`zone:`, `near:`), form slot ranges, precompiled
selectors. Cast: batch `add_spots`, tag edits, `merge_groups`, label schemes with per-group
start, pad and skip lists, seeded `shuffle`. Time: time signatures with a beat unit and compound
meter, pickup measures as a field, fermatas in counts, `total_seconds`, `marker_to_seconds`,
`measure_to_seconds`, count grids, SMPTE and `mm:ss.ff` formatting and parsing, beat-map import
from audio that informs sync without changing timing. Notes: notes anchored to a count or count
range, a form or a zone; filtering by kind and author; resolve, archive and `edited_at`.

#### Keep

Analytic, exact clock with a closed-form inverse; clear hold semantics; measure numbering that
handles changes mid-measure; precise selection error positions and close-match hints; one rename
choke point; `delete_spots` refusing to orphan facings; Decimal half-away-from-zero rounding
(`2.675 -> 2.68`); `FrozenModel` with `extra="forbid"`.

### 9.4 Sheets (`stage.sheet`)

#### Verified correct

The field templates match the rule books: 120 yd by 160 ft for NFHS, NCAA and NFL, with hashes
53'4" (NFHS), 60' (NCAA) and 70'9" (NFL) from each sideline; goal lines at ±50 yd, yard lines
every 5 yd, 10 yd end zones. The gym is a correct NBA court. Coordinate wording round-trips for all
four audience sides, stage kind, landmark origins, feet and yards.

#### Confirmed defects to fix first

- **Swapping one football field for another distorts the drill** (`replace.py:104-131`). The
  three fields share yard lines and sidelines, but the fit matches all same-named point
  landmarks, including the 42 hash points that differ by design. High school to NFL gives scale
  0.950 and a 4.9 m residual; on a 240-spot, 48-set show spots move up to 2.57 m where the right
  answer is no movement. Fit on structural landmarks only, robustly (RANSAC then least squares, or
  Huber IRLS); short-circuit to identity within one template family; report per-landmark and RMS
  residuals and refuse or warn above a threshold.
- **A mirrored landmark set gives a silent, badly wrong fit** (`replace.py:68-101`): scale 0.58,
  every spot off the sheet, no warning. Detect `det < 0` and offer `allow_reflection` or raise.
- Coincident target landmarks collapse the show to a point (only the source spread is checked);
  no matched landmarks emits `RuntimeWarning: Mean of empty slice` before the length guard; NaN
  or wrongly shaped input leaks `LinAlgError`; `scale` mapping from a zero-size sheet leaks
  `ZeroDivisionError`.
- **`Sheet` has no validators** (`models.py:190-223`): negative size, a one-point outline, ref
  `"nonsense"` and duplicate landmark IDs are accepted, and then `on_surface`, `walkable` and
  rendering leak Shapely and Pillow errors. `Region` accepts two-point and bow-tie rings, so
  `zones_at` gives wrong coverage. A `GuideLine` with no points crashes rendering with
  `IndexError`.
- **`CoordinateSystem` is validated only in `set_coordinates`** (`coords.py:119-126`): a show
  built directly or loaded with `import_json` can carry `precision=0`, and `to_coords` then
  leaks `decimal.DivisionByZero`. `to_coords` on `inf` leaks `InvalidOperation`; on NaN it prints
  `nan m right of center`; an unknown `style` silently means long style.
- **Custom wording labels can produce text the parser cannot read back**
  (`coords.py:130-139, 221-235`): labels containing digits, commas, or colliding with another
  label; `" of "` is hard-coded English. The parser rejects `-3.5 m`, `R.5`, `R1e3` and `3m`.
- `landmark_anchor` uses the vertex mean, not the area centroid, and double-counts a repeated
  closing vertex (`bounds.py:95-104`). `nearest_landmarks` accepts `k=1.5` (leaks `TypeError`),
  NaN points and unknown kinds.
- `blank_sheet(10, 6, grid=2)` and `blank_sheet(10, 6, units="ft")` get the same ref but differ
  in content (`templates.py:188-198`); `blank_sheet(inf, 5)` is accepted.
- `customize(size=...)` leaks `ValueError` and pydantic errors, keeps a stale ref and name after a
  resize, and silently keeps zones and landmarks that now lie off the sheet
  (`templates.py:222-261`). `get_sheet(ref, store)` returns template @1 even after @2 was saved,
  against its docstring. `sheet_templates("nope")` returns `[]`.
- **`render_sheet(fmt="pdf")` embeds the wall clock** (Pillow writes `CreationDate` and
  `ModDate`): two calls a second apart differ, breaking rule 3 (`render.py:319-320`). Render PDF
  through `export/pdf.py` instead.
- SVG output is not well-formed when a name holds a control character (`render.py:247-293`);
  zone holes are ignored in PNG and SVG; the canvas fits only the sheet plus 1 m, so a front
  ensemble at y = −2 m never appears in renders or video.
- "center" means three different points on three template families, so cross-family replacement
  matches the wrong landmarks (blank to stage scales everything by 0.45). Name and alias
  collisions between landmarks are accepted and resolve by order. Sheet and show zones may share
  an ID.

#### Upgrades

1. **Field language as a real `PositionLanguage`** (the pack design CLAUDE.md already describes):
   nearest yard line with "on" or "N steps inside/outside", side 1 and side 2, front and back
   sideline and hash references, configurable step size (8-to-5 is 22.5 in, 6-to-5 is 30 in),
   quarter and half steps with an explicit tie rule, and the inverse parser. Today
   `CoordinateKind.MARCHING` behaves exactly like `GRID`. Localizable templates (ICU
   MessageFormat or gettext) and a property test that every label set round-trips.
2. **Prepared geometry**: cache `shapely.prepare` per zone and an `STRtree` per sheet; `_flags`
   converts Regions to Shapely for every placement, zone and set today.
3. **Data-driven templates** with a schema, versions and rule-book citations by edition. Add
   field numbers, 1-yard ticks, hash ticks (not a continuous line), team boxes; HS and FIBA
   basketball with three-point arcs; Canadian football (110 x 65 yd, 20 yd end zones); soccer;
   WGI indoor floor (60 x 90 ft); track around a field; parade/street and arena (both enum values
   exist without templates); thrust and in-the-round stages; areas in front of and behind the
   field.
4. **Rendering quality**: supersampled antialiasing; load fonts once; bundle a Unicode font;
   physical units in SVG for true print scale; `<title>`, `<desc>` and ARIA roles; even-odd fill
   for holes; a `Theme` model with light, dark and high-contrast palettes.

### 9.5 Export and publishing (`stage.export`)

#### Verified correct

The hand-written PDF writer is spec-correct: `pdfinfo` and `pypdf` in strict mode accept it, xref
offsets and stream lengths match, and output is byte-identical across runs. Video frame timing
follows the music clock exactly through ramps and holds (2,870 of 2,870 expected frames over
119.5 s), frames are streamed to ffmpeg rather than held in memory, and dimensions are even.
Claim invariants (one spot per performer, role gating, live-only) hold.

#### Confirmed defects to fix first

**Security and privacy.**

- **Publication links are guessable and claims are unauthenticated** (`publish.py:123`): the link
  is `base_url/<publication id>`, and any caller can claim with a self-declared `Performer`. Add
  a high-entropy join token supplied by the caller (the library must stay deterministic), stored
  only as a hash, and optional per-performer invite tokens.
- **Contact details are stored inside the publication snapshot** (`export/models.py:135, 155`):
  `Claim.performer.contact` serializes with it, and `claims()` and `notify_changes` return full
  performer records. Keep contacts in a private roster keyed by performer ID and expose a
  redacted public view. Offer initials or redaction in printouts.
- **CSV injection** (`data.py:262-274`): a performer named `=HYPERLINK("http://evil")` lands in a
  chart CSV cell unchanged. Prefix `'` to cells starting with `= + - @`, tab or carriage return
  (OWASP), with an opt-out for machine pipelines.
- `base_url` is not validated: `javascript:alert(1)` is accepted and query strings are mangled.
  Parse with `urllib.parse` and require `https`.
- `qr_code(size=100000)` allocates a 10 GB image (memory DoS); an overlong link leaks
  `segno.DataOverflowError`; the documented level "M" is really Q because segno boosts by default.

**Claim state machine** (`publish.py:111-152, 252-415`).

- `pause(unpublish(pub))` reopens a closed publication; republishing from a closed one silently
  goes live. Make transitions explicit (live and paused both ways, anything to closed, closed to
  live only through an explicit reopen).
- `claim_spot` and `reassign` accept naive datetimes and claims dated before `published_at`.
- `reassign` takes no show and validates nothing: unknown spots, crew on performer spots and
  inactive spots are all accepted.
- Republishing after a spot rename drops the claim and tells the performer "spot removed"; accept
  the rename mapping that `rewrite_spot_ids` produces.
- `notify_changes` misses deleted sets, new sets and coordinate-system changes (which change every
  printed position).
- `performer_view` ignores `status`, does not check `pub.show_id == show.id`, and gives a
  designer-facing hint to a performer after a deletion.
- There is no version or etag on `Publication`: two app instances claiming from the same snapshot
  both succeed and the conflict is undetectable. Add a monotonic version and a compare-and-swap
  merge helper.

**Print and data.**

- Wide tables run off the page: a 40-column cue sheet prints text at x = 3,070 pt on a 792 pt page;
  titles are never clipped (`pdf.py:122-151`).
- **Non-Latin names print as `?`** (`pdf.py:80`): Cyrillic, CJK, Arabic, Vietnamese and emoji are
  lost. For an international product this is a real defect, not a limitation.
- CSV floats use `:g` (six significant digits): `1234567.0` becomes `1.23457e+06`, and cue seconds
  above 1,000 lose precision (`data.py:230`). Coordinate-sheet CSV drops the entity and performer,
  so concatenated sheets cannot be told apart.
- `to_text` keeps `\r` and `\t` and misaligns wide CJK characters. `export_json(include="title")`
  iterates characters; `import_json([1])` leaks `AttributeError`.
- Crossing labels are ambiguous (both sides' 30 are labelled `30`), and an out-and-back across the
  same line is reported once (`print_sheets.py:129-152`).
- `import_positions` fails on a UTF-8 BOM and on semicolon-delimited CSV (common Excel exports),
  stores facing 720 unnormalized and accepts IDs with spaces.

**Video** (`video.py`).

- Audio shorter than the range truncates the video (`-shortest`: a 4 s range became 0.5 s); a
  negative `sync_offset_s` (count 0 before the music, a legitimate case) produces a file without
  audio; an offset past the audio returns a corrupt file and no error. Only the first track is
  used, and `gain` and `section` are ignored.
- The inclusive end frame lengthens every video by one frame and duplicates boundary frames when
  ranges are concatenated; use frames at `k / fps` over `[t0, t1)`.
- Not faststart (`moov` after `mdat`), so web playback downloads the whole file first. No colour
  tags (BT.709). No pixel cap (`video_canvas(show, 1000)` is 42,000 x 25,000).
- Per frame, `show.cast.ids()` is rebuilt twice per spot and `cast.spot()` scans linearly: 70% of
  render time at 240 spots. With engine sampling, a two-minute video takes 70 s at 672 x 306.
- ffmpeg writer failures leak unwrapped; `subprocess.run` has no timeout.

#### Upgrades

1. **PDF**: FlateDecode compression (`zlib`, standard library; the 473-page, 5.9 MB book should
   shrink 4x to 6x); real Helvetica metrics; column splitting and cell wrapping; embedded subset
   TrueType (Type0 with ToUnicode) for any script, plus shaping and bidi for Arabic and Hebrew;
   `/Info` with title and an explicit `at`; `/Lang`; optional tagged PDF (PDF/UA); A4 and portrait;
   per-performer page numbering; vector field pictures so charts and dot books show the field,
   not only tables.
2. **Video**: render the background once and paste per frame; precompute per-spot styles; batch
   positions from the engine; faststart, BT.709 tags, CRF and preset; explicit `-t` with audio
   padding instead of `-shortest`; correct negative offsets; streaming output to a file object.
3. **Streaming everywhere**: `export_frames_csv` builds 14.9 MB in one string (24 s); offer
   writers that take `IO` objects, plus progress and cancellation callbacks for video, books and
   frame exports.
4. **Typed results instead of pandas attrs**: `cue_sheet` returns a `DataFrame` with
   `attrs["title"]` (experimental, lost by most operations). Return a typed table model and make
   pandas an optional extra.

#### Missing capabilities

Per-performer dot books with a mini-field picture and step counts; drill chart booklets that draw
dots on the field; staff cue sheets with measures; GIF, WebM, PNG sequences, animated SVG;
count and measure overlays, performer highlight, follow-cam, a press-box perspective view;
MusicXML and MIDI tempo-map import; a positions-CSV importer registered in `DEFAULT_FORMATS`;
claim expiry, self-release, waitlists, claim history; localized wording and A4 paper.

#### Keep

Pure, frozen data flow with explicit `at`; one `SheetCanvas` shared by PNG, video and print; the
spec-correct deterministic PDF writer; music-clock video timing; streamed frames; true inverse for
default coordinate wording; line-numbered import errors.

### 9.6 Forms and matching (`stage.choreo` models, forms, form\__, match_)

#### Verified correct

Matching is exact and deterministic: brute force over 500 random instances (n up to 5, with
groups, slot groups and forbidden pairs) found no non-optimal result for any objective, and
results do not depend on the input order of spots or slots. Lexicographic `bottleneck_then_sum`
is exact. The order-preserving dynamic program is exact and O(n·m). Infeasibility diagnosis names
the blocking constraints. Gate, split and merge chains preserve positions exactly.

#### Confirmed defects to fix first

**Forms and placements drift apart** (the core promise is "edit the form, not the dots").

- `apply_match` writes only x and y (`match_ops.py:79-101`): after matching T4..T1 onto a row,
  T1 stands at slot 3 but still says `order=0`, facings are stale, `Form.members` is unchanged,
  and `rebuild` moves everyone back. `swap` updates members; `apply_match` must too (or refuse on
  formed spots without `force`). `MatchResult` needs slot provenance (form ID and slot index).
- `place` leaves stale form references (`form_place.py:107-138`): placing a set again with a new
  form leaves other spots pointing at the old one; a pinned member leaves an empty slot that
  `filled_forms` reports as "open slots between its members". On a 300-spot, 22-set show with seven
  mixed forms per set, `filled_forms` failed on every set, because matched spots take the nearest
  open slots, not the lowest-index ones. `place` returns only the Set and never writes
  `show.forms`. Return the set and filled forms together and make open slots explicit
  (`members: tuple[SpotId | None, ...]`).
- `rebuild` drops the stored path rows of members that did not move, raises `LockedError` on a
  locked row even when nothing moves, and `force=True` still does not move pinned members
  (`form_place.py:179-203`), against rule 6.
- `edit()` leaves a stale `n` in block and custom recipes, so `slot_count` says 4 while layout
  produces 6 (`form_ops.py:97-125`); `edit(n=None)` cannot clear `n` on curve kinds; `edit`
  accepts more members than slots.
- **`transform_form` is not exact, against its docstring** (`form_ops.py:345-376`): a skewed circle
  keeps `kind="circle"` with a polyline curve and slots off by 1.09 m; meter-valued `SpacingSpec`
  fields are never scaled, so a uniform ×2 scale of a windowed line is off by 5 m and an
  interval-spaced line goes from 6 to 11 slots; audience facings are never mapped. Scale spacing
  by the similarity factor; for non-similar maps store the recipe plus a transform applied after
  spacing (exact by construction), or convert to `custom`.
- Mutable dicts inside frozen models: `form.geometry["n"] = 5` changes a frozen form, and
  `DEFAULT_FORM_FNS.fns["evil"] = ...` changes the global registry; forms are unhashable.
- `model_copy(update=...)` skips validation and is used in about a dozen edit paths (for example
  `Set(...).model_copy(update={"counts": -4})` yields a negative count and duplicated
  placements). Add `FrozenModel.replace(**changes)` that revalidates and use it everywhere.
- `clear_rows` reorders `show.transitions` (creating diff noise); `match_by_order(["A", "A"], ...)`
  silently collapses to one spot; `TransitionSpec.routes` keys are not checked against `Route.id`.

**`detect_form` breaks its own tolerance and misclassifies** (`form_fit.py:129-160`): three dots at
x = 0, 1 and 10 are accepted as a line although one spot moves 4.0 m (tol 0.1), because slots are
re-spaced evenly after the fit; a 2 x 2 block is a "circle" and a triangle or an L is an "arc"
(any three or four concyclic points fit exactly); one outlier turns a clean circle into `custom`;
`fit_line([])` leaks `RuntimeWarning` and `ValueError`; collinear input returns a garbage circle.

**Validation gaps.** NaN and inf pass `Set.counts`, `PathRow.start`, `MatchOptions.weights`,
`FacingSpec` points; negative `no_backward`, `TransitionSpec.counts` and gaps are accepted;
facings are never normalized (720 stays 720). `fill_form(interval=0)` and
`scatter_form(min_gap=0)` leak `ZeroDivisionError`; **`scatter_form(min_gap=-1)` hangs**; a
misspelled recipe key (`"anchr"`) or pattern (`"hexx"`) is silently ignored; a bad block anchor
leaks `KeyError ''`; arcs over 360° produce coincident slots; fractional `n` is truncated; a
`bool` passes as a slot index (`MatchOptions(pinned={"A": True})` pins to slot 1).

**Raw exceptions** from generators (`turns="x"`, `turns=None`, `wavelength=0`), registration
probes, `cost_fn` callbacks, `morph_targets` (IndexError), `reorder("by_x")` (ValueError),
`svg_form("")`, `edit(radius="abc")` (pydantic), numpy generator params (serialization error),
and NaN starts (an empty, confusing `MatchError`).

#### Measured performance and quality

| n   | default objective | with crossing penalty (sum)        |
| --- | ----------------- | ---------------------------------- |
| 50  | 2 ms              | 0.17 s                             |
| 150 | 13 ms             | 1.82 s                             |
| 300 | 57 ms             | 9.49 s                             |
| 500 | 0.15 s            | about 44 s per pass (extrapolated) |

Minimum separation over time for 100 spots on a 1.5 m grid with simultaneous straight moves:

| Objective                       | Minimum separation      | Spatial crossings |
| ------------------------------- | ----------------------- | ----------------- |
| `sum`                           | 0.19–0.26 m             | 0                 |
| `sum_squares`                   | **1.06 m**              | 22–42             |
| `bottleneck`                    | **0.00 m (collisions)** | 123–161           |
| `bottleneck_then_sum` (default) | 0.47–0.67 m             | 14–39             |

This confirms the known result that minimizing the sum of squared distances keeps synchronized
straight moves apart (Turpin, Michael and Kumar, CAPT, IJRR 2014), and shows that the current
crossing penalty optimizes the wrong thing: crossings in space are not collisions in time. The
crossing pass spends 9.5 s at 300 spots to change nothing under `sum` (min-sum is already
crossing-free by the triangle inequality) and removes only 4 of 64 crossings under the default.
Pure `bottleneck` returns an arbitrary assignment meeting the cap: 2,240 m total and real
collisions, against 1,619 m for the default.

Other costs: `import stage.choreo.models` takes 368 ms, 235 ms of it pandas, used only by
`Set.table()`; `Set.get` rebuilds its index per call (O(N) lookup, quadratic loops);
`suggest_swaps` takes 3.3 s at 300 spots (O(n³)); `scatter` of 10 spots at gap 0.3 m takes 6 s
because Bridson samples the whole area first.

#### Algorithm upgrades

1. **Collision-aware matching by default.** Default to `sum_squares` (or a lexicographic
   `bottleneck_then_sum_squares`), with the guarantee documented. Apply the power to distance
   only and keep penalties additive in meters (today penalties are squared with the distance, so
   units do not match, and the docstring is wrong about when penalties apply). Remove pure
   `bottleneck` or define it as `bottleneck_then_sum`.
2. **Time-conflict penalty instead of spatial crossings.** Closest point of approach of two
   linear moves over the shared window, `d_min = |Δp + t*·Δv|`, with a hinge `max(0, r − d_min)`;
   candidate pairs from a grid or KD-tree over swept segments; incremental deltas when two rows
   swap. Exact solve (CP-SAT) for small casts, best-improvement 2-opt with a tabu list beyond.
   Report the measure actually minimized.
3. **Faster bottleneck feasibility** with Hopcroft–Karp (`maximum_bipartite_matching`) or
   incremental threshold augmentation (Gabow–Tarjan), and the one-pass "max" dynamic program for
   the order-preserving cap. Low priority: 0.15 s at 500 spots is acceptable.
4. **Matching across chains of sets**: minimize total travel or conflicts over sets k to k+m
   instead of one greedy transition at a time.
5. **Form fitting done robustly.** Pratt, Taubin or Hyper-fit initial circle estimate (Kåsa is
   biased small on short arcs: radius 19.77 for a true 20 on a 30° arc), Levenberg–Marquardt
   refinement with a convergence flag; RANSAC or MSAC per primitive with `tol` as the inlier
   threshold; sequential RANSAC or J-Linkage for several forms in one cloud; model selection by
   BIC with minimum point counts (five for a circle); new primitives: ellipse (Fitzgibbon direct
   least squares), lattice or block (PCA of nearest-neighbour vectors), polyline with corners
   (Ramer–Douglas–Peucker); keep measured stations so imported spacing survives within `tol`.
6. **Vectorize the turn penalty** (`np.vectorize(angle_diff)` is a Python loop over n·m pairs) and
   compute each form's layout once in `place` (today twice).

#### API and design changes

- **Typed, versioned recipes.** Replace `Form.geometry: dict[str, Any]` with a discriminated union
  of frozen recipe models (`LineRecipe`, `ArcRecipe`, `BlockRecipe`, `FillRecipe`, ...) carrying
  `recipe_version`. This fixes stale `n`, ignored typos and late failures, and lets the JSON
  schema validate stored shows. Same for `Route.curve`, `PathRow.motion_params` and generator
  parameters (typed models, or `JsonValue` at minimum).
- One angle convention in constructors: `arc_form(start_deg)` is counter-clockwise from +x while
  `block_form(direction_deg)` uses 0 = audience. Name them distinctly or convert.
- Consistent constructor signatures (`block_form` has no `n` or `spacing`; `generated_form`
  mixes its own options with `**params`, so a generator cannot have a parameter named `spacing`).
- Generated forms record which registry and version they need; today every call must pass the
  same registry or silently falls back to the default.
- `split_form` and `merge_forms` keep open slots and editability instead of converting to custom.
- `MatchOptions.weights` becomes a typed `MatchWeights` model with documented units;
  `pinned` and `forbid` use strict integers.
- Better infeasibility hints: per-group spot and slot counts; a hint when `slot_groups` is given
  without `groups`; correct plurals.
- `place` checks overlap between slots of different forms (the chained test produced a 0.149 m
  gap).
- Import pandas lazily; cache the `Set` and `Cast` indexes.

#### Missing capabilities

Concentric-ring and trapezoid block forms; ellipse forms; stations-based curve spacing; a form to
form transition builder; matching over set chains; per-group cost report in `MatchResult.explain`;
`scale_form` that scales spacing.

#### Keep

Exact, order-invariant matching; exact lexicographic bottleneck; the order-preserving dynamic
program; constraint-relaxation diagnosis; recipes that regenerate placements with exact arc-length
stations; lock and pin checks on form edits.

### 9.7 Show layer, storage and collaboration (`stage.show`)

#### Verified correct

Undo and redo are byte-exact: a 400-step randomized interleaved history (designer and agent,
renames, regroup, set add and remove, counts with rescale, paths, undo, redo, revert) produced
306 revisions with no non-`StageError` exception and a valid head after every step but one. The
file repository's compare-and-swap by exclusive hard link produced exactly one winner in every
8-process race. Workspace paths reject `..`, empty segments, leading dots, backslashes and NUL.
No wall-clock, `uuid` or `random` use exists in `show/`.

#### Confirmed defects to fix first (data loss, corruption, authorization)

1. **`renumber` plus `save_show` deletes one set's build script and hands another set's script to
   the wrong set** (`manage.py:399-439`, `files.py:207-224`): set folders are keyed by display ID.
   Key them by a stable internal ID, or have `renumber` return a path-rename plan storage applies.
2. **`InMemoryRepository` loses updates under threads** (`repositories.py:429-437`): commit is
   check-then-append without a lock; 9 of 200 eight-thread trials produced two winners on one
   parent, and one winner's file vanished although it was told it succeeded.
3. **The file repository trusts IDs read from disk** (`file_repository.py:78-92`): a `next/` file
   containing `../../outside` makes `revisions()` load JSON outside the root; a cycle in `next/`
   makes it loop forever; `blob()` builds paths from tree content the same way. Validate every ID
   against `^[0-9a-f]{64}$` and guard cycles.
4. **`Workspace.commit` skips all validation** (`storage.py:333-370`): committing
   `{"/show/show.json": b"garbage"}` lands, and afterwards every `load_show` and even `ls('/')`
   fail until a revert; files under unknown roots are stored but can never be read.
5. **An agent can delete sets, empty the cast and change the show ID** by writing
   `/show/show.json` (`files.py:227-236`, `storage.py:308-331`); only pins and locks are checked.
   Split designer-owned metadata (ID, cast, set order, pins, dependencies) from agent-writable
   data, or add structural guards.
6. **Agents can forge designer comments** (`files.py:61`): the comments document is not
   designer-owned and `Comment.author` is free text.
7. **The `Repository` API ignores every principal rule** (`repositories.py:160-297`):
   `put_set(..., actor="agent")` moves a pinned spot, and `transaction(..., actor="agent")` writes
   `/config/limits.yaml`, which CLAUDE.md reserves for the designer. `actor` is only a label.
   Enforce ownership in the repository, or keep the raw repository private and give agents only a
   `Workspace` (and keep the sandbox import block, with a test).
8. **Show IDs collide on case-insensitive file systems** (`repositories.py:30`): on macOS
   `head("show") == head("Show")` and writing `show` conflicts with `Show`; `CON`, `nul` and
   `abc.` are accepted; a 300-character ID leaks `OSError: File name too long`. Store shows under a
   hash or encoded lower-case name with the display ID in a manifest.
9. **Raw exceptions from storage**: `ws.read("/")` leaks `IndexError`; wrong-shaped JSON in
   `show.json` or `forms.json` leaks `TypeError`, and `"sets": "12"` silently decodes as sets 1
   and 2 (`documents.py:221-241`); missing or truncated objects and trees leak
   `FileNotFoundError` and `JSONDecodeError`; filesystems without hard links leak
   `PermissionError`; invalid UTF-8 in a text file leaks `UnicodeDecodeError`. Map every backend
   failure to `StorageError` codes (`IO`, `CORRUPT`, `NO_HARDLINKS`, `NAME_TOO_LONG`).
10. **No integrity or durability guarantees**: object hashes are never verified on read (a
    swapped blob reports "does not match the Show schema", not "corrupt object"); there is no
    directory `fsync` after `os.replace` or `os.link`, so a crash after `commit()` returns can lose
    the commit or keep a link to a missing tree; losers leave orphan trees; crashed writers leave
    `.tmp-*` files nothing cleans; there is no garbage collection.
11. **No schema or layout version in stored data.** With `extra="forbid"`, a document written by a
    newer library fails in an older reader with a generic schema error. NaN and `Infinity` are
    written as non-standard JSON (`Set(counts=inf)` was saved); `-0.0` and `0.0` produce different
    bytes for equal models, creating spurious revisions.

#### History and collaboration defects

- A counts-only change is reported as a paths edit (`collab.py:114-121`), producing spurious
  `ConflictError`s on accept and spurious events; compare transition specs without `counts`.
- Form deletions are invisible to `_edit_ops` (`collab.py:132`): accepting a proposal on a deleted
  form resurrects it, and replaying `events_since` after a deletion does not reproduce head.
- `accept` never marks a proposal accepted (`ProposalState "accepted"` is never set anywhere);
  accepting twice gives a misleading overlap error; a proposal made against an unsaved show can
  never be accepted. Proposals are not persisted at all.
- Undo restores files without a referential check: undoing a spot addition after the agent stored
  a path row for that spot leaves a dangling reference. Undoing the first save empties the show
  and then reports "no revisions yet".
- `diff_show` claims "every difference" but returns `{}` for a new group, a pack change, a set cue
  or label change, a placement note change and selection changes (`history.py:150-172`).
- `history.rename_entities` is a second, weaker rename that does not rewrite expression selectors;
  `cast.rewrite.rewrite_spot_ids` already does. Delete the duplicate and route through one.
- `unpin` does not normalize `1->2` as `pin` does.
- Overlap is too coarse to merge and too fine to be safe: a whole transition is one key, and
  counts and paths of the same transition are separate keys, so a rescale proposal silently
  rescaled the designer's newer row.

#### Sets and sections defects

`join_sections` after `split_section` loses zone windows, drops markers whose label repeats at a
different count, splits facing overrides in two, duplicates cues and audio into both halves and
leaves a ramp longer than its section (`sections.py:111-323`). `remove_set` leaves notes anchored to
the removed set and transition pins on pairs that no longer exist, and `validate_show` stays ok.
`remove_set(merge_counts=True)` on the last set drops its counts. `add_set` with two placements for
one spot keeps the last silently. `insert_subset(after="A")` returns `B`. `Show.forms` keys may
differ from `form.id` and change on a storage round trip. Frozen shows contain mutable dicts
(`show.forms["zz"] = f` works) and are unhashable. A stale save at 1,000 revisions builds a 48 KB
`ConflictError`. `set_counts(rescale=True)` does not shift facing overrides or zone windows.

#### Validation gaps

`validate_show` reports none of these: form members naming unknown spots; forms whose set does not
exist; sets declaring missing forms; form keys differing from IDs; form members whose placements do
not point back; pins naming missing items or unparsable transitions; pin records disagreeing with
placement flags; notes anchored to missing items; duplicate note IDs; facing overrides naming
unknown spots or running past the show; zone selectors naming unknown spots; degenerate zones;
named selections naming unknown spots; NaN or infinite counts, tempos and waypoints; placements far
off the sheet (a structural warning; safety stays with `stagecheck`); unpinned sheets when no store
is passed; empty IDs and set IDs that cannot be stored; placements of inactive spots; comments
anchored to missing items; a non-zero first-set count in a stored show. It also costs 2.4 s for 300
spots by 60 sets and has no incremental mode.

#### Measured performance

| Operation (300 spots x 60 sets, 4.9 MB encoded) | Time                           |
| ----------------------------------------------- | ------------------------------ |
| `Show.model_validate_json`                      | 87 ms                          |
| `encode_show` / `decode_show`                   | 129 ms / 70 ms                 |
| `diff_show`, one spot moved                     | 77 ms                          |
| `validate_show`                                 | 2,447 ms                       |
| `save_show` first, file repository              | 532 ms                         |
| one-spot edit saved, file repository            | 198 ms (whole show re-encoded) |

| Revisions (file repository) | commit | `head()` | `load_show` | file reads per load |
| --------------------------- | ------ | -------- | ----------- | ------------------- |
| 100                         | 126 ms | 12.7 ms  | 100 ms      | 915                 |
| 1,000                       | 617 ms | 62.1 ms  | 517 ms      | 9,015               |

At 1,000 revisions `undo` takes 850 ms and **`events_since` over the last 200 revisions takes 95
seconds**. `head()` walks the whole `next/` chain and reads every tree; each document read walks the
log again (O(documents x revisions)); workspace caches hold one revision only.

#### Upgrades

1. **Repository layout v2**: a `repo.json` layout version; a `HEAD` file updated by compare-and-swap
   plus an append-only log or packed index, so `head()` is O(1) and the log is read once; hash
   verification on every read; directory `fsync` for durability; `fsck`/`verify` (re-hash, check
   references, find orphans, cycles and missing objects); garbage collection and packing; cleanup
   of stale temp files; a thread lock in the memory backend; a hard-link-free fallback (for
   example `O_EXCL` create) with a clear error where neither works.
2. **Format versions and migrations**: `{"format": "stage.show", "version": N}` in `show.json`; a
   migration chain `migrate_vN_to_vN+1` applied in `decode_show`; `VersionError` naming the minimum
   library version when a document is newer; old-version fixtures in tests. Strict JSON
   (`allow_nan=False`), `-0.0` normalized, and NFC normalization of IDs and text.
3. **Per-document encoding and decoding with memoization**, so a one-spot edit re-encodes one
   document; LRU caches of trees and shows keyed by revision; `events_since` diffs trees by blob
   digest and decodes only changed documents (expected about 1,000x faster).
4. **Item-level change tracking** (placement per spot, path row per spot, form, set order) enabling a
   real three-way merge `merge(base, ours, theirs)`, automatic rebase of non-overlapping proposals,
   blame ("who last moved this spot"), and a generic structural `diff_show` derived from the
   models so it is complete by construction.
5. **Authorization as a capability**: a host-issued `Session` carrying the principal, a policy hook
   `authorize(principal, action, paths)` enforced in one place below both `Workspace` and
   `Repository`; designer-only metadata split from agent-writable documents.
6. **Incremental validation** per set or transition, keyed by changed documents.
7. **A public storage-backend protocol with a conformance test kit** (races, corruption, cycles,
   unicode, case folding, long names), so SQLite, Postgres or object-store backends can be added and
   certified.

#### Missing capabilities

Portable single-file archive export and import (also backup and restore); named refs, tags and
branches for what-if versions; an audit log; event subscription (`watch(show_id, since)`); a
`retry_on_conflict` helper; a file-backed `AssetStore`; an autosave journal; persisted, listable
proposals with `accepted`, `withdrawn` and `stale` states; `move_set`; `revert` to the empty root.

#### Keep

Purity and structural sharing; deterministic revision IDs; content addressing with atomic
replace; one-winner compare-and-swap; byte-exact undo and redo; centralized lock checks; conflict
errors naming what landed; bisect-based `ShowIndex`.

## 10. Roadmap

Each phase ends with every gate green, a tagged release and a changelog entry. Work proceeds
under the project's rules: a failing `test_regression_` test before every fix, one logical change
per commit, branches merged through pull requests, an ADR before load-bearing design changes.

### Phase 0: safety net (no behaviour changes)

- Add a failing regression test, marked `xfail(strict=True)`, for every confirmed defect in this
  plan, so each fix flips one test.
- Show generators, the `seeded_show` factory, scale tiers S and M, the benchmark harness with the
  current numbers as baseline.
- CI: Python 3.12 to 3.14 on Linux, macOS and Windows; three hash seeds; coverage floor; pyright
  basic; wheel smoke test; guide exercises; one task runner.
- Packaging basics that do not change behaviour: version from the tag, `__version__`, metadata,
  schema in the wheel, sdist exclusions, dependency ranges with a lowest-direct job.
- Public API snapshot committed, so later phases show every API change explicitly.

Exit: the full defect list is encoded as tests; CI runs the matrix; benchmarks publish a
baseline.

### Phase 1: Priority 0

The thirteen items in section 3: storage data loss, traversal, cycles and commit validation;
principal enforcement in one layer; guaranteed segment bounds; all hangs and unbounded
allocations; hash-seed determinism; publication tokens, private rosters and CSV neutralization;
robust sheet replacement; form and placement consistency after matching; every lock and pin
bypass.

Exit: all Priority 0 regression tests pass; a fuzzing run of every parser finds no hang and no
non-`StageError`; a 300-spot, 60-set scenario passes the scenario checks of section 5.2.

### Phase 2: foundations (the main breaking release before 1.0)

Section 4 in full: constrained types and `allow_inf_nan=False`; one ID pattern; model invariants;
`StageError` picklable with an error-code catalogue; immutable containers and `replace()`; typed
payloads (recipes, facing values, event payloads, fixes, weights); keyword-only flags; the renamed
`ValidationError`; ordered named selections; selection precedence decided. Stored format
`stage.show` version 2 with a migration from version 1 and stored v1 fixtures in the tests.

Exit: no `dict[str, Any]` in stored models; no `type: ignore` in the library; every model rejects
the invalid inputs listed in the workstreams; v1 shows load and migrate.

### Phase 3: storage v2 and collaboration

Repository layout v2 (O(1) head, verified reads, directory fsync, `fsck`, garbage collection,
thread-safe memory backend), per-document encoding, LRU caches, digest-based `events_since`,
item-level change tracking with three-way merge and blame, persisted proposals with a full
lifecycle, incremental validation, the backend protocol with a conformance kit, portable archives.

Exit: storage stateful tests and crash injection pass; the budgets for `head()`, edits,
`events_since` and validation in section 5.3 are met at 1,000 revisions.

### Phase 4: algorithms

- Engine: vectorized batch evaluation, analytic velocity, space-time broad phase with oriented
  footprints and conflict intervals, global conflict resolution, speed and acceleration limits with
  `min_counts` and step size, easing per knot interval, facing that plans ahead, set-boundary
  continuity report.
- Matching: collision-aware default objective with a documented guarantee, time-conflict penalty,
  matching over set chains.
- Forms: robust fitting with RANSAC and model selection, new primitives, exact transforms.
- Geometry: quadrature arc length with Newton inversion, analytic derivatives and curvature,
  robust projection, feature-aware flattening, exact offsets, blue-noise scatter with exact counts,
  Lloyd relaxation, one-to-one symmetry.
- Time and selection: compiled clock with vectorized conversions; iterative selection parser over
  index sets.

Exit: every budget in section 5.3 met; numerical accuracy tests at 1e-9 relative for arc length;
mutation score above 85% on `geo`, `time`, `choreo` and `show`.

### Phase 5: capabilities

Marching `PositionLanguage` with steps, yard lines, hashes and its inverse; data-driven,
versioned templates (Canadian football, soccer, basketball variants, indoor floor, parade, arena,
thrust stage); Unicode PDF with embedded fonts, compression and vector field pictures; dot books
and chart booklets; video and animation formats with overlays; tempo-map import; geometry
additions (intersections, fillets, hulls, SVG export); grammar additions; notes on counts, forms
and zones.

Exit: each capability has a tutorial or how-to page and scenario coverage.

### Phase 6: 1.0

Generated, versioned documentation site with the Diátaxis structure; all policies published;
release workflow with provenance and SBOM; extras finalized; distribution name settled; the
public API snapshot frozen under semantic versioning.

Exit: section 2 holds in full and is checked by CI.

### Decisions the maintainer must make

These change public behaviour or stored data. Each needs an explicit decision, recorded in an ADR,
before its phase starts.

1. Distribution name on the package index.
2. Default matching objective (`sum_squares` or `bottleneck_then_sum_squares`) and removal or
   redefinition of pure `bottleneck`.
3. Selection operator precedence (`&` above `+` and `-`), or rejecting mixed operators without
   parentheses.
4. The spot ID pattern and reserved words.
5. Splitting `show.json` into designer-owned and agent-writable documents, and keying set folders by
   a stable internal ID.
6. Stored format version 2 and the support window for reading version 1.
7. Keyword-only boolean flags across the public API.
8. pandas, Pillow, segno and imageio-ffmpeg as optional extras.
9. The supported Python floor (3.12 passes today but is untested in CI).
10. How facings map under skew and non-uniform scale; the names of mirror axes; what "center"
    means on each template family.
11. Lock policy for cast edits (for example: renames allowed, positions protected).
12. Whether authorization is enforced inside `stage` (capability sessions) or only at the host
    boundary, given that agents receive library handles.
