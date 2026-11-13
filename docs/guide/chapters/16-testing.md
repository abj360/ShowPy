# Testing and quality

The library's promises (determinism, purity, one motion source, errors that teach) are only
as good as the tests that hold them. This chapter explains how the tests are organized, how
to run every gate, and how to read a failure, especially a Hypothesis failure.

## How the tests are organized

On 2026-11-11, `uv run pytest --co -q` collected 1,496 tests: 413 doctests inside `stage/`,
40 property tests, 1 golden test, and unit tests for the rest.

| Kind           | Where                                                       | What it proves                                        |
| -------------- | ----------------------------------------------------------- | ----------------------------------------------------- |
| Unit tests     | `tests/unit/<package>/test_<module>.py`, mirroring `stage/` | each function's behavior, edge cases and errors       |
| Doctests       | the `>>>` examples in every public docstring under `stage/` | the documented example really runs and prints that    |
| Property tests | `tests/property/` (Hypothesis)                              | invariants hold for **any** valid input               |
| Golden tests   | `tests/golden/`                                             | a real show replays exactly as a human reviewed it    |
| Fixtures       | `src/stage/examples.py`, re-exported by `tests/fixtures/`   | small, fully specified shows shared by tests and docs |

`pyproject.toml` sets the pytest options: `--doctest-modules` (so docstrings run),
`--strict-markers`, `--import-mode=importlib`, `testpaths = ["tests", "stage"]`,
`pythonpath = ["."]`, and `filterwarnings = ["error"]`: **every warning fails the run**, so a
deprecation cannot slip by.

### Unit tests

Every bug fix starts with a failing test (CLAUDE.md). Each trap listed in `docs/HANDOFF.md`
has a regression test that is never deleted: the stale curve cache after `model_copy`, the
segment aliasing of periodic easings, mangled doctests after scripted edits. Numeric
comparisons use `approx_equal` with an explicit tolerance.

### Property tests

A property test states a rule and lets Hypothesis search for an input that breaks it. The
library's properties include:

- spacing is even along any curve (`space_along` gaps are equal by arc length);
- a 360° rotation is the identity, and facings follow `vector_facing(L·v)`;
- every path ends on its target, whatever the style, window, waypoints and easing;
- every engine segment stays within `tol` of the true motion at nine points per segment;
- `count_to_seconds` and `seconds_to_count` invert each other;
- matching never returns a worse bottleneck than any other valid assignment, and is the same
  for any input order;
- selection algebra obeys set laws (`A - B` and `A & B` partition `A`).

### The golden test

`tests/golden/opener.py` builds a three-set, eight-trumpet show (a block opening to a company
front, then curving into an arc, with a stagger and easing). `test_golden_opener.py` replays
it with the engine at two frames per count and compares every spot's position and facing
with `tests/golden/fixtures/opener_frames.json` to `1e-9`. Any change to motion shows up
here. If the change is intended, regenerate with `STAGE_UPDATE_GOLDEN=1` and have a human
review the diff; never regenerate to turn a run green. The schema test works the same way with
`STAGE_UPDATE_SCHEMA=1` for `schemas/stage.show.v1.json`.

## Running the gates

Run these from the repository root. Pipe nothing into `tail` without `set -o pipefail`: a
pipeline returns the exit code of its last command, and `pytest | tail` once let a failing
commit through (HANDOFF "Hidden test failures").

| Gate                                     | Command                                                                                                                                                    |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| tests (unit, property, golden, doctests) | `uv run pytest`                                                                                                                                            |
| one area                                 | `uv run pytest tests/unit/choreo`                                                                                                                          |
| golden only                              | `uv run pytest -m golden`                                                                                                                                  |
| lint                                     | `uv run ruff check .`                                                                                                                                      |
| format                                   | `uv run ruff format --check .`                                                                                                                             |
| types                                    | `uv run mypy stage tests` (strict)                                                                                                                         |
| Markdown, YAML, JSON                     | `npx --yes prettier@3.6.2 --check "**/*.{md,yml,yaml,json}"`                                                                                               |
| dependency audit                         | `uv export --format requirements-txt --no-emit-project > requirements.txt && uv run pip-audit --strict --disable-pip --require-hashes -r requirements.txt` |
| this guide                               | `uv run --with ... python docs/guide/build_guide.py`, and `uv run python docs/guide/exercises/run_all_checks.py`                                           |

