#!/usr/bin/env python3

"""
test_v1_to_v2_ids.py --- tests the ids sub-step of the v1 to v2 format migration

Contains:
    test_regression_case_pair_show_renumbers_after_migration: splits the v0.3.0 fixture's
        `1a`, `1A` into `1a`, `1A.2` and renumbers the migrated show
    test_regression_legacy_set_id_con_is_renamed: renames the v0.3.0 set `con` and its folder to
        `con_`, leaving nothing under the old folder
    test_v020_form_ids_lose_hyphens: renames the v0.2.0 fixture's 15 form IDs and its selection,
        and every reference to them follows
    test_rename_rule_is_deterministic_under_hash_seeds: migrates the v0.3.0 fixture to the same
        documents and report under every hash seed
    test_regression_stored_id_props_gets_a_trailing_underscore: renames `props` and `Props`,
        keeps `props.1`
    test_rename_rule_repairs_ids_that_break_the_pattern: replaces `-`, strips edge dots, cuts to
        60 characters, names an empty result `id` and marks reserved words with `_`
    test_rename_rule_splits_case_pairs_in_show_order: keeps the first of a case pair and gives
        the next the smallest free suffix, never taking a name a valid ID keeps
    test_regression_rename_rule_refuses_an_id_it_cannot_repair: raises BAD_ID for a stored ID
        whose characters the rule leaves outside the pattern
    test_rename_ids_renames_nothing_in_a_valid_show: returns the show and an empty report
    test_regression_every_reference_follows_a_renamed_id: rewrites sets, keys, transitions,
        forms, routes, zones, selections, notes, pins and selector text, and the result passes
        every ID check
    test_regression_first_save_moves_the_files_of_renamed_set_folders: moves a set script from
        `/sets/con/` to `/sets/con_/` in one revision, nothing on the next save, for the v0.2.0
        and v0.3.0 fixtures
    test_regression_migration_refuses_to_move_a_set_file_over_another: refuses a stored file
        already at the folder a renamed set moves to
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from showpy.cast.spots import define_selection
from showpy.choreo.path_models import FacingMode, FacingOverride
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, StorageError
from showpy.core.ids import LEGACY_IDS_KEY, FormId, SetId, ZoneId
from showpy.core.types import Region
from showpy.examples import two_set_show
from showpy.notes.models import Anchor
from showpy.notes.notes import add_note
from showpy.sheet.models import Zone, ZoneKind
from showpy.show.documents import (
    SHOW_DOC,
    decode_show,
    dump_json,
    is_show_doc,
    migrate_documents,
    set_doc,
)
from showpy.show.file_repository import FileRepository
from showpy.show.integrity import blob_digest
from showpy.show.manage import add_set, renumber
from showpy.show.migrate import FIRST_VERSION, ReportBuilder
from showpy.show.migrations.v1_to_v2_ids import rename_ids
from showpy.show.models import Revision, Show
from showpy.show.pinning import pin
from showpy.show.storage import load_show, open_workspace, save_show
from showpy.show.validate import validate_show
from tests.fixtures.generators import seeded_show
from tests.helpers import TWO_HASH_SEEDS, outputs_under_hash_seeds

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "repos"
LEGACY = {LEGACY_IDS_KEY: True}
AT = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SCRIPT = b"# set script\n"
SQUARE = Region(exterior=((0.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)))
V020_FORM_RENAMES = 15
DETERMINISM_SCRIPT = """
import json, shutil, tempfile
from pathlib import Path
from showpy.show.documents import migrate_documents
from showpy.show.file_repository import FileRepository
copy = Path(tempfile.mkdtemp()) / "repo"
shutil.copytree({repo!r}, copy)
docs = dict(FileRepository(copy).documents("legacy-show"))
migrated, report = migrate_documents(docs)
print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
print(sorted(migrated))
"""


def _fixture(tag: str, tmp_path: Path) -> tuple[FileRepository, dict[str, Any]]:
    """Copies a read-only fixture repository and loads its expected.json."""
    shutil.copytree(FIXTURES / tag / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((FIXTURES / tag / "expected.json").read_text())
    return FileRepository(tmp_path / "repo"), expected


def _head(tag: str, tmp_path: Path) -> dict[str, bytes]:
    repo, expected = _fixture(tag, tmp_path)
    return dict(repo.documents(expected["show_id"]))


def _legacy_show(dump: dict[str, Any]) -> Show:
    """Validates a show dump the way migration does before its ids step: any v1 ID accepted."""
    return Show.model_validate(dump, context=LEGACY)


def _with_spot_id(old: str, new: str) -> dict[str, Any]:
    """Returns the example show's dump with one spot ID replaced everywhere it appears."""
    text = json.dumps(two_set_show().model_dump(mode="json"))
    dumped: dict[str, Any] = json.loads(text.replace(f'"{old}"', json.dumps(new)))
    return dumped


