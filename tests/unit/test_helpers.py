#!/usr/bin/env python3

"""
test_helpers.py --- checks the shared test helpers that guard the suite itself

Contains:
    test_regression_hang_timeout_never_interrupts_coverage_code(): defers the alarm past coverage
    test_regression_hang_timeout_survives_a_swallowed_alarm(): keeps alarming after a lost one
    test_regression_hang_timeout_never_raises_inside_a_finalizer(): defers the alarm past one
    test_regression_hang_timeout_leaves_no_open_file_behind(): frees what the alarm cut off
    test_regression_hang_timeout_drops_leaks_it_caused(): hides a file freed after the alarm
    test_hang_timeout_still_reports_leaks_before_the_deadline(): forwards a leak by the test
    test_hang_timeout_lets_a_quick_test_finish(): returns normally and restores the old handler
    test_only_showpy_errors_rejects_a_builtin_error(): fails a call that raises KeyError
    test_only_showpy_errors_accepts_a_showpy_error(): lets a ShowPyError and a normal return pass
    test_only_showpy_errors_names_the_input(): puts the sample input in the failure message
    test_only_showpy_errors_fails_a_hang(): fails a call that outlasts the 5 s budget
"""

import signal
import sys
import time
import weakref
from collections.abc import Callable
from pathlib import Path

import pytest

from showpy.core.errors import ShowPyError
from tests.helpers import fails_after, only_showpy_errors

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
def test_regression_hang_timeout_survives_a_swallowed_alarm() -> None:
    """Fails a hang whose alarm lands in a finalizer that swallows it (CI hang 2026-11-15).

    An exception raised inside a finalizer or GC callback is reported as unraisable and dropped,
    so a one-shot alarm there let the next-link cycle test loop until the runner ran out of
    memory. The alarm now repeats until the test frame unwinds, and no tick may leave an
    unraisable exception behind.
    """

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        end = time.monotonic() + HANG_BOUND_S
        while time.monotonic() < end:
            weakref.finalize(_Garbage(), _spin, FINALIZER_SPIN_S)
            _spin(FINALIZER_SPIN_S)

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()


class _SpinsOnDelete:
    """Runs a slow `__del__`, the finalizer shape that does not go through weakref."""

    def __init__(self, finished: list[bool]) -> None:
        self.finished = finished

    def __del__(self) -> None:
        _spin(COVERAGE_FRAME_S)
        self.finished.append(True)


def _finish_after_spin(finished: list[bool]) -> None:
    _spin(COVERAGE_FRAME_S)
    finished.append(True)


def _drop_weakref_finalized(finished: list[bool]) -> None:
    weakref.finalize(_Garbage(), _finish_after_spin, finished)


def _drop_dunder_del(finished: list[bool]) -> None:
    _SpinsOnDelete(finished)


@posix_only
@pytest.mark.parametrize("drop_garbage", [_drop_weakref_finalized, _drop_dunder_del])
def test_regression_hang_timeout_never_raises_inside_a_finalizer(
    drop_garbage: Callable[[list[bool]], None],
) -> None:
    """Defers the alarm while a finalizer runs, then fails the hang (macOS 3.12 CI 2026-11-15).

    A tick raised inside a finalizer was swallowed, the next tick landed in pytest's
    unraisable hook, and the run failed with "Failed to process unraisable exception".
    """
    finished: list[bool] = []

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        drop_garbage(finished)
        end = time.monotonic() + HANG_BOUND_S
        while time.monotonic() < end:
            pass

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()
    assert finished == [True]


@posix_only
def test_regression_hang_timeout_leaves_no_open_file_behind(tmp_path: Path) -> None:
    """Frees a file the alarm cut off before it closed (cycle test teardown error 2026-11-15).

    The alarm can land between opening a file and closing it. The traceback kept the handle
    alive, and its ResourceWarning surfaced later as an unraisable error in teardown.
    """
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        end = time.monotonic() + HANG_BOUND_S
        while time.monotonic() < end:
            handle = source.open("rb")
            _spin(FINALIZER_SPIN_S)
            handle.close()

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()


def _leak_open_file(path: Path) -> None:
    path.open("rb")  # dropped unclosed on purpose


@posix_only
def test_regression_hang_timeout_drops_leaks_it_caused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drops the ResourceWarning of a file freed after the alarm (cycle test teardown 2026-11-15).

    An alarm inside io.open() frees the half-built file at once; its unraisable ResourceWarning
    failed the hang test's teardown.
    """
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "unraisablehook", lambda unraisable: seen.append(unraisable.exc_type))

    @fails_after(DEADLINE_S)
    def hanging_test() -> None:
        try:
            _spin(HANG_BOUND_S)
        finally:
            _leak_open_file(source)

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        hanging_test()
    assert seen == []


@posix_only
def test_hang_timeout_still_reports_leaks_before_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forwards a ResourceWarning the test causes itself, before any alarm fires."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "unraisablehook", lambda unraisable: seen.append(unraisable.exc_type))

    @fails_after(HANG_BOUND_S)
    def leaking_test() -> None:
        _leak_open_file(source)

    leaking_test()
    assert seen == [ResourceWarning]


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


def _raise_key_error() -> None:
    raise KeyError("missing")


def _raise_showpy_error() -> None:
    raise ShowPyError("bad input", hint="send good input")


@posix_only
def test_only_showpy_errors_rejects_a_builtin_error() -> None:
    """Fails the test when the call raises an error that is not a ShowPyError."""
    with pytest.raises(pytest.fail.Exception, match="KeyError"):
        only_showpy_errors(_raise_key_error)


@posix_only
def test_only_showpy_errors_accepts_a_showpy_error() -> None:
    """Returns for a call that raises a ShowPyError or finishes normally."""
    only_showpy_errors(_raise_showpy_error)
    only_showpy_errors(lambda: None)


@posix_only
def test_only_showpy_errors_names_the_input() -> None:
    """Puts the input that broke the call into the failure message."""
    with pytest.raises(pytest.fail.Exception, match="'odd input'"):
        only_showpy_errors(_raise_key_error, sample="odd input")


@posix_only
def test_only_showpy_errors_fails_a_hang() -> None:
    """Fails a call that runs past the 5 s budget instead of letting it hang."""

    def spin() -> None:
        end = time.monotonic() + 2 * HANG_BOUND_S
        while time.monotonic() < end:
            pass

    with pytest.raises(pytest.fail.Exception, match="Timeout"):
        only_showpy_errors(spin)
