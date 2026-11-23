#!/usr/bin/env python3

"""
test_ledger.py --- checks the defect marker and its enforcement in tests/ledger_plugin.py

Contains:
    test_defect_marker_requires_strict_xfail: rejects a defect test without a strict xfail
    test_defect_marker_requires_matching_reason: rejects an xfail reason that differs from the ID
    test_defect_marker_rejects_bad_id: rejects an ID that does not match UP-<section>-<nn>
    test_defect_marker_accepts_strict_xfail_with_matching_reason: accepts a valid ledger test
    test_defect_marker_accepts_three_digit_id: accepts an ID numbered past 99
    test_defect_marker_accepts_evaluation_section: accepts the E section and rejects other letters
    test_defect_marker_rejects_non_strict_xfail: rejects xfail without strict=True
    test_defect_marker_accepts_parametrized_marks: accepts defect marks inside pytest.param
    test_ledger_counts_only_selected_tests: leaves deselected defect tests out of the count
    test_regression_ledger_counts_open_defects_under_xdist: counts the workers' selection under -n
    run_split_half(): runs one half of the slow session's timing split over fixed defect tests
    test_ledger_counts_each_half_of_the_timing_split: prints each half's own open count
    test_fixed_defect_fails_the_timing_half: a passing strict xfail fails the half
    test_ledger_prints_zero_open_without_defect_tests: prints Ledger: 0 open when none exist
    find_open_priority_zero_tests(): lists priority 0 tests in a source that carry a defect or xfail
        mark
    test_no_priority_zero_defect_remains: guards that no priority 0 test is an open defect
    test_priority_zero_scan_reports_a_marked_test: checks the scanner flags each marking style
    test_regression_priority_zero_scan_reports_a_bare_xfail: checks the scanner flags a bare xfail
    test_priority_zero_scan_ignores_unmarked_tests: checks the scanner passes clean tests
    PHASE_TWO_FIXES: maps each Phase 2 task to the ledger IDs it fixed
    find_defect_ids(): lists the ID of each defect mark in a test source
    is_phase_two_defect(): tells whether a ledger ID belongs to Phase 2
    test_no_phase_two_defect_remains: guards that no Phase 2 ledger ID is still an open defect
    test_defect_id_scan_reports_every_marking_style: checks the scanner sees decorators,
        params and pytestmark
    test_phase_two_check_covers_sections_and_the_table: checks which IDs count as Phase 2
"""

