#!/usr/bin/env python3

"""
test_notes_document.py --- tests the notes document beside the show and its v1 migration

Contains:
    test_regression_v1_notes_move_to_the_notes_document: moves every v1 note of each fixture
        into the notes document, same text, author and time, and leaves no notes key in show.json
    test_note_anchors_follow_the_migration_renames: renames note IDs by rule and points set and
        transition anchors at the set IDs the same migration renamed
    test_regression_v1_note_with_an_empty_id_is_renamed: renames an empty v1 note ID to `id` by
        the rename rule instead of refusing the whole show
    test_stored_v1_spot_renames_reach_note_anchors: points spot anchors of a stored v1 tree at the
        spot IDs its migration renamed
    test_v1_spot_renames_reach_note_anchors: points spot anchors of a v1 interchange document at
        the spot IDs its migration renamed
    test_first_save_writes_notes_in_the_same_revision_and_the_second_none: stores the notes
        document with the v2 documents in one workspace revision and nothing on the next save
    test_put_show_writes_notes_in_the_migration_revision: stores the notes document in the
        revision `put_show` writes over a v1 head, and nothing on the next
    test_put_script_writes_notes_in_the_migration_revision: stores the notes document in the
        migration revision `put_script` writes before the script
    test_reads_on_a_v1_head_see_the_notes_document: lists and reads the notes document through
        `migrated_view` and a workspace before the first save, writing nothing
    test_migration_changes_write_the_notes_document_and_drop_the_v1_notes: lists the notes
        document as a write and the v1 notes document as a delete
    test_a_v1_tree_without_notes_gets_no_notes_document: writes no notes document for a v1
        tree whose notes are empty
    test_v2_show_json_has_no_notes_and_show_has_no_notes_field: keeps notes out of `Show`
    test_notes_document_refuses_bad_and_repeated_note_ids: checks note IDs on the document
    test_regression_newer_notes_document_raises_version_error: refuses a notes document of a
        newer version with VersionError and an unknown format with SchemaError UNKNOWN_FORMAT
    test_notes_document_header_is_written_first: stores and exports `format` and `version` as the
        first keys of the notes document
    test_workspace_writes_a_checked_notes_document: stores a valid notes document through a
        workspace, refuses one that does not match its model and refuses the v1 notes path
    test_regression_workspace_refuses_the_notes_of_another_show: refuses a notes document whose
        `show` names another show, with a hint naming the show's ID
    test_export_json_returns_the_notes_document_beside_the_show: embeds the notes document
        under `notes` and refuses one of another show
    test_import_notes_reads_v1_and_v2_documents: returns the notes of a v2 document as given
        and migrates the notes of a v1 document with the show's renames
    test_rename_spots_rewrites_the_notes_document: follows spot renames in the notes document
        and reports orphaned notes when a rewrite drops every anchored spot
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from showpy.cast.rewrite import rewrite_spots
from showpy.cast.spots import rename_spots
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, UnknownRefError, VersionError
from showpy.core.ids import NoteId
from showpy.examples import two_set_show
from showpy.notes.models import Note, NotesDocument
from showpy.show.documents import (
    LEGACY_NOTES_DOC,
    NOTES_DOC,
    SHOW_DOC,
    dump_json,
    encode_show,
    migrate_documents,
    migrated_view,
    migration_changes,
)
from showpy.show.file_repository import FileRepository
from showpy.show.integrity import blob_digest
from showpy.show.interchange import export_json, import_json, import_notes
from showpy.show.models import Anchor, Revision, Show
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import load_show, open_workspace, save_show

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "repos"
TAGS = ["v0.1.1", "v0.2.0", "v0.3.0"]
AT = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
WRITTEN = "2026-11-12T10:00:00+00:00"

pytestmark = pytest.mark.filterwarnings("ignore::showpy.core.deprecation.DeprecatedWarning")


def _fixture(tag: str, tmp_path: Path) -> tuple[FileRepository, str]:
    shutil.copytree(FIXTURES / tag / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((FIXTURES / tag / "expected.json").read_text())
    return FileRepository(tmp_path / "repo"), expected["show_id"]


def _commit_v1(repo: FileRepository, show_id: str, changed: dict[str, bytes]) -> str:
    """Commits v1 documents through the backend, as a v0.3.0 writer stored them."""
    head = repo.head(show_id)
    tree = dict(repo.backend.tree(show_id, head))
    tree.update({path: blob_digest(raw) for path, raw in changed.items()})
    body = {"parent": head, "at": AT.isoformat(), "tree": tree}
    revision_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    revision = Revision(id=revision_id, parent=head, at=AT, changed=tuple(sorted(changed)))
    repo.backend.commit(
        show_id, revision, tree, {blob_digest(raw): raw for raw in changed.values()}
    )
    return revision_id


def _v1_note(note_id: str, anchor: dict[str, Any], text: str = "look") -> dict[str, Any]:
    return {
        "id": note_id,
        "text": text,
        "kind": "todo",
        "anchor": anchor,
        "author": "pat",
        "created_at": WRITTEN,
        "visible": True,
    }


def _first_set(docs: dict[str, bytes]) -> str:
    order: list[str] = json.loads(docs[SHOW_DOC])["sets"]
    folder = order[0]
    stored: dict[str, Any] = json.loads(docs[f"/sets/{folder}/set.json"])
    return str(stored["id"])


def _with_notes(tag: str, tmp_path: Path) -> tuple[FileRepository, str, list[dict[str, Any]]]:
    """Commits a v1 notes document of two notes on the fixture's first set and a point."""
    repo, show_id = _fixture(tag, tmp_path)
    first = _first_set(dict(repo.documents(show_id)))
    notes = [
        _v1_note("n1", {"kind": "set", "set_id": first}, "open wide"),
        _v1_note("n2", {"kind": "point", "point": [1.0, 2.0]}, "mark"),
    ]
    _commit_v1(repo, show_id, {LEGACY_NOTES_DOC: dump_json(notes)})
    return repo, show_id, notes