The same checks run as pre-commit hooks (`.pre-commit-config.yaml`) and in CI
(`.github/workflows/ci.yml`), which also builds the Docker image.

## Reading a Hypothesis failure

When a property fails, Hypothesis does three things:

1. It finds a failing input.
2. It **shrinks** it: it keeps simplifying the input (smaller numbers, shorter lists, earlier
   choices) while the test still fails, so you see the simplest case it could find.
3. It re-raises the original error with a note, `Failing test case: test_name(arg=value,
...)`, attached to it.

Here is a property that is wrong on purpose: "the shortest turn between two whole-degree
angles is never more than 90°".

```pycon
>>> from hypothesis import given, settings, strategies as st
>>> from stage.core.units import angle_diff
>>> @settings(derandomize=True, database=None, max_examples=200)
... @given(st.integers(-720, 720), st.integers(-720, 720))
... def test_turn_is_short(a, b):
...     assert abs(angle_diff(a, b)) <= 90.0
>>> try:
...     test_turn_is_short()
... except AssertionError as error:
...     print(*error.__notes__, sep="\n")
Failing test case: test_turn_is_short(
    a=0,
    b=91,
)
```

How to read it:

- **The inputs are the simplest failing case.** Hypothesis found some large failing pair,
  then shrank it to `a = 0`, `b = 91`: the smallest numbers that still break the claim. The
  property was wrong, not the code: `angle_diff` returns values in `(-180, 180]`, so the rule
  should say `<= 180`.
- **Decide which side is wrong.** Either the code has a bug, or the property claims too much.
  Read the function's contract (its docstring) before changing anything.
- **Reproduce it.** Hypothesis stores failures in `.hypothesis/` and replays them first on the
  next run. To pin a case forever, add `@example(a=0, b=91)` above `@given`; it becomes a
  named regression.
- **Floating point matters.** Shrunk floats are often `0.0`, `1.0` or tiny values like
  `5e-324`. If a failure only happens at such values, ask whether the rule needs a tolerance.

`derandomize=True` makes the search deterministic, which is why this guide can print its
output; the project's own property tests use normal random search plus the example database.
Deterministic search is still a search: a rarer failure (such as only exactly 180°) can be
missed in 200 examples, which is one reason to keep failures as `@example` cases.

The ADR 0002 addendum is a real case: the segment property test found a deviation of
0.0113 m against a 0.01 m tolerance, and the fix was denser probes at half tolerance, not a
looser test.

## How this guide is checked

The guide follows the same rules. `build_guide.py` runs every tutorial file, draws every
figure, runs every exercise check against the reference answers (must pass) and against the
untouched stubs (must fail), executes every snippet and compares it with the stored text, and
only then prints the PDF.

> **Check your understanding 16.a.** Why does the project turn every warning into an error?
>
> **Check your understanding 16.b.** A golden test fails after you change an easing. What do
> you do?

## Exercises

**Exercise 16.1 (warm-up).** Write a Hypothesis property `solve()` (returning the test
function) stating that `normalize_angle(a)` is always in `[0, 360)`. The check runs it.

**Exercise 16.2.** Write a property that `seconds_to_count(count_to_seconds(c))` returns `c`
within `1e-9` for a 120 → 180 cpm ramp over 32 counts and any `c` in `[0, 64]`. Return the
test function.

**Exercise 16.3 (harder).** The property "`space_along(curve, n)` gives equal straight-line
gaps" is false for some curves. Return a falsifying `(curve, n)` pair. The check verifies the
chords really differ by more than 1 cm.