import ast
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import pytest

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = PROJECT_ROOT / "tests"
PRIORITY_ZERO_CITATION = re.compile(r"P0 #(\d+)")
PRIORITY_ZERO_ITEMS = frozenset(range(1, 14))
ITEMS_RESOLVED_BY_DECISION = frozenset({5})  # permissions moved to the host app
MIN_PRIORITY_ZERO_TESTS = 50
OPEN_MARKS = frozenset({"defect", "xfail"})
PLUGIN_ARGS = ("-p", "tests.ledger_plugin", "-p", "no:cacheprovider")
MIN_DEFECT_MARKS = 50
DEFECT_MARK = "defect"
PHASE_TWO_SECTIONS = ("UP-4.", "UP-9.3-")
# The Phase 2 plan's ledger coverage table, less the IDs of its dropped tasks E2 and F2 and
# UP-9.4-45 (all retired with the code they test), plus UP-E-03, which C8 fixed. UP-E-01 is
# absent: its fix was deferred to a later release.
PHASE_TWO_FIXES: Final[Mapping[str, str]] = {
    "L0.2": "UP-4.2-01 UP-9.7-59",
    "L0.5": "UP-9.3-31 UP-9.3-32",
    "L0.6": (
        "UP-9.3-13 UP-9.3-14 UP-9.3-15 UP-9.3-16 UP-9.3-19 UP-9.3-20 UP-9.3-21 UP-9.3-36 "
        "UP-9.3-38 UP-9.3-39 UP-9.3-40 UP-9.3-41 UP-9.3-43 UP-9.3-44 UP-9.3-45 UP-9.3-46 "
        "UP-9.3-47 UP-9.3-48 UP-9.3-49 UP-9.3-50 UP-9.3-51 UP-9.3-52"
    ),
    "L0.9": "UP-9.7-29 UP-9.7-92",
    "A1": "UP-9.7-56 UP-9.7-57 UP-9.7-58 UP-9.7-64",
    "A2": "UP-9.7-75 UP-9.7-76 UP-9.7-77 UP-9.7-78 UP-9.7-81 UP-9.7-82 UP-9.7-88 UP-9.7-89",
    "B1": "UP-9.2-09 UP-9.2-10 UP-9.2-11 UP-9.2-14 UP-9.2-15",
    "B2": "UP-9.2-24 UP-9.2-28 UP-9.2-35 UP-9.2-36 UP-9.6-26 UP-9.6-34 UP-9.6-38",
    "C1": (
        "UP-9.1-14 UP-9.1-53 UP-9.1-54 UP-9.1-55 UP-9.1-56 UP-9.1-60 UP-9.1-62 UP-9.1-63 "
        "UP-9.1-68 UP-9.1-69 UP-9.1-70 UP-9.1-71 UP-9.1-73"
    ),
    "C2": (
        "UP-9.1-20 UP-9.1-21 UP-9.1-22 UP-9.1-23 UP-9.1-24 UP-9.1-25 UP-9.1-64 UP-9.1-65 UP-9.1-66"
    ),
    "C3": "UP-9.1-44 UP-9.1-45 UP-9.1-46 UP-9.1-48 UP-9.1-49",
    "D1": (
        "UP-9.3-56 UP-9.3-57 UP-9.3-58 UP-9.3-59 UP-9.3-60 UP-9.3-61 UP-9.3-63 UP-9.3-64 "
        "UP-9.3-65 UP-9.3-66 UP-9.3-67 UP-9.3-68 UP-9.3-69 UP-9.3-70 UP-9.3-71"
    ),
    "D2": "UP-9.3-82 UP-9.3-84 UP-9.3-85 UP-9.3-86 UP-9.3-87",
    "D3": "UP-9.3-122 UP-9.3-123 UP-9.3-124 UP-9.3-125",
    "S1": "UP-9.3-34 UP-9.3-35",
    "S2": "UP-9.3-83",
    "S3": "UP-9.3-24 UP-9.3-25 UP-9.3-27 UP-9.3-28",
    "S4": "UP-9.3-76",
    "T1": (
        "UP-9.3-95 UP-9.3-96 UP-9.3-97 UP-9.3-105 UP-9.3-106 UP-9.3-107 UP-9.3-108 UP-9.3-114 "
        "UP-9.3-116 UP-9.3-117 UP-9.3-118 UP-9.3-119 UP-9.3-120 UP-9.3-121"
    ),
    "T2": "UP-9.3-109 UP-9.3-111 UP-9.3-112 UP-9.3-113",
    "T3": "UP-9.3-98",
    "F1": "UP-9.4-08 UP-9.4-09 UP-9.4-10 UP-9.4-11 UP-9.4-12 UP-9.4-13 UP-9.4-14 UP-9.4-44",
    "G1": (
        "UP-9.6-45 UP-9.6-46 UP-9.6-47 UP-9.6-48 UP-9.6-49 UP-9.6-51 UP-9.6-52 UP-9.6-53 "
        "UP-9.6-54 UP-9.6-58 UP-9.6-60"
    ),
    "G2": "UP-9.6-11 UP-9.6-12 UP-9.6-13 UP-9.6-14 UP-9.6-62",
    "G3": "UP-9.6-19 UP-9.6-20 UP-9.6-21 UP-9.6-39 UP-9.6-40 UP-9.6-41 UP-9.7-54",
    "G4": "UP-9.6-25 UP-9.6-31 UP-9.6-37 UP-9.6-50 UP-9.6-55 UP-9.6-56 UP-9.6-57 UP-9.6-61",
    "E1": "UP-9.5-12 UP-9.5-14 UP-9.5-29 UP-9.5-30 UP-9.5-34 UP-9.5-43 UP-9.5-44",
    "C8": "UP-E-03",
}
PHASE_TWO_IDS = frozenset(i for ids in PHASE_TWO_FIXES.values() for i in ids.split())


