#!/usr/bin/env python3

"""
test_helpers.py --- checks the shared test helpers that guard the suite itself

Contains:
    test_regression_hang_timeout_never_interrupts_coverage_code(): defers the alarm past coverage
    test_regression_hang_timeout_survives_a_swallowed_alarm(): keeps alarming after a lost one
    test_hang_timeout_lets_a_quick_test_finish(): returns normally and restores the old handler
"""

import signal
import sys
import time
import weakref

import pytest

from tests.helpers import fails_after

DEADLINE_S = 0.05
COVERAGE_FRAME_S = 0.3
HANG_BOUND_S = 5.0
COVERAGE_MODULE = "coverage.collector"
FINALIZER_SPIN_S = 0.002
SPIN_IN_COVERAGE = (
    "def spin(seconds, finished):\n"
    "    end = time.monotonic() + seconds\n"
    "    while time.monotonic() < end:\n"
    "        pass\n"
    "    finished.append(True)\n"
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is POSIX only")


@posix_only
def test_regression_hang_timeout_never_interrupts_coverage_code() -> None:
    """Defers the alarm while coverage code runs, then fails the hang (CI deadlock 2026-11-14).

    An alarm raising inside coverage's tracer callback left its data lock held, and the next
    traced file deadlocked the run on Python 3.12/3.13.
    """
    namespace: dict[str, object] = {"__name__": COVERAGE_MODULE, "time": time}
    exec(SPIN_IN_COVERAGE, namespace)
    spin_in_coverage = namespace["spin"]
    assert callable(spin_in_coverage)
    finished: list[bool] = []

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        spin_in_coverage(COVERAGE_FRAME_S, finished)
        end = time.monotonic() + HANG_BOUND_S
        while time.monotonic() < end:
            pass

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()
    assert finished == [True]


class _Garbage:
    """Stands for any object whose finalizer runs Python code mid-loop."""


def _spin(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


@posix_only
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_regression_hang_timeout_survives_a_swallowed_alarm() -> None:
    """Fails a hang whose alarm lands in a finalizer that swallows it (CI hang 2026-11-15).

    An exception raised inside a finalizer or GC callback is reported as unraisable and dropped,
    so a one-shot alarm there let the next-link cycle test loop until the runner ran out of
    memory. The alarm now repeats until the test frame unwinds.
    """

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        end = time.monotonic() + HANG_BOUND_S
        while time.monotonic() < end:
            weakref.finalize(_Garbage(), _spin, FINALIZER_SPIN_S)
            _spin(FINALIZER_SPIN_S)

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()


@posix_only
def test_hang_timeout_lets_a_quick_test_finish() -> None:
    """Returns normally inside the deadline, disarms the timer and restores the old handler."""
    previous = signal.getsignal(signal.SIGALRM)
    calls: list[int] = []

    @fails_after(HANG_BOUND_S)
    def quick_test(value: int) -> None:
        calls.append(value)

    quick_test(1)
    assert calls == [1]
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == previous
