#!/usr/bin/env python3

"""
test_run_mutants.py --- tests the mutation run: kept modules, the mutmut stage and both scores

Contains:
    test_kept_modules_reads_every_stays_list: maps each area to its package, sorted, each once
    test_kept_modules_is_empty_without_a_disposition_file: no ported package, no module
    test_stage_mutmut_limits_mutmut_to_the_kept_modules: copies the library and the tests and
        adds `only_mutate` to the staged `[tool.mutmut]` table
    test_stage_mutmut_refuses_a_pyproject_without_the_table: fails closed
    test_rust_score_counts_timeouts_as_detected_and_skips_unviable: the Rust score
    test_python_score_follows_mutmut: killed and timed-out over all but skipped
    test_scores_are_none_with_nothing_tested: no division by zero
    test_run_python_scores_the_kept_modules_in_the_stage: runs mutmut in the stage
    test_run_python_reports_a_mutmut_that_cannot_run: a failing mutmut is a cannot-run record
    test_repository_mutmut_settings_name_the_ported_tests: the settings mutmut runs with
    test_regression_unreadable_cargo_mutants_outcomes_are_cannot_run: a missing, broken or
        incomplete outcomes file is recorded, the summary is still written and the run fails
    test_regression_unreadable_mutmut_stats_are_cannot_run: the same for mutmut's stats
    test_regression_a_malformed_disposition_file_is_cannot_run: no traceback, a record
"""

import json
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.run_mutants import (
    CANNOT_RUN,
    NO_KEPT_MODULES,
    SCORED,
    SUMMARY,
    kept_modules,
    python_score,
    record_scores,
    run_python,
    rust_score,
    stage_mutmut,
)
from tests.unit.tooling.test_noxfile import PORTED_TESTS, PYPROJECT

SETTINGS = '[tool.pytest.ini_options]\naddopts = ["-q"]\n\n[tool.mutmut]\nsource_paths = ["e8s/"]\n'
CLOCK = "e8s/time/clock.py"
ENGINE_MODELS = "e8s/choreo/engine_models.py"
FAKE_MUTMUT = """
import json, pathlib, sys
if sys.argv[1] == "export-cicd-stats":
    stats = {{"killed": 3, "survived": 1, "timeout": 0, "no_tests": 0, "suspicious": 0,
              "segfault": 0, "skipped": 0, "total": 4}}
    pathlib.Path("mutants").mkdir()
    pathlib.Path("mutants/mutmut-cicd-stats.json").write_text(json.dumps(stats))
sys.exit({exit_code})
"""
PROPERTY_AND_GOLDEN = ("tests/property", "tests/golden")


def _write_disposition(folder: Path, area: str, stays: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = f"moves = []\nreexport = []\nstays = {json.dumps(stays)}\ninterop = []\n"
    (folder / f"{area}.toml").write_text(text, encoding="utf-8")


def _repository(root: Path) -> Path:
    for module in (CLOCK, ENGINE_MODELS):
        (root / module).parent.mkdir(parents=True, exist_ok=True)
        (root / module).write_text("VALUE = 1\n", encoding="utf-8")
    (root / "e8s" / "__pycache__").mkdir()
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_value.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(SETTINGS, encoding="utf-8")
    ported = root / "tests" / "unit" / "rules" / "ported"
    _write_disposition(ported, "time", ["clock"])
    _write_disposition(ported, "engine", ["engine_models"])
    return ported


def test_kept_modules_reads_every_stays_list(tmp_path: Path) -> None:
    _write_disposition(tmp_path, "time", ["models", "clock"])
    _write_disposition(tmp_path, "engine", ["engine_models"])
    _write_disposition(tmp_path, "forms", ["models"])
    _write_disposition(tmp_path, "geo", [])
    assert kept_modules(tmp_path) == [
        "e8s/choreo/engine_models.py",
        "e8s/choreo/models.py",
        CLOCK,
        "e8s/time/models.py",
    ]


def test_kept_modules_is_empty_without_a_disposition_file(tmp_path: Path) -> None:
    assert kept_modules(tmp_path) == []


def test_stage_mutmut_limits_mutmut_to_the_kept_modules(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    stage = tmp_path / "stage"
    (stage / "stale").mkdir(parents=True)
    stage_mutmut(root, stage, [CLOCK])
    assert (stage / CLOCK).is_file()
    assert (stage / "tests" / "unit" / "test_value.py").is_file()
    assert not (stage / "e8s" / "__pycache__").exists()
    assert not (stage / "stale").exists()
    settings = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))
    assert settings["tool"]["mutmut"] == {"only_mutate": [CLOCK], "source_paths": ["e8s/"]}
    assert settings["tool"]["pytest"]["ini_options"] == {"addopts": ["-q"]}