@pytest.mark.parametrize("tag", TAGS)
def test_regression_v1_notes_move_to_the_notes_document(tag: str, tmp_path: Path) -> None:
    repo, show_id, notes = _with_notes(tag, tmp_path)
    migrated, _ = migrate_documents(dict(repo.documents(show_id)))
    document = json.loads(migrated[NOTES_DOC])
    assert (document["format"], document["version"], document["show"]) == (
        "e8.notes",
        1,
        repo.get_show(show_id).id,
    )
    assert [(n["id"], n["text"], n["author"]) for n in document["notes"]] == [
        (n["id"], n["text"], n["author"]) for n in notes
    ]
    assert {datetime.fromisoformat(n["created_at"]) for n in document["notes"]} == {
        datetime.fromisoformat(WRITTEN)
    }
    assert "notes" not in json.loads(migrated[SHOW_DOC])
    assert LEGACY_NOTES_DOC not in migrated


def test_note_anchors_follow_the_migration_renames(tmp_path: Path) -> None:
    repo, show_id = _fixture("v0.3.0", tmp_path)
    notes = [
        _v1_note("n-1", {"kind": "set", "set_id": "con"}),
        _v1_note("n2", {"kind": "transition", "tid": ["1A", "con"]}),
        _v1_note("N-1", {"kind": "spots", "spots": ["T1"]}),
    ]
    _commit_v1(repo, show_id, {LEGACY_NOTES_DOC: dump_json(notes)})
    migrated, report = migrate_documents(dict(repo.documents(show_id)))
    document = NotesDocument.model_validate_json(migrated[NOTES_DOC])
    assert [n.id for n in document.notes] == ["n_1", "n2", "N_1.2"]
    assert report.renamed["note"] == (("n-1", "n_1"), ("N-1", "N_1.2"))
    assert document.notes[0].anchor == Anchor.on_set("con_")
    assert document.notes[1].anchor == Anchor.on_transition(("1A.2", "con_"))
    assert document.notes[2].anchor == Anchor.on_spots(["T1"])


