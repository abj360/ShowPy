#!/usr/bin/env python3

"""
test_fuzz_storage.py --- checks the Atheris runner of the storage tests, its session and workflow

Contains:
    FakeAtheris: stands in for atheris, feeding one fixed input and saving a crash like libFuzzer
    RecordingRunner: stands in for the per-process runner, recording calls and failing some
    test_discovery_finds_given_tests_and_state_machines: sorted target IDs of a fake package
    test_discovery_skips_imported_and_plain_names: only names defined in the module count
    test_regression_storage_fuzz_refuses_an_empty_glob: no matching file exits non-zero
    test_regression_storage_fuzz_refuses_a_file_without_a_target: a matching file with no
        target exits non-zero
    test_list_prints_the_ids_as_one_json_line: the target IDs as JSON
    test_regression_runner_leaves_the_active_hypothesis_profile_unchanged: a known profile
        loaded before main is still the active one after it
    test_regression_command_line_loads_the_nightly_profile_before_discovery: the script run as
        __main__ discovers targets under the nightly profile
    test_fuzz_runs_a_target_under_libfuzzer: argv, corpus, artifact prefix and instrumentation
    test_fuzz_passing_target_exits_zero: a clean run returns 0 and leaves no crash
    test_regression_storage_fuzz_crash_exits_non_zero_and_keeps_the_input: a raising target
    test_replay_reproduces_a_raising_input: one saved input run once
    test_replay_of_a_passing_input_exits_zero: the same path stays green
    test_fuzz_refuses_unknown_targets_and_bad_seconds: bad arguments exit non-zero
    test_regression_storage_fuzz_unknown_target_creates_no_folder: a traversal-shaped target is
        refused with an E: line before any folder is made
    test_fuzz_without_atheris_fails_closed: a host without atheris exits with an E: line
    test_regression_storage_fuzz_runs_every_target_after_a_crash: with no --target every target
        runs, each failed one gets an E: line, and the run exits non-zero
    test_fuzz_all_passes_the_slot_and_exits_zero_when_every_target_passes: --seconds reaches
        every target
    test_fuzz_all_refuses_an_empty_glob_and_a_host_without_atheris: nothing runs
    test_regression_storage_fuzz_runs_each_target_in_its_own_process: a crash that ends one
        target's process leaves the later targets running
    test_fuzz_in_own_process_stops_a_target_that_runs_past_its_slot: the grace bound holds
    test_storagefuzz_session_is_on_demand_and_runs_the_nightly_profile: not a default session
    test_atheris_is_a_linux_x86_64_dev_dependency: the marker keeps it off other hosts
    test_regression_storage_fuzz_workflow_runs_every_test_in_one_check_job: one check job runs
        the session with no target after a synced environment, then the report job
    test_storage_fuzz_workflow_caches_one_corpus: restore on every run, save from main
    test_storage_fuzz_workflow_uploads_crashes_on_failure: one artifact for every target
    test_storage_fuzz_workflow_keeps_the_conventions: pins, permissions, no inline expressions,
        a timeout that covers every target's slot and grace
    test_docker_image_holds_every_path_the_storage_fuzz_tests_read: the image copies the workflow
"""

import importlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import settings

from tests.unit.tooling.test_dockerfile import DOCKERFILE, DOCKERIGNORE, missing_image_paths
from tests.unit.tooling.test_nightly_failures import REPORT_CALL, REPORT_JOB, SYNC_STEP, SYNCED
from tests.unit.tooling.test_noxfile import (
    NOXFILE,
    ROOT,
    SAVE_FROM_MAIN,
    ci_job,
    ci_job_names,
    job_steps,
    module_literal,
    session_runs,
    step_value,
    unpinned_actions,
    unprepared_rust_jobs,
    uv_runs,
    workflow_section,
)