def run_ledger_case(pytester: pytest.Pytester, source: str, *args: str) -> pytest.RunResult:
    """Runs a generated test file with the ledger plugin loaded."""
    pytester.syspathinsert(PROJECT_ROOT)
    pytester.makeini("[pytest]\nmarkers =\n    slow: slow test\n")
    pytester.makepyfile(source)
    return pytester.runpytest_inprocess(*PLUGIN_ARGS, *args)


def test_defect_marker_requires_strict_xfail(pytester: pytest.Pytester) -> None:
    """Rejects a defect test that has no xfail."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-9.1-01")
        def test_regression_missing_xfail() -> None:
            assert False
        """,
    )
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*test_regression_missing_xfail*xfail(strict=True)*"])


def test_defect_marker_requires_matching_reason(pytester: pytest.Pytester) -> None:
    """Rejects a defect test whose xfail reason differs from its ID."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-9.1-01")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-02")
        def test_regression_wrong_reason() -> None:
            assert False
        """,
    )
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*test_regression_wrong_reason*reason*UP-9.1-01*"])


def test_defect_marker_rejects_bad_id(pytester: pytest.Pytester) -> None:
    """Rejects a defect ID that does not match UP-<section>-<nn>."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("BUG-1")
        @pytest.mark.xfail(strict=True, reason="BUG-1")
        def test_regression_bad_id() -> None:
            assert False
        """,
    )
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*test_regression_bad_id*BUG-1*UP-*"])


def test_defect_marker_accepts_strict_xfail_with_matching_reason(
    pytester: pytest.Pytester,
) -> None:
    """Accepts a defect test with a strict xfail on its ID."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-9.1-01")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-01")
        def test_regression_open_defect() -> None:
            assert False
        """,
    )
    assert result.ret == 0
    result.assert_outcomes(xfailed=1)
    result.stdout.fnmatch_lines(["Ledger: 1 open"])


def test_defect_marker_accepts_three_digit_id(pytester: pytest.Pytester) -> None:
    """Accepts a defect ID whose number has three digits."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-1.1-100")
        @pytest.mark.xfail(strict=True, reason="UP-1.1-100")
        def test_regression_three_digit_id() -> None:
            assert False
        """,
    )
    assert result.ret == 0
    result.assert_outcomes(xfailed=1)
    result.stdout.fnmatch_lines(["Ledger: 1 open"])


def test_defect_marker_accepts_evaluation_section(pytester: pytest.Pytester) -> None:
    """Accepts section E for evaluation findings and rejects any other letter section."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-E-01")
        @pytest.mark.xfail(strict=True, reason="UP-E-01")
        def test_regression_evaluation_defect() -> None:
            assert False
        """,
    )
    assert result.ret == 0
    result.assert_outcomes(xfailed=1)
    for bad_id in ("UP-X-01", "UP-e-01"):
        rejected = run_ledger_case(
            pytester,
            f"""
            import pytest

            @pytest.mark.defect("{bad_id}")
            @pytest.mark.xfail(strict=True, reason="{bad_id}")
            def test_regression_bad_section_{bad_id[3].lower()}() -> None:
                assert False
            """,
        )
        assert rejected.ret != 0
        rejected.stderr.fnmatch_lines([f"*{bad_id}*UP-*"])


def test_defect_marker_rejects_non_strict_xfail(pytester: pytest.Pytester) -> None:
    """Rejects a defect test whose xfail lacks strict=True."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-9.1-01")
        @pytest.mark.xfail(reason="UP-9.1-01")
        def test_regression_lenient_xfail() -> None:
            assert False
        """,
    )
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*test_regression_lenient_xfail*xfail(strict=True)*"])


