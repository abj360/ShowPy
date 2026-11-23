#!/usr/bin/env python3

"""
test_storage.py --- tests documents, repositories, assets and the workspace file view
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from showpy.choreo.form_models import Form
from showpy.choreo.models import PlacementPatch
from showpy.choreo.path_models import PathRow, TransitionSpec
from showpy.core.errors import (
    ConflictError,
    SchemaError,
    StorageError,
    UnknownRefError,
)
from showpy.core.ids import FormId, SetId, SpotId, make_tid
from showpy.notes.models import Anchor, Note
from showpy.show.documents import decode_show, dump_json, encode_show, split_path
from showpy.show.file_repository import FileRepository
from showpy.show.manage import add_set, remove_set
from showpy.show.models import Show
from showpy.show.repositories import InMemoryAssetStore, InMemoryRepository
from showpy.show.storage import Repository, Workspace, load_show, open_workspace, save_show
from tests.fixtures.recipes import LINE_RECIPE
from tests.fixtures.shows import two_set_show
from tests.helpers import context_entries

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 11, 12, 5, tzinfo=UTC)
SHOW_ID = "show-1"

type RepoFactory = Callable[[], Repository]


@pytest.fixture(params=["memory", "file"])
def make_repo(request: pytest.FixtureRequest, tmp_path: Path) -> RepoFactory:
    if request.param == "memory":
        return InMemoryRepository
    return lambda: FileRepository(tmp_path / "repo")


def _rich_show() -> Show:
    show = two_set_show()
    form = Form(
        id=FormId("f1"),
        set=SetId("2"),
        recipe=LINE_RECIPE,
        members=(SpotId("T1"), SpotId("T2")),
    )
    spec = TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId("T1"): PathRow(entity=SpotId("T1"), start=2)},
    )
    note = Note(id="n1", text="hit", anchor=Anchor.on_set("2"), author="d", created_at=AT)
    return show.model_copy(update={"forms": {"f1": form}, "transitions": (spec,), "notes": (note,)})


def _saved(repo: Repository, show: Show | None = None) -> str:
    rev = repo.put_show(
        SHOW_ID,
        show or two_set_show(),
        expected_revision="",
        at=AT,
    )
    return rev.id


def test_documents_round_trip_rich_show() -> None:
    show = _rich_show()
    docs = encode_show(show)
    assert "/sets/2/forms.json" in docs
    assert "/transitions/1-2/paths.json" in docs
    assert decode_show(docs) == show


def test_dump_json_is_canonical_for_sets() -> None:
    assert dump_json({"t": frozenset({"b", "a", "c"})}) == dump_json({"t": frozenset("cab")})
    assert json.loads(dump_json({"t": frozenset({"b", "a"})})) == {"t": ["a", "b"]}


def test_encode_refuses_unsafe_set_ids() -> None:
    bad = two_set_show().model_copy(
        update={"sets": (two_set_show().sets[0].model_copy(update={"id": "../x"}),)}
    )
    with pytest.raises(SchemaError):
        encode_show(bad)


@pytest.mark.parametrize(
    "path",
    [
        "/sets/../config/limits.yaml",
        "sets/1/set.py",
        "//etc/passwd",
        "/sets/1/./set.py",
        "/show\\show.json",
        "",
        "/sets/1/..",
        "/renders/.hidden",
    ],
)
def test_split_path_rejects_traversal(path: str) -> None:
    with pytest.raises(StorageError):
        split_path(path)


def test_repository_round_trip_and_revision_fields(make_repo: RepoFactory) -> None:
    repo = make_repo()
    rev = _saved(repo, _rich_show())
    loaded = repo.get_show(SHOW_ID)
    assert loaded == _rich_show().model_copy(update={"revision": rev})
    (revision,) = repo.revisions(SHOW_ID)
    assert (revision.parent, revision.at) == ("", AT)
    assert "/show/show.json" in revision.changed
    assert repo.shows() == [SHOW_ID]


def test_revision_ids_are_deterministic(tmp_path: Path) -> None:
    first = _saved(InMemoryRepository())
    second = _saved(FileRepository(tmp_path / "r"))
    assert first == second


def test_stale_write_raises_conflict_listing_changes(make_repo: RepoFactory) -> None:
    repo = make_repo()
    base = _saved(repo)
    landed = repo.put_script(SHOW_ID, "1", "x = 1\n", expected_revision=base, at=LATER)
    with pytest.raises(ConflictError) as info:
        repo.put_script(SHOW_ID, "2", "y = 2\n", expected_revision=base, at=LATER)
    changes = context_entries(info.value, "changes")
    assert [c["id"] for c in changes] == [landed.id]
    assert changes[0]["changed"] == ["/sets/1/set.py"]
    assert f"{landed.id[:7]} {LATER.isoformat()} (/sets/1/set.py)" in info.value.message


def test_identical_write_is_idempotent(make_repo: RepoFactory) -> None:
    repo = make_repo()
    base = _saved(repo)
    again = repo.put_show(SHOW_ID, two_set_show(), expected_revision=base, at=LATER)
    assert again.id == base
    assert len(repo.revisions(SHOW_ID)) == 1


def test_typed_accessors(make_repo: RepoFactory) -> None:
    repo = make_repo()
    rev = _saved(repo)
    moved = repo.get_set(SHOW_ID, "2").with_placements({"T1": PlacementPatch(x=-5.0)})
    rev = repo.put_set(SHOW_ID, moved, expected_revision=rev, at=LATER).id
    assert repo.get_set(SHOW_ID, "2").get("T1").x == -5.0
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=99.0, rows={SpotId("T2"): PathRow(entity=SpotId("T2"), end=4)}
    )
    rev = repo.put_paths(SHOW_ID, spec, expected_revision=rev, at=LATER).id
    assert repo.get_paths(SHOW_ID, ("1", "2")).counts == 8.0
    form = Form(id=FormId("f"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    rev = repo.put_forms(SHOW_ID, "2", (form,), expected_revision=rev, at=LATER).id
    assert repo.get_forms(SHOW_ID, "2") == (form,)
    with pytest.raises(SchemaError):
        repo.put_forms(SHOW_ID, "1", (form,), expected_revision=rev, at=LATER)
    assert repo.list_sets(SHOW_ID) == ["1", "2"]
    assert repo.get_timeline(SHOW_ID) == two_set_show().timeline
    assert (repo.get_limits(SHOW_ID), repo.get_waivers(SHOW_ID)) == ("", "")
    with pytest.raises(UnknownRefError):
        repo.get_script(SHOW_ID, "1")


def test_put_show_deletes_removed_set_documents(make_repo: RepoFactory) -> None:
    repo = make_repo()
    rev = _saved(repo, add_set(two_set_show(), "3", 4.0))
    rev = repo.put_script(SHOW_ID, "3", "pass\n", expected_revision=rev, at=AT).id
    repo.put_show(SHOW_ID, two_set_show(), expected_revision=rev, at=LATER)
    assert not [p for p in repo.paths(SHOW_ID) if p.startswith("/sets/3/")]


def test_unknown_show_revision_and_bad_ids(make_repo: RepoFactory) -> None:
    repo = make_repo()
    _saved(repo)
    with pytest.raises(UnknownRefError):
        repo.get_show("show-2")
    with pytest.raises(UnknownRefError):
        repo.get_revision(SHOW_ID, "nope")
    with pytest.raises(StorageError):
        repo.head("../escape")


def test_file_repository_persists_and_stays_under_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    rev = _saved(FileRepository(root))
    reopened = FileRepository(root)
    assert reopened.head(SHOW_ID) == rev
    assert reopened.get_show(SHOW_ID).set("2").get("T1").y == 9.0
    assert all(root in p.parents for p in tmp_path.rglob("*") if p.is_file())


def test_file_repository_race_lets_only_one_writer_land(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    base = _saved(FileRepository(root))
    one, two = FileRepository(root), FileRepository(root)
    one.put_script(SHOW_ID, "1", "a\n", expected_revision=base, at=AT)
    with pytest.raises(ConflictError):
        two.put_script(SHOW_ID, "1", "b\n", expected_revision=base, at=AT)
    assert len(FileRepository(root).revisions(SHOW_ID)) == 2


def test_asset_store() -> None:
    store = InMemoryAssetStore()
    uri = store.put(b"video")
    assert store.put(b"video") == uri
    assert store.get(uri) == b"video"
    with pytest.raises(UnknownRefError):
        store.get("asset:sha256:00")
    with pytest.raises(SchemaError):
        store.get("file:///etc/passwd")


def _workspaces(repo: Repository) -> tuple[Workspace, Workspace]:
    _saved(repo)
    return open_workspace(repo, SHOW_ID), open_workspace(repo, SHOW_ID)


def test_workspace_reads_documents_and_derived_tables() -> None:
    agent, _ = _workspaces(InMemoryRepository())
    assert json.loads(agent.read("/show/show.json"))["sets"] == ["1", "2"]
    index_lines = str(agent.read("/show/index.csv")).splitlines()
    assert index_lines[0] == "set,counts,start_count,measure,seconds,cue"
    assert index_lines[2].startswith("2,8.0,8.0,,4.0")
    assert str(agent.read("/sets/1/placements.csv")).splitlines()[1].startswith("T1,0.0,5.0")
    assert agent.read("/config/limits.yaml") == ""
    assert json.loads(agent.read("/sets/1/forms.json")) == []
    assert json.loads(agent.read("/transitions/1-2/paths.json"))["counts"] == 8.0
    assert json.loads(agent.read("/canvas/selection.json"))["ids"] == []


def test_workspace_ls() -> None:
    agent, _ = _workspaces(InMemoryRepository())
    assert agent.ls("/") == [
        "canvas/", "config/", "exports/", "lib/", "publish/", "renders/", "sets/", "show/",
        "time/", "transitions/",
    ]  # fmt: skip
    assert agent.ls("/sets") == ["1/", "2/"]
    assert agent.ls("/sets/1/") == ["forms.json", "placements.csv"]
    assert agent.ls("/transitions") == ["1-2/"]
    assert agent.ls("/renders") == []
    with pytest.raises(UnknownRefError):
        agent.ls("/actions")


def test_unknown_paths_suggest_close_matches() -> None:
    agent, _ = _workspaces(InMemoryRepository())
    with pytest.raises(UnknownRefError) as info:
        agent.read("/sets/1/forms.jsn")
    assert "/sets/1/forms.json" in info.value.hint
    with pytest.raises(UnknownRefError):
        agent.read("/actions/actions.json")
    with pytest.raises(UnknownRefError):
        agent.read("/sets/1/set.json")
    with pytest.raises(UnknownRefError):
        agent.read("/sets/9/placements.csv")


@pytest.mark.parametrize(
    "path", ["/sets/../config/limits.yaml", "/renders/../../etc/passwd", "renders/a.png"]
)
def test_workspace_rejects_traversal(path: str) -> None:
    agent, designer = _workspaces(InMemoryRepository())
    with pytest.raises(StorageError):
        agent.read(path)
    with pytest.raises(StorageError):
        designer.write(path, b"x", at=AT)


def test_agent_writes_designer_files_and_sees_designer_edits() -> None:
    agent, designer = _workspaces(InMemoryRepository())
    agent.write("/config/limits.yaml", "max_step_m: 1.0\n", at=AT)
    agent.write("/config/waivers.yaml", "[]\n", at=AT)
    agent.write("/canvas/selection.json", '{"ids": ["T1"]}', at=AT)
    designer.refresh()
    designer.write("/config/limits.yaml", "max_step_m: 1.2\n", at=LATER)
    agent.refresh()
    assert agent.read("/config/limits.yaml") == "max_step_m: 1.2\n"


def test_regression_library_leaves_permissions_to_the_host() -> None:
    """Lands an agent's set deletion, retitle and limits write; the host decides who may write."""
    agent, designer = _workspaces(InMemoryRepository())
    show = load_show(agent)
    save_show(agent, remove_set(show, "2").replace(title="Retitled by agent"), AT)
    agent.write("/config/limits.yaml", "max_speed: 99\n", at=LATER)
    designer.refresh()
    reloaded = load_show(designer)
    assert [s.id for s in reloaded.sets] == ["1"]
    assert reloaded.title == "Retitled by agent"
    assert designer.read("/config/limits.yaml") == "max_speed: 99\n"