SCRIPT = ROOT / "scripts" / "fuzz_storage.py"
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
NIGHTLY_STORAGE = ROOT / ".github" / "workflows" / "nightly-storage.yml"
STORAGE_FUZZ_SESSION = "storagefuzz"
FUZZ_JOB = "storage-fuzz"
FUZZ_RUN = "uv run nox -s storagefuzz"
CORPUS_KEY = "storage-fuzz-corpus-${{ github.run_id }}"
CORPUS_PREFIX = "storage-fuzz-corpus-"
WITH_INDENT = 10
NOT_A_TARGET = "not a fuzz target"
HELPERS = """
from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule


@given(st.binary())
def test_imported_given(data):
    pass


class ImportedMachine(RuleBasedStateMachine):
    @rule()
    def fine(self):
        pass
"""
ALPHA = """
from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule

from fake_storage_helpers import ImportedMachine, test_imported_given

THRESHOLD = 3


@given(st.binary())
def test_roundtrip(data):
    assert isinstance(data, bytes)


def test_plain():
    pass


class SaveMachine(RuleBasedStateMachine):
    @rule()
    def save(self):
        pass


class NotAMachine:
    pass
"""
BETA = """
from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule


@given(st.binary())
def test_crash(data):
    raise RuntimeError("storage invariant broken")


class CrashMachine(RuleBasedStateMachine):
    @rule()
    def boom(self):
        raise RuntimeError("machine invariant broken")
"""
PROBE = """
import json
import os
from pathlib import Path

from hypothesis import given, settings, strategies as st

Path(os.environ["PROFILE_RECORD"]).write_text(
    json.dumps({"no_deadline": settings().deadline is None})
)


@given(st.binary())
def test_probe(data):
    pass
"""
KNOWN_PROFILE = "known-storage-fuzz-test-profile"
KNOWN_EXAMPLES = 17
ALPHA_IDS = ["test_storage_alpha::SaveMachine", "test_storage_alpha::test_roundtrip"]
BETA_IDS = ["test_storage_beta::CrashMachine", "test_storage_beta::test_crash"]
FUZZ_INPUT = b"\x00" * 8
SYNC_BUDGET_SECONDS = 600
CRASH_NAME = "crash-0"
SECONDS = 7
NO_ATHERIS = "E: atheris is not installed"
TRAVERSAL_FOLDER = "storage-fuzz-traversal-probe"
FAKE_ATHERIS_MODULE = """
import os
import time
from contextlib import nullcontext
from pathlib import Path

_STATE = {}


def instrument_imports(include=None, exclude=None):
    return nullcontext()


def Setup(args, test_one_input):
    _STATE["argv"] = list(args)
    _STATE["test"] = test_one_input
    return list(args)


def Fuzz():
    time.sleep(float(os.environ.get("FAKE_FUZZ_SLEEP", "0")))
    try:
        _STATE["test"](b"\\x00" * 8)
    except Exception:
        prefix = next(arg for arg in _STATE["argv"] if arg.startswith("-artifact_prefix="))
        crashes = Path(prefix.split("=", 1)[1])
        crashes.mkdir(parents=True, exist_ok=True)
        (crashes / "crash-0").write_bytes(b"\\x00" * 8)
        raise SystemExit(77) from None
"""
CRASH_EXIT = 77


class FakeAtheris:
    """Stands in for atheris: feeds one fixed input and saves a crash like libFuzzer does.

    Attributes:
        included: The package names given to instrument_imports.
        argv: The libFuzzer arguments given to Setup.
    """

    def __init__(self) -> None:
        self.included: list[str] = []
        self.argv: list[str] = []
        self._test: Callable[[bytes], None] | None = None

    def instrument_imports(
        self, include: Sequence[str] | None = None, exclude: Sequence[str] | None = None
    ) -> AbstractContextManager[None]:
        assert exclude is None
        self.included = list(include or [])
        return _quiet()

    def Setup(self, args: Sequence[str], test_one_input: Callable[[bytes], None]) -> list[str]:
        self.argv = list(args)
        self._test = test_one_input
        return list(args)

    def Fuzz(self) -> None:
        assert self._test is not None
        try:
            self._test(FUZZ_INPUT)
        except Exception:
            prefix = next(arg for arg in self.argv if arg.startswith("-artifact_prefix="))
            crashes = Path(prefix.split("=", 1)[1])
            crashes.mkdir(parents=True, exist_ok=True)
            (crashes / CRASH_NAME).write_bytes(FUZZ_INPUT)
            raise SystemExit(77) from None