def test_defect_marker_accepts_parametrized_marks(pytester: pytest.Pytester) -> None:
    """Accepts defect and strict xfail marks attached through pytest.param."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        OPEN = [pytest.mark.defect("UP-9.1-01"), pytest.mark.xfail(strict=True, reason="UP-9.1-01")]

        @pytest.mark.parametrize("value", [pytest.param(1, marks=OPEN), 2])
        def test_regression_param(value: int) -> None:
            assert value == 2
        """,
    )
    assert result.ret == 0
    result.assert_outcomes(passed=1, xfailed=1)
    result.stdout.fnmatch_lines(["Ledger: 1 open"])


def test_ledger_counts_only_selected_tests(pytester: pytest.Pytester) -> None:
    """Leaves defect tests deselected by -m out of the open count."""
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.slow
        @pytest.mark.defect("UP-9.1-01")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-01")
        def test_regression_slow_defect() -> None:
            assert False
        """,
        "-m",
        "not slow",
    )
    result.stdout.fnmatch_lines(["Ledger: 0 open"])


def test_regression_ledger_counts_open_defects_under_xdist(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prints the workers' open count, not the controller's empty collection, under -n."""
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    result = run_ledger_case(
        pytester,
        """
        import pytest

        @pytest.mark.defect("UP-9.1-01")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-01")
        def test_regression_first_defect() -> None:
            assert False

        @pytest.mark.defect("UP-9.1-02")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-02")
        def test_regression_second_defect() -> None:
            assert False

        @pytest.mark.slow
        @pytest.mark.defect("UP-9.1-03")
        @pytest.mark.xfail(strict=True, reason="UP-9.1-03")
        def test_regression_deselected_defect() -> None:
            assert False
        """,
        "-n",
        "2",
        "-m",
        "not slow",
    )
    assert result.ret == 0
    result.stdout.fnmatch_lines(["Ledger: 2 open"])


SPLIT_RUN_INI = "[pytest]\nmarkers =\n    slow: slow test\n    timing: timing test\n"
SPLIT_RUN_SOURCE = """
import pytest

@pytest.mark.slow
@pytest.mark.defect("UP-9.1-01")
@pytest.mark.xfail(strict=True, reason="UP-9.1-01")
def test_regression_parallel_defect() -> None:
    assert False

@pytest.mark.slow
@pytest.mark.defect("UP-9.1-02")
@pytest.mark.xfail(strict=True, reason="UP-9.1-02")
def test_regression_second_parallel_defect() -> None:
    assert False

@pytest.mark.slow
@pytest.mark.timing
@pytest.mark.defect("UP-9.1-03")
@pytest.mark.xfail(strict=True, reason="UP-9.1-03")
def test_regression_timing_defect() -> None:
    assert {timing_outcome}
"""


def run_split_half(pytester: pytest.Pytester, timing_outcome: str, *args: str) -> pytest.RunResult:
    """Runs one half of the slow session's timing split over a fixed set of defect tests.

    Args:
        pytester: The pytester fixture holding the generated project.
        timing_outcome: Python expression the timing defect test asserts.
        args: The half's marker expression and worker flags.

    Returns:
        result: The pytest run of that half.
    """
    pytester.syspathinsert(PROJECT_ROOT)
    pytester.makeini(SPLIT_RUN_INI)
    pytester.makepyfile(SPLIT_RUN_SOURCE.format(timing_outcome=timing_outcome))
    return pytester.runpytest_inprocess(*PLUGIN_ARGS, *args)


def test_ledger_counts_each_half_of_the_timing_split(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prints each half's own open count: 2 on workers, 1 in the timing half without workers."""
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    parallel = run_split_half(pytester, "False", "-m", "slow and not timing", "-n", "2")
    assert parallel.ret == 0
    parallel.stdout.fnmatch_lines(["Ledger: 2 open"])
    timing = run_split_half(pytester, "False", "-m", "slow and timing", "-n", "0")
    assert timing.ret == 0
    timing.assert_outcomes(xfailed=1)
    timing.stdout.fnmatch_lines(["Ledger: 1 open"])


def test_fixed_defect_fails_the_timing_half(pytester: pytest.Pytester) -> None:
    """Fails the run without workers when a timing defect test passes, as strict xfail requires."""
    result = run_split_half(pytester, "True", "-m", "slow and timing", "-n", "0")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["Ledger: 1 open"])