def test_stage_mutmut_refuses_a_pyproject_without_the_table(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    (root / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"no \[tool.mutmut\] table"):
        stage_mutmut(root, tmp_path / "stage", [CLOCK])


def test_rust_score_counts_timeouts_as_detected_and_skips_unviable() -> None:
    outcomes = {"caught": 5, "missed": 3, "timeout": 2, "unviable": 9, "total_mutants": 19}
    assert rust_score({**outcomes, "outcomes": []}) == {
        "status": SCORED,
        **outcomes,
        "score": 0.7,
    }


def test_python_score_follows_mutmut() -> None:
    stats = {
        "killed": 6,
        "survived": 2,
        "timeout": 1,
        "no_tests": 1,
        "suspicious": 0,
        "segfault": 0,
        "skipped": 2,
        "total": 12,
        "check_was_interrupted_by_user": 0,
    }
    record = python_score(stats)
    assert record["status"] == SCORED
    assert record["score"] == 0.7
    assert record["no_tests"] == 1
    assert "check_was_interrupted_by_user" not in record


def test_scores_are_none_with_nothing_tested() -> None:
    assert (
        rust_score(
            dict.fromkeys(("caught", "missed", "timeout"), 0)
            | {
                "unviable": 4,
                "total_mutants": 4,
            }
        )["score"]
        is None
    )
    stats = dict.fromkeys(
        ("killed", "survived", "timeout", "no_tests", "suspicious", "segfault"), 0
    ) | {"skipped": 3, "total": 3}
    assert python_score(stats)["score"] is None


def _fake_mutmut(folder: Path, exit_code: int) -> list[str]:
    script = folder / "fake_mutmut.py"
    script.write_text(FAKE_MUTMUT.format(exit_code=exit_code), encoding="utf-8")
    return [sys.executable, str(script)]


def test_run_python_scores_the_kept_modules_in_the_stage(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    ported = _repository(root)
    stage = tmp_path / "stage"
    record = run_python(_fake_mutmut(tmp_path, 0), root, ported, stage)
    assert record["status"] == SCORED
    assert record["score"] == 0.75
    settings = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))
    assert settings["tool"]["mutmut"]["only_mutate"] == [ENGINE_MODELS, CLOCK]


def test_run_python_reports_a_mutmut_that_cannot_run(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    ported = _repository(root)
    record = run_python(_fake_mutmut(tmp_path, 1), root, ported, tmp_path / "stage")
    assert record == {"status": CANNOT_RUN, "detail": "mutmut run exited 1"}


def test_repository_mutmut_settings_name_the_ported_tests() -> None:
    settings = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mutmut"]
    assert settings["source_paths"] == ["e8s/"]
    assert "only_mutate" not in settings
    ported = [path for path in PORTED_TESTS if path != "tests/golden"]
    selection = settings["pytest_add_cli_args_test_selection"]
    assert selection == [*ported, *PROPERTY_AND_GOLDEN]


FAKE_CARGO_MUTANTS = """
import pathlib, sys
out = pathlib.Path("mutants.out")
out.mkdir()
if {outcomes!r} is not None:
    (out / "outcomes.json").write_text({outcomes!r}, encoding="utf-8")
sys.exit(2)
"""
FAKE_MUTMUT_STATS = """
import pathlib, sys
if sys.argv[1] == "export-cicd-stats" and {stats!r} is not None:
    pathlib.Path("mutants").mkdir()
    pathlib.Path("mutants/mutmut-cicd-stats.json").write_text({stats!r}, encoding="utf-8")
"""
GOOD_COUNTS = {"caught": 1, "missed": 1, "timeout": 0, "unviable": 0, "total_mutants": 2}
UNREADABLE = {
    "missing": None,
    "not json": "{",
    "not an object": "[]",
    "missing key": json.dumps({"caught": 1}),
    "bad value": json.dumps({**GOOD_COUNTS, "missed": "many"}),
    "null value": json.dumps({**GOOD_COUNTS, "missed": None}),
}


@pytest.mark.parametrize("outcomes", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_regression_unreadable_cargo_mutants_outcomes_are_cannot_run(
    tmp_path: Path, outcomes: str | None
) -> None:
    script = tmp_path / "fake_cargo_mutants.py"
    script.write_text(FAKE_CARGO_MUTANTS.format(outcomes=outcomes), encoding="utf-8")
    (tmp_path / "ported").mkdir()
    exit_code = record_scores(
        [sys.executable, str(script)], tmp_path, ported_root=tmp_path / "ported"
    )
    assert exit_code == 1
    summary = json.loads((tmp_path / SUMMARY).read_text(encoding="utf-8"))
    assert summary["rust"]["status"] == CANNOT_RUN
    assert summary["rust"]["detail"].startswith("cannot score mutants.out/outcomes.json: ")
    assert summary["python"] == {"status": NO_KEPT_MODULES}


@pytest.mark.parametrize("stats", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_regression_unreadable_mutmut_stats_are_cannot_run(
    tmp_path: Path, stats: str | None
) -> None:
    root = tmp_path / "repo"
    ported = _repository(root)
    script = tmp_path / "fake_mutmut.py"
    script.write_text(FAKE_MUTMUT_STATS.format(stats=stats), encoding="utf-8")
    record = run_python([sys.executable, str(script)], root, ported, tmp_path / "stage")
    assert record["status"] == CANNOT_RUN
    assert str(record["detail"]).startswith("cannot score mutants/mutmut-cicd-stats.json: ")


def test_regression_a_malformed_disposition_file_is_cannot_run(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    ported = _repository(root)
    (ported / "time.toml").write_text("moves = []\n", encoding="utf-8")
    record = run_python(_fake_mutmut(tmp_path, 0), root, ported, tmp_path / "stage")
    assert record["status"] == CANNOT_RUN
    assert str(record["detail"]).startswith("cannot read the disposition files: ")
