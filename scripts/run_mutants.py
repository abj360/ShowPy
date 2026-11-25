#!/usr/bin/env python3

"""
run_mutants.py --- records Rust and Python mutation scores without gating on them

cargo-mutants mutates e8s-core and runs its tests in a copy of the tree. mutmut mutates only the
library modules that ported packages keep in Python: the `stays` lists of
tests/unit/rules/ported/*.toml. mutmut reads its settings from the pyproject.toml of the folder
it runs in, so it runs in a staging folder holding copies of the library and the tests and a
pyproject.toml whose `[tool.mutmut]` table also names those modules in `only_mutate`; no other
module is mutated. With no kept module, mutmut does not run. Both records go to
mutants-summary.json. A score never fails the run; a tool that cannot run does, and so does
output that cannot be scored, which is recorded with its reason beside the other tool's record.

Contains:
    SCORED: the status of a tool that ran and was scored
    CANNOT_RUN: the status of a tool that could not run
    NO_KEPT_MODULES: the Python status while no ported package keeps a module
    UNREADABLE: the errors that mark a tool output as one that cannot be scored
    kept_modules(): lists the library files that ported packages keep in Python
    stage_mutmut(): fills the staging folder mutmut runs in, limited to the kept modules
    rust_score(): reads cargo-mutants' outcome counts into a score record
    python_score(): reads mutmut's CI stats into a score record
    run_rust(): runs cargo-mutants on e8s-core and returns its record
    run_python(): runs mutmut over the kept modules and returns its record
    record_scores(): runs both tools, writes the summary and returns the exit code
    main(): parses the tool paths and returns record_scores' exit code
"""

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTED_ROOT = Path("tests/unit/rules/ported")
LIBRARY = "e8s"
TESTS = "tests"
PYPROJECT = "pyproject.toml"
SUMMARY = Path("mutants-summary.json")
STAGE = Path(".mutmut")
CORE_PACKAGE = "e8s-core"
# The two core areas that live in e8s/choreo; every other area is its own package.
AREA_PACKAGES = {"engine": "choreo", "forms": "choreo"}
COPY_SKIP = ("__pycache__", ".results", ".hypothesis")
MUTMUT_TABLE = "[tool.mutmut]\n"
MUTMUT = (sys.executable, "-m", "mutmut")
MUTMUT_STATS = Path("mutants/mutmut-cicd-stats.json")
CARGO_MUTANTS_OUTCOMES = Path("mutants.out/outcomes.json")
# cargo-mutants exits 0 when every mutant was caught, 2 when some were missed and 3 when some
# timed out; every other code means it could not run, such as 4 for failing baseline tests.
CARGO_MUTANTS_SCORED_EXITS = frozenset({0, 2, 3})
SCORED = "scored"
CANNOT_RUN = "cannot run"
NO_KEPT_MODULES = "no ported Python modules yet"
RUST_COUNTS = ("caught", "missed", "timeout", "unviable", "total_mutants")
PYTHON_COUNTS = (
    "killed",
    "survived",
    "timeout",
    "no_tests",
    "suspicious",
    "segfault",
    "skipped",
    "total",
)
SCORE_DIGITS = 4
# What a missing, unreadable, malformed or incomplete tool output raises while it is scored.
UNREADABLE = (OSError, ValueError, KeyError, TypeError)

type Record = dict[str, str | int | float | None]


def kept_modules(ported_root: Path) -> list[str]:
    """Lists the library files that ported packages keep in Python.

    Args:
        ported_root: The folder of `<area>.toml` disposition files.

    Returns:
        modules: Repository-relative posix paths of the `stays` modules, sorted, each once.
    """
    modules: set[str] = set()
    for path in sorted(ported_root.glob("*.toml")):
        table = tomllib.loads(path.read_text(encoding="utf-8"))
        package = AREA_PACKAGES.get(path.stem, path.stem)
        modules.update(f"{LIBRARY}/{package}/{name}.py" for name in table["stays"])
    return sorted(modules)


def stage_mutmut(root: Path, stage: Path, modules: Sequence[str]) -> None:
    """Fills the staging folder mutmut runs in: the library, the tests and a limited pyproject.

    Args:
        root: The repository root.
        stage: The staging folder; any earlier content is removed.
        modules: The library files mutmut may mutate.

    Raises:
        ValueError: When the repository's pyproject.toml has no `[tool.mutmut]` table.
    """
    settings = (root / PYPROJECT).read_text(encoding="utf-8")
    if MUTMUT_TABLE not in settings:
        raise ValueError(f"{PYPROJECT} has no {MUTMUT_TABLE.strip()} table")
    only_mutate = f"only_mutate = {json.dumps(list(modules))}\n"
    shutil.rmtree(stage, ignore_errors=True)
    for folder in (LIBRARY, TESTS):
        shutil.copytree(root / folder, stage / folder, ignore=shutil.ignore_patterns(*COPY_SKIP))
    (stage / PYPROJECT).write_text(
        settings.replace(MUTMUT_TABLE, MUTMUT_TABLE + only_mutate, 1), encoding="utf-8"
    )


def _score(detected: int, tested: int) -> float | None:
    return round(detected / tested, SCORE_DIGITS) if tested else None


