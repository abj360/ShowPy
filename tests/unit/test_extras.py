#!/usr/bin/env python3

"""
test_extras.py --- tests that optional dependencies load only when an extra-gated call needs them

Contains:
    block_modules(): makes importlib.import_module raise ImportError for some top-level modules
    run_fresh(): runs a script in a fresh interpreter at the repository root
    optional_dependencies(): reads the extras table of pyproject.toml
    test_core_modules_import_no_optional_dependency: imports every module in a fresh interpreter
        and finds no optional dependency loaded
    test_missing_extra_names_the_install_command: speeds() without pandas names e8s[tables]
    test_regression_gated_call_fails_closed_without_its_module: every gated call raises
        MissingExtraError naming its extra when any one of its optional modules is missing
    test_gate_failure_reports_a_call_that_runs_without_its_extra: proves the gate check can fail
    test_require_extra_returns_the_installed_module: returns the module when it imports
    test_require_extra_chains_the_import_error: keeps the ImportError as the cause
    test_all_extra_is_the_union_of_the_others: keeps `all` equal to every other extra combined
    test_gated_calls_name_declared_extras: every extra a hint names exists in pyproject.toml
    test_core_dependencies_hold_no_optional_package: keeps every package an extra installs out of
        core
"""

import importlib
import re
import subprocess
import sys
import tomllib
from collections.abc import Collection
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from e8s.choreo.motion_stats import speeds
from e8s.core.error_codes import ErrorCode
from e8s.core.extras import MissingExtraError, require_extra
from e8s.examples import EXAMPLE_TID, two_set_show
from scripts.check_core_install import GATED_CALLS, OPTIONAL_MODULES, GatedCall, gate_failure

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
ALL_EXTRA = "all"
# Fewer modules than this means the walk stopped early, not that e8s is small.
MIN_MODULES = 100
IMPORT_EVERY_MODULE = """
from scripts.check_core_install import import_every_module, loaded_optional_modules
names = import_every_module()
print(len(names), ",".join(loaded_optional_modules()))
"""
GATE_CASES = [
    pytest.param(gated, module, id=f"{gated.name}-{module}")
    for gated in GATED_CALLS
    for module in gated.modules
]


def block_modules(monkeypatch: pytest.MonkeyPatch, blocked: Collection[str]) -> None:
    """Makes importlib.import_module raise ImportError for the given top-level modules.

    Args:
        monkeypatch: The test's monkeypatch fixture, which undoes the patch afterwards.
        blocked: Top-level module names to treat as not installed.
    """
    real_import = importlib.import_module

    def guarded_import(name: str, package: str | None = None) -> ModuleType:
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", guarded_import)


def run_fresh(script: str) -> str:
    """Runs a script in a fresh interpreter at the repository root and returns its output."""
    done = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_core_modules_import_no_optional_dependency() -> None:
    count, _, loaded = run_fresh(IMPORT_EVERY_MODULE).partition(" ")
    assert int(count) > MIN_MODULES
    assert loaded == ""


def test_missing_extra_names_the_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    block_modules(monkeypatch, {"pandas"})
    with pytest.raises(MissingExtraError) as caught:
        speeds(two_set_show(), EXAMPLE_TID)
    assert caught.value.code == ErrorCode.MISSING_EXTRA
    assert "e8s[tables]" in caught.value.hint
    assert "speeds" in caught.value.message


@pytest.mark.parametrize(("gated", "module"), GATE_CASES)
def test_regression_gated_call_fails_closed_without_its_module(
    monkeypatch: pytest.MonkeyPatch, gated: GatedCall, module: str
) -> None:
    block_modules(monkeypatch, {module})
    assert gate_failure(gated) is None


def test_gate_failure_reports_a_call_that_runs_without_its_extra() -> None:
    ungated = GatedCall("two_set_show", "tables", ("pandas",), two_set_show)
    assert gate_failure(ungated) == "E: two_set_show ran without the tables extra"


def test_require_extra_returns_the_installed_module() -> None:
    assert require_extra("pandas", extra="tables", needed_by="speeds") is pd


def test_require_extra_chains_the_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    block_modules(monkeypatch, {"pandas"})
    with pytest.raises(MissingExtraError) as caught:
        require_extra("pandas", extra="tables", needed_by="speeds")
    assert isinstance(caught.value.__cause__, ImportError)
    assert caught.value.context["module"] == "pandas"
    assert caught.value.hint == (
        'install the extra: pip install "e8s[tables]" or uv add "e8s[tables]"'
    )


def _package(spec: str) -> str:
    """Returns the package name of a requirement such as `pandas>=3.1.1`."""
    return re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0]


def optional_dependencies() -> dict[str, list[str]]:
    """Reads the extras table of pyproject.toml."""
    extras: dict[str, list[str]] = tomllib.loads(PYPROJECT.read_text())["project"][
        "optional-dependencies"
    ]
    return extras


def test_all_extra_is_the_union_of_the_others() -> None:
    extras = optional_dependencies()
    others = {spec for name, specs in extras.items() if name != ALL_EXTRA for spec in specs}
    assert sorted(extras[ALL_EXTRA]) == sorted(others)


def test_gated_calls_name_declared_extras() -> None:
    assert {gated.extra for gated in GATED_CALLS} <= optional_dependencies().keys()
    assert {module for gated in GATED_CALLS for module in gated.modules} == set(OPTIONAL_MODULES)


def test_core_dependencies_hold_no_optional_package() -> None:
    core = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    optional = {_package(spec) for specs in optional_dependencies().values() for spec in specs}
    assert optional
    assert not [spec for spec in core if _package(spec) in optional]
