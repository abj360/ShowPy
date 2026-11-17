#!/usr/bin/env python3

"""
test_set_keys.py --- tests stable set keys: assignment, key-addressed storage and path mapping

Contains:
    test_set_key_defaults_to_the_id_and_is_written_only_when_it_differs: fills the key from the
        ID, keeps it across replace and dumps it only when it differs from the ID
    test_regression_set_key_must_pass_the_id_check: refuses keys outside the ADR 0006 pattern
    test_regression_show_refuses_set_keys_that_repeat_ignoring_case: refuses two sets whose keys
        differ only by case
    test_renumber_keeps_keys: renames display IDs and leaves every key alone
    test_regression_add_set_after_renumber_picks_a_free_key: gives a new set `2` the key `2.2`
        while a renumbered set still holds key `2`
    test_regression_insert_subset_after_renumber_picks_a_free_key: gives a new sub-set `1A` the
        key `1A.2` while key `1A` is live
    test_regression_import_positions_new_set_picks_a_free_key: gives an imported set a free key
    test_regression_never_renumbered_v020_show_encodes_to_its_stored_documents: re-encodes the
        v0.2.0 fixture head to the same paths and canonical bytes, with no `key` field
    test_v020_head_documents_redump_to_their_stored_bytes: checks the fixture head is already
        canonical; ADR 0014 (task A7) updates this test when it normalizes `-0.0`
    test_encode_after_renumber_addresses_sets_by_key: writes folders, set order and transition
        folders by key and decodes back to the same show
    test_regression_decode_defaults_a_missing_key_to_the_folder_name: reads a set.json without a
        key as keyed by its folder
    test_regression_set_json_keyed_for_another_folder_is_refused: refuses a set.json whose key
        names another folder
    test_regression_forms_set_check_runs_outside_the_decode_cache: refuses forms that no longer
        name their set's ID, with a warm cache and without one
    test_regression_paths_transition_check_runs_outside_the_decode_cache: refuses paths that no
        longer name their sets' IDs, with a warm cache and without one
    test_regression_orphan_check_addresses_sets_by_key: refuses a show document under a display
        ID that is not a key
    test_workspace_reads_and_lists_sets_by_display_id: reads scripts and lists folders by display
        ID after a renumber
    test_workspace_writes_and_commits_through_the_key: lands writes and commits addressed by
        display ID in the key folders
    test_regression_workspace_refuses_paths_that_alias_another_sets_key: refuses a commit or write
        whose folder names no set but is another set's key, also ignoring case
    test_repository_scripts_follow_the_set_key: reads and writes scripts by display ID
    test_revert_after_renumber_restores_the_earlier_tree: commits stored paths back unchanged
    test_display_and_stored_paths_round_trip: maps set and transition paths both ways
    test_regression_stored_folder_aliasing_a_display_id_is_refused: refuses to show a stored folder
        that is no key under another set's display ID
    test_regression_v020_repository_renumber_keeps_its_script: keeps the fixture's build script
        with its set across insert, renumber and save
    test_regression_join_sections_rekeys_a_tail_set_whose_key_clashes: gives a joined set whose
        key the first section holds a free key and keeps every other key
    test_regression_legacy_set_ids_outside_the_id_pattern_load_and_round_trip: loads a tree
        whose set folders are IDs v0.2 accepted but ADR 0006 refuses, with no loss
    test_regression_constructors_refuse_a_reserved_key: refuses `con` as a key from add_set and
        insert_subset's rule, and as an explicit key stored in set.json
    test_regression_put_set_keeps_the_stored_key: keeps a renumbered set's folder and script when
        put_set gets a freshly built set, and refuses a mismatching explicit key
    test_regression_workspace_has_no_public_stored_commit: keeps stored-path commits private
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from showpy.choreo.forms import line_form
from showpy.choreo.models import PathRow, Set, TransitionSpec
from showpy.core.errors import SchemaError, ShowPyError, StorageError
from showpy.examples import two_set_show
from showpy.export.positions import import_positions
from showpy.show.decode_cache import DecodeCache
from showpy.show.documents import (
    SHOW_DOC,
    decode_show,
    dump_json,
    encode_show,
    forms_doc,
    is_show_doc,
    set_doc,
)
from showpy.show.file_repository import FileRepository
from showpy.show.history import revert
from showpy.show.integrity import blob_digest, require_valid_digests, require_valid_tree
from showpy.show.manage import add_set, insert_subset, remove_set, renumber
from showpy.show.models import Show
from showpy.show.repositories import InMemoryRepository
from showpy.show.sections import join_sections
from showpy.show.set_keys import to_display_path, to_stored_path
from showpy.show.storage import Workspace, load_show, open_workspace, save_show
from showpy.show.validate import validate_show

AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
SHOW_ID = "keys"
SCRIPT_ONE_A = b"# script of 1A\n"
SCRIPT_TWO = b"# script of 2\n"
SUBSET_COUNTS = 2.0
LAST_SET_COUNTS = 6.0
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.2.0"
FIXTURE_SCRIPT = "/sets/2/set.py"


def _renumbered() -> Show:
    """Builds sets 1, 2, 3 keyed 1, 1A, 2: a sub-set inserted after 1, then a renumber."""
    inserted, _ = insert_subset(two_set_show(), "1", SUBSET_COUNTS)
    return renumber(inserted)[0]


def _saved_renumbered() -> tuple[InMemoryRepository, Workspace, str]:
    """Saves the inserted show with two scripts, then the renumbered show on top."""
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    inserted, _ = insert_subset(two_set_show(), "1", SUBSET_COUNTS)
    save_show(ws, inserted, AT)
    scripted = ws.commit({"/sets/1A/set.py": SCRIPT_ONE_A, "/sets/2/set.py": SCRIPT_TWO}, at=AT)
    save_show(ws, renumber(load_show(ws))[0], LATER)
    return repo, ws, scripted.id


def _keys(show: Show) -> list[str | None]:
    return [s.key for s in show.sets]


def _fixture_tree(tmp_path: Path) -> tuple[FileRepository, str, dict[str, bytes]]:
    expected: dict[str, Any] = json.loads((FIXTURE / "expected.json").read_text())
    shutil.copytree(FIXTURE / "repo", tmp_path / "repo")
    repo = FileRepository(tmp_path / "repo")
    show_id = expected["show_id"]
    tree = {p: repo.read(show_id, p) or b"" for p in repo.paths(show_id)}
    return repo, show_id, tree


def test_set_key_defaults_to_the_id_and_is_written_only_when_it_differs() -> None:
    fresh = Set(id="2", counts=1.0)
    moved = fresh.replace(id="3")
    assert fresh.key == "2"
    assert "key" not in fresh.model_dump()
    assert moved.key == "2"
    assert moved.model_dump()["key"] == "2"
    assert Set.model_validate(moved.model_dump()) == moved
    assert Set.model_validate(fresh.model_dump()) == fresh


@pytest.mark.parametrize("key", ["a-b", "con", "x" * 65, "1.", "", "all"])
def test_regression_set_key_must_pass_the_id_check(key: str) -> None:
    with pytest.raises(SchemaError):
        Set(id="1", counts=0.0, key=key)


def test_regression_show_refuses_set_keys_that_repeat_ignoring_case() -> None:
    show = two_set_show()
    first, second = show.sets
    with pytest.raises(SchemaError):
        show.replace(sets=(first.replace(key="a"), second.replace(key="A")))
    with pytest.raises(SchemaError):
        show.replace(sets=(first, second.replace(key="1")))


def test_renumber_keeps_keys() -> None:
    show = _renumbered()
    assert show.set_ids() == ["1", "2", "3"]
    assert _keys(show) == ["1", "1A", "2"]


def test_regression_add_set_after_renumber_picks_a_free_key() -> None:
    show = remove_set(_renumbered(), "2")
    assert _keys(show) == ["1", "2"]
    added = add_set(show, "2", SUBSET_COUNTS, after="1")
    assert added.set_ids() == ["1", "2", "3"]
    assert added.set("2").key == "2.2"
    assert added.set("3").key == "2"


def test_regression_insert_subset_after_renumber_picks_a_free_key() -> None:
    show, new_id = insert_subset(_renumbered(), "1", 1.0)
    assert new_id == "1A"
    assert show.set("1A").key == "1A.2"


def test_regression_import_positions_new_set_picks_a_free_key() -> None:
    imported = import_positions(_renumbered(), "set,counts,entity,x,y\n1A,4,T1,1,1\n", "trumpets")
    assert imported.set("1A").key == "1A.2"
    assert imported.set("2").key == "1A"


def test_regression_never_renumbered_v020_show_encodes_to_its_stored_documents(
    tmp_path: Path,
) -> None:
    _, _, tree = _fixture_tree(tmp_path)
    stored = {p: raw for p, raw in tree.items() if is_show_doc(p)}
    encoded = encode_show(decode_show(tree))
    assert set(encoded) == set(stored)
    assert {p: encoded[p] for p in stored} == {
        p: dump_json(json.loads(raw)) for p, raw in stored.items()
    }
    assert not [p for p in encoded if p.endswith("/set.json") and "key" in json.loads(encoded[p])]


def test_v020_head_documents_redump_to_their_stored_bytes(tmp_path: Path) -> None:
    """Pins today's canonical form; task A7 updates this when ADR 0014 normalizes `-0.0`."""
    _, _, tree = _fixture_tree(tmp_path)
    stored = {p: raw for p, raw in tree.items() if is_show_doc(p)}
    assert {p: dump_json(json.loads(raw)) for p, raw in stored.items()} == stored


