#!/usr/bin/env python3

"""
test_files.py --- encodes audited workspace-file and ownership defects as strict xfails

Contains:
    test_regression_commit_refuses_garbage_show_document: UP-9.7-06
    test_regression_commit_refuses_unknown_root: UP-9.7-07
    test_regression_agent_cannot_delete_sets_through_show_json: UP-9.7-08
    test_regression_agent_cannot_empty_cast_through_show_json: UP-9.7-09
    test_regression_agent_cannot_change_show_id_through_show_json: UP-9.7-10
    test_regression_agent_cannot_forge_designer_comments: UP-9.7-11
    test_regression_reading_root_is_a_library_error: UP-9.7-18
    test_regression_invalid_utf8_text_file_is_a_library_error: UP-9.7-25
"""

import json
from datetime import UTC, datetime

import pytest

from showpy.core.errors import LockedError, ShowPyError
from showpy.notes.models import Anchor
from showpy.show.documents import SHOW_DOC, dump_json
from showpy.show.files import COMMENTS_DOC
from showpy.show.models import Comment
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
    ws.commit({path: raw}, at=LATER, message="store")
    ws.read(path)


@pytest.mark.defect("UP-9.7-06")
@pytest.mark.xfail(strict=True, reason="UP-9.7-06")
def test_regression_commit_refuses_garbage_show_document() -> None:
    """Refuses a commit of bytes that are not a show document (audit 9.7 item 4; P0 #4)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        designer.commit({SHOW_DOC: b"garbage"}, at=LATER, message="oops")


@pytest.mark.defect("UP-9.7-07")
@pytest.mark.xfail(strict=True, reason="UP-9.7-07")
def test_regression_commit_refuses_unknown_root() -> None:
    """Refuses a commit under a root no reader can open (audit 9.7 item 4; P0 #4)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        designer.commit({"/mystery/x.txt": b"1"}, at=LATER, message="oops")


@pytest.mark.defect("UP-9.7-08")
@pytest.mark.xfail(strict=True, reason="UP-9.7-08")
def test_regression_agent_cannot_delete_sets_through_show_json() -> None:
    """Refuses an agent rewrite of show.json that drops sets (audit 9.7 item 5; P0 #5)."""
    agent, _ = _session()
    body = _show_json(agent) | {"sets": ["1"]}
    with pytest.raises(LockedError):
        agent.write(SHOW_DOC, dump_json(body), at=LATER)


@pytest.mark.defect("UP-9.7-09")
@pytest.mark.xfail(strict=True, reason="UP-9.7-09")
def test_regression_agent_cannot_empty_cast_through_show_json() -> None:
    """Refuses an agent rewrite of show.json that empties the cast (audit 9.7 item 5; P0 #5)."""
    agent, _ = _session()
    body = _show_json(agent) | {"cast": {"groups": {}, "spots": []}}
    with pytest.raises(LockedError):
        agent.write(SHOW_DOC, dump_json(body), at=LATER)


@pytest.mark.defect("UP-9.7-10")
@pytest.mark.xfail(strict=True, reason="UP-9.7-10")
def test_regression_agent_cannot_change_show_id_through_show_json() -> None:
    """Refuses an agent show.json rewrite that changes the show ID (audit 9.7 item 5; P0 #5)."""
    agent, _ = _session()
    body = _show_json(agent) | {"id": "hijacked"}
    with pytest.raises(LockedError):
        agent.write(SHOW_DOC, dump_json(body), at=LATER)


@pytest.mark.defect("UP-9.7-11")
@pytest.mark.xfail(strict=True, reason="UP-9.7-11")
def test_regression_agent_cannot_forge_designer_comments() -> None:
    """Keeps an agent from writing a designer-authored comment (audit 9.7 item 6; P0 #5)."""
    agent, _ = _session()
    forged = Comment(id="c1", anchor=Anchor.on_set("2"), text="approved", author="designer")
    try:
        agent.write(COMMENTS_DOC, dump_json([forged.model_dump(mode="python")]), at=LATER)
    except LockedError:
        return
    stored = json.loads(agent.read(COMMENTS_DOC))
    assert {c["author"] for c in stored} == {"agent"}


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
