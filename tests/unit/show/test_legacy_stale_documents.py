#!/usr/bin/env python3

"""
test_legacy_stale_documents.py --- normalizes stale documents of a v0.2.0 repository only once

The fixture is migrated with two documents left stale: set 1's drill document, written with the
`-0.0` coordinates v0.2 stored, and a reformatted sheet document.

Contains:
    test_regression_writes_to_different_documents_normalize_once: adds normalizing plus two edits
        for two writes to different show documents, then nothing more for a save_show
    test_regression_commits_to_different_documents_normalize_once: does the same for two raw
        commits, which name only the documents they rewrite
    test_regression_script_write_normalizes_every_show_document: normalizes all documents on the
        first write of any kind
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from e8s.show.documents import SHOW_DOC, dump_json, dump_show_doc, is_show_doc, migrate_documents
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace
from tests.fixtures.legacy_v020 import NEGATIVE_ZERO, retitled, v020_workspace

COMMITTED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)

STALE_DRILL = "/sets/1/drill.json"
STALE_DOCS = {STALE_DRILL, "/show/sheet.json"}


def _two_stale_documents(tmp_path: Path) -> tuple[FileRepository, Workspace, int]:
    """Copies the fixture and reformats its migrated sheet document, so two documents are
    non-canonical.

    The commit also stores show.json, which any commit of a show document needs, and every
    other changed document in its migrated shape, so no stored ID is a v1 ID; the drill document
    of set 1, written with the `-0.0` coordinates v0.2 stored, and the reformatted sheet stay
    stale.
    """
    repo, ws, expected = v020_workspace(tmp_path)
    tree = ws.tree_at(expected["head"])
    migrated, _ = migrate_documents(tree)
    sheet = json.loads(migrated["/show/sheet.json"])
    current: dict[str, bytes | None] = {
        path: raw
        for path, raw in migrated.items()
        if is_show_doc(path) and path not in STALE_DOCS and tree.get(path) != raw
    }
    current.update(dict.fromkeys(p for p in tree if is_show_doc(p) and p not in migrated))
    stale_drill = _drill_with_stored_coordinates(tree, migrated)
    assert NEGATIVE_ZERO.search(stale_drill)
    repo.transaction(
        expected["show_id"],
        {
            **current,
            STALE_DRILL: stale_drill,
            "/show/sheet.json": json.dumps(sheet, indent=4).encode(),
        },
        expected_revision=expected["head"],
        at=COMMITTED_AT,
    )
    ws.refresh()
    return repo, ws, len(repo.revisions(expected["show_id"]))


def _drill_with_stored_coordinates(
    tree: Mapping[str, bytes], migrated: Mapping[str, bytes]
) -> bytes:
    """Writes set 1's migrated drill rows with the stored x and y, `-0.0` kept, not canonically."""
    stored = json.loads(tree["/sets/1/set.json"])["placements"]
    order = json.loads(migrated["/sets/1/set.json"])["placement_order"]
    drill = json.loads(migrated[STALE_DRILL])
    for spot, row in zip(order, stored, strict=True):
        drill[spot] = {**drill[spot], "x": row["x"], "y": row["y"]}
    return (json.dumps(drill, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _after(repo: FileRepository, ws: Workspace, count: int) -> list[Revision]:
    return repo.revisions(ws.show_id)[count:]


def _timeline(ws: Workspace) -> bytes:
    document = json.loads(ws.read("/time/timeline.json"))
    document["total_counts"] = 100.0
    return json.dumps(document).encode()


def test_regression_writes_to_different_documents_normalize_once(tmp_path: Path) -> None:
    repo, ws, count = _two_stale_documents(tmp_path)
    ws.write("/show/show.json", retitled(ws, "Renamed"), at=COMMITTED_AT)
    ws.write("/time/timeline.json", _timeline(ws), at=COMMITTED_AT)
    assert [set(r.changed) for r in _after(repo, ws, count)] == [
        STALE_DOCS,
        {"/show/show.json"},
        {"/time/timeline.json"},
    ]
    save_show(ws, load_show(ws), COMMITTED_AT)
    assert len(_after(repo, ws, count)) == 3


def test_regression_commits_to_different_documents_normalize_once(tmp_path: Path) -> None:
    repo, ws, count = _two_stale_documents(tmp_path)
    tree = ws.tree_at(ws.revision)
    first = json.loads(ws.read(SHOW_DOC))
    first["title"] = "Renamed"
    second = json.loads(tree["/transitions/4-5/paths.json"])
    second["notes"] = "slow"
    ws.commit({SHOW_DOC: dump_show_doc(first)}, at=COMMITTED_AT)
    ws.commit({"/transitions/4-5/paths.json": dump_json(second)}, at=COMMITTED_AT)
    assert [set(r.changed) for r in _after(repo, ws, count)] == [
        STALE_DOCS,
        {"/show/show.json"},
        {"/transitions/4-5/paths.json"},
    ]
    save_show(ws, load_show(ws), COMMITTED_AT)
    assert len(_after(repo, ws, count)) == 3


def test_regression_script_write_normalizes_every_show_document(tmp_path: Path) -> None:
    repo, ws, count = _two_stale_documents(tmp_path)
    ws.write("/sets/2/set.py", b"# changed\n", at=COMMITTED_AT)
    assert [set(r.changed) for r in _after(repo, ws, count)] == [STALE_DOCS, {"/sets/2/set.py"}]