@contextmanager
def _quiet() -> Iterator[None]:
    yield


@pytest.fixture
def runner() -> Iterator[ModuleType]:
    """Imports the runner script with sys.path and sys.modules restored afterwards."""
    saved_path = list(sys.path)
    saved_modules = set(sys.modules)
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.modules.pop("fuzz_storage", None)
    module = importlib.import_module("fuzz_storage")
    yield module
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_modules:
        del sys.modules[name]


def make_package(root: Path, files: dict[str, str]) -> Path:
    """Writes fake tests/property files under root and the shared helper module."""
    folder = root / "tests" / "property"
    folder.mkdir(parents=True)
    (root / "fake_storage_helpers.py").write_text(HELPERS, encoding="utf-8")
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def package(tmp_path: Path) -> Path:
    return make_package(
        tmp_path,
        {
            "test_storage_alpha.py": ALPHA,
            "test_storage_beta.py": BETA,
            "test_other_gamma.py": "raise RuntimeError('never imported')",
        },
    )


def fuzz(runner: ModuleType, root: Path, *args: str, atheris: FakeAtheris | None = None) -> int:
    """Runs the runner's main with a fake atheris and returns its exit code."""
    fake = atheris or FakeAtheris()
    return int(runner.main(list(args), root=root, load_atheris=lambda: fake))


def exit_code(runner: ModuleType, root: Path, *args: str) -> int:
    """Returns the exit code of a run, whether it returns it or leaves through SystemExit."""
    try:
        return fuzz(runner, root, *args)
    except SystemExit as stopped:
        return stopped.code if isinstance(stopped.code, int) else 1


def test_discovery_finds_given_tests_and_state_machines(runner: ModuleType, package: Path) -> None:
    assert sorted(runner.discover_targets(package)) == sorted(ALPHA_IDS + BETA_IDS)
    assert list(runner.discover_targets(package)) == ALPHA_IDS + BETA_IDS


def test_discovery_skips_imported_and_plain_names(runner: ModuleType, package: Path) -> None:
    ids = list(runner.discover_targets(package))
    assert not any("Imported" in name or "imported" in name for name in ids)
    assert not any(name.endswith(("test_plain", "NotAMachine")) for name in ids)