def _with_transition(show: Show) -> Show:
    spec = TransitionSpec(
        id=("2", "3"), counts=LAST_SET_COUNTS, rows={"T1": PathRow(entity="T1", start=1)}
    )
    return show.replace(transitions=(spec,))


def test_encode_after_renumber_addresses_sets_by_key() -> None:
    show = _with_transition(_renumbered())
    docs = encode_show(show)
    assert json.loads(docs[SHOW_DOC])["sets"] == ["1", "1A", "2"]
    assert {p for p in docs if p.startswith("/sets/")} == {
        "/sets/1/set.json",
        "/sets/1A/set.json",
        "/sets/2/set.json",
    }
    assert "/transitions/1A-2/paths.json" in docs
    assert json.loads(docs["/sets/1A/set.json"])["key"] == "1A"
    assert "key" not in json.loads(docs["/sets/1/set.json"])
    assert decode_show(docs) == show


def test_regression_decode_defaults_a_missing_key_to_the_folder_name() -> None:
    docs = encode_show(_renumbered())
    body = json.loads(docs["/sets/1A/set.json"])
    del body["key"]
    decoded = decode_show({**docs, "/sets/1A/set.json": dump_json(body)})
    assert decoded.set("2").key == "1A"
    assert decoded == _renumbered()