def test_regression_v1_note_with_an_empty_id_is_renamed(tmp_path: Path) -> None:
    repo, show_id = _fixture("v0.3.0", tmp_path)
    notes = [
        _v1_note("", {"kind": "set", "set_id": "con"}),
        _v1_note("id", {"kind": "point", "point": [0.0, 0.0]}),
    ]
    _commit_v1(repo, show_id, {LEGACY_NOTES_DOC: dump_json(notes)})
    migrated, report = migrate_documents(dict(repo.documents(show_id)))
    document = NotesDocument.model_validate_json(migrated[NOTES_DOC])
    assert [n.id for n in document.notes] == ["id.2", "id"]
    assert report.renamed["note"] == (("", "id.2"),)
    assert document.notes[0].anchor == Anchor.on_set("con_")
    assert repo.get_show(show_id).id == document.show


def test_stored_v1_spot_renames_reach_note_anchors(tmp_path: Path) -> None:
    repo, show_id = _fixture("v0.3.0", tmp_path)
    respelled = {
        path: raw.replace(b'"B1"', b'"brass-2"')
        for path, raw in repo.documents(show_id).items()
        if path.endswith(".json") and b'"B1"' in raw
    }
    notes = [_v1_note("n1", {"kind": "spots", "spots": ["brass-2", "B2"]})]
    _commit_v1(repo, show_id, {**respelled, LEGACY_NOTES_DOC: dump_json(notes)})
    migrated, report = migrate_documents(dict(repo.documents(show_id)))
    assert ("brass-2", "brass_2") in report.renamed["spot"]
    document = NotesDocument.model_validate_json(migrated[NOTES_DOC])
    assert document.notes[0].anchor == Anchor.on_spots(["brass_2", "B2"])


def test_v1_spot_renames_reach_note_anchors() -> None:
    dump = two_set_show().model_dump(mode="json")
    text = json.dumps(dump).replace('"T1"', '"T-1"')
    legacy: dict[str, Any] = json.loads(text)
    legacy["notes"] = [_v1_note("n1", {"kind": "spots", "spots": ["T-1", "T2"]})]
    document = {"schema": "showpy.show.v1", "show": legacy}
    notes = import_notes(document)
    assert import_json(document).cast.ids()[0] == "T_1"
    assert notes.notes[0].anchor == Anchor.on_spots(["T_1", "T2"])


@pytest.mark.parametrize("tag", TAGS)
def test_first_save_writes_notes_in_the_same_revision_and_the_second_none(
    tag: str, tmp_path: Path
) -> None:
    repo, show_id, _ = _with_notes(tag, tmp_path)
    stored = [r.id for r in repo.revisions(show_id)]
    ws = open_workspace(repo, show_id)
    first = save_show(ws, load_show(ws), AT)
    assert first.parent == stored[-1]
    assert {SHOW_DOC, NOTES_DOC, LEGACY_NOTES_DOC} <= set(first.changed)
    after = dict(repo.documents(show_id))
    assert LEGACY_NOTES_DOC not in after
    assert (
        after[NOTES_DOC]
        == migrate_documents(dict(repo.documents(show_id, stored[-1])))[0][NOTES_DOC]
    )
    ws.refresh()
    again = save_show(ws, load_show(ws), AT)
    assert again.id == first.id
    assert [r.id for r in repo.revisions(show_id)] == [*stored, first.id]


def test_put_show_writes_notes_in_the_migration_revision(tmp_path: Path) -> None:
    repo, show_id, _ = _with_notes("v0.3.0", tmp_path)
    head = repo.head(show_id)
    first = repo.put_show(show_id, repo.get_show(show_id), expected_revision=head, at=AT)
    assert NOTES_DOC in first.changed
    assert LEGACY_NOTES_DOC in first.changed
    again = repo.put_show(show_id, repo.get_show(show_id), expected_revision=first.id, at=AT)
    assert again.id == first.id