def test_agent_writes_forms_paths_scripts_and_renders() -> None:
    repo = InMemoryRepository()
    agent, _ = _workspaces(repo)
    form = Form(id=FormId("f"), set=SetId("2"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    agent.write("/sets/2/forms.json", dump_json([form.model_dump(mode="python")]), at=AT)
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): PathRow(entity=SpotId("T1"), end=6)}
    )
    agent.write("/transitions/1-2/paths.json", spec.model_dump_json(), at=AT)
    agent.write("/sets/2/set.py", "def build(ctx):\n    return ctx.prev\n", at=AT)
    agent.write("/renders/set-2.png", b"\x89PNG", at=AT)
    show = load_show(agent)
    assert show.forms == {"f": form}
    assert show.transition(("1", "2")) == spec
    assert agent.read("/renders/set-2.png") == b"\x89PNG"
    assert agent.ls("/sets/2") == ["forms.json", "placements.csv", "set.py"]
    assert repo.get_script(SHOW_ID, "2").startswith("def build")
    agent.write("/sets/2/forms.json", "[]", at=LATER)
    assert load_show(agent).forms == {}


def test_workspace_write_validates_content() -> None:
    agent, _ = _workspaces(InMemoryRepository())
    wrong = Form(id=FormId("f"), set=SetId("1"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    with pytest.raises(SchemaError):
        agent.write("/sets/2/forms.json", dump_json([wrong.model_dump(mode="python")]), at=AT)
    with pytest.raises(SchemaError):
        agent.write("/show/sheet.json", "{not json", at=AT)
    with pytest.raises(SchemaError):
        agent.write("/canvas/comments.json", '[{"id": 1}]', at=AT)
    with pytest.raises(UnknownRefError):
        agent.write("/sets/9/set.py", "pass\n", at=AT)


def test_stale_workspace_write_lists_designer_changes() -> None:
    agent, designer = _workspaces(InMemoryRepository())
    landed = designer.write("/sets/1/set.py", "pass\n", at=AT)
    with pytest.raises(ConflictError) as info:
        agent.write("/sets/2/set.py", "pass\n", at=LATER)
    assert context_entries(info.value, "changes")[0]["id"] == landed.id
    agent.refresh()
    agent.write("/sets/2/set.py", "pass\n", at=LATER)


def test_save_show_conflict_lists_designer_changes() -> None:
    agent, designer = _workspaces(InMemoryRepository())
    mine = load_show(agent)
    theirs = load_show(designer)
    moved = theirs.sets[1].with_placements({"T2": PlacementPatch(x=10.0)})
    landed = save_show(designer, theirs.model_copy(update={"sets": (theirs.sets[0], moved)}), AT)
    with pytest.raises(ConflictError) as info:
        save_show(agent, mine.model_copy(update={"title": "New"}), LATER)
    assert context_entries(info.value, "changes")[0]["changed"] == list(landed.changed)
    agent.refresh()
    fresh = load_show(agent)
    save_show(agent, fresh.model_copy(update={"title": "New"}), LATER)
    assert load_show(agent).set("2").get("T2").x == 10.0


def test_workspace_save_lands_edits_to_pinned_items() -> None:
    agent, designer = _workspaces(InMemoryRepository())
    show = load_show(designer)
    pinned = show.sets[1].with_placements({"T1": PlacementPatch(pinned=True)})
    save_show(designer, show.model_copy(update={"sets": (show.sets[0], pinned)}), AT)
    agent.refresh()
    current = load_show(agent)
    moved = current.sets[1].with_placements({"T1": PlacementPatch(x=3.0)}, force=True)
    save_show(agent, current.model_copy(update={"sets": (current.sets[0], moved)}), LATER)
    assert load_show(agent).set("2").get("T1").x == 3.0


def test_open_workspace_before_first_save() -> None:
    ws = open_workspace(InMemoryRepository(), "fresh")
    assert ws.revision == ""
    assert "show/" in ws.ls("/")
    with pytest.raises(StorageError):
        load_show(ws)
    with pytest.raises(StorageError):
        ws.read("/show/show.json")
    rev = save_show(ws, two_set_show(), AT)
    assert ws.revision == rev.id
