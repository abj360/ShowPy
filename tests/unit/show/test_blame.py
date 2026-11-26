#!/usr/bin/env python3

"""
test_blame.py --- tests that blame names the revision that last changed one show item

Contains:
    test_blame_names_the_revision_that_last_moved_a_spot: decodes only the candidate revisions
    test_blame_of_an_untouched_item_is_the_first_save: skips the revision that only re-encodes
        layout-1 documents
    test_blame_of_a_removed_or_unknown_item_is_none: finds nothing at the head
    test_blame_of_a_show_without_revisions_is_none: finds no revision in an empty repository
    test_blame_names_each_kind_by_its_documents: finds set, spot, sheet, timeline, set order and
        show items through the documents that hold them
"""

from datetime import UTC, datetime, timedelta

from e8s.cast.spots import delete_spots
from e8s.choreo.models import PlacementPatch
from e8s.examples import two_set_show
from e8s.show.blame import blame
from e8s.show.change_models import ItemKey
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.layout1 import layout1_documents

SHOW_ID = "blamed"
AT = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)


def _drill(set_key: str, spot: str) -> ItemKey:
    return ItemKey(kind="placement", path=(set_key, spot), role="drill")


def _move(show: Show, set_id: str, spot: str, x: float) -> Show:
    index = next(i for i, s in enumerate(show.sets) if s.id == set_id)
    moved = show.sets[index].with_placements({spot: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(*show.sets[:index], moved, *show.sets[index + 1 :]))


def _save_all(repo: InMemoryRepository, edits: list[tuple[str, str, float]]) -> list[str]:
    """Saves the example show, then one revision per edit; returns the revision IDs."""
    ws = open_workspace(repo, SHOW_ID)
    ids = [save_show(ws, two_set_show(), AT).id]
    for n, (set_id, spot, x) in enumerate(edits, start=1):
        ws = open_workspace(repo, SHOW_ID)
        ids.append(save_show(ws, _move(load_show(ws), set_id, spot, x), AT + STEP * n).id)
    return ids


class _CountingWorkspace(Workspace):
    """Records every revision whose show is decoded."""

    decoded: list[str]

    def show_at(self, revision: str) -> Show | None:
        self.decoded.append(revision)
        return super().show_at(revision)


def _counting(repo: InMemoryRepository) -> _CountingWorkspace:
    ws = _CountingWorkspace(repo, SHOW_ID, repo.head(SHOW_ID))
    ws.decoded = []
    return ws


def test_blame_names_the_revision_that_last_moved_a_spot() -> None:
    repo = InMemoryRepository()
    ids = _save_all(
        repo,
        [("2", "T1", 1.0), ("2", "T2", 2.0), ("2", "T1", 3.0), ("1", "T3", 5.5)],
    )
    ws = _counting(repo)
    found = blame(ws, _drill("2", "T1"))
    assert found is not None
    assert found.id == ids[3]
    assert set(ws.decoded) == {ids[3], ids[2]}

    ws = _counting(repo)
    found = blame(ws, _drill("1", "T1"))
    assert found is not None
    assert found.id == ids[0]
    assert set(ws.decoded) == {ids[4], ids[3], ids[0]}


def test_blame_of_an_untouched_item_is_the_first_save() -> None:
    repo = InMemoryRepository()
    first = repo.transaction(
        SHOW_ID, layout1_documents(two_set_show()), expected_revision="", at=AT
    )
    ws = open_workspace(repo, SHOW_ID)
    reencoded = save_show(ws, load_show(ws), AT + STEP)
    assert reencoded.id != first.id
    ws = open_workspace(repo, SHOW_ID)
    edited = save_show(ws, _move(load_show(ws), "2", "T1", 1.0), AT + 2 * STEP)
    ws = open_workspace(repo, SHOW_ID)
    untouched = blame(ws, _drill("2", "T2"))
    assert untouched is not None
    assert untouched.id == first.id
    moved = blame(ws, _drill("2", "T1"))
    assert moved is not None
    assert moved.id == edited.id


def test_blame_of_a_removed_or_unknown_item_is_none() -> None:
    repo = InMemoryRepository()
    _save_all(repo, [])
    ws = open_workspace(repo, SHOW_ID)
    assert blame(ws, _drill("2", "NOPE")) is None
    assert blame(ws, _drill("../x", "T1")) is None
    save_show(ws, delete_spots(load_show(ws), "T4"), AT + STEP)
    assert blame(open_workspace(repo, SHOW_ID), _drill("2", "T4")) is None


def test_blame_of_a_show_without_revisions_is_none() -> None:
    ws = open_workspace(InMemoryRepository(), SHOW_ID)
    assert blame(ws, _drill("2", "T1")) is None


def test_blame_names_each_kind_by_its_documents() -> None:
    repo = InMemoryRepository()
    ids = _save_all(repo, [])
    ws = open_workspace(repo, SHOW_ID)
    show = load_show(ws)
    retitled = save_show(ws, show.replace(title="Renamed"), AT + STEP)
    ws = open_workspace(repo, SHOW_ID)
    keys = [
        ItemKey(kind="set", path=("2",), role="timing"),
        ItemKey(kind="spot", path=("T1",)),
        ItemKey(kind="sheet", path=()),
        ItemKey(kind="timeline", path=()),
        ItemKey(kind="set_order", path=()),
    ]
    for key in keys:
        found = blame(ws, key)
        assert found is not None
        assert found.id == ids[0]
    title = blame(ws, ItemKey(kind="show", path=()))
    assert title is not None
    assert title.id == retitled.id
