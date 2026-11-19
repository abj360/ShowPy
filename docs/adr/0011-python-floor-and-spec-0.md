# ADR 0011: Follow SPEC 0 for Python; the floor rises to 3.13 at v0.4.0

- Status: Accepted (2026-11-19)
- Date: 2026-11-18
- Deciders: the maintainer (abj360)

## Context

`pyproject.toml` declares `requires-python = ">=3.12"`, ruff targets `py312` and pyright checks
against 3.12. When the audit was written (2026-11-13, on v0.1.1), 3.12 passed locally but CI ran
one Python on one OS, so the floor was a claim nobody tested; audit decision 9 asked for the
supported floor.

That gap is closed: since Phase 0 (v0.2.0, 2026-11-15) CI runs the full suite on Python 3.12,
3.13 and 3.14, each on Linux (`ubuntu-24.04`), macOS (`macos-15`) and Windows (`windows-2022`),
plus hash seeds, slow tests, the wheel smoke test and a lowest-direct resolution job
(`.github/workflows/ci.yml`). 3.12 passes there today. The question is now policy, not evidence.

SPEC 0 (Scientific Python's minimum supported dependencies) recommends supporting each Python
minor version for 36 months after its first release. Applied to the versions in play (release
dates from the CPython release schedule PEPs):

| Python | Released                   | SPEC 0 support ends (36 months) | Status on 2026-11-18 |
| ------ | -------------------------- | ------------------------------- | -------------------- |
| 3.12   | 2023-10-02                 | 2026-10-02                      | past the window      |
| 3.13   | 2024-10-07                 | 2027-10-07                      | supported            |
| 3.14   | 2025-10-07                 | 2028-10-07                      | supported            |
| 3.15   | 2026-10 (PEP 790 schedule) | 2029-10                         | not yet in CI        |

The dates are computed from SPEC 0's 36-month rule, not copied from its published table.

What 3.13 gives this phase, checked against the interpreters `uv` installs here (3.12.14,
3.13.15):

- `warnings.deprecated` (PEP 702), which ADR 0009's deprecation policy uses: absent in 3.12,
  present in 3.13.
- `copy.replace` and `typing.ReadOnly` for `TypedDict` (PEP 705), useful for the typed patch
  models of this phase.
- `typing-extensions` is locked only for `python_full_version < '3.13'` (`uv.lock`), so a 3.13
  floor needs no backport.

Python 3.15: `uv` here lists `cpython-3.15.0rc2` as its newest build, so whether 3.15.0 final is
installable on CI runners was not verified for this ADR. The lockfile already holds `cp315`
wheels for numpy, scipy, shapely, pydantic-core, pandas and Pillow.

## Decision

1. **SPEC 0 is the Python support policy.** ShowPy supports every Python minor version inside
   SPEC 0's 36-month window and drops a version in the first minor release after its window
   closes. Dropping a version is a breaking change listed in release notes.
2. **v0.4.0 raises the floor to 3.13**: `requires-python = ">=3.13"`, ruff `target-version =
"py313"`, pyright `pythonVersion = "3.13"`, classifiers for 3.13 and 3.14, and the CI matrix
   loses its three 3.12 jobs.
3. **New Python versions join CI at their final release.** 3.15 joins the matrix (3 OS) as soon as
   `uv` installs 3.15.0 final on all three runners; until then, no pre-release job blocks
   merging. The classifier follows the CI job, never precedes it.
4. **Core dependency floors follow SPEC 0 in Phase 6**, together with the lowest-direct job that
   tests them. Phase 2 does not widen or tighten numpy, scipy, shapely or pydantic floors.
5. **One interpreter for local work**: `.python-version` stays `3.14`.

## Alternatives

- **Keep 3.12 until 1.0.** Rejected: SPEC 0's window for 3.12 closed on 2026-10-02, the library's
  own deprecation tool would need `typing_extensions` as a new declared dependency, and three CI
  jobs per run test a version the ecosystem is leaving. Its cost is reversible, though: see
  "What would change our mind".
- **Support only the newest two versions (3.14 and 3.15).** Rejected: 3.15 is not on CI yet,
  and consumers on 3.13 would lose upgrades with no ecosystem reason.
- **NEP 29 instead of SPEC 0.** Rejected: SPEC 0 supersedes NEP 29 in the Scientific Python
  community and also covers core dependencies, which Phase 6 needs.

## Consequences

- A consumer still on 3.12 stays on v0.3.x until it upgrades Python. The host apps that pin
  ShowPy (the maintainer's own) must confirm their interpreter before adopting v0.4.0.
- CI time per pull request drops by three of nine matrix jobs, until 3.15 adds three back.
- ADR 0009's `showpy.core.deprecation` uses `warnings.deprecated` from the standard library.
- Code may use 3.13 features (`copy.replace`, PEP 696 type-parameter defaults, `ReadOnly`
  `TypedDict` keys) from v0.4.0 on.

## Numbers

| Measure                                     | Today (v0.3.0)                  | After (v0.4.0)                    |
| ------------------------------------------- | ------------------------------- | --------------------------------- |
| Python versions supported and tested in CI  | 3 (3.12, 3.13, 3.14)            | 2 (3.13, 3.14), 3 once 3.15 lands |
| CI matrix jobs (OS x Python)                | 9                               | 6, then 9 with 3.15               |
| Months 3.12 is past SPEC 0's window         | 1.5 (since 2026-10-02)          | —                                 |
| New runtime dependencies needed for PEP 702 | 1 on 3.12 (`typing-extensions`) | 0                                 |

## What would change our mind

- A consumer that pins ShowPy cannot leave 3.12 before v0.6.0: then v0.4.x restores 3.12 in a
  patch release (CI jobs plus `typing-extensions`), and the floor rises at v0.6.0 instead.
- SPEC 0 changes its window: follow the new window from the next minor release.

## References

- Audit `docs/upgrade-plan.md`: section 6 ("Policies"), section 7 ("Matrix"), section 10
  decision 9.
- SPEC 0, "Minimum Supported Dependencies" (scientific-python.org/specs/spec-0000); PEP 693
  (3.12), PEP 719 (3.13), PEP 745 (3.14) and PEP 790 (3.15) release schedules; PEP 702.
- Code: `pyproject.toml` (`requires-python`, `[tool.ruff]`, `[tool.pyright]`),
  `.github/workflows/ci.yml` (matrix), `.python-version`, `uv.lock`.
- Related: ADR 0009 (deprecation policy uses `warnings.deprecated`).
