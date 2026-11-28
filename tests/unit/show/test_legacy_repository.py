#!/usr/bin/env python3

"""
test_legacy_repository.py --- guards that a repository written by v0.2.0 stays readable

Contains:
    test_v020_repository_loads_without_loss: lists the fixture's revisions and decodes a head show
        equal to the one stored in expected.json
    test_v020_revision_records_carry_legacy_keys: finds actor, message and kind in every stored
        revision record
    test_commit_on_v020_repository_gets_a_new_scheme_id: commits on a copy and checks that the new
        ID uses the domain-tagged scheme while stored IDs and the head chain stay as written
    test_regression_v02_revisions_load_without_authorship: loads the fixture's revisions with only
        id, parent, at and changed, every stored ID still listed and nothing on disk rewritten
    test_regression_v020_head_loads_with_its_stored_negative_zero: keeps the stored sign of every
        `-0.0` on load and rewrites nothing
    test_regression_first_save_normalizes_v020_documents_in_one_revision: adds exactly one
        revision holding only re-encoded show documents with no `-0.0` and the show.json header
    test_regression_normalizing_revision_is_invisible_to_diff_and_events: shows an empty
        `diff_show` and no events since the pre-upgrade head
    test_regression_second_save_adds_no_normalizing_revision: adds nothing on a repeat save
    test_regression_normalization_keeps_tags_selection_scripts_and_pin: carries them through
        both the load and the normalizing save
    test_regression_workspace_write_normalizes_first_then_edits: lists only the edited document in
        the write's own revision, with an empty `diff_show` across the normalizing one
    test_regression_workspace_commit_normalizes_first_then_edits: normalizes the document a commit
        rewrites and adds the show.json header, then commits the edit
    test_regression_second_workspace_write_adds_no_normalizing_revision: adds one revision only
    test_regression_stale_write_on_a_legacy_repository_lands_nothing: refuses a stale write
        with ConflictError, adds no revision and leaves the show as it was
    test_regression_edit_conflicting_after_normalizing_keeps_it_and_retries_on_top: keeps the
        normalizing revision when a competing writer lands before the edit, then retries on top
    test_regression_commit_of_the_canonical_form_adds_only_the_normalizing_revision: returns the
        head, with no empty revision, when nothing remains after normalizing
    test_new_records_carrying_authorship_are_refused: refuses actor on a new Revision or
        CanvasEvent
    test_stored_record_with_an_unknown_key_is_refused: drops only the legacy keys from a stored
        record, so any other extra key still fails
    test_regression_v020_revision_with_a_naive_time_reads_as_utc: reads a stored revision whose
        time has no timezone as UTC, leaving its tree file as stored
"""

import hashlib
import json
import math
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, SchemaError, StorageError
from e8s.core.util import content_hash
from e8s.show.collab_models import CanvasEvent
from e8s.show.document_paths import CAST_DOC, is_set_document
from e8s.show.document_reading import decode_show_dump
from e8s.show.documents import (
    LEGACY_NOTES_DOC,
    SHEET_DOC,
    SHOW_DOC,
    dump_json,
    is_show_doc,
    migrate_documents,
)
from e8s.show.events import events_since
from e8s.show.file_repository import FileRepository
from e8s.show.history import diff_show
from e8s.show.history_models import Revision
from e8s.show.migrate import FIRST_VERSION
from e8s.show.models import Show
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.layout1 import upgraded_copy
from tests.fixtures.legacy_v020 import (
    NEGATIVE_ZERO,
    V020_FIXTURE,
    retitled,
    v020_expected,
    v020_workspace,
)

FIXTURE = V020_FIXTURE
LEGACY_KEYS = {"actor", "message", "kind"}
REVISION_FIELDS = {"id", "parent", "at", "changed"}
COMMITTED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
HEX_LENGTH = 64
SET_SUFFIX = "/set.json"
SET_DOCUMENT_NAMES = ("set.json", "drill.json", "facing.json", "labels.json")


def test_v020_repository_loads_without_loss(tmp_path: Path) -> None:
    expected = v020_expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    show_id = expected["show_id"]
    assert [r.id for r in repo.revisions(show_id)] == expected["revisions"]
    assert repo.head(show_id) == expected["head"]
    assert repo.get_show(show_id) == decode_show_dump(expected["show"], FIRST_VERSION)


def test_v020_revision_records_carry_legacy_keys() -> None:
    expected = v020_expected()
    trees = FIXTURE / "repo" / expected["show_id"] / "trees"
    for revision_id in expected["revisions"]:
        record = json.loads((trees / f"{revision_id}.json").read_text())
        assert set(record["revision"]) >= LEGACY_KEYS