def test_regression_set_json_keyed_for_another_folder_is_refused() -> None:
    docs = encode_show(_renumbered())
    body = json.loads(docs["/sets/1A/set.json"]) | {"key": "zz"}
    with pytest.raises(SchemaError):
        decode_show({**docs, "/sets/1A/set.json": dump_json(body)})


def _validate(
    before: dict[str, bytes], after: dict[str, bytes], cache: DecodeCache | None
) -> Show | None:
    by_digest = {blob_digest(raw): raw for raw in after.values()}
    return require_valid_digests(
        {p: blob_digest(raw) for p, raw in before.items()},
        {p: blob_digest(raw) for p, raw in after.items()},
        by_digest.__getitem__,
        cache,
    )


def test_regression_forms_set_check_runs_outside_the_decode_cache() -> None:
    show = _renumbered()
    form = line_form("f", ["T1", "T2"], (0.0, 0.0), (4.0, 0.0), set_id="2")
    docs = {**encode_show(show), forms_doc("1A"): dump_json([form.model_dump(mode="python")])}
    cache = DecodeCache()
    _validate({}, docs, cache)
    body = json.loads(docs[set_doc("1A")]) | {"id": "9"}
    relabeled = {**docs, set_doc("1A"): dump_json(body)}
    for used in (cache, None):
        with pytest.raises(SchemaError):
            _validate(docs, relabeled, used)


def test_regression_paths_transition_check_runs_outside_the_decode_cache() -> None:
    docs = encode_show(_with_transition(_renumbered()))
    cache = DecodeCache()
    _validate({}, docs, cache)
    body = json.loads(docs[set_doc("2")]) | {"id": "9"}
    relabeled = {**docs, set_doc("2"): dump_json(body)}
    for used in (cache, None):
        with pytest.raises(SchemaError):
            _validate(docs, relabeled, used)


def test_regression_orphan_check_addresses_sets_by_key() -> None:
    docs = encode_show(_renumbered())
    with pytest.raises(SchemaError) as caught:
        require_valid_tree(docs, {**docs, "/sets/3/forms.json": b"[]"})
    assert "/sets/3/forms.json" in str(caught.value.context)


