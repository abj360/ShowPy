#!/usr/bin/env python3

"""
test_fuzz_show.py --- fuzzes the show decoders and workspace paths; only ShowPyError may escape

Contains:
    test_fuzz_decode_show_bytes(): decodes known documents replaced by arbitrary bytes
    test_fuzz_decode_show_json_shapes(): decodes known documents replaced by JSON of any shape
    test_fuzz_decode_show_field_shapes(): decodes show.json whose fields hold JSON of any shape
    test_fuzz_decode_show_mutations(): decodes real documents with one nested value replaced
    test_fuzz_decode_show_cached(): decodes mutated documents through a warm decode cache
    test_fuzz_split_path(): splits arbitrary text as a workspace path
    test_fuzz_read(): reads arbitrary text, then display and stored paths after a renumber
    test_fuzz_ls(): lists arbitrary text as a directory path
    test_fuzz_staged_changes_bytes(): stages arbitrary bytes at every known path
    test_fuzz_staged_changes_json_shapes(): stages JSON of any shape at every known path
    test_fuzz_write_paths(): writes arbitrary bytes to arbitrary or known paths
    test_fuzz_commit_paths(): commits arbitrary changes to arbitrary or known paths
    test_fuzz_apply_event_payloads(): applies canvas events carrying JSON payloads of any shape
    test_fuzz_decode_comments(): decodes arbitrary bytes as a comments file
    test_fuzz_commit_then_read(): reads back whatever bytes a commit accepted at a known path
"""

import json
from datetime import UTC, datetime
from typing import get_args

from hypothesis import given
from hypothesis import strategies as st

from showpy.show.collab import apply_event
from showpy.show.decode_cache import DecodeCache
from showpy.show.documents import SHOW_DOC, decode_show, encode_show, split_path
from showpy.show.files import all_files, staged_changes
from showpy.show.integrity import blob_digest
from showpy.show.manage import insert_subset, renumber
from showpy.show.models import CanvasEvent, EventKind, Show
from showpy.show.storage import Workspace
from showpy.show.structured import decode_comments
from tests.fixtures.shows import two_set_show
from tests.helpers import HOSTILE_JSON, only_showpy_errors, open_session

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 11, 12, 5, tzinfo=UTC)
JSON_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=8),
)
JSON_VALUES = st.recursive(
    JSON_LEAVES,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=6), inner, max_size=4)
    ),
    max_leaves=12,
)
RAW_BYTES = st.one_of(st.binary(max_size=64), st.sampled_from(sorted(HOSTILE_JSON.values())))
PATH_PIECES = st.sampled_from(
    [
        *["/", "sets", "show", "transitions", "1", "1A", "2", "3", "renders"],
        *["set.py", "set.json", "forms.json", "paths.json", "placements.csv"],
        *["..", ".", "", "\\", "\x00", "é", "1-2", "2-3"],
    ]
)


def _shows() -> tuple[Show, Show]:
    inserted, _ = insert_subset(two_set_show(), "1", 2.0)
    return two_set_show(), renumber(inserted)[0]


PLAIN, RENUMBERED = _shows()
STORED = encode_show(RENUMBERED)
STORED_PATHS = sorted(STORED)
FREE_PATHS = ["/renders/a.txt", "/exports/x.json", "/config/limits.yaml", "/canvas/selection.json"]
FREE_PATHS += ["/canvas/comments.json", "/publish/claims.json", "/lib/drafts/a.py"]
PATH_TEXT = st.one_of(
    st.text(max_size=40),
    st.lists(PATH_PIECES, max_size=5).map("/".join),
    st.lists(PATH_PIECES, max_size=5).map(lambda parts: "/" + "/".join(parts)),
)


def _session_workspace() -> Workspace:
    _, agent, _ = open_session(RENUMBERED, AT)
    return agent


READ_ONLY = _session_workspace()
KNOWN_PATHS = sorted({*STORED_PATHS, *all_files(RENUMBERED, STORED), *FREE_PATHS})
KNOWN_OR_TEXT = st.one_of(st.sampled_from(KNOWN_PATHS), PATH_TEXT)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value).encode()


@given(st.dictionaries(st.sampled_from(STORED_PATHS), RAW_BYTES, max_size=4))
def test_fuzz_decode_show_bytes(replaced: dict[str, bytes]) -> None:
    only_showpy_errors(lambda: decode_show({**STORED, **replaced}), replaced)


@given(st.dictionaries(st.sampled_from(STORED_PATHS), JSON_VALUES.map(_json_bytes), max_size=4))
def test_fuzz_decode_show_json_shapes(replaced: dict[str, bytes]) -> None:
    only_showpy_errors(lambda: decode_show({**STORED, **replaced}), replaced)


@given(st.dictionaries(st.sampled_from(sorted(json.loads(STORED[SHOW_DOC]))), JSON_VALUES))
def test_fuzz_decode_show_field_shapes(fields: dict[str, object]) -> None:
    body = {**json.loads(STORED[SHOW_DOC]), **fields}
    only_showpy_errors(lambda: decode_show({**STORED, SHOW_DOC: _json_bytes(body)}), fields)


