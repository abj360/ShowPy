#!/usr/bin/env python3

"""
test_migrate.py --- tests the stored format version, the migration chain and its report

Contains:
    test_version_of_reads_no_header_as_version_1(): treats a show.json without header as v1
    test_version_of_reads_the_current_header(): returns the version of a current header
    test_version_of_refuses_a_newer_version(): raises VersionError naming the version and newest
    test_version_of_refuses_a_bad_header(): raises UNKNOWN_FORMAT for a wrong format, half a
        header, a version 1 header, a boolean or a text version
    test_read_version_parses_show_json_strictly(): refuses a missing show.json, a non-object
        and non-strict JSON
    test_report_builder_groups_renames_by_kind_in_order(): keeps renames per kind in call order
    test_migration_report_is_read_only(): refuses edits to the renamed map
    test_sub_steps_run_in_the_fixed_order(): lists the JSON and model sub-steps in their order
    test_json_steps_never_mutate_their_input(): leaves every fixture's parsed documents unchanged
    test_current_documents_skip_the_json_steps(): returns current documents as they are
    test_validation_after_ids_keeps_accepting_legacy_ids_until_ids_are_renamed(): passes the
        legacy-ID context to the last validation exactly while the ids step keeps v1 IDs
    test_require_current_version_refuses_an_older_show_json(): raises OLD_FORMAT for v1
    test_every_fixture_migrates_to_its_expected_v2(): matches expected_v2.json, documents and
        report, regenerated only under E8S_UPDATE_MIGRATION=1
    test_v1_fixtures_decode_to_their_oracle(): decodes each fixture head equal to expected.json
    test_first_save_after_migration_writes_one_revision_and_the_second_none(): saves v2 once
    test_stored_revision_ids_survive_migration(): keeps every stored revision ID and record
    test_regression_migration_keeps_tags_selection_scripts_and_pins(): carries them through the
        migration and the first save
    test_regression_migration_keeps_a_stored_negative_zero(): keeps the sign of the v0.1.1
        fixture's stored `-0.0` through the in-memory migration
    test_strict_json_refuses_nan_tokens(): refuses NaN and infinity tokens in any document
    test_strict_json_refuses_duplicate_keys(): refuses a repeated key, naming the document
    test_v030_reader_refuses_a_v2_tree(): fails the frozen v1 schema on the v2 header alone
    test_decode_caches_nothing_for_an_older_tree(): never caches documents that get migrated
    test_writes_of_a_v1_show_json_are_refused(): refuses OLD_FORMAT through commit and write
    test_regression_raw_commit_into_a_v1_tree_needs_a_current_show_json(): refuses a commit of
        any show document while show.json stays v1
    test_regression_current_trees_validate_with_the_checked_context(): passes CHECKED_CONTEXT to
        every model a v2 tree or v2 import builds, and refuses a v2 tree holding a v1 ID
"""

import copy
import itertools
import json
import math
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError
from e8s.core.ids import LEGACY_IDS_KEY
from e8s.core.json_types import JsonValue
from e8s.core.resources import schema_path
from e8s.examples import crossing_show, two_set_show
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_reading import decode_show_dump
from e8s.show.documents import (
    LEGACY_NOTES_DOC,
    NOTES_DOC,
    SHEET_DOC,
    TIMELINE_DOC,
    decode_show,
    encode_show,
    forms_doc,
    is_show_doc,
    migrate_documents,
    paths_doc,
    set_doc,
)
from e8s.show.file_repository import FileRepository
from e8s.show.integrity import blob_digest
from e8s.show.interchange import export_json, import_json
from e8s.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    FIRST_VERSION,
    FORMAT,
    LEGACY_CONTEXT,
    SHOW_DOC,
    MigrationReport,
    ReportBuilder,
    format_header,
    migrate_objects,
    read_version,
    require_current_version,
    version_of,
    without_header,
)
from e8s.show.migrations import (
    V1_TO_V2_BESIDE_STEPS,
    V1_TO_V2_JSON_STEPS,
    V1_TO_V2_MODEL_STEPS,
)
from e8s.show.migrations.v1_to_v2_ids import KEEPS_LEGACY_IDS
from e8s.show.models import Show
from e8s.show.storage import load_show, open_workspace, save_show
from tests.helpers import json_doc

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "repos"
TAGS = ["v0.1.1", "v0.2.0", "v0.3.0"]
TAGGED_TAGS = ["v0.1.1", "v0.2.0"]
EXPECTED_V2 = "expected_v2.json"
UPDATE_FLAG = "E8S_UPDATE_MIGRATION"
AT = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
V1_SCHEMA = "showpy.show.v1.json"
JSON_STEP_ORDER = [
    "migrate_forms",
    "migrate_paths",
    "migrate_selectors",
    "migrate_timeline",
    "migrate_facings",
    "migrate_sheet",
    "migrate_cast",
    "migrate_events",
]


