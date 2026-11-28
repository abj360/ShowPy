#!/usr/bin/env python3

"""
test_set_keys.py --- tests stable set keys: assignment, key-addressed storage and path mapping

Contains:
    test_set_key_defaults_to_the_id_and_is_written_only_when_it_differs: fills the key from the
        ID, keeps it across replace and dumps it only when it differs from the ID
    test_regression_set_key_must_pass_the_id_check: refuses keys outside the ADR 0006 pattern
    test_regression_show_refuses_set_keys_that_repeat_ignoring_case: refuses two sets whose keys
        differ only by case
    test_regression_set_without_a_key_must_pass_the_id_check: refuses a set whose ID, its
        defaulted key, breaks the ID check, unless validated with legacy IDs accepted
    test_regression_show_refuses_ids_as_keys_that_repeat_ignoring_case: refuses two sets whose
        defaulted keys differ only by case, unless validated with legacy IDs accepted
    test_renumber_keeps_keys: renames display IDs and leaves every key alone
    test_regression_add_set_after_renumber_picks_a_free_key: gives a new set `2` the key `2.2`
        while a renumbered set still holds key `2`
    test_regression_insert_subset_after_renumber_picks_a_free_key: gives a new sub-set `1A` the
        key `1A.2` while key `1A` is live
    test_regression_import_positions_new_set_picks_a_free_key: gives an imported set a free key
    test_regression_never_renumbered_v020_show_encodes_to_its_stored_documents: re-encodes the
        v0.2.0 fixture head to the same paths and canonical bytes, its 16 v1 IDs renamed, with
        no `key` field
    test_v020_head_documents_redump_to_their_stored_bytes: re-dumps the fixture head to its stored
        bytes except the documents ADR 0014 normalizes (`-0.0` or unsorted keys), found by scan
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
        whose set folders are IDs v0.2 accepted but ADR 0006 refuses, renamed and with no loss
    test_regression_constructors_refuse_a_reserved_key: refuses `con` as a key from add_set and
        insert_subset's rule, and as an explicit key stored in set.json
    test_regression_workspace_has_no_public_stored_commit: keeps stored-path commits private
    test_regression_new_set_folder_must_pass_the_id_check: refuses a set whose folder fails
        check_id in the model, put_show, save_show and a raw commit, leaving the repository
        unchanged
    test_regression_stored_legacy_folders_keep_saving: edits, saves and reloads a tree that
        held /sets/con/ under its migrated `con_`, and renumbers a legacy show without creating a
        folder
    test_regression_dashed_set_folder_commit_is_refused: refuses a raw commit under /sets/a-b/
        with an E8sError and stores nothing
    test_regression_new_set_key_must_be_nfc: refuses a new folder whose key NFC changes
    test_regression_v020_show_with_set_ids_differing_by_case_loads: decodes stored v0.2 documents
        whose set folders differ only by case as `1a`, `1A.2` and round-trips them
    test_regression_v020_case_pair_show_adds_a_set_with_a_free_key: adds a set whose ID matches a
        migrated key ignoring case under a free key, then saves and reloads it
    test_regression_v020_case_pair_show_renumbers_once_migrated: renumbers a migrated v0.2 case
        pair show, keeping its keys, and saves it; Phase 1 refused this renumber
    test_regression_new_set_folders_differing_by_case_are_refused: refuses a commit adding a set
        folder that matches another folder ignoring case, storing nothing
"""

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from e8s.choreo.forms import line_form
from e8s.choreo.models import PlacementPatch, Set
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.core.errors import E8sError, SchemaError, StorageError
from e8s.core.ids import LEGACY_IDS_KEY, SetId, SpotId, make_tid
from e8s.core.util import content_hash
from e8s.examples import two_set_show
from e8s.show.decode_cache import DecodeCache
from e8s.show.documents import (
    LEGACY_NOTES_DOC,
    SHEET_DOC,
    SHOW_DOC,
    decode_show,
    dump_json,
    dump_show_doc,
    encode_show,
    forms_doc,
    is_show_doc,
    migrate_documents,
    set_doc,
)
from e8s.show.file_repository import FileRepository
from e8s.show.history import revert
from e8s.show.history_models import Revision
from e8s.show.integrity import (
    blob_digest,
    require_new_set_keys,
    require_valid_digests,
    require_valid_tree,
)
from e8s.show.interchange import import_positions
from e8s.show.manage import add_set, insert_subset, remove_set
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.migrate import ReportBuilder, format_header, without_header
from e8s.show.migrations.v1_to_v2_forms import migrate_forms
from e8s.show.models import Show
from e8s.show.renumber import renumber
from e8s.show.sections import join_sections
from e8s.show.set_keys import to_display_path, to_stored_path
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.layout1 import layout1_documents, role_split_documents, upgraded_copy
from tests.helpers import json_doc

AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LEGACY = {LEGACY_IDS_KEY: True}
LATER = datetime(2026, 11, 16, 9, 5, tzinfo=UTC)
SHOW_ID = "keys"
SCRIPT_ONE_A = b"# script of 1A\n"
SCRIPT_TWO = b"# script of 2\n"
SUBSET_COUNTS = 2.0
LAST_SET_COUNTS = 6.0
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.2.0"
FIXTURE_SCRIPT = "/sets/2/set.py"
V020_ID_RENAMES = 16
NEGATIVE_ZERO = re.compile(rb"-0\.0\b")


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
    repo = FileRepository(upgraded_copy(FIXTURE / "repo", tmp_path))
    show_id = expected["show_id"]
    tree = {p: repo.read(show_id, p) or b"" for p in repo.paths(show_id)}
    return repo, show_id, tree


def test_set_key_defaults_to_the_id_and_is_written_only_when_it_differs() -> None:
    fresh = Set(id=SetId("2"), counts=1.0)
    moved = fresh.replace(id="3")
    assert fresh.key == "2"
    assert "key" not in fresh.to_json()
    assert moved.key == "2"
    assert moved.to_json()["key"] == "2"
    assert Set.from_json(moved.to_json()) == moved
    assert Set.from_json(fresh.to_json()) == fresh


@pytest.mark.parametrize("key", ["a-b", "con", "x" * 65, "1.", "", "all"])
def test_regression_set_key_must_pass_the_id_check(key: str) -> None:
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=0.0, key=key)


def test_regression_show_refuses_set_keys_that_repeat_ignoring_case() -> None:
    show = two_set_show()
    first, second = show.sets
    with pytest.raises(SchemaError):
        show.replace(sets=(first.replace(key="a"), second.replace(key="A")))
    with pytest.raises(SchemaError):
        show.replace(sets=(first, second.replace(key="1")))


@pytest.mark.parametrize("set_id", ["con", "a-b", "1.", "x" * 65, "Props"])
def test_regression_set_without_a_key_must_pass_the_id_check(set_id: str) -> None:
    with pytest.raises(SchemaError):
        Set(id=SetId(set_id), counts=0.0)
    legacy = Set._validated({"id": set_id, "counts": 0.0}, context=LEGACY)
    assert legacy.key == set_id


def test_regression_show_refuses_ids_as_keys_that_repeat_ignoring_case() -> None:
    show = two_set_show()
    first, second = show.sets
    pair = (first.replace(id="1a", key=None), second.replace(id="1A", key=None))
    with pytest.raises(SchemaError):
        show.replace(sets=pair, transitions=())
    dumped = show.replace(sets=(first, second), transitions=()).to_json()
    dumped["sets"] = [s.to_json() for s in pair]
    assert Show._validated(dumped, context=LEGACY).set_ids() == ["1a", "1A"]


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


def _without_dropped_fields(show_doc: dict[str, Any]) -> dict[str, Any]:
    """Returns a v1 show.json without the fields format v2 drops: each spot's `claimable`, the
    coordinate system and the show's zones."""
    cast = show_doc["cast"]
    spots = [{k: v for k, v in spot.items() if k != "claimable"} for spot in cast["spots"]]
    kept = {k: v for k, v in show_doc.items() if k not in ("coordinates", "zones")}
    return {**kept, "cast": {**cast, "spots": spots}}


def _without_dropped_sheet_fields(sheet_doc: dict[str, Any]) -> dict[str, Any]:
    """Returns a v1 sheet.json without the fields format v2 drops."""
    dropped = ("landmarks", "zones", "default_coordinates")
    return {k: v for k, v in sheet_doc.items() if k not in dropped}