def rust_score(outcomes: Mapping[str, object]) -> Record:
    """Reads cargo-mutants' outcome counts into a score record.

    The score counts a timed-out mutant as detected and leaves unviable ones out.

    Args:
        outcomes: The parsed mutants.out/outcomes.json.

    Returns:
        record: The status, the counts and the score, or a None score with no viable mutant.
    """
    counts = {key: int(str(outcomes[key])) for key in RUST_COUNTS}
    detected = counts["caught"] + counts["timeout"]
    return {
        "status": SCORED,
        **counts,
        "score": _score(detected, detected + counts["missed"]),
    }


def python_score(stats: Mapping[str, object]) -> Record:
    """Reads mutmut's CI stats into a score record, scored the way mutmut scores them.

    Args:
        stats: The parsed mutants/mutmut-cicd-stats.json.

    Returns:
        record: The status, the counts and the score: killed and timed-out mutants over all
            but the skipped ones, or None when none was tested.
    """
    counts = {key: int(str(stats[key])) for key in PYTHON_COUNTS}
    return {
        "status": SCORED,
        **counts,
        "score": _score(counts["killed"] + counts["timeout"], counts["total"] - counts["skipped"]),
    }


def _cannot_run(detail: str) -> Record:
    print(f"E: {detail}", file=sys.stderr)
    return {"status": CANNOT_RUN, "detail": detail}


def _read_record(
    folder: Path, output: Path, score: Callable[[Mapping[str, object]], Record]
) -> Record:
    """Scores one tool's JSON output, or returns a cannot-run record naming why it could not.

    Args:
        folder: The folder the tool ran in.
        output: The output file, relative to that folder.
        score: The function that turns the parsed output into a score record.

    Returns:
        record: The score record, or a cannot-run record when the output is missing,
            unreadable, not JSON, not an object or lacks a whole-number count.
    """
    try:
        return score(json.loads((folder / output).read_text(encoding="utf-8")))
    except UNREADABLE as error:
        return _cannot_run(f"cannot score {output.as_posix()}: {error!r}")


def run_rust(cargo_mutants: Sequence[str], root: Path) -> Record:
    """Runs cargo-mutants on e8s-core, with a locked Cargo.lock, and returns its record.

    Args:
        cargo_mutants: The command that starts cargo-mutants.
        root: The repository root, where mutants.out is written.

    Returns:
        record: The score record, or a cannot-run record for any other exit code or an
            outcomes file that cannot be scored.
    """
    command = [*cargo_mutants, "mutants", "--package", CORE_PACKAGE, "--cargo-arg=--locked"]
    exit_code = subprocess.run(command, cwd=root, check=False).returncode
    if exit_code not in CARGO_MUTANTS_SCORED_EXITS:
        return _cannot_run(f"cargo-mutants exited {exit_code}")
    return _read_record(root, CARGO_MUTANTS_OUTCOMES, rust_score)


def run_python(mutmut: Sequence[str], root: Path, ported_root: Path, stage: Path) -> Record:
    """Runs mutmut over the modules ported packages keep and returns its record.

    Args:
        mutmut: The command that starts mutmut.
        root: The repository root.
        ported_root: The folder of disposition files.
        stage: The staging folder mutmut runs in.

    Returns:
        record: The score record; a no-module record when no package keeps a module; a
            cannot-run record when the disposition files cannot be read, the stage cannot be
            filled, a mutmut command fails or its stats cannot be scored.
    """
    try:
        modules = kept_modules(ported_root)
    except UNREADABLE as error:
        return _cannot_run(f"cannot read the disposition files: {error!r}")
    if not modules:
        print(f"Skipping mutmut: {NO_KEPT_MODULES}")
        return {"status": NO_KEPT_MODULES}
    try:
        stage_mutmut(root, stage, modules)
    except (OSError, ValueError) as error:
        return _cannot_run(f"cannot stage mutmut: {error!r}")
    for action in ("run", "export-cicd-stats"):
        exit_code = subprocess.run([*mutmut, action], cwd=stage, check=False).returncode
        if exit_code != 0:
            return _cannot_run(f"mutmut {action} exited {exit_code}")
    return _read_record(stage, MUTMUT_STATS, python_score)


def record_scores(
    cargo_mutants: Sequence[str],
    root: Path = ROOT,
    *,
    mutmut: Sequence[str] = MUTMUT,
    ported_root: Path | None = None,
) -> int:
    """Runs cargo-mutants, then mutmut, and writes both records to the summary file.

    Args:
        cargo_mutants: The command that starts cargo-mutants.
        root: The repository root; the summary and the staging folder go under it.
        mutmut: The command that starts mutmut.
        ported_root: The folder of disposition files; PORTED_ROOT under root by default.

    Returns:
        exit_code: 1 when a tool could not run, else 0, whatever the scores.
    """
    summary = {
        "rust": run_rust(cargo_mutants, root),
        "python": run_python(mutmut, root, ported_root or root / PORTED_ROOT, root / STAGE),
    }
    (root / SUMMARY).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {SUMMARY.as_posix()}")
    return 1 if any(record["status"] == CANNOT_RUN for record in summary.values()) else 0


def main(argv: list[str] | None = None) -> int:
    """Parses the cargo-mutants path and returns record_scores' exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("--cargo-mutants", required=True, help="the cargo-mutants executable")
    args = parser.parse_args(argv)
    return record_scores([args.cargo_mutants])


if __name__ == "__main__":
    sys.exit(main())
