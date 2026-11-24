#!/usr/bin/env python3

"""
run.py --- runs parity areas under the v0.4.0 oracle and the working tree and gates the result

`nox -s parity -- <area> [--speed] [--digest PATH]` runs `python -m tests.parity.run`. It builds
the oracle, runs each area's cases on both sides with oracle_runner.py, prints the tables,
writes the digest, runs the area's tests/parity/test_parity_<area>.py and fails on any gate.

Contains:
    FLIPPED_FILE: the flipped cases, compared against their ledger expectation
    REPO_ROOT: the repository root the case modules import from
    AreaRun: one area's reports, speed report and core outputs
    runner_command(): builds the oracle_runner.py command, isolated and writing no bytecode
    runner_environment(): drops every PYTHON* variable except PYTHONHASHSEED
    run_side(): runs oracle_runner.py for one area under one interpreter
    run_area(): runs both sides of one area and compares every case
    main(): builds the oracle, runs each area, prints the tables and fails on any gate
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.build_oracle import build_oracle
from tests.parity.cases import Case, Output, load_all_cases, load_cases, read_outputs
from tests.parity.compare import (
    Flip,
    ParityReport,
    SpeedReport,
    compare_case,
    compare_speed,
    digest_lines,
    format_speed,
    format_table,
    load_flipped,
)

ALL_AREAS = "all"
PARITY_FOLDER = Path(__file__).resolve().parent
REPO_ROOT = PARITY_FOLDER.parents[1]
RUNNER = PARITY_FOLDER / "oracle_runner.py"
FLIPPED_FILE = PARITY_FOLDER / "flipped.toml"
RUNS_FOLDER = Path(".parity") / "runs"
TIMINGS_FILE = "timings.json"
RUNNER_FILE = "runner.json"
HASH_SEED = "PYTHONHASHSEED"
PYTHON_VARIABLES = "PYTHON"
# -I would also imply -E, which ignores PYTHONHASHSEED; -s and -P keep the rest of its
# isolation, and runner_environment drops the other PYTHON* variables -E would have ignored.
RUNNER_FLAGS = ("-s", "-P", "-B")
CORE_E8S = REPO_ROOT / "e8s"
LANE_TESTS = "test_parity_{area}.py"


@dataclass(frozen=True)
class AreaRun:
    """Holds one area's comparison.

    Attributes:
        reports: Qualified case name to its parity report.
        speed: The timed cases' minima on both sides.
        core: Qualified case name to the working tree's outputs.
    """

    reports: Mapping[str, ParityReport]
    speed: SpeedReport
    core: Mapping[str, Mapping[str, Output]]


def runner_command(python: Path, area: str, out: Path, e8s_from: Path) -> list[str]:
    """Builds the oracle_runner.py command for one area under one interpreter.

    -s and -P keep the user site and the script folder off sys.path, and -B keeps the
    interpreter from writing bytecode, so the oracle venv's digest stays the one its READY
    records. The command runs with runner_environment, so PYTHONHASHSEED applies.

    Args:
        python: The interpreter to run.
        area: The area whose cases run.
        out: The folder the runner writes.
        e8s_from: The folder e8s must be imported from; the runner refuses any other.

    Returns:
        command: The argument vector.
    """
    return [
        str(python),
        *RUNNER_FLAGS,
        str(RUNNER),
        area,
        str(out),
        "--cases-root",
        str(REPO_ROOT),
        "--e8s-from",
        str(e8s_from),
    ]


def runner_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Returns the environment for a runner: no PYTHON* variable except PYTHONHASHSEED.

    Args:
        environment: The driver's environment.

    Returns:
        filtered: The same variables without PYTHONPATH, PYTHONHOME, PYTHONSTARTUP and the
            other PYTHON* variables that could change what the runner imports.
    """
    return {
        name: value
        for name, value in environment.items()
        if not name.startswith(PYTHON_VARIABLES) or name == HASH_SEED
    }


def _check_hash_seed(out: Path, area: str) -> None:
    probe = json.loads((out / RUNNER_FILE).read_text(encoding="utf-8"))
    if probe.get("hash_seed") != os.environ.get(HASH_SEED):
        raise SystemExit(f"E: parity runner for {area} ran under hash seed {probe!r}")


