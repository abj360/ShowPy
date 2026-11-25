#!/usr/bin/env python3

"""
test_fuzz_storage.py --- checks the Atheris runner of the storage tests, its session and workflow

Contains:
    FakeAtheris: stands in for atheris, feeding one fixed input and saving a crash like libFuzzer
    test_discovery_finds_given_tests_and_state_machines: sorted target IDs of a fake package
    test_discovery_skips_imported_and_plain_names: only names defined in the module count
    test_regression_storage_fuzz_refuses_an_empty_glob: no matching file exits non-zero
    test_regression_storage_fuzz_refuses_a_file_without_a_target: a matching file with no
        target exits non-zero
    test_list_prints_the_ids_as_one_json_line: the matrix input
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
    test_fuzz_without_atheris_fails_closed: a host without atheris exits with an E: line
    test_storagefuzz_session_is_on_demand_and_runs_the_nightly_profile: not a default session
    test_atheris_is_a_linux_x86_64_dev_dependency: the marker keeps it off other hosts
    test_storage_fuzz_workflow_lists_then_fuzzes_each_test: list job feeds the matrix
    test_storage_fuzz_workflow_caches_the_corpus_per_test: restore always, save from main
    test_storage_fuzz_workflow_uploads_crashes_on_failure: per-test artifact name
    test_storage_fuzz_workflow_keeps_the_conventions: pins, permissions, no inline expressions
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
STORAGE_FUZZ_SESSION = "storagefuzz"
LIST_JOB = "storage-fuzz-list"
FUZZ_JOB = "storage-fuzz"
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
        [],
    ],
)
def test_fuzz_refuses_unknown_targets_and_bad_seconds(
    runner: ModuleType, package: Path, args: list[str]
) -> None:
    assert exit_code(runner, package, *args) != 0


def test_fuzz_without_atheris_fails_closed(
    runner: ModuleType, package: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> ModuleType:
        raise ImportError("no atheris")

    code = runner.main(["--target", ALPHA_IDS[1]], root=package, load_atheris=missing)
    assert code != 0
    assert NO_ATHERIS in capsys.readouterr().err


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
    return NIGHTLY.read_text(encoding="utf-8")


def test_storage_fuzz_workflow_lists_then_fuzzes_each_test() -> None:
    workflow = _nightly()
    assert ci_job_names(workflow) == ["nightly", LIST_JOB, FUZZ_JOB]
    listing = ci_job(workflow, LIST_JOB)
    assert "runs-on: ubuntu-24.04" in listing
    assert "run: uv sync --locked" in listing
    assert "uv run nox -s storagefuzz -- --list" in listing
    assert "targets: ${{ steps.list.outputs.targets }}" in listing
    fuzzing = ci_job(workflow, FUZZ_JOB)
    assert "runs-on: ubuntu-24.04" in fuzzing
    assert "needs: storage-fuzz-list" in fuzzing
    assert "fail-fast: false" in fuzzing
    assert "test: ${{ fromJSON(needs.storage-fuzz-list.outputs.targets) }}" in fuzzing
    assert 'run: uv run nox -s storagefuzz -- --target "$TARGET"' in fuzzing
    assert "TARGET: ${{ matrix.test }}" in fuzzing
    assert "--seconds" not in fuzzing
    assert workflow_section(workflow, "on").count("cron") == 1


def test_storage_fuzz_workflow_caches_the_corpus_per_test() -> None:
    steps = job_steps(ci_job(_nightly(), FUZZ_JOB))
    key = "storage-fuzz-corpus-${{ steps.slug.outputs.slug }}-${{ github.run_id }}"
    prefix = "storage-fuzz-corpus-${{ steps.slug.outputs.slug }}-"
    restore = next(step for step in steps if "uses: actions/cache/restore@" in step)
    assert step_value(restore, "path", 10) == "fuzz-corpus/"
    assert step_value(restore, "key", 10) == key
    assert step_value(restore, "restore-keys", 10).strip() == prefix
    save = next(step for step in steps if "uses: actions/cache/save@" in step)
    assert step_value(save, "if") == f"${{{{ always() && {SAVE_FROM_MAIN} }}}}"
    assert step_value(save, "key", 10) == key
    assert step_value(save, "path", 10) == "fuzz-corpus/"
    order = [steps.index(step) for step in (restore, save)]
    assert order[0] < next(i for i, s in enumerate(steps) if "nox -s storagefuzz" in s) < order[1]


def test_storage_fuzz_workflow_uploads_crashes_on_failure() -> None:
    steps = job_steps(ci_job(_nightly(), FUZZ_JOB))
    upload = next(step for step in steps if "uses: actions/upload-artifact@" in step)
    assert step_value(upload, "if") == "${{ failure() }}"
    assert step_value(upload, "name", 10) == "storage-fuzz-crashes-${{ steps.slug.outputs.slug }}"
    assert step_value(upload, "path", 10) == "fuzz-crashes/"
    slug = next(step for step in steps if "id: slug" in step)
    assert "slug=${TARGET//::/__}" in slug
    assert "TARGET: ${{ matrix.test }}" in slug


def test_storage_fuzz_workflow_keeps_the_conventions() -> None:
    workflow = _nightly()
    assert unpinned_actions(workflow) == []
    assert workflow_section(workflow, "permissions") == "permissions:\n  contents: read\n"
    assert workflow.count("uses: actions/checkout@") == workflow.count("persist-credentials: false")
    assert not {LIST_JOB, FUZZ_JOB} & set(unprepared_rust_jobs(workflow))
    for name in (LIST_JOB, FUZZ_JOB):
        job = ci_job(workflow, name)
        assert re.search(r"^    timeout-minutes: \d+$", job, re.MULTILINE)
        for run in re.findall(r"run: (.*)", job):
            assert "${{" not in run
    limit = re.search(r"timeout-minutes: (\d+)", ci_job(workflow, FUZZ_JOB))
    assert limit is not None
    assert int(limit[1]) * 60 > SYNC_BUDGET_SECONDS + 300


def test_nightly_job_keeps_its_steps() -> None:
    steps = job_steps(ci_job(_nightly(), "nightly"))
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