def test_workspace_reads_and_lists_sets_by_display_id() -> None:
    repo, ws, _ = _saved_renumbered()
    assert ws.read("/sets/2/set.py") == SCRIPT_ONE_A.decode()
    assert ws.read("/sets/3/set.py") == SCRIPT_TWO.decode()
    assert ws.ls("/sets") == ["1/", "2/", "3/"]
    assert ws.ls("/sets/2") == ["forms.json", "placements.csv", "set.py"]
    assert ws.ls("/sets/1") == ["forms.json", "placements.csv"]
    assert ws.ls("/transitions") == ["1-2/", "2-3/"]
    assert not [p for p in repo.paths(SHOW_ID) if p.startswith("/sets/3/")]


def test_workspace_writes_and_commits_through_the_key() -> None:
    repo, ws, _ = _saved_renumbered()
    ws.write("/sets/3/set.py", "# new 3\n", at=LATER)
    assert repo.read(SHOW_ID, "/sets/2/set.py") == b"# new 3\n"
    ws.commit({"/sets/2/set.py": b"# new 2\n"}, at=LATER)
    assert repo.read(SHOW_ID, "/sets/1A/set.py") == b"# new 2\n"
    form = line_form("f", ["T1", "T2"], (0.0, 0.0), (4.0, 0.0), set_id="2")
    ws.write("/sets/2/forms.json", dump_json([form.model_dump(mode="python")]), at=LATER)
    assert repo.read(SHOW_ID, "/sets/1A/forms.json") is not None
    spec = TransitionSpec(
        id=("2", "3"), counts=LAST_SET_COUNTS, rows={"T1": PathRow(entity="T1", end=4)}
    )
    ws.write("/transitions/2-3/paths.json", spec.model_dump_json(), at=LATER)
    assert "/transitions/1A-2/paths.json" in repo.paths(SHOW_ID)
    assert load_show(ws).transition(("2", "3")) == spec
    assert json.loads(ws.read("/transitions/2-3/paths.json"))["rows"]["T1"]["end"] == 4


@pytest.mark.parametrize(
    "path", ["/sets/1A/set.py", "/sets/1a/set.py", "/transitions/1-1A/paths.json"]
)
def test_regression_workspace_refuses_paths_that_alias_another_sets_key(path: str) -> None:
    repo, ws, _ = _saved_renumbered()
    before = {p: repo.read(SHOW_ID, p) for p in repo.paths(SHOW_ID)}
    with pytest.raises(StorageError) as caught:
        ws.commit({path: b"# not mine\n"}, at=LATER)
    assert caught.value.code == "AMBIGUOUS_PATH"
    with pytest.raises(ShowPyError):
        ws.write(path, "# not mine\n", at=LATER)
    assert {p: repo.read(SHOW_ID, p) for p in repo.paths(SHOW_ID)} == before


def test_repository_scripts_follow_the_set_key() -> None:
    repo, ws, _ = _saved_renumbered()
    assert repo.get_script(SHOW_ID, "2") == SCRIPT_ONE_A.decode()
    repo.put_script(SHOW_ID, "3", "# put 3\n", expected_revision=ws.revision, at=LATER)
    assert repo.read(SHOW_ID, "/sets/2/set.py") == b"# put 3\n"


def test_revert_after_renumber_restores_the_earlier_tree() -> None:
    repo, ws, scripted = _saved_renumbered()
    revert(ws, scripted, LATER)
    assert load_show(ws).set_ids() == ["1", "1A", "2"]
    assert ws.read("/sets/1A/set.py") == SCRIPT_ONE_A.decode()
    assert repo.paths(SHOW_ID, scripted) == repo.paths(SHOW_ID)


@pytest.mark.parametrize(
    ("display", "stored"),
    [
        ("/sets/2/set.py", "/sets/1A/set.py"),
        ("/sets/3/forms.json", "/sets/2/forms.json"),
        ("/sets/1/placements.csv", "/sets/1/placements.csv"),
        ("/transitions/2-3/paths.json", "/transitions/1A-2/paths.json"),
        ("/sets/9/set.py", "/sets/9/set.py"),
        ("/renders/2.png", "/renders/2.png"),
        ("/show/show.json", "/show/show.json"),
        ("/transitions/12/paths.json", "/transitions/12/paths.json"),
    ],
)
def test_display_and_stored_paths_round_trip(display: str, stored: str) -> None:
    show = _renumbered()
    assert to_stored_path(display, show) == stored
    assert to_display_path(stored, show) == display
    assert to_stored_path(display, None) == display


def test_regression_stored_folder_aliasing_a_display_id_is_refused() -> None:
    with pytest.raises(StorageError) as caught:
        to_display_path("/sets/3/set.py", _renumbered())
    assert caught.value.code == "AMBIGUOUS_PATH"


