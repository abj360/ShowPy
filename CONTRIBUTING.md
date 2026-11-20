# Contributing to ShowPy

Thanks for helping make choreography exact, safe and fast to write.

## Setup

```bash
export PATH=~/.nvm/versions/node/v22.23.5/bin:$PATH   # Node's bin/ first; there is no system node
npm ci                               # pinned Prettier; the lint gate runs it through npx
uv sync                              # Python 3.13+ env (3.14 by default) with every pinned dependency
uv run pre-commit install            # lint and types per commit, pytest per push
```

Or run everything in a clean container:

```bash
docker compose -f docker/docker-compose.yml up --build
```

## Workflow

1. Branch from `main` as `<type>/<short-desc>`, e.g. `feat/arc-spacing`. One logical change per branch.
2. Read `docs/showpy-api-reference.md` and `docs/adr/` for the area you touch. If the code and the reference disagree, flag it in the PR. Don't silently pick one.
3. Write an ADR in `docs/adr/` before any architecturally load-bearing code.
4. Write the failing test first, run it, and watch it fail for the right reason.
5. Write the smallest change that makes it pass, then refactor while green.
6. Run every gate locally (see [Pre-merge checklist](#pre-merge-checklist)).
7. Commit each atomic change as soon as it is complete. Push only with approval.

## Commit standards

- Use Conventional prefixes: `feat fix perf refactor test chore docs ci style`, with an optional scope, e.g. `feat(choreo):`.
- The body says what changed and why, in plain language. Don't restate the diff.
- Each commit is atomic, passes lint and tests on its own, and is committed under your own identity. Never add AI or tool attribution trailers.

## Code standards

- **Units.** Use meters on the stage plane, degrees for facing (0 = facing the audience, counter-clockwise positive) and counts for time. Convert only in `showpy/core/units.py` and `showpy/sheet/coords.py`.
- **Purity.** Data classes are frozen Pydantic models. Edit functions return new objects and raise `LockedError` on pinned or locked items unless the caller passes `force=True`.
- **Determinism.** Randomness only goes through `showpy.core.util.seeded_rng`. Time stamps are passed in as arguments; the library never reads the clock.
- **One source of motion.** Anything that needs positions over time calls `showpy.choreo.engine.PathEngine`. Never interpolate motion anywhere else.
- **Checks are never weakened.** Never weaken a validation check, threshold or default to make a test pass. Limits are user-owned configuration that library code never edits.
- **Errors.** Raise `ShowPyError` subclasses with `code`, `message`, `context` and a `hint` that says what is allowed.
- **Style.**
  - Every file opens with a module docstring: `<file> --- <role>`, then a `Contains:` index. Library modules under `showpy/` have no shebang; scripts and tests open with one.
  - Every public function has a verb-first docstring and a runnable `>>>` example.
  - Lines are at most 100 characters, with double quotes.
  - Comments only for genuinely non-obvious reasons.
- **Size.** Keep functions under about 30 lines and modules under about 500 lines. Split by responsibility.

## Testing

| Suite                 | Where                                     | Command                        |
| --------------------- | ----------------------------------------- | ------------------------------ |
| Unit                  | `tests/unit/<package>/` mirrors `showpy/` | `uv run pytest tests/unit`     |
| Property (Hypothesis) | `tests/property/`                         | `uv run pytest tests/property` |
| Golden replays        | `tests/golden/`                           | `uv run pytest -m golden`      |
| Doctests              | every public function                     | included in `uv run pytest`    |

- Tests assert real behavior and must be able to fail. Mocks must be able to fail too.
- Every bug fix starts with a failing regression test whose name begins with `test_regression_`. Never delete it.
- Golden fixtures change only on purpose: `SHOWPY_UPDATE_GOLDEN=1 uv run pytest -m golden`, then a human reviews the diff.
- Warnings are errors.
- Branch coverage of `showpy` has a floor in `COVERAGE_FLOOR` in `noxfile.py`, enforced by `nox -s tests`: 96% (measured 96.34% on 2026-11-14, rounded down). The floor never decreases: raise it when coverage rises, and add tests instead of lowering it.

## Pre-merge checklist

- [ ] `uv run ruff format --check . && uv run ruff check .` clean
- [ ] `uv run nox -s types` clean (mypy `--strict` and pyright basic)
- [ ] `uv run pytest` green, and `uv run pytest -m golden` green
- [ ] New functions and classes have verb-first docstrings with Args, Returns or Attributes where relevant
- [ ] No commented-out code and no comments restating the obvious
- [ ] Tests added alongside the change, and they can fail
- [ ] Committed as the author: no tool identity, no AI trailer
- [ ] `docker compose -f docker/docker-compose.yml up --build` passes

## Review & release

- Answer review comments with follow-up commits, not force-pushes.
- CI (lint, types, tests, dependency scan, docker build) must be green before merge.
- After a merge to `main`, confirm CI is green before merging anything else.
- Report security issues through private vulnerability reporting (see `SECURITY.md`), never in a public issue.
