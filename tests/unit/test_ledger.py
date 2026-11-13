#!/usr/bin/env python3

"""
test_ledger.py --- checks the defect marker and its enforcement in tests/ledger_plugin.py

Contains:
    test_defect_marker_requires_strict_xfail: rejects a defect test without a strict xfail
    test_defect_marker_requires_matching_reason: rejects an xfail reason that differs from the ID
    test_defect_marker_rejects_bad_id: rejects an ID that does not match UP-<section>-<nn>
    test_defect_marker_accepts_strict_xfail_with_matching_reason: accepts a valid ledger test
"""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ARGS = ("-p", "tests.ledger_plugin", "-p", "no:cacheprovider")


def run_ledger_case(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    """Runs a generated test file with the ledger plugin loaded."""
    pytester.syspathinsert(PROJECT_ROOT)
    pytester.makepyfile(source)
    return pytester.runpytest_inprocess(*PLUGIN_ARGS)


def test_defect_marker_requires_strict_xfail(pytester: pytest.Pytester) -> None:
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
