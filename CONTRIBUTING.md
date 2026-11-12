# Contributing to Eight

Thanks for helping make choreography exact, safe and fast to write.

## Setup

```bash
uv sync                              # Python 3.14 env with every pinned dependency
uv run pre-commit install            # ruff, mypy --strict and pytest on every commit
```

Or run everything in a clean container:

```bash
docker compose -f docker/docker-compose.yml up --build
```

## Workflow

1. Branch from `main` as `<type>/<short-desc>`, e.g. `feat/arc-spacing`. One logical change per branch.
2. Read `docs/stage-library-api-reference.md` and `docs/adr/` for the area you touch. If the code and the reference disagree, flag it in the PR. Don't silently pick one.
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

- **Units.** Use meters on the stage plane, degrees for facing (0 = facing the audience, counter-clockwise positive) and counts for time. Convert only in `stage/core/units.py` and `stage/sheet/coords.py`.
- **Purity.** Data classes are frozen Pydantic models. Edit functions return new objects and raise `LockedError` on pinned or locked items unless the caller passes `force=True`.
- **Determinism.** Randomness only goes through `stage.core.util.seeded_rng`. Time stamps are passed in as arguments; the library never reads the clock.
- **One source of motion.** Anything that needs positions over time calls `stage.choreo.engine.PathEngine`. Never interpolate motion anywhere else.
- **`stagecheck` is locked.** Never import it from `stage`, and never weaken a check to make a test pass.
- **Errors.** Raise `StageError` subclasses with `code`, `message`, `context` and a `hint` that says what is allowed.
- **Style.**
  - Every file opens with a shebang and a module docstring: `<file> --- <role>`, then a `Contains:` index.
  - Every public function has a verb-first docstring and a runnable `>>>` example.
  - Lines are at most 100 characters, with double quotes.
  - Comments only for genuinely non-obvious reasons.
- **Size.** Keep functions under about 30 lines and modules under about 500 lines. Split by responsibility.

## Testing

| Suite | Where | Command |
| --- | --- | --- |
| Unit | `tests/unit/<package>/` mirrors `stage/` | `uv run pytest tests/unit` |
| Property (Hypothesis) | `tests/property/` | `uv run pytest tests/property` |
| Golden replays | `tests/golden/` | `uv run pytest -m golden` |
| Doctests | every public function | included in `uv run pytest` |

- Tests assert real behavior and must be able to fail. Mocks must be able to fail too.
- Every bug fix starts with a failing regression test whose name begins with `test_regression_`. Never delete it.
- Golden fixtures change only on purpose: `STAGE_UPDATE_GOLDEN=1 uv run pytest -m golden`, then a human reviews the diff.
- Warnings are errors.

## Pre-merge checklist

- [ ] `uv run ruff format --check . && uv run ruff check .` clean
- [ ] `uv run mypy stage tests` clean (strict)
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
- Report security issues privately to the maintainer, never in a public issue.