def test_put_script_writes_notes_in_the_migration_revision(tmp_path: Path) -> None:
    repo, show_id, _ = _with_notes("v0.3.0", tmp_path)
    stored = [r.id for r in repo.revisions(show_id)]
    revision = repo.put_script(show_id, "con_", "# new\n", expected_revision=stored[-1], at=AT)
    log = repo.revisions(show_id)
    assert [r.id for r in log] == [*stored, log[-2].id, revision.id]
    assert NOTES_DOC in log[-2].changed
    assert repo.revisions(show_id)[-1].changed == ("/sets/con_/set.py",)
    assert repo.read(show_id, NOTES_DOC) is not None


def test_reads_on_a_v1_head_see_the_notes_document(tmp_path: Path) -> None:
    repo, show_id, _ = _with_notes("v0.3.0", tmp_path)
    head = repo.head(show_id)
    view = migrated_view(dict(repo.documents(show_id)))
    assert NOTES_DOC in view
    assert LEGACY_NOTES_DOC not in view
    ws = open_workspace(repo, show_id)
    read = ws.read(NOTES_DOC)
    assert isinstance(read, str)
    assert [n.id for n in NotesDocument.model_validate_json(read).notes] == ["n1", "n2"]
    assert "notes.json" in ws.ls("/notes/")
    assert repo.head(show_id) == head


def test_migration_changes_write_the_notes_document_and_drop_the_v1_notes(
    tmp_path: Path,
) -> None:
    repo, show_id, _ = _with_notes("v0.2.0", tmp_path)
    changes = migration_changes(dict(repo.documents(show_id)))
    assert changes[LEGACY_NOTES_DOC] is None
    written = changes[NOTES_DOC]
    assert written is not None
    assert NotesDocument.model_validate_json(written).show == repo.get_show(show_id).id


@pytest.mark.parametrize("tag", TAGS)
def test_a_v1_tree_without_notes_gets_no_notes_document(tag: str, tmp_path: Path) -> None:
    repo, show_id = _fixture(tag, tmp_path)
    migrated, _ = migrate_documents(dict(repo.documents(show_id)))
    assert NOTES_DOC not in migrated
    assert LEGACY_NOTES_DOC not in migrated


def test_v2_show_json_has_no_notes_and_show_has_no_notes_field() -> None:
    assert "notes" not in Show.model_fields
    assert "notes" not in json.loads(encode_show(two_set_show())[SHOW_DOC])
    assert LEGACY_NOTES_DOC not in encode_show(two_set_show())
    with pytest.raises(SchemaError):
        Show.model_validate({**two_set_show().model_dump(mode="json"), "notes": []})


def _note(note_id: str) -> Note:
    return Note(id=NoteId(note_id), text="t", anchor=Anchor.on_set("1"), author="d", created_at=AT)


def test_notes_document_refuses_bad_and_repeated_note_ids() -> None:
    with pytest.raises(SchemaError) as caught:
        _note("note-1")
    assert "note" in caught.value.message
    with pytest.raises(SchemaError):
        _note("")
    with pytest.raises(SchemaError) as repeated:
        NotesDocument(show="show-1", notes=(_note("n1"), _note("N1")))
    assert "N1" in repeated.value.message


def test_regression_newer_notes_document_raises_version_error() -> None:
    with pytest.raises(VersionError) as caught:
        NotesDocument.model_validate({"format": "e8.notes", "version": 2, "show": "show-1"})
    assert caught.value.context["version"] == 2
    with pytest.raises(VersionError):
        NotesDocument.model_validate_json(b'{"format": "e8.notes", "version": 3, "show": "s"}')
    with pytest.raises(SchemaError) as unknown:
        NotesDocument.model_validate({"format": "e8.other", "version": 1, "show": "show-1"})
    assert unknown.value.code == ErrorCode.UNKNOWN_FORMAT