def test_ledger_prints_zero_open_without_defect_tests(pytester: pytest.Pytester) -> None:
    """Prints Ledger: 0 open when no test carries the defect marker."""
    result = run_ledger_case(pytester, "def test_plain() -> None:\n    assert True\n")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["Ledger: 0 open"])


def _mentions_open_mark(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in OPEN_MARKS)
        or (isinstance(n, ast.Attribute) and n.attr in OPEN_MARKS)
        for n in ast.walk(node)
    )


def _is_module_open_mark(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
    return "pytestmark" in names and _mentions_open_mark(node.value)


def _collect_priority_zero(
    body: list[ast.stmt], is_marked: bool, found: list[tuple[str, frozenset[int], bool]]
) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            marked = is_marked or any(_mentions_open_mark(d) for d in node.decorator_list)
            _collect_priority_zero(node.body, marked, found)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            items = frozenset(
                int(m) for m in PRIORITY_ZERO_CITATION.findall(ast.get_docstring(node) or "")
            )
            if items:
                marked = is_marked or any(_mentions_open_mark(d) for d in node.decorator_list)
                found.append((node.name, items, marked))


def scan_priority_zero(source: str) -> list[tuple[str, frozenset[int], bool]]:
    """Returns (test name, cited items, is defect- or xfail-marked) for each test citing P0 #."""
    tree = ast.parse(source)
    module_marked = any(_is_module_open_mark(n) for n in tree.body)
    found: list[tuple[str, frozenset[int], bool]] = []
    _collect_priority_zero(tree.body, module_marked, found)
    return found


def find_open_priority_zero_tests(source: str) -> list[str]:
    """Lists the priority 0 tests in a source that carry a defect or xfail marker."""
    return [name for name, _, marked in scan_priority_zero(source) if marked]


def test_no_priority_zero_defect_remains() -> None:
    """Guards that no test citing a priority 0 item is still an open defect."""
    cited: set[int] = set()
    count = 0
    open_tests: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        for name, items, marked in scan_priority_zero(path.read_text(encoding="utf-8")):
            count += 1
            cited |= items
            if marked:
                open_tests.append(f"{path.relative_to(PROJECT_ROOT)}::{name}")
    assert count >= MIN_PRIORITY_ZERO_TESTS
    assert cited >= PRIORITY_ZERO_ITEMS - ITEMS_RESOLVED_BY_DECISION
    assert open_tests == []


def test_priority_zero_scan_reports_a_marked_test() -> None:
    """Flags a priority 0 test marked by decorator, class decorator or module pytestmark."""
    docstring = '''    """Does it (P0 #4)."""\n'''
    by_decorator = f'@defect("UP-9.7-01")\ndef test_a() -> None:\n{docstring}    pass\n'
    by_class = (
        f'@pytest.mark.defect("UP-9.7-01")\nclass TestA:\n'
        f"    def test_b(self) -> None:\n    {docstring}        pass\n"
    )
    by_module = (
        f'pytestmark = pytest.mark.defect("UP-9.7-01")\n'
        f"def test_c() -> None:\n{docstring}    pass\n"
    )
    assert find_open_priority_zero_tests(by_decorator) == ["test_a"]
    assert find_open_priority_zero_tests(by_class) == ["test_b"]
    assert find_open_priority_zero_tests(by_module) == ["test_c"]


def test_regression_priority_zero_scan_reports_a_bare_xfail() -> None:
    """Flags a priority 0 test marked only by a strict xfail, by attribute or by name."""
    docstring = '''    """Does it (P0 #4)."""\n'''
    by_attribute = f"@pytest.mark.xfail(strict=True)\ndef test_a() -> None:\n{docstring}    pass\n"
    by_name = f"@xfail(strict=True)\ndef test_b() -> None:\n{docstring}    pass\n"
    by_module = f"pytestmark = pytest.mark.xfail\ndef test_c() -> None:\n{docstring}    pass\n"
    assert find_open_priority_zero_tests(by_attribute) == ["test_a"]
    assert find_open_priority_zero_tests(by_name) == ["test_b"]
    assert find_open_priority_zero_tests(by_module) == ["test_c"]


def test_priority_zero_scan_ignores_unmarked_tests() -> None:
    """Passes a priority 0 test with no defect marker."""
    source = '''def test_a() -> None:\n    """Does it (P0 #4)."""\n    pass\n'''
    assert find_open_priority_zero_tests(source) == []


def _is_defect_mark(func: ast.expr) -> bool:
    return (isinstance(func, ast.Name) and func.id == DEFECT_MARK) or (
        isinstance(func, ast.Attribute) and func.attr == DEFECT_MARK
    )


def find_defect_ids(source: str) -> list[str]:
    """Lists the ID of each defect mark in a test source, in source order.

    A mark is a call of `defect` or `pytest.mark.defect` whose first argument is a string, so
    decorators, `pytest.param(..., marks=...)` and module `pytestmark` are all seen.
    """
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call)]
    return [
        call.args[0].value
        for call in sorted(calls, key=lambda c: (c.lineno, c.col_offset))
        if _is_defect_mark(call.func)
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ]


