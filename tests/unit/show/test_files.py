#!/usr/bin/env python3

"""
test_files.py --- encodes workspace-file defects and write behavior

Contains:
    test_regression_commit_refuses_garbage_show_document: refuses a commit of bytes that are not a
        show document
    test_regression_commit_refuses_unknown_root: refuses a commit under a root no reader can open
    test_any_valid_write_lands: lands an agent workspace write to the limits file
    test_regression_derived_tables_are_read_only: refuses writes to the derived index and
        placement tables
    test_regression_reading_root_is_a_library_error: raises a ShowPyError, not IndexError, when
        reading the root path
    test_regression_invalid_utf8_text_file_is_a_library_error: maps invalid UTF-8 in a text file to
        a ShowPyError
"""

import json
from datetime import UTC, datetime

import pytest

from showpy.core.errors import LockedError, ShowPyError
from showpy.show.documents import SHOW_DOC
from showpy.show.storage import Workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 11, 12, 5, tzinfo=UTC)
INVALID_UTF8 = b"\xff\xfe\x00"


def _session() -> tuple[Workspace, Workspace]:
    _, agent, designer = open_session(two_set_show(), AT)
    return agent, designer


def _show_json(ws: Workspace) -> dict[str, object]:
    document: dict[str, object] = json.loads(ws.read(SHOW_DOC))
    return document


def _store_then_read(ws: Workspace, path: str, raw: bytes) -> None:
    ws.commit({path: raw}, at=LATER)
    ws.read(path)


def test_regression_commit_refuses_garbage_show_document() -> None:
    """Refuses a commit of bytes that are not a show document (audit 9.7 item 4; P0 #4)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        designer.commit({SHOW_DOC: b"garbage"}, at=LATER)


def test_regression_commit_refuses_unknown_root() -> None:
    """Refuses a commit under a root no reader can open (audit 9.7 item 4; P0 #4)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        designer.commit({"/mystery/x.txt": b"1"}, at=LATER)


def test_any_valid_write_lands() -> None:
    agent, _ = _session()
    revision = agent.write("/config/limits.yaml", "max_speed: 99\n", at=LATER)
    assert agent.revision == revision.id
    assert agent.read("/config/limits.yaml") == "max_speed: 99\n"


@pytest.mark.parametrize("path", ["/show/index.csv", "/sets/1/placements.csv"])
def test_regression_derived_tables_are_read_only(path: str) -> None:
    agent, designer = _session()
    for ws in (agent, designer):
        with pytest.raises(LockedError) as info:
            ws.write(path, "entity\n", at=LATER)
        assert info.value.code == "READ_ONLY"


@pytest.mark.defect("UP-9.7-18")
@pytest.mark.xfail(strict=True, reason="UP-9.7-18")
def test_regression_reading_root_is_a_library_error() -> None:
    """Raises a ShowPyError, not IndexError, when reading the root path (audit 9.7 item 9)."""
    agent, _ = _session()
    with pytest.raises(ShowPyError):
        agent.read("/")


@pytest.mark.defect("UP-9.7-25")
@pytest.mark.xfail(strict=True, reason="UP-9.7-25")
def test_regression_invalid_utf8_text_file_is_a_library_error() -> None:
    """Maps invalid UTF-8 in a text file to a ShowPyError (audit 9.7 item 9)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        _store_then_read(designer, "/renders/a.txt", INVALID_UTF8)
