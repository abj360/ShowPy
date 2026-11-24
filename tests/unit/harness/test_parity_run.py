#!/usr/bin/env python3

"""
test_parity_run.py --- checks how the parity driver launches the oracle and the working tree

Contains:
    test_runner_writes_no_bytecode: every launch passes -B, so the oracle venv digest holds
    test_runner_command_keeps_isolation_but_reads_the_hash_seed: -s -P, never -I or -E
    test_runner_environment_keeps_only_the_hash_seed_of_the_python_variables: no PYTHONPATH
    test_regression_runner_sees_the_hash_seed_it_was_given: same seed, same hash; not across
    test_runner_refuses_an_e8s_from_another_folder: a launch with the wrong --e8s-from exits
    test_import_e8s_refuses_an_e8s_outside_the_expected_folder: the guard itself
    test_cases_module_must_come_from_the_cases_root: a case module from elsewhere is refused
    test_sides_must_differ_in_interpreter_and_e8s: one interpreter or e8s for both is refused
    test_run_area_refuses_the_working_tree_as_its_own_oracle: nothing launches
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import e8s
from tests.parity.oracle_runner import check_cases_module, import_e8s
from tests.parity.run import (
    CORE_E8S,
    REPO_ROOT,
    RUNNER,
    RUNNER_FILE,
    require_distinct_sides,
    run_area,
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


def test_runner_refuses_an_e8s_from_another_folder(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    command = runner_command(Path(sys.executable), "time", out, tmp_path / "elsewhere")
    result = subprocess.run(
        command,
        env=runner_environment(os.environ),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0
    assert "E: e8s imported from" in result.stderr
    assert not list(out.glob("*.parity"))


def test_import_e8s_refuses_an_e8s_outside_the_expected_folder(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: e8s imported from"):
        import_e8s(tmp_path)
    assert import_e8s(E8S_FOLDER) is e8s


def test_cases_module_must_come_from_the_cases_root(tmp_path: Path) -> None:
    planted = SimpleNamespace(__file__=str(tmp_path / "tests" / "parity" / "cases.py"))
    with pytest.raises(SystemExit, match=r"^E: tests\.parity\.cases imported from"):
        check_cases_module(planted, REPO_ROOT)
    real = SimpleNamespace(__file__=str(REPO_ROOT / "tests" / "parity" / "cases.py"))
    check_cases_module(real, REPO_ROOT)


def test_sides_must_differ_in_interpreter_and_e8s(tmp_path: Path) -> None:
    core = Path(sys.executable)
    oracle = tmp_path / "venv" / "bin" / "python"
    require_distinct_sides(oracle, oracle.parents[1], core, CORE_E8S)
    with pytest.raises(SystemExit, match=r"^E: the oracle and the working tree"):
        require_distinct_sides(core, oracle.parents[1], core, CORE_E8S)
    with pytest.raises(SystemExit, match=r"^E: the oracle and the working tree"):
        require_distinct_sides(oracle, REPO_ROOT, core, CORE_E8S)
    with pytest.raises(SystemExit, match=r"^E: the oracle and the working tree"):
        require_distinct_sides(oracle, CORE_E8S / "time", core, CORE_E8S)


def test_run_area_refuses_the_working_tree_as_its_own_oracle(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"^E: the oracle and the working tree"):
        run_area("time", Path(sys.executable), tmp_path / "work", {})
    assert not (tmp_path / "work").exists()
