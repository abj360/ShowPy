#!/usr/bin/env python3

"""
test_stale_temps.py --- tests which temp files a layout-2 commit removes, whatever its time says

Contains:
    test_stale_temp_cutoff_is_the_earlier_time_less_the_age: takes the age from the commit's time
        or the file system's now, whichever is earlier
    test_stale_temp_cutoff_never_overflows: clamps a cut-off before year one, and takes a naive
        time as the caller's local time
    test_regression_a_forged_future_time_does_not_sweep_fresh_temp_files: keeps another writer's
        fresh temp file under a commit dated in year 9999, and still removes a stale one
    test_regression_a_year_one_commit_lands_in_a_file_repository: lands a commit dated within a
        day of the earliest time instead of overflowing
"""

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from e8s.examples import two_set_show
from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.file_repository import FileRepository
from e8s.show.stale_temps import EARLIEST, stale_temp_cutoff

SHOW_ID = "show_1"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)
YEAR_ONE = datetime(1, 1, 1, 12, tzinfo=UTC)
TWO_DAYS_S = 2 * 24 * 3600


def test_stale_temp_cutoff_is_the_earlier_time_less_the_age() -> None:
    earlier = NOW - timedelta(days=3)
    assert stale_temp_cutoff(earlier, NOW.timestamp()) == earlier - STALE_TEMP_AGE
    assert stale_temp_cutoff(FAR_FUTURE, NOW.timestamp()) == NOW - STALE_TEMP_AGE


def test_stale_temp_cutoff_never_overflows() -> None:
    assert stale_temp_cutoff(YEAR_ONE, NOW.timestamp()) == EARLIEST
    assert stale_temp_cutoff(datetime.min.replace(tzinfo=UTC), NOW.timestamp()) == EARLIEST
    naive = datetime(2026, 11, 20, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    expected = naive.astimezone(UTC) - STALE_TEMP_AGE
    assert stale_temp_cutoff(naive, NOW.timestamp()) == expected


def test_regression_a_forged_future_time_does_not_sweep_fresh_temp_files(tmp_path: Path) -> None:
    repo = FileRepository(tmp_path)
    head = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=NOW).id
    fresh, stale = tmp_path / "tmp" / ".tmp-other-writer", tmp_path / "tmp" / ".tmp-crashed"
    fresh.write_bytes(b"partial")
    stale.write_bytes(b"partial")
    then = time.time() - TWO_DAYS_S
    os.utime(stale, (then, then))
    repo.transaction(SHOW_ID, {"/renders/a.txt": b"a"}, expected_revision=head, at=FAR_FUTURE)
    assert fresh.read_bytes() == b"partial"
    assert not stale.exists()
    assert sorted(p.name for p in (tmp_path / "tmp").iterdir()) == [fresh.name]


def test_regression_a_year_one_commit_lands_in_a_file_repository(tmp_path: Path) -> None:
    repo = FileRepository(tmp_path)
    landed = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=YEAR_ONE)
    assert FileRepository(tmp_path).revisions(SHOW_ID) == [landed]
