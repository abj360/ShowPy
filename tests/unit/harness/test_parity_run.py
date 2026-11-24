#!/usr/bin/env python3

"""
test_parity_run.py --- checks how the parity driver launches the oracle and the working tree

Contains:
    test_runner_writes_no_bytecode: every launch passes -B, so the oracle venv digest holds
"""

from pathlib import Path

from tests.parity.run import RUNNER, runner_command

PYTHON = Path("venv", "bin", "python")


def test_runner_writes_no_bytecode() -> None:
    command = runner_command(PYTHON, "time", Path("out"), Path("e8s"))
    assert command[0] == str(PYTHON)
    assert "-B" in command[: command.index(str(RUNNER))]
