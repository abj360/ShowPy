"""
conformance.py --- checks that a repository backend keeps the storage contract

Contains:
    CHECK_NAMES: names every conformance check, in the order check_backend runs them
    check_backend(): runs every conformance check on fresh backends and reports which passed
"""

from collections.abc import Callable

from e8s.show.backend import RepositoryBackend
from e8s.show.conformance_checks import (
    blob_reads_are_verified,
    errors_are_storage_errors,
    failed_commit_changes_nothing,
    head_matches_log,
    ids_are_never_trusted,
    log_since_is_ordered,
    long_names,
    one_winner_per_parent,
    read_your_writes,
    unicode_and_case_paths,
)
from e8s.show.conformance_steps import BrokenRuleError
from e8s.show.storage_models import ConformanceReport

type Check = Callable[[RepositoryBackend], None]

_CHECKS: tuple[tuple[str, Check], ...] = (
    ("one_winner_per_parent", one_winner_per_parent),
    ("read_your_writes", read_your_writes),
    ("failed_commit_changes_nothing", failed_commit_changes_nothing),
    ("log_since_is_ordered", log_since_is_ordered),
    ("head_matches_log", head_matches_log),
    ("blob_reads_are_verified", blob_reads_are_verified),
    ("ids_are_never_trusted", ids_are_never_trusted),
    ("errors_are_storage_errors", errors_are_storage_errors),
    ("unicode_and_case_paths", unicode_and_case_paths),
    ("long_names", long_names),
)
CHECK_NAMES = tuple(name for name, _ in _CHECKS)


def _failure(error: Exception) -> str:
    if isinstance(error, BrokenRuleError):
        return str(error)
    return f"raised {type(error).__name__}: {error}"


def check_backend(make_backend: Callable[[], RepositoryBackend]) -> ConformanceReport:
    """Runs every conformance check, each on a fresh backend, and reports which passed.

    A check that finds a broken rule, or that raises anything, fails with the reason; the other
    checks still run. Each check writes shows `show_1`, `show_2` and a few more with odd names,
    so `make_backend` must return an empty backend each time it is called.

    Args:
        make_backend: Returns a new, empty backend; called once per check.

    Returns:
        report: The names of the checks that passed, and each failed check with its reason.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> report = check_backend(lambda: InMemoryRepository().backend)
    >>> report.failed, len(report.passed)
    ((), 10)
    """
    passed: list[str] = []
    failed: list[tuple[str, str]] = []
    for name, check in _CHECKS:
        try:
            check(make_backend())
        # A third-party backend may raise anything; every exception fails only its own check.
        except Exception as error:
            failed.append((name, _failure(error)))
        else:
            passed.append(name)
    return ConformanceReport(passed=tuple(passed), failed=tuple(failed))