def test_commit_on_v020_repository_gets_a_new_scheme_id(tmp_path: Path) -> None:
    expected = v020_expected()
    repo = FileRepository(upgraded_copy(FIXTURE / "repo", tmp_path))
    show_id = expected["show_id"]
    head_show = repo.get_show(show_id)
    revision = repo.put_show(
        show_id,
        head_show.replace(title=f"{head_show.title} v2"),
        expected_revision=expected["head"],
        at=COMMITTED_AT,
    )
    tree = repo.backend.tree(show_id, revision.id)
    body = {
        "parent": expected["head"],
        "at": COMMITTED_AT.isoformat(),
        "changes": {path: tree.get(path) for path in revision.changed},
    }
    legacy_scheme = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    assert revision.changed
    assert revision.id == content_hash(body, domain="showpy.revision")
    assert revision.id != hashlib.sha256(legacy_scheme).hexdigest()
    assert len(revision.id) == HEX_LENGTH
    assert [r.id for r in repo.revisions(show_id)] == [*expected["revisions"], revision.id]
    assert repo.get_show(show_id, expected["head"]) == decode_show_dump(
        expected["show"], FIRST_VERSION
    )
    assert content_hash(head_show) != expected["legacy_content_hash"]


def test_regression_v02_revisions_load_without_authorship(tmp_path: Path) -> None:
    expected = v020_expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    before = {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()}
    revisions = FileRepository(tmp_path / "repo").revisions(expected["show_id"])
    assert [r.id for r in revisions] == expected["revisions"]
    for revision in revisions:
        assert set(type(revision)._field_names()) == REVISION_FIELDS
        assert set(revision.to_json()) == REVISION_FIELDS
    assert {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()} == before


def test_new_records_carrying_authorship_are_refused() -> None:
    with pytest.raises(SchemaError):
        Revision.from_json(
            {"id": "a", "parent": "", "at": COMMITTED_AT, "changed": [], "actor": "designer"}
        )
    with pytest.raises(SchemaError):
        CanvasEvent.from_json(
            {"id": "e", "at": COMMITTED_AT, "kind": "comment", "payload": {}, "actor": "agent"}
        )


def test_stored_record_with_an_unknown_key_is_refused(tmp_path: Path) -> None:
    expected = v020_expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    record_path = tmp_path / "repo" / expected["show_id"] / "trees" / f"{expected['head']}.json"
    record = json.loads(record_path.read_text())
    record["revision"]["author"] = "pat"
    record_path.write_text(json.dumps(record))
    with pytest.raises(StorageError) as caught:
        FileRepository(tmp_path / "repo").revisions(expected["show_id"])
    assert caught.value.code == ErrorCode.CORRUPT
    assert isinstance(caught.value.__cause__, SchemaError)


def _negative_zero_paths(tree: Mapping[str, bytes]) -> set[str]:
    return {p for p, raw in tree.items() if is_show_doc(p) and NEGATIVE_ZERO.search(raw)}


def _forms_paths(tree: Mapping[str, bytes]) -> set[str]:
    return {p for p in tree if p.endswith("/forms.json")}


def _form_reference_paths(tree: Mapping[str, bytes]) -> set[str]:
    """Lists the set documents whose placements name a form; every v0.2.0 form ID is renamed."""
    return {
        p
        for p, raw in tree.items()
        if p.endswith("/set.json") and any(row.get("form") for row in json.loads(raw)["placements"])
    }


def _role_split_paths(tree: Mapping[str, bytes]) -> set[str]:
    """Lists every set document of a layout-1 tree with the role documents it splits into."""
    folders = [
        p.removesuffix(SET_SUFFIX) for p in tree if is_set_document(p) and p.endswith(SET_SUFFIX)
    ]
    return {f"{folder}/{name}" for folder in folders for name in SET_DOCUMENT_NAMES}


def _normalized_paths(tree: Mapping[str, bytes]) -> set[str]:
    """Lists what the first save rewrites: `-0.0` documents, forms documents for their v2
    recipes, set documents for their renamed form IDs, show.json for its v2 header, renamed
    selection and dropped coordinates, sheet.json for its dropped landmarks and coordinates, and
    the v1 notes document, which it deletes (the fixture holds no notes). Every set document is
    split into set.json and its role documents, and the cast moves to the cast document."""
    return (
        _negative_zero_paths(tree)
        | _forms_paths(tree)
        | _form_reference_paths(tree)
        | _role_split_paths(tree)
        | {SHOW_DOC, SHEET_DOC, LEGACY_NOTES_DOC, CAST_DOC}
    )


def _signs(show: Show) -> list[float]:
    return [p.x for s in show.sets for p in s.placements]


def test_regression_v020_head_loads_with_its_stored_negative_zero(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()}
    tree = ws.tree_at(expected["head"])
    assert _negative_zero_paths(tree)
    show = load_show(ws)
    assert any(math.copysign(1.0, x) < 0 for x in _signs(show) if x == 0)
    assert {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()} == before
    assert repo.head(expected["show_id"]) == expected["head"]