def _spot_renames(*ids: str) -> tuple[tuple[str, str], ...]:
    """Renames the example show's spots T1, T2, ... to `ids` and returns the spot renames."""
    dump = two_set_show().model_dump(mode="json")
    text = json.dumps(dump)
    for number, new in enumerate(ids, start=1):
        text = text.replace(f'"T{number}"', json.dumps(new))
    report = ReportBuilder(FIRST_VERSION)
    rename_ids(_legacy_show(json.loads(text)), report)
    return report.build().renamed.get("spot", ())


def test_regression_case_pair_show_renumbers_after_migration(tmp_path: Path) -> None:
    repo, expected = _fixture("v0.3.0", tmp_path)
    show = repo.get_show(expected["show_id"])
    assert show.set_ids()[-3:] == ["1a", "1A.2", "con_"]
    assert [s.key for s in show.sets][-3:] == ["1a", "1A.2", "con_"]
    renumbered, mapping = renumber(show)
    assert renumbered.set_ids() == [str(n) for n in range(1, len(show.sets) + 1)]
    assert [s.key for s in renumbered.sets] == [s.key for s in show.sets]
    assert mapping[SetId("1A.2")] == "10"
    _, report = migrate_documents(_head("v0.3.0", tmp_path / "again"))
    assert report.renamed["set"] == (("1A", "1A.2"), ("con", "con_"))


def test_regression_legacy_set_id_con_is_renamed(tmp_path: Path) -> None:
    docs = _head("v0.3.0", tmp_path)
    assert set_doc("con") in docs
    migrated, _ = migrate_documents(docs)
    assert set_doc("con_") in migrated
    assert not [p for p in migrated if p.startswith("/sets/con/")]
    show = decode_show(migrated)
    assert show.set("con_").key == "con_"
    assert json.loads(migrated[SHOW_DOC])["sets"][-1] == "con_"


def test_v020_form_ids_lose_hyphens(tmp_path: Path) -> None:
    docs = _head("v0.2.0", tmp_path)
    migrated, report = migrate_documents(docs)
    forms = report.renamed["form"]
    assert len(forms) == V020_FORM_RENAMES
    assert forms[0] == ("brass-2", "brass_2")
    assert all(new == old.replace("-", "_") for old, new in forms)
    assert report.renamed["selection"] == (("front-four", "front_four"),)
    assert sum(len(pairs) for pairs in report.renamed.values()) == V020_FORM_RENAMES + 1
    show = decode_show(migrated)
    assert sorted(show.forms) == sorted(new for _, new in forms)
    assert "brass_2" in show.set("2").forms
    assert {p.form for p in show.set("2").placements} - {None} <= set(show.forms)
    assert list(show.cast.selections) == ["front_four"]
    assert validate_show(show).ok


def test_rename_rule_is_deterministic_under_hash_seeds() -> None:
    script = DETERMINISM_SCRIPT.format(repo=str(FIXTURES / "v0.3.0" / "repo"))
    outputs = outputs_under_hash_seeds(script, TWO_HASH_SEEDS)
    assert outputs[0] == outputs[1]
    assert '["1A", "1A.2"]' in outputs[0]


@pytest.mark.parametrize(("stored", "renamed"), [("props", "props_"), ("Props", "Props_")])
def test_regression_stored_id_props_gets_a_trailing_underscore(stored: str, renamed: str) -> None:
    report = ReportBuilder(FIRST_VERSION)
    show = rename_ids(_legacy_show(_with_spot_id("T1", stored)), report)
    assert show.cast.ids()[0] == renamed
    assert show.sets[0].placements[0].entity == renamed
    assert report.build().renamed["spot"] == ((stored, renamed),)
    kept = ReportBuilder(FIRST_VERSION)
    assert rename_ids(_legacy_show(_with_spot_id("T1", "props.1")), kept).cast.ids()[0] == (
        "props.1"
    )
    assert kept.build().renamed == {}