def test_version_of_reads_no_header_as_version_1() -> None:
    assert version_of({"id": "show-1", "sets": []}, name=SHOW_DOC) == 1


def test_version_of_reads_the_current_header() -> None:
    assert format_header() == {"format": FORMAT, "version": CURRENT_VERSION}
    assert version_of({**format_header(), "id": "x"}, name=SHOW_DOC) == CURRENT_VERSION


def test_version_of_refuses_a_newer_version() -> None:
    with pytest.raises(VersionError) as caught:
        version_of({"format": FORMAT, "version": CURRENT_VERSION + 1}, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.VERSION
    assert caught.value.context["version"] == CURRENT_VERSION + 1
    assert caught.value.context["newest"] == CURRENT_VERSION
    assert str(CURRENT_VERSION + 1) in str(caught.value)


@pytest.mark.parametrize(
    "header",
    [
        {"format": "stage.show", "version": 2},
        {"format": FORMAT},
        {"version": 2},
        {"format": FORMAT, "version": 1},
        {"format": FORMAT, "version": True},
        {"format": FORMAT, "version": "2"},
        {"format": FORMAT, "version": 2.0},
    ],
)
def test_version_of_refuses_a_bad_header(header: dict[str, object]) -> None:
    with pytest.raises(SchemaError) as caught:
        version_of(header, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.UNKNOWN_FORMAT
    assert caught.value.context["path"] == SHOW_DOC


def test_read_version_parses_show_json_strictly() -> None:
    assert read_version({SHOW_DOC: b'{"id": "x"}'}) == 1
    assert read_version({SHOW_DOC: json.dumps(format_header()).encode()}) == CURRENT_VERSION
    with pytest.raises(StorageError):
        read_version({})
    with pytest.raises(SchemaError):
        read_version({SHOW_DOC: b"[]"})
    with pytest.raises(SchemaError) as caught:
        read_version({SHOW_DOC: b'{"format": "e8.show", "format": "x", "version": 2}'})
    assert caught.value.code == ErrorCode.STRICT_JSON


def test_report_builder_groups_renames_by_kind_in_order() -> None:
    builder = ReportBuilder(1)
    builder.rename("form", "brass-2", "brass_2")
    builder.rename("set", "con", "con_")
    builder.rename("form", "brass-3", "brass_3")
    builder.repair("sorted tempo sections by count")
    report = builder.build()
    assert report == MigrationReport(
        from_version=1,
        renamed={
            "form": (("brass-2", "brass_2"), ("brass-3", "brass_3")),
            "set": (("con", "con_"),),
        },
        repairs=("sorted tempo sections by count",),
    )
    assert json_doc(report)["renamed"]["set"] == [["con", "con_"]]


def test_migration_report_is_read_only() -> None:
    report = ReportBuilder(1).build()
    assert dict(report.renamed) == {}
    with pytest.raises(TypeError):
        report.renamed["spot"] = ()  # type: ignore[index]
    with pytest.raises(SchemaError):
        MigrationReport(from_version=0)


def test_sub_steps_run_in_the_fixed_order() -> None:
    assert [step.__name__ for step in V1_TO_V2_JSON_STEPS] == JSON_STEP_ORDER
    assert [step.__name__ for step in V1_TO_V2_MODEL_STEPS] == ["rename_ids"]
    assert [step.__name__ for step in V1_TO_V2_BESIDE_STEPS] == ["move_notes"]


@pytest.mark.parametrize("tag", TAGS)
def test_json_steps_never_mutate_their_input(tag: str, tmp_path: Path) -> None:
    docs = _head(tag, tmp_path)
    objects: dict[str, JsonValue] = {
        path: json.loads(raw) for path, raw in sorted(docs.items()) if is_show_doc(path)
    }
    snapshot = copy.deepcopy(objects)
    version = read_version(docs)
    migrate_objects(objects, version, ReportBuilder(version))
    assert objects == snapshot


def test_current_documents_skip_the_json_steps() -> None:
    objects: dict[str, JsonValue] = {SHOW_DOC: {"id": "x", "sets": []}}
    assert migrate_objects(objects, CURRENT_VERSION, ReportBuilder(CURRENT_VERSION)) is objects


def test_validation_after_ids_keeps_accepting_legacy_ids_until_ids_are_renamed() -> None:
    assert LEGACY_CONTEXT == {LEGACY_IDS_KEY: True}
    assert (LEGACY_CONTEXT if KEEPS_LEGACY_IDS else None) == CHECKED_CONTEXT


def test_require_current_version_refuses_an_older_show_json() -> None:
    require_current_version({**format_header(), "id": "x"}, name=SHOW_DOC)
    with pytest.raises(SchemaError) as caught:
        require_current_version({"id": "x"}, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.OLD_FORMAT
    assert without_header({**format_header(), "id": "x"}) == {"id": "x"}


def _fixture(tag: str, tmp_path: Path) -> tuple[FileRepository, dict[str, Any]]:
    """Copies a read-only fixture repository and loads its expected.json."""
    shutil.copytree(FIXTURES / tag / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((FIXTURES / tag / "expected.json").read_text())
    return FileRepository(tmp_path / "repo"), expected


def _head(tag: str, tmp_path: Path) -> dict[str, bytes]:
    repo, expected = _fixture(tag, tmp_path)
    return dict(repo.documents(expected["show_id"]))


def _record(docs: dict[str, bytes]) -> dict[str, object]:
    """Lists the migrated show documents and notes document as JSON and the report, as
    expected_v2.json holds them."""
    migrated, report = migrate_documents(docs)
    documents = {
        p: json.loads(raw)
        for p, raw in sorted(migrated.items())
        if is_show_doc(p) or p == NOTES_DOC
    }
    return {"documents": documents, "report": report.to_json()}


@pytest.mark.parametrize("tag", TAGS)
def test_every_fixture_migrates_to_its_expected_v2(tag: str, tmp_path: Path) -> None:
    docs = _head(tag, tmp_path)
    record = _record(docs)
    path = FIXTURES / tag / EXPECTED_V2
    if os.environ.get(UPDATE_FLAG) == "1":
        path.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    assert record == json.loads(path.read_text())
    migrated, _ = migrate_documents(docs)
    assert {p: raw for p, raw in migrated.items() if not is_show_doc(p) and p != NOTES_DOC} == {
        p: raw for p, raw in docs.items() if not is_show_doc(p)
    }
    assert migrated[SHOW_DOC].splitlines()[1:3] == [
        b'  "format": "e8.show",',
        b'  "version": 2,',
    ]
    assert read_version(docs) == 1
    assert read_version(migrated) == CURRENT_VERSION
    assert decode_show(migrated) == decode_show(docs)
    assert migrate_documents(migrated) == (migrated, MigrationReport(from_version=CURRENT_VERSION))


@pytest.mark.parametrize("tag", TAGS)
def test_v1_fixtures_decode_to_their_oracle(tag: str, tmp_path: Path) -> None:
    repo, expected = _fixture(tag, tmp_path)
    assert [r.id for r in repo.revisions(expected["show_id"])] == expected["revisions"]
    oracle = decode_show_dump(expected["show"], FIRST_VERSION)
    assert repo.get_show(expected["show_id"]) == oracle


@pytest.mark.parametrize("tag", TAGS)
def test_first_save_after_migration_writes_one_revision_and_the_second_none(
    tag: str, tmp_path: Path
) -> None:
    repo, expected = _fixture(tag, tmp_path)
    ws = open_workspace(repo, expected["show_id"])
    first = save_show(ws, load_show(ws), AT)
    assert first.parent == expected["head"]
    assert SHOW_DOC in first.changed
    assert (
        repo.documents(expected["show_id"])[SHOW_DOC]
        == migrate_documents(_head(tag, tmp_path / "again"))[0][SHOW_DOC]
    )
    ws.refresh()
    again = save_show(ws, load_show(ws), AT)
    assert again.id == first.id
    assert [r.id for r in repo.revisions(expected["show_id"])] == [*expected["revisions"], first.id]


@pytest.mark.parametrize("tag", TAGS)
def test_stored_revision_ids_survive_migration(tag: str, tmp_path: Path) -> None:
    repo, expected = _fixture(tag, tmp_path)
    show_id = expected["show_id"]
    before = {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()}
    old_shows = [repo.get_show(show_id, revision) for revision in expected["revisions"]]
    assert {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()} == before
    ws = open_workspace(repo, show_id)
    save_show(ws, load_show(ws), AT)
    after = {p: p.read_bytes() for p in (tmp_path / "repo").rglob("*") if p.is_file()}
    assert {p: after[p] for p in before} == before
    assert [r.id for r in repo.revisions(show_id)][: len(expected["revisions"])] == expected[
        "revisions"
    ]
    assert [repo.get_show(show_id, r) for r in expected["revisions"]] == old_shows


@pytest.mark.parametrize("tag", TAGGED_TAGS)
def test_regression_migration_keeps_tags_selection_scripts_and_pins(
    tag: str, tmp_path: Path
) -> None:
    repo, expected = _fixture(tag, tmp_path)
    docs = dict(repo.documents(expected["show_id"]))
    migrated, _ = migrate_documents(docs)
    ws = open_workspace(repo, expected["show_id"])
    saved = save_show(ws, load_show(ws), AT)
    first_spots = [s.id for s in decode_show(docs).cast.spots][:4]
    for show in (decode_show(migrated), repo.get_show(expected["show_id"], saved.id)):
        assert [s.tags for s in show.cast.spots if s.tags] == [("horn", "lead")]
        assert dict(show.cast.selections) == {"front_four": tuple(first_spots)}
        assert [(p.target, p.reason) for p in show.pins] == [("entity", "soloist")]
    scripts = {p: raw for p, raw in docs.items() if p.endswith("/set.py")}
    assert scripts
    assert {p: migrated[p] for p in scripts} == scripts
    assert {p: repo.documents(expected["show_id"])[p] for p in scripts} == scripts


def test_regression_migration_keeps_a_stored_negative_zero(tmp_path: Path) -> None:
    """Keeps the sign of the `-0.0` v0.1.1 stored for set 1's first placement."""
    docs = _head("v0.1.1", tmp_path)
    assert b"-0.0" in docs[set_doc("1")]
    first = decode_show(docs).set("1").placements[0]
    assert first.x == 0.0
    assert math.copysign(1.0, first.x) == -1.0


@pytest.mark.parametrize("path", [SHOW_DOC, set_doc("1"), TIMELINE_DOC])
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_refuses_nan_tokens(path: str, token: str, tmp_path: Path) -> None:
    docs = _head("v0.2.0", tmp_path)
    document = json.loads(docs[path])
    document["x_extra"] = 1.5
    docs[path] = json.dumps(document).replace("1.5", token).encode()
    with pytest.raises(SchemaError) as caught:
        decode_show(docs)
    assert caught.value.code == ErrorCode.STRICT_JSON
    assert caught.value.context["path"] == path
    with pytest.raises(SchemaError):
        migrate_documents(docs)


def test_strict_json_refuses_duplicate_keys() -> None:
    docs = encode_show(two_set_show())
    docs[SHOW_DOC] = docs[SHOW_DOC].replace(b'  "title"', b'  "title": "Twice",\n  "title"', 1)
    with pytest.raises(SchemaError) as caught:
        decode_show(docs)
    assert caught.value.code == ErrorCode.STRICT_JSON
    assert caught.value.context["path"] == SHOW_DOC
    assert caught.value.context["key"] == "title"


def _assembled_as_v030_did(tree: dict[str, bytes]) -> dict[str, Any]:
    """Assembles a stored tree into one Show dump the way the v0.3.0 decoder did."""
    meta = json.loads(tree[SHOW_DOC])
    order: list[str] = meta.pop("sets")
    sets = [{"key": key, **json.loads(tree[set_doc(key)])} for key in order]
    forms = {
        form["id"]: form for key in order for form in json.loads(tree.get(forms_doc(key), b"[]"))
    }
    pairs = [pair for pair in itertools.pairwise(order) if paths_doc(pair) in tree]
    return {
        **meta,
        "sheet": json.loads(tree[SHEET_DOC]),
        "notes": json.loads(tree.get(LEGACY_NOTES_DOC, b"[]")),
        "timeline": json.loads(tree[TIMELINE_DOC]),
        "sets": sets,
        "forms": forms,
        "transitions": [json.loads(tree[paths_doc(pair)]) for pair in pairs],
        "revision": "",
    }


def test_v030_reader_refuses_a_v2_tree(tmp_path: Path) -> None:
    schema = json.loads(schema_path(V1_SCHEMA).read_text())
    validator = validator_for(schema)(schema)
    docs = _head("v0.3.0", tmp_path)
    assert list(validator.iter_errors(_assembled_as_v030_did(docs))) == []
    migrated, _ = migrate_documents(docs)
    errors = [error.message for error in validator.iter_errors(_assembled_as_v030_did(migrated))]
    assert any("'format'" in message and "'version'" in message for message in errors)


def test_decode_caches_nothing_for_an_older_tree(tmp_path: Path) -> None:
    docs = _head("v0.2.0", tmp_path)
    digests = {path: blob_digest(raw) for path, raw in docs.items()}
    cache = DecodeCache()
    decode_show(docs, cache=cache, digests=digests)
    assert len(cache) == 0
    migrated, _ = migrate_documents(docs)
    decode_show(migrated, cache=cache, digests={p: blob_digest(r) for p, r in migrated.items()})
    assert len(cache) > 0


def test_writes_of_a_v1_show_json_are_refused(tmp_path: Path) -> None:
    repo, expected = _fixture("v0.2.0", tmp_path)
    ws = open_workspace(repo, expected["show_id"])
    save_show(ws, load_show(ws), AT)
    ws.refresh()
    v1_meta = without_header(json.loads(ws.read(SHOW_DOC)))
    head = repo.head(expected["show_id"])
    raw = json.dumps(v1_meta)
    with pytest.raises(SchemaError) as committed:
        ws.commit({SHOW_DOC: raw.encode()}, at=AT)
    with pytest.raises(SchemaError) as written:
        ws.write(SHOW_DOC, raw, at=AT)
    assert committed.value.code == written.value.code == ErrorCode.OLD_FORMAT
    assert repo.head(expected["show_id"]) == head


def test_regression_current_trees_validate_with_the_checked_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hands CHECKED_CONTEXT to every model of a v2 decode and a v2 import.

    The ids step renames every v1 ID, so a v2 tree holding one such as `T-1` was never written by
    a save and is refused; the checked context lets nothing through.
    """
    docs = encode_show(crossing_show())
    exported = export_json(decode_show(docs))
    legacy = {p: raw.replace(b'"T1"', b'"T-1"') for p, raw in docs.items()}
    with pytest.raises(SchemaError):
        decode_show(legacy)
    with pytest.raises(SchemaError):
        import_json(json.loads(json.dumps(exported).replace('"T1"', '"T-1"')))
    contexts: list[object] = []
    for model in (Show, Set, Form, TransitionSpec):
        original = model._validated

        def spy(obj: object, *, _original: Any = original, **kwargs: Any) -> object:
            contexts.append(kwargs.get("context"))
            return _original(obj, **kwargs)

        monkeypatch.setattr(model, "_validated", spy)
    assert decode_show(docs) == import_json(exported) == crossing_show()
    assert contexts
    assert all(context == CHECKED_CONTEXT for context in contexts)


def test_regression_raw_commit_into_a_v1_tree_needs_a_current_show_json(tmp_path: Path) -> None:
    """Refuses a repository commit of set.json alone into a never-saved v1 tree.

    show.json versions the whole tree, so a current document beside a v1 show.json would be
    read as v1; the commit must bring a current show.json or go through save_show().
    """
    repo, expected = _fixture("v0.2.0", tmp_path)
    show_id = expected["show_id"]
    raw = repo.documents(show_id)["/sets/2/set.json"]
    edited = raw.replace(b'"label": null', b'"label": "Opening"', 1)
    assert edited != raw
    with pytest.raises(SchemaError) as caught:
        repo.transaction(
            show_id, {"/sets/2/set.json": edited}, expected_revision=expected["head"], at=AT
        )
    assert caught.value.code == ErrorCode.OLD_FORMAT
    assert "save_show" in (caught.value.hint or "")
    assert repo.head(show_id) == expected["head"]