def test_regression_never_renumbered_v020_show_encodes_to_its_stored_documents(
    tmp_path: Path,
) -> None:
    _, _, tree = _fixture_tree(tmp_path)
    stored = {p: raw for p, raw in tree.items() if is_show_doc(p) and p != LEGACY_NOTES_DOC}
    encoded = encode_show(decode_show(tree))
    parsed = {p: json.loads(raw) for p, raw in stored.items()}
    canonical = {p: dump_json(doc) for p, doc in migrate_forms(parsed, ReportBuilder(1)).items()}
    show_doc = _without_dropped_fields(json.loads(stored[SHOW_DOC]))
    canonical[SHEET_DOC] = dump_json(_without_dropped_sheet_fields(parsed[SHEET_DOC]))
    canonical[SHOW_DOC] = dump_show_doc({**format_header(), **show_doc})
    _, report = migrate_documents(tree)
    renames = [pair for pairs in report.renamed.values() for pair in pairs]
    assert len(renames) == V020_ID_RENAMES
    for old, new in renames:
        quoted = (json.dumps(old).encode(), json.dumps(new).encode())
        canonical = {p: raw.replace(*quoted) for p, raw in canonical.items()}
    assert encoded == role_split_documents(canonical)
    assert not [p for p in encoded if p.endswith("/set.json") and "key" in json.loads(encoded[p])]


def _holds_negative_zero(value: Any) -> bool:
    if isinstance(value, float):
        return value == 0 and math.copysign(1.0, value) < 0
    if isinstance(value, dict):
        return any(_holds_negative_zero(v) for v in value.values())
    if isinstance(value, list):
        return any(_holds_negative_zero(v) for v in value)
    return False


def _normalized_by_adr_0014(raw: bytes) -> bool:
    parsed = json.loads(raw)
    stored_order = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    sorted_order = json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return _holds_negative_zero(parsed) or stored_order != sorted_order


def test_v020_head_documents_redump_to_their_stored_bytes(tmp_path: Path) -> None:
    _, _, tree = _fixture_tree(tmp_path)
    stored = {p: raw for p, raw in tree.items() if is_show_doc(p)}
    normalized = {p for p, raw in stored.items() if _normalized_by_adr_0014(raw)}
    assert normalized
    for path, raw in stored.items():
        redumped = dump_json(json.loads(raw))
        assert (redumped != raw) == (path in normalized), path
        if path in normalized:
            assert redumped == NEGATIVE_ZERO.sub(b"0.0", raw), path


def _with_transition(show: Show) -> Show:
    spec = TransitionSpec(
        id=make_tid("2", "3"),
        counts=LAST_SET_COUNTS,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=1)},
    )
    return show.replace(transitions=(spec,))


def test_encode_after_renumber_addresses_sets_by_key() -> None:
    show = _with_transition(_renumbered())
    docs = encode_show(show)
    assert json.loads(docs[SHOW_DOC])["sets"] == ["1", "1A", "2"]
    assert {p for p in docs if p.startswith("/sets/")} == {
        f"/sets/{key}/{name}"
        for key in ("1", "1A", "2")
        for name in ("set.json", "drill.json", "facing.json", "labels.json")
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
    docs = {**encode_show(show), forms_doc("1A"): dump_json([form._dumped()])}
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
    ws.write("/sets/2/forms.json", dump_json([form._dumped()]), at=LATER)
    assert repo.read(SHOW_ID, "/sets/1A/forms.json") is not None
    spec = TransitionSpec(
        id=make_tid("2", "3"),
        counts=LAST_SET_COUNTS,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=4)},
    )
    ws.write("/transitions/2-3/paths.json", json.dumps(spec.to_json()), at=LATER)
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
    with pytest.raises(E8sError):
        ws.write(path, "# not mine\n", at=LATER)
    assert {p: repo.read(SHOW_ID, p) for p in repo.paths(SHOW_ID)} == before


def test_repository_scripts_follow_the_set_key() -> None:
    repo, ws, _ = _saved_renumbered()
    assert ws.read("/sets/2/set.py") == SCRIPT_ONE_A.decode()
    ws.write("/sets/3/set.py", "# put 3\n", at=LATER)
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


@pytest.mark.parametrize(
    ("first_id", "second_id", "migrated"),
    [("con", LEGACY_LONG_ID, ["con_", "L" * 60]), ("1.", "aux.txt", ["1", "aux_.txt"])],
)
def test_regression_legacy_set_ids_outside_the_id_pattern_load_and_round_trip(
    first_id: str, second_id: str, migrated: list[str]
) -> None:
    docs = _case_pair_docs(first_id, second_id)
    show = decode_show(docs)
    assert show.set_ids() == migrated
    assert _keys(show) == migrated
    assert decode_show(encode_show(show)) == show
    repo = _stored_repo(docs)
    ws = open_workspace(repo, SHOW_ID)
    assert load_show(ws).replace(revision="") == show
    ws.write(f"/sets/{migrated[1]}/set.py", "# legacy\n", at=LATER)
    assert repo.read(SHOW_ID, f"/sets/{migrated[1]}/set.py") == b"# legacy\n"
    old_folders = (f"/sets/{first_id}/", f"/sets/{second_id}/")
    assert not [p for p in repo.paths(SHOW_ID) if p.startswith(old_folders)]
    assert encode_show(load_show(ws)) == encode_show(show)