def test_regression_v020_repository_renumber_keeps_its_script(tmp_path: Path) -> None:
    repo, show_id, tree = _fixture_tree(tmp_path)
    ws = open_workspace(repo, show_id)
    inserted, _ = insert_subset(load_show(ws), "1", 1.0, force=True)
    save_show(ws, renumber(inserted)[0], AT)
    ws.refresh()
    assert ws.read("/sets/3/set.py") == tree[FIXTURE_SCRIPT].decode()
    assert repo.read(show_id, FIXTURE_SCRIPT) == tree[FIXTURE_SCRIPT]


def test_regression_join_sections_rekeys_a_tail_set_whose_key_clashes() -> None:
    first = two_set_show()
    second = renumber(add_set(two_set_show(), "3", 4.0))[0]
    relabelled = second.replace(sets=tuple(s.replace(id=f"B{s.id}") for s in second.sets))
    joined = join_sections(first, relabelled)
    assert joined.set_ids() == ["1", "2", "B1", "B2", "B3"]
    assert _keys(joined) == ["1", "2", "B1", "B2", "3"]


LEGACY_LONG_ID = "L" * 65


def _legacy_show(first_id: str, second_id: str) -> Show:
    """Builds the example show with set IDs v0.2 stored but ADR 0006 refuses as new keys."""
    show = two_set_show()
    renamed = tuple(
        Set.model_validate({**s.model_dump(), "id": new_id})
        for s, new_id in zip(show.sets, (first_id, second_id), strict=True)
    )
    return show.replace(sets=renamed, transitions=(), forms={})


@pytest.mark.parametrize(("first_id", "second_id"), [("con", LEGACY_LONG_ID), ("1.", "aux.txt")])
def test_regression_legacy_set_ids_outside_the_id_pattern_load_and_round_trip(
    first_id: str, second_id: str
) -> None:
    show = _legacy_show(first_id, second_id)
    docs = encode_show(show)
    assert f"/sets/{first_id}/set.json" in docs
    assert f"/sets/{second_id}/set.json" in docs
    assert decode_show(docs) == show
    repo = InMemoryRepository()
    repo.transaction(SHOW_ID, docs, expected_revision="", at=AT)
    ws = open_workspace(repo, SHOW_ID)
    assert load_show(ws).replace(revision="") == show
    ws.write(f"/sets/{second_id}/set.py", "# legacy\n", at=LATER)
    assert repo.read(SHOW_ID, f"/sets/{second_id}/set.py") == b"# legacy\n"
    assert encode_show(load_show(ws)) == docs


def test_regression_constructors_refuse_a_reserved_key() -> None:
    with pytest.raises(SchemaError):
        add_set(two_set_show(), "con", 4.0)
    with pytest.raises(SchemaError):
        Set(id="1", counts=0.0, key="con")
    docs = encode_show(_legacy_show("con", "2"))
    body = json.loads(docs["/sets/con/set.json"]) | {"key": "con"}
    with pytest.raises(SchemaError):
        decode_show({**docs, "/sets/con/set.json": dump_json(body)})


def _put_set_repo() -> tuple[InMemoryRepository, Workspace]:
    """Saves 1, 1A, 2 with a script on 1A, then removes 1 and renumbers: sets 1, 2 keyed 1A, 2."""
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    inserted, _ = insert_subset(two_set_show(), "1", SUBSET_COUNTS)
    save_show(ws, inserted, AT)
    ws.commit({"/sets/1A/set.py": SCRIPT_ONE_A}, at=AT)
    save_show(ws, renumber(remove_set(load_show(ws), "1"))[0], LATER)
    return repo, ws


def test_regression_put_set_keeps_the_stored_key() -> None:
    repo, ws = _put_set_repo()
    current = load_show(ws).set("1")
    assert current.key == "1A"
    fresh = Set(id="1", counts=0.0, placements=current.placements, label="fresh")
    revision = repo.put_set(SHOW_ID, fresh, expected_revision=ws.revision, at=LATER)
    assert repo.get_set(SHOW_ID, "1").key == "1A"
    assert repo.get_set(SHOW_ID, "1").label == "fresh"
    assert repo.get_script(SHOW_ID, "1") == SCRIPT_ONE_A.decode()
    assert validate_show(repo.get_show(SHOW_ID)).ok
    with pytest.raises(SchemaError):
        repo.put_set(SHOW_ID, fresh.replace(key="zz"), expected_revision=revision.id, at=LATER)


def test_regression_workspace_has_no_public_stored_commit() -> None:
    assert not hasattr(Workspace, "commit_stored")