def test_regression_first_save_normalizes_v020_documents_in_one_revision(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    show_id, head = expected["show_id"], expected["head"]
    before = ws.tree_at(head)
    saved = save_show(ws, load_show(ws), COMMITTED_AT)
    log = repo.revisions(show_id)
    assert [r.id for r in log] == [*expected["revisions"], saved.id]
    assert saved.parent == head
    assert set(saved.changed) == _normalized_paths(before)
    after = ws.tree_at(saved.id)
    assert not _negative_zero_paths(after)
    unchanged = set(before) - set(saved.changed)
    assert {p: after[p] for p in unchanged} == {p: before[p] for p in unchanged}
    assert set(after) == (set(before) | _role_split_paths(before) | {CAST_DOC}) - {LEGACY_NOTES_DOC}
    scripts = {p for p in before if p.endswith("/set.py")}
    assert scripts
    assert not scripts & set(saved.changed)


def test_regression_save_with_an_edit_normalizes_first_then_edits(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    show = load_show(ws)
    saved = save_show(ws, show.replace(title="Renamed"), COMMITTED_AT)
    log = repo.revisions(expected["show_id"])
    assert [r.id for r in log][:3] == expected["revisions"]
    assert len(log) == 5
    assert log[4].id == saved.id
    assert log[3].parent == expected["head"]
    assert set(log[3].changed) == _normalized_paths(ws.tree_at(expected["head"]))
    assert log[4].changed == ("/show/show.json",)
    assert not _negative_zero_paths(ws.tree_at(saved.id))


def test_regression_normalizing_revision_is_invisible_to_diff_and_events(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    before = load_show(ws)
    saved = save_show(ws, before, COMMITTED_AT)
    after = ws.show_at(saved.id)
    assert after is not None
    assert diff_show(before, after) == {}
    assert events_since(ws, expected["head"]) == []
    assert repo.get_show(expected["show_id"], expected["head"]) == before


def test_regression_second_save_adds_no_normalizing_revision(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    first = save_show(ws, load_show(ws), COMMITTED_AT)
    ws.refresh()
    again = save_show(ws, load_show(ws), COMMITTED_AT)
    assert again.id == first.id
    assert len(repo.revisions(expected["show_id"])) == len(expected["revisions"]) + 1


def test_regression_normalization_keeps_tags_selection_scripts_and_pin(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    head = expected["head"]
    show = load_show(ws)
    scripts = {p: raw for p, raw in ws.tree_at(head).items() if p.endswith("/set.py")}
    saved = save_show(ws, show, COMMITTED_AT)
    for loaded in (show, repo.get_show(expected["show_id"], saved.id)):
        assert [s.tags for s in loaded.cast.spots if s.tags] == [("horn", "lead")]
        assert loaded.cast.selections == {"front_four": ("B1", "B2", "B3", "B4")}
        assert [(p.id, p.target, p.reason) for p in loaded.pins] == [("B3", "entity", "soloist")]
    after = ws.tree_at(saved.id)
    assert {p: after[p] for p in scripts} == scripts


def _assert_normalized_then_edited(
    repo: FileRepository, ws: Workspace, expected: dict[str, Any], edit: Revision
) -> None:
    log = repo.revisions(expected["show_id"])
    assert [r.id for r in log][:3] == expected["revisions"]
    assert len(log) == 5
    assert log[4].id == edit.id
    assert log[3].parent == expected["head"]
    assert set(log[3].changed) == _normalized_paths(ws.tree_at(expected["head"]))
    assert log[4].changed == ("/show/show.json",)
    before, after = ws.show_at(expected["head"]), ws.show_at(log[3].id)
    assert before is not None
    assert after is not None
    assert diff_show(before, after) == {}
    assert not _negative_zero_paths(ws.tree_at(edit.id))


def test_regression_workspace_write_normalizes_first_then_edits(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    edit = ws.write("/show/show.json", retitled(ws, "Renamed"), at=COMMITTED_AT)
    _assert_normalized_then_edited(repo, ws, expected, edit)


def test_regression_workspace_commit_normalizes_first_then_edits(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    migrated, _ = migrate_documents(ws.tree_at(ws.revision))
    document = json.loads(migrated["/sets/1/set.json"])
    document["label"] = "Opening"
    edit = ws.commit({"/sets/1/set.json": dump_json(document)}, at=COMMITTED_AT)
    log = repo.revisions(expected["show_id"])
    assert [r.id for r in log][:3] == expected["revisions"]
    assert [set(r.changed) for r in log[3:]] == [
        {"/sets/1/set.json", SHOW_DOC, SHEET_DOC, LEGACY_NOTES_DOC, CAST_DOC}
        | _forms_paths(ws.tree_at(expected["head"]))
        | _form_reference_paths(ws.tree_at(expected["head"]))
        | _role_split_paths(ws.tree_at(expected["head"])),
        {"/sets/1/set.json"},
    ]
    assert log[4].id == edit.id
    before, normalized = ws.show_at(expected["head"]), ws.show_at(log[3].id)
    assert before is not None
    assert normalized is not None
    assert diff_show(before, normalized) == {}
    assert not NEGATIVE_ZERO.search(ws.tree_at(edit.id)["/sets/1/set.json"])
    assert not NEGATIVE_ZERO.search(ws.tree_at(edit.id)["/sets/1/drill.json"])


def test_regression_second_workspace_write_adds_no_normalizing_revision(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    ws.write("/show/show.json", retitled(ws, "Renamed"), at=COMMITTED_AT)
    ws.write("/show/show.json", retitled(ws, "Renamed again"), at=COMMITTED_AT)
    assert len(repo.revisions(expected["show_id"])) == len(expected["revisions"]) + 3


def test_regression_stale_write_on_a_legacy_repository_lands_nothing(tmp_path: Path) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    before = load_show(ws)
    raw = retitled(ws, "Renamed")
    with pytest.raises(ConflictError):
        ws.write(
            "/show/show.json", raw, expected_revision=expected["revisions"][1], at=COMMITTED_AT
        )
    assert len(repo.revisions(expected["show_id"])) == len(expected["revisions"])
    ws.refresh()
    assert load_show(ws) == before


def _revision_count(repo: FileRepository, expected: dict[str, Any]) -> int:
    return len(repo.revisions(expected["show_id"])) - len(expected["revisions"])


def _competing_writer_lands_before_second_commit(repo: FileRepository, ws: Workspace) -> None:
    original = ws._commit_stored
    calls: list[int] = []

    def commit(*args: Any, **kwargs: Any) -> Revision:
        calls.append(1)
        if len(calls) == 2:
            rival = open_workspace(repo, ws.show_id)
            rival.write("/sets/2/set.py", b"# rival\n", at=COMMITTED_AT)
        return original(*args, **kwargs)

    ws._commit_stored = commit  # type: ignore[method-assign]


def test_regression_edit_conflicting_after_normalizing_keeps_it_and_retries_on_top(
    tmp_path: Path,
) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    before = load_show(ws)
    _competing_writer_lands_before_second_commit(repo, ws)
    raw = retitled(ws, "Renamed")
    with pytest.raises(ConflictError):
        ws.write("/show/show.json", raw, at=COMMITTED_AT)
    log = repo.revisions(expected["show_id"])[len(expected["revisions"]) :]
    assert len(log) == 2
    assert set(log[0].changed) == _normalized_paths(ws.tree_at(expected["head"]))
    assert log[1].changed == ("/sets/2/set.py",)
    kept = repo.get_show(expected["show_id"], log[0].id)
    assert diff_show(before, kept) == {}
    del ws._commit_stored
    ws.refresh()
    retry = ws.write("/show/show.json", raw, at=COMMITTED_AT)
    assert retry.changed == ("/show/show.json",)
    assert _revision_count(repo, expected) == 3


def test_regression_commit_of_the_canonical_form_adds_only_the_normalizing_revision(
    tmp_path: Path,
) -> None:
    repo, ws, expected = v020_workspace(tmp_path)
    migrated, _ = migrate_documents(ws.tree_at(expected["head"]))
    canonical = {p: migrated[p] for p in ("/sets/1/set.json", "/sets/1/drill.json")}
    head = ws.commit(canonical, at=COMMITTED_AT)
    log = repo.revisions(expected["show_id"])[len(expected["revisions"]) :]
    assert [r.id for r in log] == [head.id]
    assert repo.head(expected["show_id"]) == head.id
    assert set(head.changed) == _normalized_paths(ws.tree_at(expected["head"]))


def test_regression_v020_revision_with_a_naive_time_reads_as_utc(tmp_path: Path) -> None:
    """Reads a stored v0.2.0 revision with no timezone as UTC, keeping its stored record."""
    expected = v020_expected()
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    first = expected["revisions"][0]
    record_path = tmp_path / "repo" / expected["show_id"] / "trees" / f"{first}.json"
    record = json.loads(record_path.read_text())
    aware = datetime.fromisoformat(record["revision"]["at"])
    record["revision"]["at"] = aware.replace(tzinfo=None).isoformat()
    record_path.write_text(json.dumps(record))
    stored = record_path.read_bytes()
    revision = repo.revisions(expected["show_id"])[0]
    assert revision.id == first
    assert revision.at == aware.replace(tzinfo=UTC)
    assert record_path.read_bytes() == stored
