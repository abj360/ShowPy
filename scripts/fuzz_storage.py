#!/usr/bin/env python3

"""
fuzz_storage.py --- fuzzes the storage property tests under libFuzzer through Atheris

Every `@given` test and every `RuleBasedStateMachine` that tests/property/test_storage_*.py
defines is a target. Hypothesis turns each into a function of one byte string, so libFuzzer's
coverage of e8s steers the test's choices. A run with no target fails closed. Without --target
every target is fuzzed in turn, each in its own process, because libFuzzer ends the process on a
crash; the run goes on after a crash and fails at the end, naming each failed target.

Contains:
    STORAGE_GLOB: the files whose tests are fuzz targets
    FUZZ_SECONDS_PER_TEST: how long libFuzzer fuzzes one target in a nightly run
    TARGET_GRACE_SECONDS: how long past its slot one target's process may run before it is
        stopped and counted as failed
    NoTargetsError: raised when no file or no target is found
    discover_targets(): maps each target ID to the function that runs one input
    fuzz_target(): fuzzes one target under Atheris and libFuzzer
    run_in_own_process(): fuzzes one target in a child process of this script, bounded in time
    fuzz_all(): fuzzes every target in turn and names each one that failed
    replay_target(): runs one saved input through one target, once
    main(): parses the arguments and returns the exit code
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Protocol

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, get_state_machine_test

STORAGE_GLOB = "tests/property/test_storage_*.py"
FUZZ_SECONDS_PER_TEST = 300
# libFuzzer checks its time limit only between inputs, and each process imports e8s first.
TARGET_GRACE_SECONDS = 120
PROFILE_ENV = "HYPOTHESIS_PROFILE"
NIGHTLY_PROFILE = "nightly"
ID_SEPARATOR = "::"
PATH_SEPARATOR = "__"
CORPUS_ROOT = "fuzz-corpus"
CRASH_ROOT = "fuzz-crashes"
INSTRUMENTED_PACKAGES = ["e8s"]
SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
USAGE_ERROR = 2
FAILURE = 1

TestInput = Callable[[bytes], None]
TargetRunner = Callable[[str, int], int]


class AtherisModule(Protocol):
    """Names the part of atheris that the runner calls."""

    def instrument_imports(
        self, include: Sequence[str] | None = None, exclude: Sequence[str] | None = None
    ) -> AbstractContextManager[None]:
        """Returns a context manager that adds libFuzzer coverage to the imports inside it."""
        ...

    def Setup(self, args: Sequence[str], test_one_input: TestInput) -> list[str]:
        """Registers the libFuzzer arguments and the function that takes each input."""
        ...

    def Fuzz(self) -> None:
        """Runs libFuzzer until its time limit or a crash."""
        ...


class NoTargetsError(Exception):
    """Reports that no file matches STORAGE_GLOB or that the files hold no fuzz target."""


def _import_atheris() -> AtherisModule:
    """Imports atheris, which only Linux x86_64 hosts install."""
    import atheris  # pyright: ignore[reportMissingModuleSource]

    return atheris


def _load_module(path: Path, root: Path) -> ModuleType:
    """Imports one test file under its file stem, with the repository root importable."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise NoTargetsError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def _function_target(candidate: object) -> TestInput | None:
    """Returns the fuzz entry point of a Hypothesis test function, None for anything else."""
    fuzz_one_input = getattr(getattr(candidate, "hypothesis", None), "fuzz_one_input", None)
    if not callable(fuzz_one_input):
        return None

    def run(data: bytes) -> None:
        fuzz_one_input(data)

    return run


def _machine_target(machine: type[RuleBasedStateMachine]) -> TestInput | None:
    """Returns the fuzz entry point of a state machine, None when it has no entry point."""
    return _function_target(get_state_machine_test(machine))


def _module_targets(module: ModuleType) -> dict[str, TestInput]:
    """Finds the targets that a module itself defines, keyed by `<file stem>::<name>`."""
    found: dict[str, TestInput] = {}
    for name, value in vars(module).items():
        if getattr(value, "__module__", None) != module.__name__:
            continue
        is_machine = isinstance(value, type) and issubclass(value, RuleBasedStateMachine)
        target = _machine_target(value) if is_machine else _function_target(value)
        if target is not None:
            found[f"{module.__name__}{ID_SEPARATOR}{name}"] = target
    return found


def discover_targets(root: Path) -> dict[str, TestInput]:
    """Maps each fuzz target ID to the function that runs one input through that target.

    Args:
        root: The repository root that STORAGE_GLOB is read from.

    Returns:
        targets: Target IDs, sorted, each `<file stem>::<name>`, with their entry points.

    Raises:
        NoTargetsError: When no file matches STORAGE_GLOB or the files define no target.
    """
    files = sorted(root.glob(STORAGE_GLOB))
    if not files:
        raise NoTargetsError(f"no storage fuzz targets match {STORAGE_GLOB}")
    found: dict[str, TestInput] = {}
    for path in files:
        found.update(_module_targets(_load_module(path, root)))
    if not found:
        raise NoTargetsError(f"no storage fuzz targets in {', '.join(p.name for p in files)}")
    return dict(sorted(found.items()))


def _safe_name(target_id: str) -> str:
    """Turns a target ID into one folder name."""
    return target_id.replace(ID_SEPARATOR, PATH_SEPARATOR)


