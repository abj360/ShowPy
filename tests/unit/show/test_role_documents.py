#!/usr/bin/env python3

"""
test_role_documents.py --- checks that storing sets and the cast as role documents keeps content

Set E8S_UPDATE_GOLDEN=1 to rewrite the interchange goldens from their stored inputs; the diff then
needs human review. The seeded golden's input is the seeded show frozen as JSON, so live float math
never reaches the bytes compared; E8S_REBUILD_GOLDEN_INPUT=1 rebuilds that input from seeded_show
on the current machine, which can move the last bits of floats, so review its diff too.

Contains:
    test_export_json_bytes_are_unchanged_by_the_role_split: exports a seeded show and the head of
        the v0.4.0 fixture with the bytes pinned before sets were stored as role documents
    test_regression_seeded_export_golden_reads_frozen_show_data: builds the seeded export golden's
        show from the stored input file, never from live float math
    test_regression_interchange_goldens_check_out_byte_exact: finds the interchange golden folder
        marked `-text` in .gitattributes, so no checkout rewrites its line endings
    test_reserved_roots_are_refused_until_a_format_defines_them: refuses a path under `/props/`
        or `/tracks/` with BAD_PATH and a hint naming the reserved root
    test_cast_proposals_and_assets_roots_are_known: accepts documents under `/cast/`,
        `/proposals/` and `/assets/`
    test_role_split_round_trips_the_seeded_show: decodes the role documents of a seeded show,
        300 x 60 in the slow suite, into an equal show
    test_role_documents_hold_the_role_fields_keyed_by_spot: stores the set, drill, facing and
        labels documents and the cast document with the fields of their roles
    test_regression_layout1_set_documents_still_decode: decodes every revision of the v0.4.0
        fixture, whose sets hold placements in set.json and whose show.json holds the cast, and
        checks the layout-1 test encoder against its head bytes
    test_first_save_over_layout1_documents_changes_no_content: re-encodes the fixture head as
        role documents in one revision whose `changes()` is empty
    test_regression_tree_with_both_set_forms_is_refused: refuses a set.json with placements beside
        role documents, and a show.json cast beside the cast document
    test_regression_role_documents_without_set_json_are_refused: refuses drill, facing and labels
        documents of a set with no set.json
    test_regression_partial_role_set_is_refused: refuses a role-split set missing a role document
    test_regression_placement_order_must_match_the_role_documents: refuses a missing, extra or
        repeated spot between the placement order and the role documents
    test_regression_role_field_in_another_document_is_refused: refuses a placement field stored in
        a document that does not hold its role
    test_regression_older_format_tree_with_role_documents_is_refused: refuses role documents in a
        tree of format version 1
    test_workspace_reads_and_writes_the_cast_document_and_keeps_role_documents_internal: reads and
        writes /cast/cast.json, refuses a cast in show.json beside it, and neither lists, reads nor
        writes a role document
"""

import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, UnknownRefError
from e8s.examples import two_set_show
from e8s.show.documents import SHOW_DOC, decode_show, dump_json, encode_show, is_show_doc
from e8s.show.file_repository import FileRepository
from e8s.show.integrity import KNOWN_ROOTS, require_known_path
from e8s.show.interchange import export_json
from e8s.show.items import changes
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.generators import seeded_show
from tests.fixtures.layout1 import layout1_documents

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
V040 = FIXTURES / "repos" / "v0.4.0"
GOLDEN = FIXTURES / "interchange"
UPDATE_FLAG = "E8S_UPDATE_GOLDEN"
REBUILD_INPUT_FLAG = "E8S_REBUILD_GOLDEN_INPUT"
SEEDED_EXPORT = "seeded_show_export"
V040_EXPORT = "v040_head_export"
SEEDED_INPUT = GOLDEN / "seeded_show_input.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
GITATTRIBUTES = REPO_ROOT / ".gitattributes"
BYTE_EXACT_GOLDENS = ("tests/fixtures/interchange/**",)
SEED, SPOTS, SETS = 7, 16, 5
JSON_INDENT = 1
LARGE_SPOTS, LARGE_SETS = 300, 60
AT = datetime(2026, 11, 24, tzinfo=UTC)
CAST_DOC = "/cast/cast.json"
SET_DOC = "/sets/2/set.json"
DRILL_DOC = "/sets/2/drill.json"
FACING_DOC = "/sets/2/facing.json"
LABELS_DOC = "/sets/2/labels.json"
ROLE_DOCS = (DRILL_DOC, FACING_DOC, LABELS_DOC)
ORDER_FIELD = "placement_order"
LABEL_PAD = 2
ROLE_SUFFIXES = ("/drill.json", "/facing.json", "/labels.json")


