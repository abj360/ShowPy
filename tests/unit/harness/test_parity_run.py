#!/usr/bin/env python3

"""
test_parity_run.py --- checks how the parity driver launches the oracle and the working tree

Contains:
    test_runner_writes_no_bytecode: every launch passes -B, so the oracle venv digest holds
    test_runner_command_keeps_isolation_but_reads_the_hash_seed: -s -P, never -I or -E
    test_runner_environment_keeps_only_the_hash_seed_of_the_python_variables: no PYTHONPATH
    test_regression_runner_sees_the_hash_seed_it_was_given: same seed, same hash; not across
"""

import json
import sys
from pathlib import Path

import pytest

import e8s
from tests.parity.run import (
    RUNNER,
    RUNNER_FILE,
    run_side,
    runner_command,
    runner_environment,
)

PYTHON = Path("venv", "bin", "python")
E8S_FOLDER = Path(e8s.__file__).resolve().parent


def _flags() -> list[str]:
    command = runner_command(PYTHON, "time", Path("out"), Path("e8s"))
    return command[1 : command.index(str(RUNNER))]


def test_runner_writes_no_bytecode() -> None:
    command = runner_command(PYTHON, "time", Path("out"), Path("e8s"))
    assert command[0] == str(PYTHON)
    assert "-B" in _flags()


def test_runner_command_keeps_isolation_but_reads_the_hash_seed() -> None:
    flags = _flags()
    assert {"-s", "-P"} <= set(flags)
    assert not {"-I", "-E"} & set(flags)


def test_runner_environment_keeps_only_the_hash_seed_of_the_python_variables() -> None:
    environment = {
        "PATH": "/bin",
        "HOME": "/home/parity",
        "PYTHONHASHSEED": "1",
        "PYTHONPATH": "/elsewhere",
        "PYTHONSTARTUP": "/elsewhere/startup.py",
        "PYTHONHOME": "/elsewhere",
    }
    assert runner_environment(environment) == {
        "PATH": "/bin",
        "HOME": "/home/parity",
        "PYTHONHASHSEED": "1",
    }


def test_regression_runner_sees_the_hash_seed_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = []
    for index, seed in enumerate(("0", "0", "1")):
        monkeypatch.setenv("PYTHONHASHSEED", seed)
        out = tmp_path / f"launch-{index}"
        run_side(Path(sys.executable), "time", out, E8S_FOLDER)
        probe = json.loads((out / RUNNER_FILE).read_text(encoding="utf-8"))
        assert probe["hash_seed"] == seed
        probes.append(probe["hash_probe"])
    assert probes[0] == probes[1]
    assert probes[1] != probes[2]