def test_regression_constructors_refuse_a_reserved_key() -> None:
    with pytest.raises(SchemaError):
        add_set(two_set_show(), "con", 4.0)
    with pytest.raises(SchemaError):
        Set(id=SetId("1"), counts=0.0, key="con")
    docs = _case_pair_docs("con", "2")
    body = json.loads(docs["/sets/con/set.json"]) | {"key": "con"}
    with pytest.raises(SchemaError):
        decode_show({**docs, "/sets/con/set.json": dump_json(body)})


def test_regression_workspace_has_no_public_stored_commit() -> None:
    assert not hasattr(Workspace, "commit_stored")


def _with_fresh_set(show: Show, set_id: str) -> Show:
    fresh = Set.from_json({**show.sets[-1].to_json(), "id": set_id})
    return show.replace(sets=(*show.sets, fresh))


def _with_legacy_set(show: Show, set_id: str) -> Show:
    """Adds a set whose ID only the legacy context accepts, as a v1 reader would build it."""
    dumped = json_doc(show)
    fresh = {**dumped["sets"][-1], "id": set_id}
    fresh.pop("key", None)
    return Show._validated({**dumped, "sets": [*dumped["sets"], fresh]}, context=LEGACY)


def test_regression_new_set_folder_must_pass_the_id_check() -> None:
    """Refuses a set whose folder fails check_id: in the model, in put_show and save_show of a
    show built with the legacy context, and in a raw commit; the repository stays unchanged."""
    repo = InMemoryRepository()
    head = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT).id
    before = repo.paths(SHOW_ID)
    ws = open_workspace(repo, SHOW_ID)
    for set_id in ("con", LEGACY_LONG_ID):
        with pytest.raises(SchemaError):
            _with_fresh_set(load_show(ws), set_id)
        with pytest.raises(SchemaError):
            repo.put_show(
                SHOW_ID, _with_legacy_set(load_show(ws), set_id), expected_revision=head, at=LATER
            )
        with pytest.raises(SchemaError):
            save_show(ws, _with_legacy_set(load_show(ws), set_id), LATER)
    with pytest.raises(SchemaError):
        repo.transaction(SHOW_ID, {"/sets/con/set.json": b"{}"}, expected_revision=head, at=LATER)
    assert repo.paths(SHOW_ID) == before
    assert repo.head(SHOW_ID) == head


def test_regression_stored_legacy_folders_keep_saving() -> None:
    repo = _stored_repo(_case_pair_docs("con", "2"))
    ws = open_workspace(repo, SHOW_ID)
    show = load_show(ws)
    moved = show.set("con_").with_placements({"T1": PlacementPatch(x=3.0)})
    save_show(ws, show.replace(sets=(moved, show.sets[1]), title="edited"), LATER)
    reloaded = repo.get_show(SHOW_ID)
    assert reloaded.set("con_").get("T1").x == 3.0
    assert reloaded.title == "edited"
    ws.write("/sets/con_/set.py", "# legacy\n", at=LATER)
    assert not [p for p in repo.paths(SHOW_ID) if p.startswith("/sets/con/")]
    legacy = _stored_repo(_case_pair_docs("1", "1A"))
    lws = open_workspace(legacy, SHOW_ID)
    save_show(lws, renumber(load_show(lws))[0], LATER)
    assert [s.key for s in legacy.get_show(SHOW_ID).sets] == ["1", "1A"]
    assert legacy.get_show(SHOW_ID).set_ids() == ["1", "2"]


def test_regression_dashed_set_folder_commit_is_refused() -> None:
    repo, ws, _ = _saved_renumbered()
    before = {p: repo.read(SHOW_ID, p) for p in repo.paths(SHOW_ID)}
    for path in ("/sets/a-b/set.py", "/sets/a-b/set.json", "/transitions/2-a-b/paths.json"):
        with pytest.raises(E8sError):
            repo.transaction(SHOW_ID, {path: b"{}"}, expected_revision=ws.revision, at=LATER)
    assert {p: repo.read(SHOW_ID, p) for p in repo.paths(SHOW_ID)} == before


