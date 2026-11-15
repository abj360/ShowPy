#!/usr/bin/env python3

"""
test_ledger.py --- checks the defect marker and its enforcement in tests/ledger_plugin.py

Contains:
    test_defect_marker_requires_strict_xfail: rejects a defect test without a strict xfail
    test_defect_marker_requires_matching_reason: rejects an xfail reason that differs from the ID
    test_defect_marker_rejects_bad_id: rejects an ID that does not match UP-<section>-<nn>
    test_defect_marker_accepts_strict_xfail_with_matching_reason: accepts a valid ledger test
    test_defect_marker_accepts_three_digit_id: accepts an ID numbered past 99
    test_defect_marker_rejects_non_strict_xfail: rejects xfail without strict=True
    test_defect_marker_accepts_parametrized_marks: accepts defect marks inside pytest.param
    test_ledger_counts_only_selected_tests: leaves deselected defect tests out of the count
    test_ledger_prints_zero_open_without_defect_tests: prints Ledger: 0 open when none exist
"""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def test_ledger_prints_zero_open_without_defect_tests(pytester: pytest.Pytester) -> None:
    """Prints Ledger: 0 open when no test carries the defect marker."""
    result = run_ledger_case(pytester, "def test_plain() -> None:\n    assert True\n")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["Ledger: 0 open"])