def run_side(python: Path, area: str, out: Path, e8s_from: Path) -> None:
    """Runs oracle_runner.py for one area under one interpreter, under the driver's hash seed.

    Args:
        python: The interpreter to run.
        area: The area whose cases run.
        out: The folder the runner writes; emptied first.
        e8s_from: The folder e8s must be imported from; the runner refuses any other.
    """
    if out.exists():
        for stale in out.iterdir():
            stale.unlink()
    out.mkdir(parents=True, exist_ok=True)
    command = runner_command(python, area, out, e8s_from)
    result = subprocess.run(command, env=runner_environment(os.environ), check=False)
    if result.returncode != 0:
        raise SystemExit(f"E: parity runner for {area} under {python} exited {result.returncode}")
    _check_hash_seed(out, area)


def _timings(folder: Path) -> dict[str, float]:
    timings = json.loads((folder / TIMINGS_FILE).read_text(encoding="utf-8"))
    return {str(name): float(seconds) for name, seconds in timings.items()}


def _side_outputs(folder: Path, cases: Sequence[Case]) -> dict[str, dict[str, Output]]:
    outputs = read_outputs(folder)
    if sorted(outputs) != sorted(case.name for case in cases):
        raise SystemExit(f"E: {folder} holds cases {sorted(outputs)}, not the loaded ones")
    return outputs


def run_area(area: str, oracle_python: Path, work: Path, flips: Mapping[str, Flip]) -> AreaRun:
    """Runs one area's cases under the oracle and the working tree, then compares them.

    Args:
        area: The area to run.
        oracle_python: The oracle venv's interpreter, from build_oracle.
        work: The folder both sides write under.
        flips: The flipped cases by qualified name.

    Returns:
        run: Each case's report, the speed report and the core outputs.
    """
    cases = load_cases(area)
    run_side(oracle_python, area, work / "oracle", oracle_python.parents[1])
    run_side(Path(sys.executable), area, work / "core", CORE_E8S)
    oracle = _side_outputs(work / "oracle", cases)
    core = _side_outputs(work / "core", cases)
    reports = {
        case.qualified_name: compare_case(
            case.qualified_name, oracle[case.name], core[case.name], flips
        )
        for case in cases
    }
    speed = compare_speed(_timings(work / "oracle"), _timings(work / "core"))
    named_core = {case.qualified_name: core[case.name] for case in cases}
    return AreaRun(reports=reports, speed=speed, core=named_core)


def _areas(area: str) -> list[str]:
    if area != ALL_AREAS:
        return [area]
    return sorted({case.area for case in load_all_cases()})


def _run_lane_tests(areas: Sequence[str]) -> bool:
    tests = [PARITY_FOLDER / LANE_TESTS.format(area=area) for area in areas]
    present = [str(path) for path in tests if path.is_file()]
    if not present:
        return True
    command = [sys.executable, "-m", "pytest", "-m", "parity", "-p", "no:cacheprovider"]
    return subprocess.run([*command, *present], check=False).returncode == 0


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare the working tree with v0.4.0.")
    parser.add_argument("area", help=f"an area with a cases_<area>.py, or {ALL_AREAS!r}")
    parser.add_argument("--speed", action="store_true", help="fail when a core case is slower")
    parser.add_argument("--digest", type=Path, help="write the SHA-256 of every core output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Builds the oracle, runs each area on both sides, prints the tables, fails on a gate."""
    arguments = _arguments(argv)
    flips = load_flipped(FLIPPED_FILE, load_all_cases())
    oracle_python = build_oracle()
    areas = _areas(arguments.area)
    passed, digests = True, []
    for area in areas:
        run = run_area(area, oracle_python, RUNS_FOLDER / area, flips)
        print(format_table(run.reports), flush=True)
        passed &= all(report.passed for report in run.reports.values())
        if arguments.speed:
            print(format_speed(run.speed), flush=True)
            passed &= run.speed.passed
        digests += [line for name, core in run.core.items() for line in digest_lines(name, core)]
    if arguments.digest is not None:
        arguments.digest.parent.mkdir(parents=True, exist_ok=True)
        arguments.digest.write_bytes("".join(f"{line}\n" for line in digests).encode("utf-8"))
    passed &= _run_lane_tests(areas)
    if not passed:
        raise SystemExit("E: parity failed")


if __name__ == "__main__":
    main()
