#!/usr/bin/env python3

"""
test_history_models.py --- tests the revision record's time stamp rule

Contains:
    test_regression_revision_refuses_a_naive_at: refuses a revision time with no timezone, naming
        the fix, and keeps one with any offset
    test_regression_stored_revision_with_a_naive_at_fails_closed: reports a stored revision whose
        time has no timezone as a corrupt file, never as a crash
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.show.backend_checks import stored_revision
from e8s.show.history_models import Revision

AWARE_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
NAIVE_AT = AWARE_AT.replace(tzinfo=None)
REVISION_ID = "a" * 64
SLOT = Path("slot.json")


def test_regression_revision_refuses_a_naive_at() -> None:
    """Refuses a revision time with no timezone and names the fix; any offset is kept."""
    with pytest.raises(SchemaError) as caught:
        Revision(id=REVISION_ID, parent="", at=NAIVE_AT)
    assert "timezone" in caught.value.message
    assert "tzinfo=UTC" in caught.value.hint
    offset = AWARE_AT.astimezone(timezone(timedelta(hours=2)))
    assert Revision(id=REVISION_ID, parent="", at=offset).at == AWARE_AT


def test_regression_stored_revision_with_a_naive_at_fails_closed() -> None:
    """Reports a stored revision whose time has no timezone as a corrupt file, never a crash."""
    stored: dict[str, JsonValue] = {
        "id": REVISION_ID,
        "parent": "",
        "at": NAIVE_AT.isoformat(),
        "changed": [],
    }
    with pytest.raises(StorageError) as caught:
        stored_revision("show_1", SLOT, stored)
    assert caught.value.code == ErrorCode.CORRUPT
    assert isinstance(caught.value.__cause__, SchemaError)
    assert "timezone" in caught.value.__cause__.message
    aware: dict[str, JsonValue] = {**stored, "at": AWARE_AT.isoformat()}
    assert stored_revision("show_1", SLOT, aware).at == AWARE_AT
