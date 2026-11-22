#!/usr/bin/env python3

"""
test_files.py --- encodes workspace-file defects and write behavior

Contains:
    test_regression_structured_check_refuses_nesting_past_the_cap: refuses a .json file nested
        MAX_NESTING + 1 levels, as the strict parser does
    test_regression_structured_json_refuses_duplicate_keys: refuses a committed JSON file that
        repeats a key
    test_regression_structured_json_refuses_nan: refuses a committed JSON file holding NaN
    test_regression_commit_refuses_garbage_show_document: refuses a commit of bytes that are not a
        show document
    test_regression_commit_refuses_unknown_root: refuses a commit under a root no reader can open
    test_any_valid_write_lands: lands an agent workspace write to the limits file
    test_regression_derived_tables_are_read_only: refuses writes to the derived index and
        placement tables
    test_regression_reading_root_is_a_library_error: raises a ShowPyError, not IndexError, when
        reading the root path
    test_regression_reading_root_names_a_directory: raises StorageError BAD_PATH for the root
    test_regression_invalid_utf8_text_file_is_a_library_error: maps invalid UTF-8 in a text file to
        a ShowPyError
    test_regression_index_csv_neutralizes_formula_cues: quotes every formula-looking cue cell
    test_regression_index_csv_neutralizes_set_ids: quotes a formula-like set ID
    test_regression_placements_csv_neutralizes_formula_notes: quotes every formula-looking note
    test_regression_placements_csv_keeps_negative_numbers_unquoted: keeps a negative x a number
    test_regression_bad_comments_file_is_a_schema_error: raises SchemaError for comments files of
        the wrong shape, invalid JSON or invalid UTF-8
"""

import csv
import io
import json
from datetime import UTC, datetime

import pytest

from showpy.core.canonical import MAX_NESTING
from showpy.core.errors import LockedError, SchemaError, ShowPyError, StorageError
from showpy.show.documents import SHOW_DOC
from showpy.show.files import _index_csv
from showpy.show.models import Show
from showpy.show.storage import Workspace
from showpy.show.structured import decode_comments, require_structured
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


FORMULA_LEADS = ["=", "+", "-", "@", "\t", "\r"]


def _rows(text: str | bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(str(text), newline="")))


def _show_with_cues(cues: list[str]) -> Show:
    show = two_set_show()
    first = show.sets[0].model_copy(update={"cue": cues[0]})
    second = show.sets[1].model_copy(update={"cue": cues[1]})
    return show.model_copy(update={"sets": (first, second)})


@pytest.mark.parametrize("lead", FORMULA_LEADS)
def test_regression_index_csv_neutralizes_formula_cues(lead: str) -> None:
    """Quotes every formula-looking cue cell and leaves a plain negative number alone."""
    cue = f'{lead}HYPERLINK("http://x","click")'
    _, agent, _ = open_session(_show_with_cues([cue, "-12.5"]), AT)
    rows = _rows(agent.read("/show/index.csv"))
    assert rows[1][-1] == f"'{cue}"
    assert rows[2][-1] == "-12.5"


def test_regression_index_csv_neutralizes_set_ids() -> None:
    """Quotes a formula-like set ID in the derived index table."""
    show = two_set_show()
    renamed = show.sets[1].model_copy(update={"id": "=1+2"})
    edited = show.model_copy(update={"sets": (show.sets[0], renamed)})
    rows = _rows(_index_csv(edited).decode())
    assert rows[2][0] == "'=1+2"


@pytest.mark.parametrize("lead", FORMULA_LEADS)
def test_regression_placements_csv_neutralizes_formula_notes(lead: str) -> None:
    """Quotes every formula-looking note cell in a set's placement table."""
    note = f"{lead}cmd|' /C calc'!A0"
    show = two_set_show()
    first = show.sets[0]
    noted = first.with_placements({first.placements[0].entity: {"note": note}})
    show = show.model_copy(update={"sets": (noted, show.sets[1])})
    _, agent, _ = open_session(show, AT)
    rows = _rows(agent.read("/sets/1/placements.csv"))
    note_column = rows[0].index("note")
    assert rows[1][note_column] == f"'{note}"
    assert rows[1][rows[0].index("x")] == "0.0"


def test_regression_placements_csv_keeps_negative_numbers_unquoted() -> None:
    """Leaves a negative x coordinate a plain number while a note beside it is quoted."""
    show = two_set_show()
    first = show.sets[0]
    entity = first.placements[0].entity
    moved = first.with_placements({entity: {"x": -3.5, "note": "=1+1"}})
    show = show.model_copy(update={"sets": (moved, show.sets[1])})
    _, agent, _ = open_session(show, AT)
    rows = _rows(agent.read("/sets/1/placements.csv"))
    assert rows[1][rows[0].index("x")] == "-3.5"
    assert rows[1][rows[0].index("note")] == "'=1+1"


@pytest.mark.parametrize("raw", [b'{"a": 1, "a": 2}', b'{"a": 1, "a": 1}'])
def test_regression_structured_json_refuses_duplicate_keys(raw: bytes) -> None:
    """Refuses a committed JSON file that repeats a key, as the strict parser does."""
    _, designer = _session()
    with pytest.raises(SchemaError, match="repeated"):
        designer.commit({"/exports/x.json": raw}, at=LATER)


@pytest.mark.parametrize("raw", [b'{"a": NaN}', b"[Infinity]", b"[-Infinity]"])
def test_regression_structured_json_refuses_nan(raw: bytes) -> None:
    """Refuses a committed JSON file holding NaN or infinity, as the strict parser does."""
    _, designer = _session()
    with pytest.raises(SchemaError, match="non-standard token"):
        designer.commit({"/exports/x.json": raw}, at=LATER)


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


def test_regression_reading_root_is_a_library_error() -> None:
    """Raises a ShowPyError, not IndexError, when reading the root path (audit 9.7 item 9)."""
    agent, _ = _session()
    with pytest.raises(ShowPyError):
        agent.read("/")


def test_regression_reading_root_names_a_directory() -> None:
    """Raises StorageError BAD_PATH for the root, as writing a directory does."""
    agent, _ = _session()
    with pytest.raises(StorageError) as excinfo:
        agent.read("/")
    assert excinfo.value.code == "BAD_PATH"


def test_regression_invalid_utf8_text_file_is_a_library_error() -> None:
    """Maps invalid UTF-8 in a text file to a ShowPyError (audit 9.7 item 9)."""
    _, designer = _session()
    with pytest.raises(ShowPyError):
        _store_then_read(designer, "/renders/a.txt", INVALID_UTF8)


@pytest.mark.parametrize("raw", [b'{"id": "c1"}', b"[1]", b"{", b"", INVALID_UTF8])
def test_regression_bad_comments_file_is_a_schema_error(raw: bytes) -> None:
    """Raises SchemaError, not pydantic or decode errors, for a malformed comments file."""
    with pytest.raises(SchemaError):
        decode_comments(raw)


def test_decode_comments_reads_a_stored_list() -> None:
    assert decode_comments(b"[]") == []


def test_regression_structured_check_refuses_nesting_past_the_cap() -> None:
    deep = b"[" * (MAX_NESTING + 1) + b"]" * (MAX_NESTING + 1)
    with pytest.raises(SchemaError, match="nests deeper than"):
        require_structured("/scripts/extra.json", deep)
    require_structured("/scripts/extra.json", b"[" * MAX_NESTING + b"]" * MAX_NESTING)
