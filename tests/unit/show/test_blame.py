#!/usr/bin/env python3

"""
test_blame.py --- tests that blame names the revision that last changed one show item

Contains:
    test_blame_names_the_revision_that_last_moved_a_spot: decodes only the candidate revisions
    test_blame_of_an_untouched_item_is_the_first_save: skips the revision that only re-encodes
        layout-1 documents
    test_blame_of_a_removed_or_unknown_item_is_none: finds nothing at the head, decoding only it
    test_blame_of_a_show_without_revisions_is_none: finds no revision in an empty repository
    test_every_item_kind_has_a_blame_edit: lists an edit for each item kind
    test_blame_names_the_editing_revision_of_every_item_kind: finds one later edit per kind and
        role through the documents that hold the item
    test_blame_refuses_a_key_with_the_wrong_path_length: raises SchemaError naming the shape
    test_blame_refuses_a_corrupt_candidate_or_parent_revision: raises StorageError, never a guess
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from e8s.cast.spots import delete_spots
from e8s.choreo.models import PlacementPatch
from e8s.core.errors import SchemaError, StorageError
from e8s.examples import two_set_show
from e8s.show.blame import blame
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.items import changes
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.show_io import load_show, save_show
from e8s.show.workspace import Workspace, open_workspace
from tests.fixtures.layout1 import layout1_documents
from tests.unit.show.test_history import KIND_EDITS, _rich

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
    assert set(ws.decoded) == {ids[4], ids[3], ids[2]}

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
    ids = _save_all(repo, [("2", "T1", 1.0), ("2", "T2", 2.0)])
    ws = _counting(repo)
    assert blame(ws, _drill("2", "NOPE")) is None
    assert ws.decoded == [ids[-1]]
    assert blame(ws, _drill("../x", "T1")) is None
    head = open_workspace(repo, SHOW_ID)
    removed = save_show(head, delete_spots(load_show(head), "T4"), AT + 9 * STEP)
    ws = _counting(repo)
    assert blame(ws, _drill("2", "T4")) is None
    assert ws.decoded == [removed.id]


def test_blame_of_a_show_without_revisions_is_none() -> None:
    ws = open_workspace(InMemoryRepository(), SHOW_ID)
    assert blame(ws, _drill("2", "T1")) is None


def _facing(show: Show) -> Show:
    edited = show.sets[1].with_placements({"T1": PlacementPatch(facing=45.0)}, force=True)
    return show.replace(sets=(show.sets[0], edited, *show.sets[2:]))


def _note(show: Show) -> Show:
    edited = show.sets[1].with_placements({"T1": PlacementPatch(note="solo")}, force=True)
    return show.replace(sets=(show.sets[0], edited, *show.sets[2:]))


type _Edit = tuple[Callable[[Show], Show], Callable[[Show], Show], str | None]

BLAME_EDITS: dict[str, _Edit] = {
    **{kind: (prepare, edit, None) for kind, (prepare, edit, _) in KIND_EDITS.items()},
    "placement": (KIND_EDITS["placement"][0], KIND_EDITS["placement"][1], "drill"),
    "set_order": (
        lambda s: s.replace(transitions=()),
        lambda s: s.replace(sets=s.sets[::-1]),
        None,
    ),
    "placement:facing": (lambda s: s, _facing, "facing"),
    "placement:labels": (lambda s: s, _note, "labels"),
}


def test_every_item_kind_has_a_blame_edit() -> None:
    assert {name.split(":")[0] for name in BLAME_EDITS} == set(get_args(ItemKind.__value__))


@pytest.mark.parametrize("name", sorted(BLAME_EDITS))
def test_blame_names_the_editing_revision_of_every_item_kind(name: str) -> None:
    prepare, edit, role = BLAME_EDITS[name]
    kind = name.split(":")[0]
    before = prepare(_rich())
    after = edit(before)
    keys = [
        c.key
        for c in changes(before, after).changes
        if c.key.kind == kind and (role is None or c.key.role == role)
    ]
    assert keys
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    first = save_show(ws, before, AT)
    edited = save_show(open_workspace(repo, SHOW_ID), after.replace(revision=first.id), AT + STEP)
    assert first.id != edited.id
    for key in keys:
        found = blame(open_workspace(repo, SHOW_ID), key)
        assert found is not None
        assert found.id == edited.id, key


@pytest.mark.parametrize(
    "key",
    [
        ItemKey(kind="placement", path=()),
        ItemKey(kind="placement", path=("2",)),
        ItemKey(kind="set", path=()),
        ItemKey(kind="transition", path=("1-2", "T1")),
        ItemKey(kind="pin", path=("entity",)),
        ItemKey(kind="sheet", path=("x",)),
    ],
)
def test_blame_refuses_a_key_with_the_wrong_path_length(key: ItemKey) -> None:
    repo = InMemoryRepository()
    _save_all(repo, [])
    with pytest.raises(SchemaError) as raised:
        blame(open_workspace(repo, SHOW_ID), key)
    assert raised.value.hint


class _CorruptWorkspace(_CountingWorkspace):
    """Fails to decode one revision, as a tree stored by another writer could."""

    corrupt: str

    def show_at(self, revision: str) -> Show | None:
        if revision == self.corrupt:
            raise SchemaError("not valid JSON", hint="repair it")
        return super().show_at(revision)


@pytest.mark.parametrize("which", [3, 2])
def test_blame_refuses_a_corrupt_candidate_or_parent_revision(which: int) -> None:
    """Fails closed on the candidate (3) and on its parent (2), never blaming a guess."""
    repo = InMemoryRepository()
    ids = _save_all(repo, [("2", "T1", 1.0), ("2", "T2", 2.0), ("2", "T1", 3.0)])
    ws = _CorruptWorkspace(repo, SHOW_ID, ids[-1])
    ws.decoded = []
    ws.corrupt = ids[which]
    with pytest.raises(StorageError) as raised:
        blame(ws, _drill("2", "T1"))
    assert raised.value.context == {"revision": ids[which], "show": SHOW_ID}
