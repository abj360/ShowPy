#!/usr/bin/env python3

"""
oracle_runner.py --- runs one area's parity cases under one interpreter and writes their outputs

Runs with `-s -P -B`, under the oracle venv's interpreter or the working tree's, and refuses
`-I` or `-E`, which would drop PYTHONHASHSEED. It imports e8s first and refuses an e8s from
anywhere but `--e8s-from`, then appends `--cases-root` to the end of sys.path to import the
case modules, so the cases never shadow the e8s under test. It records the hash seed it saw and
a string hash in runner.json, so the driver can check that the seed reached it.

Usage: `python -s -P -B oracle_runner.py <area> <out> --cases-root <repo> --e8s-from <folder>`

Contains:
    RUNS: the timed runs per timed case; timings.json keeps their minimum
    hash_probe(): records the hash seed the runner sees and a string hash under it
    import_e8s(): imports e8s and checks where it came from
    check_cases_module(): refuses a case module imported from outside the cases root
    minimum_run_s(): returns a case's fastest of RUNS runs, in seconds
    main(): runs every case of the area, writing <case>.parity files and timings.json
"""

import argparse
import importlib
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

RUNS = 7
TIMINGS_FILE = "timings.json"
RUNNER_FILE = "runner.json"
HASH_SEED = "PYTHONHASHSEED"
PROBE_TEXT = "e8s parity hash probe"
CASES_MODULE = "tests.parity.cases"


def hash_probe() -> dict[str, object]:
    """Records the PYTHONHASHSEED the runner sees and the hash of a fixed string under it.

    Returns:
        probe: `hash_seed` (None when unset) and `hash_probe`, equal across launches under the
            same fixed seed and different under another.
    """
    if sys.flags.ignore_environment:
        raise SystemExit("E: the parity runner ignores PYTHONHASHSEED under -I or -E; use -s -P")
    return {"hash_seed": os.environ.get(HASH_SEED), "hash_probe": hash(PROBE_TEXT)}


def import_e8s(expected: Path) -> ModuleType:
    """Imports e8s and refuses one that does not live under the expected folder.

    Args:
        expected: The folder the e8s package must be imported from.

    Returns:
        e8s: The imported package.
    """
    e8s = importlib.import_module("e8s")
    location = Path(str(e8s.__file__)).resolve()
    if not location.is_relative_to(expected.resolve()):
        raise SystemExit(f"E: e8s imported from {location}, expected under {expected}")
    return e8s


def check_cases_module(module: object, cases_root: Path) -> None:
    """Refuses a case module that was not imported from under the cases root.

    Args:
        module: The imported tests.parity.cases module.
        cases_root: The repository root the case modules must come from.
    """
    location = Path(str(getattr(module, "__file__", ""))).resolve()
    if not location.is_relative_to(cases_root.resolve()):
        raise SystemExit(f"E: {CASES_MODULE} imported from {location}, not {cases_root}")


def minimum_run_s(run: object, e8s: ModuleType) -> float:
    """Returns the fastest of RUNS calls of a case's run function, in seconds."""
    if not callable(run):
        raise SystemExit(f"E: case run {run!r} is not callable")
    fastest = float("inf")
    for _ in range(RUNS):
        start = time.perf_counter()
        run(e8s)
        fastest = min(fastest, time.perf_counter() - start)
    return fastest


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one area's parity cases.")
    parser.add_argument("area")
    parser.add_argument("out", type=Path)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--e8s-from", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Runs every case of one area and writes its outputs and the timed cases' minima."""
    arguments = _arguments(argv)
    probe = json.dumps(hash_probe(), sort_keys=True)
    (arguments.out / RUNNER_FILE).write_text(f"{probe}\n", encoding="utf-8")
    e8s = import_e8s(arguments.e8s_from)
    sys.path.append(str(arguments.cases_root.resolve()))
    from tests.parity.cases import load_cases, write_outputs

    check_cases_module(sys.modules[CASES_MODULE], arguments.cases_root)
    timings: dict[str, float] = {}
    for case in load_cases(arguments.area):
        write_outputs(arguments.out, case.name, case.run(e8s))
        if case.timed:
            timings[case.qualified_name] = minimum_run_s(case.run, e8s)
    if sys.modules["e8s"] is not e8s:
        raise SystemExit("E: a case replaced the e8s module under test")
    timings_text = json.dumps(timings, sort_keys=True, indent=1)
    (arguments.out / TIMINGS_FILE).write_text(f"{timings_text}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