@pytest.mark.parametrize(
    ("stored", "renamed"),
    [
        ("flag-1", "flag_1"),
        (".T1.", "T1"),
        ("L" * 65, "L" * 60),
        ("..", "id"),
        ("all", "all_"),
        ("ALL", "ALL_"),
        ("con", "con_"),
        ("aux.5", "aux_.5"),
        ("a-b.", "a_b"),
        ("\u212a1", "K1"),
    ],
)
def test_rename_rule_repairs_ids_that_break_the_pattern(stored: str, renamed: str) -> None:
    assert _spot_renames(stored) == ((stored, renamed),)


def test_rename_rule_splits_case_pairs_in_show_order() -> None:
    assert _spot_renames("t1", "T1") == (("T1", "T1.2"),)
    assert _spot_renames("a-b", "a_b") == (("a-b", "a_b.2"),)
    assert _spot_renames("A-B", "a_b", "A_B.2") == (("A-B", "A_B.3"),)
    assert _spot_renames("x-1", "x.1", "X.1") == (("x-1", "x_1"), ("X.1", "X.1.2"))
    long_pair = ("M" * 64, "m" * 64)
    assert _spot_renames(*long_pair) == ((long_pair[1], "m" * 62 + ".2"),)


@pytest.mark.parametrize("stored", ["T 1", "T1*", "a..b", "é1"])
def test_regression_rename_rule_refuses_an_id_it_cannot_repair(stored: str) -> None:
    with pytest.raises(SchemaError) as caught:
        _spot_renames(stored)
    assert caught.value.code == ErrorCode.BAD_ID
    assert caught.value.context["kind"] == "spot"
    assert caught.value.context["id"] == stored
    assert "0.3" in caught.value.hint


def test_rename_ids_renames_nothing_in_a_valid_show() -> None:
    report = ReportBuilder(FIRST_VERSION)
    show = two_set_show()
    assert rename_ids(show, report) == show
    assert report.build().renamed == {}


def _rich_show() -> Show:
    """Builds a valid show that refers to a set, a form, a route, a selection and a note."""
    show = add_set(seeded_show(3, 8, 3), "zq", 4.0)
    show = define_selection(show, "sq", ["B1", "B2"])
    show = pin(pin(pin(show, "set", "zq"), "transition", "3→zq"), "form", "brass_2")
    show = add_note(show, "look", Anchor.on_set("zq"), "nq", AT, "designer")
    show = add_note(show, "move", Anchor.on_transition(("3", "zq")), "nr", AT, "designer")
    show = add_note(show, "lead", Anchor.on_spots(["B1"]), "ns", AT, "designer")
    overrides = tuple(
        FacingOverride.model_validate(
            {"entities": entities, "mode": FacingMode.AUDIENCE, "start": 0.0, "end": 1.0}
        )
        for entities in ("sel:sq + form:brass_2", ["B1", "B2"])
    )
    show = show.replace(facing_overrides=overrides)
    zone = Zone(id=ZoneId("zz"), kind=ZoneKind.STAGING, polygon=SQUARE, applies_to="sel:sq")
    return show.replace(sheet=show.sheet.replace(zones=(zone,)))


def _legacy_rich_dump() -> dict[str, Any]:
    """Gives the rich show v1 IDs: set `con`, form `brass-2`, route `r-1`, zone `props`."""
    text = json.dumps(_rich_show().model_dump(mode="json"))
    for valid, stored in (
        ("zq", "con"),
        ("brass_2", "brass-2"),
        ('"sq"', '"s-q"'),
        ("sel:sq", "sel:s-q"),
        ('"nq"', '"n-q"'),
        ('"zz"', '"props"'),
    ):
        text = text.replace(valid, stored)
    dump: dict[str, Any] = json.loads(text)
    for spec in dump["transitions"]:
        assert list(spec["routes"]) in ([], ["route"])
        if spec["routes"]:
            spec["routes"] = {"r-1": {**spec["routes"]["route"], "id": "r-1"}}
        for row in spec["rows"].values():
            if row.get("route") is not None:
                row["route"] = "r-1"
    return dump