@given(PATH_TEXT)
def test_fuzz_split_path(path: str) -> None:
    only_showpy_errors(lambda: split_path(path), path)


JSON_DOC_PATHS = [path for path in STORED_PATHS if path.endswith(".json")]
MUTATIONS = st.tuples(
    st.sampled_from(JSON_DOC_PATHS), st.lists(st.integers(min_value=0), max_size=8), JSON_VALUES
)


def _replace_nested(document: object, picks: list[int], value: object) -> object:
    """Returns the document with the value at the picked nested position replaced."""
    if not picks or not isinstance(document, dict | list) or not document:
        return value
    if isinstance(document, dict):
        key = sorted(document)[picks[0] % len(document)]
        return {**document, key: _replace_nested(document[key], picks[1:], value)}
    index = picks[0] % len(document)
    return [
        *document[:index],
        _replace_nested(document[index], picks[1:], value),
        *document[index + 1 :],
    ]


def _mutated(path: str, picks: list[int], value: object) -> dict[str, bytes]:
    document = _replace_nested(json.loads(STORED[path]), picks, value)
    return {**STORED, path: _json_bytes(document)}


def _digests(docs: dict[str, bytes]) -> dict[str, str]:
    return {path: blob_digest(raw) for path, raw in docs.items()}


@given(MUTATIONS)
def test_fuzz_decode_show_mutations(mutation: tuple[str, list[int], object]) -> None:
    only_showpy_errors(lambda: decode_show(_mutated(*mutation)), mutation)


@given(MUTATIONS)
def test_fuzz_decode_show_cached(mutation: tuple[str, list[int], object]) -> None:
    cache = DecodeCache()
    decode_show(STORED, cache=cache, digests=_digests(STORED))
    docs = _mutated(*mutation)
    only_showpy_errors(lambda: decode_show(docs, cache=cache, digests=_digests(docs)), mutation)


@given(KNOWN_OR_TEXT)
def test_fuzz_read(path: str) -> None:
    only_showpy_errors(lambda: READ_ONLY.read(path), path)


@given(KNOWN_OR_TEXT)
def test_fuzz_ls(path: str) -> None:
    only_showpy_errors(lambda: READ_ONLY.ls(path), path)


@given(st.sampled_from(KNOWN_PATHS), RAW_BYTES, st.booleans())
def test_fuzz_staged_changes_bytes(path: str, raw: bytes, is_renumbered: bool) -> None:
    show = RENUMBERED if is_renumbered else PLAIN
    stored = STORED if is_renumbered else encode_show(PLAIN)
    only_showpy_errors(lambda: staged_changes(path, raw, show, stored), (path, raw))


@given(st.sampled_from(KNOWN_PATHS), JSON_VALUES.map(_json_bytes))
def test_fuzz_staged_changes_json_shapes(path: str, raw: bytes) -> None:
    only_showpy_errors(lambda: staged_changes(path, raw, RENUMBERED, STORED), (path, raw))


@given(KNOWN_OR_TEXT, st.one_of(RAW_BYTES, JSON_VALUES.map(_json_bytes)))
def test_fuzz_write_paths(path: str, raw: bytes) -> None:
    workspace = _session_workspace()
    only_showpy_errors(lambda: workspace.write(path, raw, at=LATER), (path, raw))


@given(
    st.dictionaries(
        KNOWN_OR_TEXT,
        st.one_of(st.none(), RAW_BYTES, JSON_VALUES.map(_json_bytes)),
        max_size=3,
    )
)
def test_fuzz_commit_paths(changes: dict[str, bytes | None]) -> None:
    workspace = _session_workspace()
    only_showpy_errors(lambda: workspace.commit(changes, at=LATER), changes)


@given(st.sampled_from(KNOWN_PATHS), RAW_BYTES)
def test_fuzz_commit_then_read(path: str, raw: bytes) -> None:
    workspace = _session_workspace()

    def commit_and_read() -> None:
        workspace.commit({path: raw}, at=LATER)
        workspace.read(path)

    only_showpy_errors(commit_and_read, (path, raw))


EVENT_KINDS = st.sampled_from(get_args(EventKind.__value__))
OP_PAYLOADS = st.one_of(
    st.dictionaries(st.text(max_size=6), JSON_VALUES, max_size=4),
    st.fixed_dictionaries({"op": st.sampled_from(["placement", "form", "paths", "counts", "x"])}),
    st.fixed_dictionaries(
        {"op": st.just("counts"), "set_id": JSON_VALUES, "counts": JSON_VALUES},
    ),
)


@given(EVENT_KINDS, OP_PAYLOADS)
def test_fuzz_apply_event_payloads(kind: EventKind, payload: dict[str, object]) -> None:
    def replay() -> Show:
        event = CanvasEvent.model_validate({"id": "e", "at": AT, "kind": kind, "payload": payload})
        return apply_event(PLAIN, event, force=True)

    only_showpy_errors(replay, (kind, payload))


@given(st.one_of(RAW_BYTES, JSON_VALUES.map(_json_bytes)))
def test_fuzz_decode_comments(raw: bytes) -> None:
    only_showpy_errors(lambda: decode_comments(raw), raw)
