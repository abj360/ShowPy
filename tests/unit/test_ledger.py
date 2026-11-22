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
    test_ledger_prints_zero_open_without_defect_tests: prints Ledger: 0 open when none exist
    find_open_priority_zero_tests(): lists priority 0 tests in a source that carry a defect or xfail
        mark
    test_no_priority_zero_defect_remains: guards that no priority 0 test is an open defect
    test_priority_zero_scan_reports_a_marked_test: checks the scanner flags each marking style
    test_regression_priority_zero_scan_reports_a_bare_xfail: checks the scanner flags a bare xfail
    test_priority_zero_scan_ignores_unmarked_tests: checks the scanner passes clean tests
"""

import ast
import re
from pathlib import Path

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