def test_regression_every_reference_follows_a_renamed_id() -> None:
    dump = _legacy_rich_dump()
    assert any(spec["routes"] for spec in dump["transitions"])
    report = ReportBuilder(FIRST_VERSION)
    show = rename_ids(_legacy_show(dump), report)
    renamed = report.build().renamed
    assert renamed["set"] == (("con", "con_"),)
    assert ("brass-2", "brass_2") in renamed["form"]
    assert set(renamed["route"]) == {("r-1", "r_1")}
    assert renamed["zone"] == (("props", "props_"),)
    assert renamed["selection"] == (("s-q", "s_q"),)
    assert renamed["note"] == (("n-q", "n_q"),)
    assert show.set_ids()[-1] == "con_"
    assert show.sets[-1].key == "con_"
    assert show.transition_ids()[-1] == ("3", "con_")
    assert {(p.target, p.id) for p in show.pins} >= {
        ("set", "con_"),
        ("transition", "3→con_"),
        ("form", "brass_2"),
    }
    assert [n.id for n in show.notes] == ["n_q", "nr", "ns"]
    assert show.notes[0].anchor.set_id == "con_"
    assert show.notes[1].anchor.tid == ("3", "con_")
    assert show.notes[2].anchor.spots == ("B1",)
    assert show.forms[FormId("brass_2")].id == "brass_2"
    assert "brass_2" in show.set("2").forms
    assert any(p.form == "brass_2" for p in show.set("2").placements)
    routed = [spec for spec in show.transitions if spec.routes]
    assert all(list(spec.routes) == ["r_1"] for spec in routed)
    assert all(
        row.route in (None, "r_1") for spec in show.transitions for row in spec.rows.values()
    )
    assert [o.entities for o in show.facing_overrides] == ["sel:s_q + form:brass_2", ("B1", "B2")]
    assert [(z.id, z.applies_to) for z in show.sheet.zones] == [("props_", "sel:s_q")]
    assert list(show.cast.selections) == ["s_q"]
    assert Show.model_validate(show.model_dump(mode="json")) == show
    assert validate_show(show).ok


def _commit_v1(repo: FileRepository, show_id: str, changed: dict[str, bytes]) -> None:
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


def _hand_set_con(docs: dict[str, bytes]) -> dict[str, bytes]:
    """Adds a set `con` after the last set, as v0.2 stored it: folder named by ID, no key."""
    meta = json.loads(docs[SHOW_DOC])
    last = meta["sets"][-1]
    meta["sets"] = [*meta["sets"], "con"]
    body = json.loads(docs[set_doc(last)])
    placements = [{**p, "form": None, "order": None} for p in body["placements"]]
    stored = {**body, "id": "con", "counts": 4.0, "forms": [], "placements": placements}
    stored.pop("key", None)
    return {SHOW_DOC: dump_json(meta), set_doc("con"): dump_json(stored)}


@pytest.mark.parametrize("tag", ["v0.2.0", "v0.3.0"])
def test_regression_first_save_moves_the_files_of_renamed_set_folders(
    tag: str, tmp_path: Path
) -> None:
    repo, expected = _fixture(tag, tmp_path)
    show_id = expected["show_id"]
    docs = dict(repo.documents(show_id))
    added = {} if set_doc("con") in docs else _hand_set_con(docs)
    _commit_v1(repo, show_id, {**added, "/sets/con/set.py": SCRIPT})
    stored = [r.id for r in repo.revisions(show_id)]
    migrated, _ = migrate_documents(dict(repo.documents(show_id)))
    assert migrated["/sets/con_/set.py"] == SCRIPT
    assert not [p for p in migrated if p.startswith("/sets/con/")]
    ws = open_workspace(repo, show_id)
    first = save_show(ws, load_show(ws), AT)
    assert first.parent == stored[-1]
    after = dict(repo.documents(show_id))
    assert not [p for p in after if p.startswith("/sets/con/")]
    assert after["/sets/con_/set.py"] == SCRIPT
    assert {p: raw for p, raw in after.items() if is_show_doc(p)} == {
        p: raw for p, raw in migrated.items() if is_show_doc(p)
    }
    ws.refresh()
    again = save_show(ws, load_show(ws), AT)
    assert again.id == first.id
    assert [r.id for r in repo.revisions(show_id)] == [*stored, first.id]
    assert ws.read("/sets/con_/set.py") == SCRIPT.decode()


def test_regression_migration_refuses_to_move_a_set_file_over_another(tmp_path: Path) -> None:
    docs = _head("v0.3.0", tmp_path)
    clashing = {**docs, "/sets/con/set.py": SCRIPT, "/sets/con_/set.py": b"# other\n"}
    with pytest.raises(StorageError) as caught:
        migrate_documents(clashing)
    assert caught.value.context["path"] == "/sets/con/set.py"
    assert caught.value.context["target"] == "/sets/con_/set.py"