def fuzz_target(atheris: AtherisModule, root: Path, target_id: str, seconds: int) -> int:
    """Fuzzes one target under libFuzzer, keeping its corpus and any crash input under root.

    Args:
        atheris: The atheris module.
        root: The repository root holding the tests and the corpus folders.
        target_id: The target to fuzz, as discover_targets names it.
        seconds: How long libFuzzer runs.

    Returns:
        exit_code: 0 when the time limit passes; a crash ends the process through libFuzzer.
    """
    corpus = root / CORPUS_ROOT / _safe_name(target_id)
    crashes = root / CRASH_ROOT / _safe_name(target_id)
    corpus.mkdir(parents=True, exist_ok=True)
    crashes.mkdir(parents=True, exist_ok=True)
    with atheris.instrument_imports(include=INSTRUMENTED_PACKAGES):
        targets = discover_targets(root)
    if target_id not in targets:
        raise NoTargetsError(f"unknown target {target_id}; known: {', '.join(targets)}")
    argv = [
        sys.argv[0],
        str(corpus),
        f"-max_total_time={seconds}",
        f"-artifact_prefix={crashes}/",
    ]
    atheris.Setup(argv, targets[target_id])
    atheris.Fuzz()
    return 0


def run_in_own_process(
    target_id: str, seconds: int, *, script: Path = SCRIPT, grace: int = TARGET_GRACE_SECONDS
) -> int:
    """Fuzzes one target in a child process of the runner script, stopped past its slot.

    Args:
        target_id: The target to fuzz, as discover_targets names it.
        seconds: How long libFuzzer runs.
        script: The runner script the child runs; its repository is the child's root.
        grace: How long past `seconds` the child may run before it is stopped.

    Returns:
        exit_code: The child's exit code, or 1 when it was stopped.
    """
    command = [sys.executable, str(script), "--target", target_id, "--seconds", str(seconds)]
    limit = seconds + grace
    try:
        return subprocess.run(command, check=False, timeout=limit).returncode
    except subprocess.TimeoutExpired:
        print(f"E: {target_id} ran past {limit} s", file=sys.stderr)
        return FAILURE


def fuzz_all(root: Path, seconds: int, run_target: TargetRunner) -> int:
    """Fuzzes every target in turn, going on after a failure, then names each failed target.

    Args:
        root: The repository root that STORAGE_GLOB is read from.
        seconds: How long libFuzzer fuzzes each target.
        run_target: Fuzzes one target for a number of seconds and returns its exit code.

    Returns:
        exit_code: 0 when every target passed, 1 otherwise.
    """
    failed: dict[str, int] = {}
    for target_id in discover_targets(root):
        print(f"Fuzzing {target_id} for {seconds} s", flush=True)
        code = run_target(target_id, seconds)
        if code != 0:
            failed[target_id] = code
    for target_id, code in failed.items():
        print(f"E: {target_id} failed with exit {code}", file=sys.stderr)
    return FAILURE if failed else 0


def replay_target(root: Path, target_id: str, crash_file: Path) -> int:
    """Runs one saved input through one target, once.

    Args:
        root: The repository root that STORAGE_GLOB is read from.
        target_id: The target to run, as discover_targets names it.
        crash_file: The saved input, such as one under fuzz-crashes/.

    Returns:
        exit_code: 0 when the input passes, 1 when the target raises.
    """
    targets = discover_targets(root)
    if target_id not in targets:
        raise NoTargetsError(f"unknown target {target_id}; known: {', '.join(targets)}")
    try:
        targets[target_id](crash_file.read_bytes())
    except Exception:
        traceback.print_exc()
        print(f"E: {target_id} failed on {crash_file}", file=sys.stderr)
        return FAILURE
    return 0


def _positive_seconds(text: str) -> int:
    """Parses a positive whole number of seconds."""
    if not text.isdecimal() or int(text) == 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive whole number")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    """Builds the command-line parser."""
    parser = argparse.ArgumentParser(prog="fuzz_storage.py", description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the target IDs as JSON")
    parser.add_argument("--target", help="the target to fuzz or replay; all targets when absent")
    parser.add_argument("--seconds", type=_positive_seconds, default=FUZZ_SECONDS_PER_TEST)
    parser.add_argument("--replay", type=Path, help="run this saved input once")
    return parser


def _register_profile() -> None:
    """Loads the nightly Hypothesis profile: no deadline, no derandomization.

    Only the command line calls this, so tests that call main keep their own profile.
    """
    settings.register_profile(NIGHTLY_PROFILE, deadline=None, derandomize=False)
    settings.load_profile(os.environ.get(PROFILE_ENV, NIGHTLY_PROFILE))


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    load_atheris: Callable[[], AtherisModule] = _import_atheris,
    run_target: TargetRunner = run_in_own_process,
) -> int:
    """Parses the arguments, then lists, fuzzes or replays targets.

    Args:
        argv: The arguments after the script name, sys.argv[1:] when None.
        root: The repository root that STORAGE_GLOB is read from.
        load_atheris: Returns the atheris module; fails on a host without it.
        run_target: Fuzzes one target in its own process when no --target is given.

    Returns:
        exit_code: 0 on success, non-zero on a refusal or a replayed crash.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    target: str | None = args.target
    replay: Path | None = args.replay
    if args.list and target is not None:
        parser.error("give --list, --target ID, or neither to fuzz every target")
    try:
        if args.list:
            print(json.dumps(list(discover_targets(root))))
            return 0
        if replay is not None:
            if target is None:
                parser.error("--replay needs --target")
            return replay_target(root, target, replay)
        try:
            atheris = load_atheris()
        except ImportError:
            print("E: atheris is not installed; it installs on Linux x86_64 only", file=sys.stderr)
            return FAILURE
        if target is None:
            return fuzz_all(root, args.seconds, run_target)
        return fuzz_target(atheris, root, target, args.seconds)
    except (NoTargetsError, OSError) as error:
        print(f"E: {error}", file=sys.stderr)
        return FAILURE


if __name__ == "__main__":
    _register_profile()
    raise SystemExit(main())