def test_regression_storage_fuzz_refuses_an_empty_glob(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests" / "property").mkdir(parents=True)
    assert fuzz(runner, tmp_path, "--list") != 0
    assert "E: no storage fuzz targets match tests/property/test_storage_*.py" in (
        capsys.readouterr().err
    )


def test_regression_storage_fuzz_refuses_a_file_without_a_target(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_package(tmp_path, {"test_storage_empty.py": "VALUE = 1\n"})
    assert fuzz(runner, tmp_path, "--list") != 0
    assert capsys.readouterr().err.startswith("E: ")


def test_list_prints_the_ids_as_one_json_line(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fuzz(runner, package, "--list") == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == ALPHA_IDS + BETA_IDS


def test_fuzz_runs_a_target_under_libfuzzer(runner: ModuleType, package: Path) -> None:
    fake = FakeAtheris()
    target = ALPHA_IDS[1]
    assert fuzz(runner, package, "--target", target, "--seconds", str(SECONDS), atheris=fake) == 0
    safe = "test_storage_alpha__test_roundtrip"
    assert fake.included == ["e8s"]
    assert (package / "fuzz-corpus" / safe).is_dir()
    assert str(package / "fuzz-corpus" / safe) in fake.argv
    assert f"-max_total_time={SECONDS}" in fake.argv
    assert f"-artifact_prefix={package / 'fuzz-crashes' / safe}/" in fake.argv
    assert ":" not in safe


def test_regression_runner_leaves_the_active_hypothesis_profile_unchanged(
    runner: ModuleType, package: Path
) -> None:
    original = settings.get_current_profile_name()
    settings.register_profile(KNOWN_PROFILE, derandomize=True, max_examples=KNOWN_EXAMPLES)
    settings.load_profile(KNOWN_PROFILE)
    try:
        assert fuzz(runner, package, "--list") == 0
        assert fuzz(runner, package, "--target", ALPHA_IDS[1]) == 0
        assert settings.get_current_profile_name() == KNOWN_PROFILE
        assert (settings().derandomize, settings().max_examples) == (True, KNOWN_EXAMPLES)
    finally:
        settings.load_profile(original)


def test_regression_command_line_loads_the_nightly_profile_before_discovery(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "fuzz_storage.py"
    script.parent.mkdir()
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    make_package(tmp_path, {"test_storage_probe.py": PROBE})
    record = tmp_path / "profile.json"
    env = {**os.environ, "PROFILE_RECORD": str(record)}
    env.pop("HYPOTHESIS_PROFILE", None)
    done = subprocess.run(
        [sys.executable, "-I", str(script), "--list"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == {"no_deadline": True}
    assert json.loads(done.stdout) == ["test_storage_probe::test_probe"]


def test_fuzz_runs_the_state_machine_target(runner: ModuleType, package: Path) -> None:
    assert fuzz(runner, package, "--target", ALPHA_IDS[0]) == 0


def test_fuzz_default_slot_is_the_named_constant(runner: ModuleType, package: Path) -> None:
    fake = FakeAtheris()
    assert fuzz(runner, package, "--target", ALPHA_IDS[1], atheris=fake) == 0
    assert runner.FUZZ_SECONDS_PER_TEST == 300
    assert "-max_total_time=300" in fake.argv


def test_fuzz_passing_target_exits_zero(runner: ModuleType, package: Path) -> None:
    assert fuzz(runner, package, "--target", ALPHA_IDS[1]) == 0
    assert list((package / "fuzz-crashes").rglob("*")) == [
        package / "fuzz-crashes" / "test_storage_alpha__test_roundtrip"
    ]


@pytest.mark.parametrize("target", BETA_IDS)
def test_regression_storage_fuzz_crash_exits_non_zero_and_keeps_the_input(
    runner: ModuleType, package: Path, target: str
) -> None:
    with pytest.raises(SystemExit) as stopped:
        fuzz(runner, package, "--target", target)
    assert stopped.value.code != 0
    saved = list((package / "fuzz-crashes").rglob(CRASH_NAME))
    assert [path.read_bytes() for path in saved] == [FUZZ_INPUT]


@pytest.mark.parametrize("target", BETA_IDS)
def test_replay_reproduces_a_raising_input(
    runner: ModuleType,
    package: Path,
    tmp_path: Path,
    target: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    crash = tmp_path / "crash-input"
    crash.write_bytes(FUZZ_INPUT)
    assert fuzz(runner, package, "--replay", str(crash), "--target", target) != 0
    assert "E: " in capsys.readouterr().err


def test_replay_of_a_passing_input_exits_zero(
    runner: ModuleType, package: Path, tmp_path: Path
) -> None:
    crash = tmp_path / "input"
    crash.write_bytes(FUZZ_INPUT)
    assert fuzz(runner, package, "--replay", str(crash), "--target", ALPHA_IDS[1]) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--target", "test_storage_alpha::nope"],
        ["--target", ALPHA_IDS[1], "--seconds", "0"],
        ["--target", ALPHA_IDS[1], "--seconds", "-3"],
        ["--replay", "missing-file", "--target", ALPHA_IDS[1]],
        ["--replay", "missing-file"],
        ["--list", "--target", ALPHA_IDS[1]],
    ],
)
def test_fuzz_refuses_unknown_targets_and_bad_seconds(
    runner: ModuleType, package: Path, args: list[str]
) -> None:
    assert exit_code(runner, package, *args) != 0


def test_regression_storage_fuzz_unknown_target_creates_no_folder(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A target such as ../../x named folders outside the root before it was refused."""
    escape = package.parent / TRAVERSAL_FOLDER
    assert fuzz(runner, package, "--target", f"../../{TRAVERSAL_FOLDER}") == 1
    assert capsys.readouterr().err.startswith("E: unknown target ../../")
    assert not escape.exists()
    assert not (package / "fuzz-corpus").exists()
    assert not (package / "fuzz-crashes").exists()


def test_fuzz_without_atheris_fails_closed(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> ModuleType:
        raise ImportError("no atheris")

    code = runner.main(["--target", ALPHA_IDS[1]], root=package, load_atheris=missing)
    assert code != 0
    assert NO_ATHERIS in capsys.readouterr().err


class RecordingRunner:
    """Stands in for the per-process runner, recording each call and failing chosen targets.

    Attributes:
        calls: Each (target ID, seconds) pair it was asked to run, in order.
    """

    def __init__(self, failing: Sequence[str] = ()) -> None:
        self.calls: list[tuple[str, int]] = []
        self._failing = set(failing)

    def __call__(self, target_id: str, seconds: int) -> int:
        self.calls.append((target_id, seconds))
        return CRASH_EXIT if target_id in self._failing else 0


def fuzz_all(runner: ModuleType, root: Path, run: RecordingRunner, *args: str) -> int:
    """Runs the runner's main with no --target, a fake atheris and a recording runner."""
    fake = FakeAtheris()
    return int(runner.main(list(args), root=root, load_atheris=lambda: fake, run_target=run))


def test_regression_storage_fuzz_runs_every_target_after_a_crash(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A per-test matrix could not report through the one nightly check job; one job runs all."""
    run = RecordingRunner(failing=BETA_IDS)
    assert fuzz_all(runner, package, run) != 0
    assert run.calls == [(target, 300) for target in ALPHA_IDS + BETA_IDS]
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("E: ")]
    assert [line.split()[1] for line in errors] == BETA_IDS
    assert all(f"exit {CRASH_EXIT}" in line for line in errors)


def test_fuzz_all_passes_the_slot_and_exits_zero_when_every_target_passes(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = RecordingRunner()
    assert fuzz_all(runner, package, run, "--seconds", str(SECONDS)) == 0
    assert run.calls == [(target, SECONDS) for target in ALPHA_IDS + BETA_IDS]
    assert "E: " not in capsys.readouterr().err


def test_fuzz_all_refuses_an_empty_glob_and_a_host_without_atheris(
    runner: ModuleType, package: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    (empty / "tests" / "property").mkdir(parents=True)
    run = RecordingRunner()
    assert fuzz_all(runner, empty, run) != 0
    assert "E: no storage fuzz targets match" in capsys.readouterr().err

    def missing() -> ModuleType:
        raise ImportError("no atheris")

    assert runner.main([], root=package, load_atheris=missing, run_target=run) != 0
    assert NO_ATHERIS in capsys.readouterr().err
    assert run.calls == []


def copy_runner(root: Path) -> Path:
    """Copies the runner script under root/scripts, so its ROOT is root."""
    script = root / "scripts" / "fuzz_storage.py"
    script.parent.mkdir()
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return script


def fake_atheris_env(root: Path, **extra: str) -> dict[str, str]:
    """Writes a fake atheris module and returns an environment that imports it first."""
    folder = root / "fake_atheris"
    folder.mkdir()
    (folder / "atheris.py").write_text(FAKE_ATHERIS_MODULE, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(folder), **extra}
    env.pop("HYPOTHESIS_PROFILE", None)
    return env


def test_regression_storage_fuzz_runs_each_target_in_its_own_process(tmp_path: Path) -> None:
    """libFuzzer ends the process on a crash, so targets after it ran only in their own process."""
    script = copy_runner(tmp_path)
    make_package(tmp_path, {"test_storage_alpha.py": ALPHA, "test_storage_beta.py": BETA})
    done = subprocess.run(
        [sys.executable, str(script), "--seconds", "1"],
        capture_output=True,
        text=True,
        env=fake_atheris_env(tmp_path),
        cwd=tmp_path,
        check=False,
    )
    assert done.returncode != 0
    errors = [line for line in done.stderr.splitlines() if line.startswith("E: ")]
    assert [line.split()[1] for line in errors] == BETA_IDS, done.stderr
    crashes = sorted(path.parent.name for path in (tmp_path / "fuzz-crashes").rglob(CRASH_NAME))
    assert crashes == [target.replace("::", "__") for target in BETA_IDS]
    corpora = sorted(path.name for path in (tmp_path / "fuzz-corpus").iterdir())
    assert corpora == [target.replace("::", "__") for target in ALPHA_IDS + BETA_IDS]


def test_fuzz_in_own_process_stops_a_target_that_runs_past_its_slot(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = copy_runner(tmp_path)
    make_package(tmp_path, {"test_storage_alpha.py": ALPHA})
    env = fake_atheris_env(tmp_path, FAKE_FUZZ_SLEEP="30")
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    monkeypatch.setenv("FAKE_FUZZ_SLEEP", env["FAKE_FUZZ_SLEEP"])
    code = runner.run_in_own_process(ALPHA_IDS[1], 1, script=script, grace=1)
    assert code != 0
    assert f"E: {ALPHA_IDS[1]} ran past 2 s" in capsys.readouterr().err


def test_storagefuzz_session_is_on_demand_and_runs_the_nightly_profile() -> None:
    source = NOXFILE.read_text(encoding="utf-8")
    defaults = module_literal(source, "nox.options.sessions")
    assert isinstance(defaults, list)
    assert STORAGE_FUZZ_SESSION not in defaults
    assert uv_runs(source, STORAGE_FUZZ_SESSION) == [
        ["python", "scripts/fuzz_storage.py", "*session.posargs"]
    ]
    assert module_literal(source, "STORAGE_FUZZ_SCRIPT") == "scripts/fuzz_storage.py"
    body = source[source.index(f"def {STORAGE_FUZZ_SESSION}(") :]
    body = body[: body.index("\n\n\n")]
    assert 'session.env["HYPOTHESIS_PROFILE"] = FUZZ_PROFILE' in body
    assert 'session.env["PYTHONHASHSEED"] = DEFAULT_HASH_SEED' in body
    assert session_runs(source, STORAGE_FUZZ_SESSION) == []


def test_atheris_is_a_linux_x86_64_dev_dependency() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pin = "\"atheris==3.1.0; sys_platform == 'linux' and platform_machine == 'x86_64'\""
    assert pin in text
    assert text.index(pin) > text.index("[dependency-groups]")


def _nightly() -> str:
    return NIGHTLY_STORAGE.read_text(encoding="utf-8")


def _fuzz_steps() -> list[str]:
    return job_steps(ci_job(_nightly(), FUZZ_JOB))


def _step_index(steps: list[str], text: str) -> int:
    return next(index for index, step in enumerate(steps) if text in step)


def test_regression_storage_fuzz_workflow_runs_every_test_in_one_check_job() -> None:
    """A per-test matrix job could never report a storage fuzz failure through the report job."""
    workflow = _nightly()
    assert ci_job_names(workflow) == [FUZZ_JOB, REPORT_JOB]
    assert "matrix" not in workflow
    job = ci_job(workflow, FUZZ_JOB)
    assert "runs-on: ubuntu-24.04" in job
    steps = job_steps(job)
    fuzzing = [step for step in steps if "nox -s storagefuzz" in step]
    assert [step_value(step, "run") for step in fuzzing] == [FUZZ_RUN]
    assert step_value(fuzzing[0], "if") == SYNCED
    assert steps.index(SYNC_STEP) < steps.index(fuzzing[0])
    assert "--seconds" not in job
    assert "--target" not in job
    assert workflow_section(workflow, "on").count("cron") == 1


def test_storage_fuzz_workflow_caches_one_corpus() -> None:
    steps = _fuzz_steps()
    restore = steps[_step_index(steps, "uses: actions/cache/restore@")]
    assert step_value(restore, "if") == ""
    assert step_value(restore, "path", WITH_INDENT) == "fuzz-corpus/"
    assert step_value(restore, "key", WITH_INDENT) == CORPUS_KEY
    assert step_value(restore, "restore-keys", WITH_INDENT).strip() == CORPUS_PREFIX
    save = steps[_step_index(steps, "uses: actions/cache/save@")]
    assert step_value(save, "if") == f"${{{{ always() && {SAVE_FROM_MAIN} }}}}"
    assert step_value(save, "key", WITH_INDENT) == CORPUS_KEY
    assert step_value(save, "path", WITH_INDENT) == "fuzz-corpus/"
    order = [_step_index(steps, text) for text in ("cache/restore@", "storagefuzz", "cache/save@")]
    assert order == sorted(order)


def test_storage_fuzz_workflow_uploads_crashes_on_failure() -> None:
    steps = _fuzz_steps()
    upload = steps[_step_index(steps, "uses: actions/upload-artifact@")]
    assert step_value(upload, "if") == "${{ failure() }}"
    assert step_value(upload, "name", WITH_INDENT) == "storage-fuzz-crashes"
    assert step_value(upload, "path", WITH_INDENT) == "fuzz-crashes/"
    assert _step_index(steps, "storagefuzz") < steps.index(upload)


def test_storage_fuzz_workflow_keeps_the_conventions(runner: ModuleType) -> None:
    workflow = _nightly()
    assert unpinned_actions(workflow) == [f"uses: {REPORT_CALL}"]
    assert workflow_section(workflow, "permissions") == "permissions:\n  contents: read\n"
    assert workflow.count("uses: actions/checkout@") == workflow.count("persist-credentials: false")
    assert FUZZ_JOB not in unprepared_rust_jobs(workflow)
    job = ci_job(workflow, FUZZ_JOB)
    for run in re.findall(r"run: (.*)", job):
        assert "${{" not in run
    limit = re.search(r"^    timeout-minutes: (\d+)$", job, re.MULTILINE)
    assert limit is not None
    targets = runner.discover_targets(ROOT)
    assert targets
    slot = runner.FUZZ_SECONDS_PER_TEST + runner.TARGET_GRACE_SECONDS
    assert int(limit[1]) * 60 >= SYNC_BUDGET_SECONDS + len(targets) * slot


def test_nightly_job_keeps_its_steps() -> None:
    steps = job_steps(ci_job(NIGHTLY.read_text(encoding="utf-8"), "nightly"))
    runs = [step_value(step, "run") for step in steps]
    assert runs[-4:] == [
        "uv run nox -s tests",
        "uv run nox -s slow",
        "uv run nox -s fuzz",
        "uv run nox -s bench -- --tier L",
    ]


def test_docker_image_holds_every_path_the_storage_fuzz_tests_read() -> None:
    this = Path(__file__)
    missing = missing_image_paths(DOCKERFILE, DOCKERIGNORE, this)
    assert missing == [], f"E: docker/Dockerfile does not copy {missing}"