def is_phase_two_defect(defect_id: str) -> bool:
    """Tells whether a ledger ID is one Phase 2 was planned to fix."""
    return defect_id.startswith(PHASE_TWO_SECTIONS) or defect_id in PHASE_TWO_IDS


def test_no_phase_two_defect_remains() -> None:
    """Guards that no ledger ID Phase 2 fixed, or any UP-4 or UP-9.3 ID, is still an open defect."""
    marks: list[tuple[str, str]] = [
        (str(path.relative_to(PROJECT_ROOT)), defect_id)
        for path in sorted(TESTS_ROOT.rglob("*.py"))
        for defect_id in find_defect_ids(path.read_text(encoding="utf-8"))
    ]
    assert len(marks) >= MIN_DEFECT_MARKS
    assert [f"{path}: {i}" for path, i in marks if is_phase_two_defect(i)] == []


def test_defect_id_scan_reports_every_marking_style() -> None:
    """Sees a helper decorator, a pytest mark, a parametrized mark and a module pytestmark."""
    source = (
        'pytestmark = pytest.mark.defect("UP-9.3-13")\n'
        '@defect("UP-9.7-59")\n'
        "def test_a() -> None:\n"
        "    pass\n"
        '@pytest.mark.defect("UP-4.2-01")\n'
        '@pytest.mark.parametrize("n", [pytest.param(1, marks=pytest.mark.defect("UP-E-03"))])\n'
        "def test_b(n: int) -> None:\n"
        "    pass\n"
        "def test_c() -> None:\n"
        '    defect_id = "UP-9.6-11"\n'
        "    pytest.mark.defect(defect_id)\n"
    )
    assert find_defect_ids(source) == ["UP-9.3-13", "UP-9.7-59", "UP-4.2-01", "UP-E-03"]


def test_phase_two_check_covers_sections_and_the_table() -> None:
    """Counts every UP-4 and UP-9.3 ID and the table's IDs, and leaves later phases alone."""
    assert is_phase_two_defect("UP-4.9-99")
    assert is_phase_two_defect("UP-9.3-200")
    assert is_phase_two_defect("UP-9.6-45")
    assert is_phase_two_defect("UP-E-03")
    assert not is_phase_two_defect("UP-E-01")
    assert not is_phase_two_defect("UP-9.5-55")
    assert not is_phase_two_defect("UP-9.4-45")
    assert not is_phase_two_defect("UP-9.1-72")