def _v040_head(tmp_path: Path) -> Show:
    """Copies the read-only v0.4.0 fixture and loads its head show."""
    shutil.copytree(V040 / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((V040 / "expected.json").read_text())
    return FileRepository(tmp_path / "repo").get_show(expected["show_id"], expected["head"])


def _export_bytes(show: Show) -> bytes:
    """Writes the interchange document of a show, keeping the key order export_json gives."""
    text = json.dumps(export_json(show), indent=JSON_INDENT, ensure_ascii=False)
    return f"{text}\n".encode()


def _seeded_input() -> Show:
    """Loads the seeded show frozen as JSON; E8S_REBUILD_GOLDEN_INPUT=1 refreezes it first.

    The input keeps insertion order, not sorted keys: export_json writes mappings such as the
    cast's groups and the forms in insertion order, so sorted keys would change the golden bytes.
    """
    if os.environ.get(REBUILD_INPUT_FLAG) == "1":
        text = json.dumps(seeded_show(SEED, SPOTS, SETS).to_json(), indent=JSON_INDENT)
        SEEDED_INPUT.write_text(f"{text}\n")
    return Show.from_json(json.loads(SEEDED_INPUT.read_bytes()))


def _golden_show(name: str, tmp_path: Path) -> Show:
    """Returns the stored show a golden exports: the frozen seeded show or the v0.4.0 head."""
    return _seeded_input() if name == SEEDED_EXPORT else _v040_head(tmp_path)


@pytest.mark.parametrize("name", [SEEDED_EXPORT, V040_EXPORT])
def test_export_json_bytes_are_unchanged_by_the_role_split(name: str, tmp_path: Path) -> None:
    golden = GOLDEN / f"{name}.json"
    exported = _export_bytes(_golden_show(name, tmp_path))
    if os.environ.get(UPDATE_FLAG) == "1":
        golden.write_bytes(exported)
    assert exported == golden.read_bytes()


def test_regression_seeded_export_golden_reads_frozen_show_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def live_math(*_: object) -> Show:
        raise AssertionError("the golden input must come from stored data")

    monkeypatch.setattr(sys.modules[__name__], "seeded_show", live_math)
    stored = Show.from_json(json.loads(SEEDED_INPUT.read_bytes()))
    assert _golden_show(SEEDED_EXPORT, tmp_path) == stored


@pytest.mark.skipif(not GITATTRIBUTES.is_file(), reason="needs a source checkout")
@pytest.mark.parametrize("pattern", BYTE_EXACT_GOLDENS)
def test_regression_interchange_goldens_check_out_byte_exact(pattern: str) -> None:
    rules = [line.split() for line in GITATTRIBUTES.read_text().splitlines()]
    assert [pattern, "-text"] in rules
    assert GOLDEN.relative_to(REPO_ROOT).as_posix() == pattern.removesuffix("/**")


@pytest.mark.parametrize("path", ["/props/p1.json", "/tracks/state.json", "/props/a/b.json"])
def test_reserved_roots_are_refused_until_a_format_defines_them(path: str) -> None:
    with pytest.raises(StorageError) as caught:
        require_known_path(path)
    assert caught.value.code == ErrorCode.BAD_PATH
    root = path[: path.index("/", 1) + 1]
    assert root in caught.value.message
    assert "reserved" in caught.value.hint
    assert root not in KNOWN_ROOTS


@pytest.mark.parametrize("path", ["/cast/cast.json", "/proposals/p1.json", "/assets/a.png"])
def test_cast_proposals_and_assets_roots_are_known(path: str) -> None:
    require_known_path(path)


def _v040_repository(tmp_path: Path) -> tuple[FileRepository, dict[str, Any]]:
    """Copies the read-only v0.4.0 fixture and loads its expected.json."""
    shutil.copytree(V040 / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((V040 / "expected.json").read_text())
    return FileRepository(tmp_path / "repo"), expected


def _edited(path: str, edit: Any) -> dict[str, bytes]:
    """Encodes two_set_show and replaces one document with `edit` applied to its parsed JSON."""
    docs = encode_show(two_set_show())
    return {**docs, path: dump_json(edit(json.loads(docs[path])))}


@pytest.mark.parametrize(
    ("spots", "sets"),
    [(SPOTS, SETS), pytest.param(LARGE_SPOTS, LARGE_SETS, marks=pytest.mark.slow)],
)
def test_role_split_round_trips_the_seeded_show(spots: int, sets: int) -> None:
    show = seeded_show(SEED, spots, sets)
    docs = encode_show(show)
    assert decode_show(docs) == show
    for current in show.sets:
        folder = f"/sets/{current.key}/"
        assert {f"{folder}{name}" for name in ("drill.json", "facing.json", "labels.json")} <= set(
            docs
        )
        stored = json.loads(docs[f"{folder}set.json"])
        assert "placements" not in stored
        assert stored[ORDER_FIELD] == list(current.entity_ids())


def test_role_documents_hold_the_role_fields_keyed_by_spot() -> None:
    show = two_set_show()
    docs = encode_show(show)
    placement = show.set("2").get("T1")
    assert set(json.loads(docs[SET_DOC])) == {
        "id", "counts", "forms", "label", "cue", "source_hash", "state", ORDER_FIELD,
    }  # fmt: skip
    drill = json.loads(docs[DRILL_DOC])
    assert list(drill) == sorted(show.set("2").entity_ids())
    assert drill["T1"] == {
        "x": placement.x, "y": placement.y, "on_field": True, "form": None, "order": None,
        "pinned": False,
    }  # fmt: skip
    assert json.loads(docs[FACING_DOC])["T1"] == {"facing": placement.facing}
    assert json.loads(docs[LABELS_DOC])["T1"] == {"note": None}
    assert "cast" not in json.loads(docs[SHOW_DOC])
    assert json.loads(docs[CAST_DOC]) == show.cast.to_json()


def test_regression_layout1_set_documents_still_decode(tmp_path: Path) -> None:
    repo, expected = _v040_repository(tmp_path)
    for revision in expected["revisions"]:
        loaded = repo.get_show(expected["show_id"], revision)
        assert loaded == Show.from_json(expected["shows"][revision])
    head = repo.documents(expected["show_id"], expected["head"])
    stored = {p: raw for p, raw in head.items() if is_show_doc(p)}
    assert layout1_documents(repo.get_show(expected["show_id"], expected["head"])) == stored
    assert decode_show(layout1_documents(two_set_show())) == two_set_show()


def test_first_save_over_layout1_documents_changes_no_content(tmp_path: Path) -> None:
    repo, expected = _v040_repository(tmp_path)
    ws = open_workspace(repo, expected["show_id"])
    before = load_show(ws)
    saved = save_show(ws, before, AT)
    assert saved.parent == expected["head"]
    after = ws.show_at(saved.id)
    assert after is not None
    assert changes(before, after).changes == ()
    tree = ws.tree_at(saved.id)
    assert CAST_DOC in tree
    assert "cast" not in json.loads(tree[SHOW_DOC])
    assert all(b'"placements"' not in tree[f"/sets/{s.key}/set.json"] for s in after.sets)
    assert {CAST_DOC, SHOW_DOC} <= set(saved.changed)
    ws.refresh()
    assert save_show(ws, load_show(ws), AT).id == saved.id


def test_regression_tree_with_both_set_forms_is_refused() -> None:
    show = two_set_show()
    layout1_set = dump_json(show.set("2").to_json())
    with pytest.raises(SchemaError, match="placements"):
        decode_show({**encode_show(show), SET_DOC: layout1_set})
    with pytest.raises(SchemaError, match="cast"):
        decode_show(_edited(SHOW_DOC, lambda meta: {**meta, "cast": show.cast.to_json()}))
    with pytest.raises(SchemaError, match=ORDER_FIELD):
        decode_show(_edited(SET_DOC, lambda doc: {**doc, "placements": []}))


def test_regression_role_documents_without_set_json_are_refused() -> None:
    docs = encode_show(two_set_show())
    without = {p: raw for p, raw in docs.items() if p != SET_DOC}
    with pytest.raises(SchemaError, match=r"set\.json"):
        decode_show(without)
    orphan = {**docs, "/sets/9/drill.json": docs[DRILL_DOC]}
    with pytest.raises(SchemaError, match="/sets/9/"):
        decode_show(orphan)


@pytest.mark.parametrize("missing", ROLE_DOCS)
def test_regression_partial_role_set_is_refused(missing: str) -> None:
    docs = {p: raw for p, raw in encode_show(two_set_show()).items() if p != missing}
    with pytest.raises(SchemaError, match=re.escape(missing.rsplit("/", 1)[1])):
        decode_show(docs)


@pytest.mark.parametrize(
    ("path", "edit"),
    [
        (SET_DOC, lambda doc: {**doc, ORDER_FIELD: doc[ORDER_FIELD][1:]}),
        (SET_DOC, lambda doc: {**doc, ORDER_FIELD: [*doc[ORDER_FIELD], doc[ORDER_FIELD][0]]}),
        (DRILL_DOC, lambda doc: {k: v for k, v in doc.items() if k != "T1"}),
        (FACING_DOC, lambda doc: {**doc, "T9": {"facing": 0.0}}),
        (SET_DOC, lambda doc: {**doc, ORDER_FIELD: "T1"}),
        (SET_DOC, lambda doc: {**doc, ORDER_FIELD: [*doc[ORDER_FIELD][:-1], 4]}),
        (LABELS_DOC, lambda doc: [*doc]),
        (LABELS_DOC, lambda doc: {**doc, "T1": "solo"}),
    ],
)
def test_regression_placement_order_must_match_the_role_documents(path: str, edit: Any) -> None:
    with pytest.raises(SchemaError):
        decode_show(_edited(path, edit))


@pytest.mark.parametrize(
    ("path", "field"), [(DRILL_DOC, "facing"), (FACING_DOC, "x"), (LABELS_DOC, "pinned")]
)
def test_regression_role_field_in_another_document_is_refused(path: str, field: str) -> None:
    with pytest.raises(SchemaError, match=field):
        decode_show(_edited(path, lambda doc: {**doc, "T1": {**doc["T1"], field: 1}}))


def test_regression_older_format_tree_with_role_documents_is_refused() -> None:
    docs = layout1_documents(two_set_show())
    meta = json.loads(docs[SHOW_DOC])
    v1 = {k: v for k, v in meta.items() if k not in ("format", "version")}
    role = encode_show(two_set_show())[DRILL_DOC]
    with pytest.raises(SchemaError, match=r"drill\.json"):
        decode_show({**docs, SHOW_DOC: dump_json(v1), DRILL_DOC: role})


def test_workspace_reads_and_writes_the_cast_document_and_keeps_role_documents_internal() -> None:
    ws = open_workspace(InMemoryRepository(), "show_1")
    show = two_set_show()
    save_show(ws, show, AT)
    assert ws.ls("/cast") == ["cast.json"]
    assert json.loads(ws.read(CAST_DOC)) == show.cast.to_json()
    cast = show.cast.replace(label_scheme=show.cast.label_scheme.replace(pad=LABEL_PAD)).to_json()
    ws.write(CAST_DOC, json.dumps(cast), at=AT)
    assert load_show(ws).cast.label_scheme.pad == LABEL_PAD
    assert "drill.json" not in ws.ls("/sets/2")
    with pytest.raises(UnknownRefError):
        ws.read(DRILL_DOC)
    with pytest.raises(UnknownRefError):
        ws.write(DRILL_DOC, ws.tree_at(ws.revision)[DRILL_DOC], at=AT)
    meta = {**json.loads(ws.read(SHOW_DOC)), "cast": cast}
    with pytest.raises(SchemaError, match="cast"):
        ws.write(SHOW_DOC, json.dumps(meta), at=AT)