def test_notes_document_header_is_written_first(tmp_path: Path) -> None:
    repo, show_id, _ = _with_notes("v0.1.1", tmp_path)
    migrated, _ = migrate_documents(dict(repo.documents(show_id)))
    assert migrated[NOTES_DOC].splitlines()[:3] == [
        b"{",
        b'  "format": "e8.notes",',
        b'  "version": 1,',
    ]
    exported = export_json(two_set_show(), notes=NotesDocument(show=two_set_show().id))
    assert list(exported["notes"])[:2] == ["format", "version"]


def test_workspace_writes_a_checked_notes_document() -> None:
    show = two_set_show()
    repo = InMemoryRepository()
    repo.put_show(show.id, show, expected_revision="", at=AT)
    ws = open_workspace(repo, show.id)
    raw = dump_json(NotesDocument(show=show.id, notes=(_note("n1"),)).model_dump(mode="json"))
    ws.write(NOTES_DOC, raw, at=AT)
    assert repo.read(show.id, NOTES_DOC) == raw
    assert "notes.json" in ws.ls("/notes/")
    with pytest.raises(SchemaError) as caught:
        ws.write(NOTES_DOC, b'{"format": "e8.notes", "version": 1, "notes": []}', at=AT)
    assert caught.value.context["path"] == NOTES_DOC
    with pytest.raises(UnknownRefError):
        ws.write(LEGACY_NOTES_DOC, b"[]", at=AT)


def test_regression_workspace_refuses_the_notes_of_another_show() -> None:
    show = two_set_show()
    repo = InMemoryRepository()
    repo.put_show(show.id, show, expected_revision="", at=AT)
    ws = open_workspace(repo, show.id)
    raw = dump_json(NotesDocument(show="other-show").model_dump(mode="json"))
    with pytest.raises(SchemaError) as caught:
        ws.write(NOTES_DOC, raw, at=AT)
    assert caught.value.context["show"] == show.id
    assert show.id in caught.value.hint
    assert repo.read(show.id, NOTES_DOC) is None


def test_export_json_returns_the_notes_document_beside_the_show() -> None:
    show = two_set_show()
    document = NotesDocument(show=show.id, notes=(_note("n1"),))
    exported = export_json(show, notes=document)
    assert exported["notes"] == {
        "format": "e8.notes",
        "version": 1,
        "show": show.id,
        "notes": [note.model_dump(mode="json") for note in document.notes],
    }
    assert "notes" not in exported["show"]
    assert import_json(exported) == show
    assert import_notes(exported) == document
    with pytest.raises(SchemaError):
        export_json(show, notes=document.model_copy(update={"show": "other"}))


def test_import_notes_reads_v1_and_v2_documents() -> None:
    show = two_set_show()
    assert import_notes(export_json(show)) == NotesDocument(show=show.id)
    legacy = show.model_dump(mode="json")
    legacy["notes"] = [_v1_note("n-1", {"kind": "set", "set_id": "2"})]
    notes = import_notes({"schema": "showpy.show.v1", "show": legacy})
    assert [n.id for n in notes.notes] == ["n_1"]
    with pytest.raises(SchemaError) as caught:
        import_notes({**export_json(show), "notes": {"format": "e8.notes", "version": 1}})
    assert caught.value.code == ErrorCode.SCHEMA


def test_rename_spots_rewrites_the_notes_document() -> None:
    show = two_set_show()
    document = NotesDocument(
        show=show.id,
        notes=(
            Note(
                id=NoteId("n1"), text="t", anchor=Anchor.on_spots(["T1"]), author="d", created_at=AT
            ),
        ),
    )
    renamed, report = rename_spots(show, {"T1": "S1"}, notes=document)
    assert renamed.cast.ids()[0] == "S1"
    assert report.notes is not None
    assert report.notes.notes[0].anchor == Anchor.on_spots(["S1"])
    _, dropped = rewrite_spots(show, lambda i: None if i == "T1" else i, notes=document)
    assert [n.id for n in dropped.orphaned_notes] == ["n1"]
    assert dropped.notes == NotesDocument(show=show.id)
    with pytest.raises(SchemaError):
        rename_spots(show, {"T1": "S1"}, notes=document.model_copy(update={"show": "other"}))