def test_regression_new_set_key_must_be_nfc() -> None:
    with pytest.raises(SchemaError):
        require_new_set_keys([], ["/sets/\u212a1/set.py"])


def _case_pair_docs(first_id: str, second_id: str) -> dict[str, bytes]:
    """Writes the example show as v0.2 stored it, under set folders named by the given IDs."""
    docs = layout1_documents(two_set_show())
    meta = without_header(json.loads(docs[SHOW_DOC]))
    meta["sets"] = [first_id, second_id]
    docs[SHOW_DOC] = dump_json(meta)
    for old_id, new_id in (("1", first_id), ("2", second_id)):
        body = json.loads(docs.pop(set_doc(old_id)))
        assert "key" not in body
        docs[set_doc(new_id)] = dump_json({**body, "id": new_id})
    return docs


def _stored_repo(docs: dict[str, bytes]) -> InMemoryRepository:
    """Stores documents as v0.2 did, through the backend, so no Phase 1 commit check runs."""
    repo = InMemoryRepository()
    tree = {p: blob_digest(raw) for p, raw in docs.items()}
    revision = Revision(id=content_hash(tree), parent="", at=AT, changed=tuple(sorted(docs)))
    repo.backend.commit(SHOW_ID, revision, tree, {blob_digest(raw): raw for raw in docs.values()})
    return repo


def test_regression_v020_show_with_set_ids_differing_by_case_loads() -> None:
    docs = _case_pair_docs("1a", "1A")
    show = decode_show(docs)
    assert show.set_ids() == ["1a", "1A.2"]
    assert _keys(show) == ["1a", "1A.2"]
    meta = {**format_header(), **json.loads(docs[SHOW_DOC]), "sets": ["1a", "1A.2"]}
    body = {**json.loads(docs[set_doc("1A")]), "id": "1A.2"}
    moved = {p: raw for p, raw in docs.items() if p != set_doc("1A")}
    expected = {**moved, SHOW_DOC: dump_show_doc(meta), set_doc("1A.2"): dump_json(body)}
    assert encode_show(show) == role_split_documents(expected)
    assert decode_show(encode_show(show)) == show
    assert load_show(open_workspace(_stored_repo(docs), SHOW_ID)).set_ids() == ["1a", "1A.2"]


def test_regression_v020_case_pair_show_adds_a_set_with_a_free_key() -> None:
    repo = _stored_repo(_case_pair_docs("ab", "AB"))
    ws = open_workspace(repo, SHOW_ID)
    added = add_set(load_show(ws), "Ab", SUBSET_COUNTS, after="AB.2")
    assert _keys(added) == ["ab", "AB.2", "Ab.3"]
    save_show(ws, added, LATER)
    assert _keys(repo.get_show(SHOW_ID)) == ["ab", "AB.2", "Ab.3"]
    assert "/sets/Ab.3/set.json" in repo.paths(SHOW_ID)
    assert not [p for p in repo.paths(SHOW_ID) if p.startswith("/sets/AB/")]


def test_regression_v020_case_pair_show_renumbers_once_migrated() -> None:
    """Renumbers the case pair migration split, which Phase 1 refused to renumber."""
    repo = _stored_repo(_case_pair_docs("1a", "1A"))
    head = repo.head(SHOW_ID)
    show = repo.get_show(SHOW_ID)
    renumbered, mapping = renumber(show)
    assert mapping == {"1a": "1", "1A.2": "2"}
    assert _keys(renumbered) == ["1a", "1A.2"]
    assert repo.head(SHOW_ID) == head
    ws = open_workspace(repo, SHOW_ID)
    save_show(ws, renumbered, LATER)
    assert repo.get_show(SHOW_ID).set_ids() == ["1", "2"]
    assert _keys(repo.get_show(SHOW_ID)) == ["1a", "1A.2"]


def test_regression_new_set_folders_differing_by_case_are_refused() -> None:
    repo = InMemoryRepository()
    with pytest.raises(SchemaError):
        repo.transaction(SHOW_ID, _case_pair_docs("1a", "1A"), expected_revision="", at=AT)
    assert repo.head(SHOW_ID) == ""
    inserted, _ = insert_subset(two_set_show(), "1", SUBSET_COUNTS)
    head = repo.put_show(SHOW_ID, inserted, expected_revision="", at=AT).id
    with pytest.raises(SchemaError):
        repo.transaction(SHOW_ID, {"/sets/1a/set.py": b""}, expected_revision=head, at=LATER)
    assert repo.head(SHOW_ID) == head
