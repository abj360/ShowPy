#!/usr/bin/env python3

"""
test_tree_items.py --- tests that item changes read from changed documents equal whole-show ones

Contains:
    test_tree_changes_equal_whole_show_changes_after_random_edits: lists the same item changes as
        changes() of the two whole shows, for every edit that changes no show.json or cast
    test_tree_changes_decode_only_the_changed_documents: misses the decode cache once per changed
        set or paths document and never validates a show
    test_tree_changes_retime_the_stored_paths_into_a_set_whose_counts_changed: reads the paths
        into a set whose new counts they take, though their document did not change
    test_reads_alone_refuses_show_cast_and_set_id_changes: sends pins, new sets, cast edits and
        renumbers to the whole-show comparison
    test_reads_alone_refuses_older_and_layout1_trees: sends v1 and layout-1 trees to the
        whole-show comparison
    test_reading_a_refused_tree_alone_fails_closed: raises for every changed document a
        whole-show decode refuses, whichever way the tree is read
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.choreo.recipe_models import LineRecipe
from e8s.core.errors import E8sError
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.geo.shapes import line
from e8s.show.changes import apply_change
from e8s.show.collab_models import CountsOp, FormOp, PathsOp
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_paths import CAST_DOC, forms_doc, paths_doc, role_docs, set_doc
from e8s.show.documents import (
    SHEET_DOC,
    SHOW_DOC,
    TIMELINE_DOC,
    decode_show,
    dump_json,
    encode_show,
)
from e8s.show.integrity import LazyBlobs, blob_digest, changed_paths
from e8s.show.items import changes
from e8s.show.manage import add_set, insert_subset
from e8s.show.models import Show
from e8s.show.pinning import pin
from e8s.show.renumber import renumber
from e8s.show.show_io import load_show, save_show
from e8s.show.tree_items import reads_alone, tree_changes
from e8s.show.tree_parts import TreeParts
from e8s.time.models import TempoSection
from tests.fixtures.layout1 import layout1_documents
from tests.fixtures.shows import two_set_show
from tests.helpers import open_session

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)
FORM = Form(
    id=FormId("f1"),
    set=SetId("2"),
    recipe=LineRecipe(curve=line((0.0, 0.0), (6.0, 0.0))),
    members=(SpotId("T1"), SpotId("T2")),
)
MAX_EXAMPLES = 25
MAX_EDITS = 6
SETS = ("2", "3", "4")
FAST_KINDS = (
    "move",
    "face",
    "note",
    "counts",
    "label",
    "form_add",
    "form_edit",
    "form_delete",
    "paths",
    "paths_delete",
    "sheet",
    "timeline",
)
TEMPO = 120.0


def _four_set_show() -> Show:
    return add_set(add_set(two_set_show(), "3", 4.0), "4", 6.0)


def _patched(show: Show, set_id: str, entity: str, patch: PlacementPatch) -> Show:
    position = show.set_index(set_id)
    edited = show.sets[position].with_placements({entity: patch}, force=True)
    return show.replace(sets=(*show.sets[:position], edited, *show.sets[position + 1 :]))


def _without_form(show: Show) -> Show:
    kept = {k: f for k, f in show.forms.items() if k != "f1"}
    sets = tuple(s.replace(forms=tuple(f for f in s.forms if f != "f1")) for s in show.sets)
    return show.replace(forms=kept, sets=sets)


def _with_form(show: Show, form: Form) -> Show:
    return apply_change(show, [FormOp(form=form)], force=True)


def _with_paths(show: Show, number: int) -> Show:
    row = PathRow(entity=SpotId("T2"), start=number % 4)
    spec = TransitionSpec(id=make_tid("2", "3"), counts=4.0, rows={SpotId("T2"): row})
    return apply_change(show, [PathsOp(spec=spec)], force=True)


def _relabelled(show: Show, number: int) -> Show:
    position = show.set_index("3")
    edited = show.sets[position].replace(label=f"hit {number}")
    return show.replace(sets=(*show.sets[:position], edited, *show.sets[position + 1 :]))


def _retimed(show: Show, number: int) -> Show:
    tempo = TempoSection(at_count=0.0, counts_per_minute=TEMPO + number)
    return show.replace(timeline=show.timeline.replace(tempos=(tempo,)))


def _edit(show: Show, kind: str, number: int) -> Show:
    set_id = SETS[number % len(SETS)]
    edits: dict[str, Callable[[], Show]] = {
        "move": lambda: _patched(show, set_id, "T1", PlacementPatch(x=float(number))),
        "face": lambda: _patched(show, set_id, "T2", PlacementPatch(facing=10.0 * number)),
        "note": lambda: _patched(show, set_id, "T3", PlacementPatch(note=f"note {number}")),
        "counts": lambda: apply_change(show, [CountsOp(set_id=SetId("3"), counts=4.0 + number)]),
        "label": lambda: _relabelled(show, number),
        "form_add": lambda: _with_form(show, FORM),
        "form_edit": lambda: _with_form(show, FORM.replace(members=FORM.members[::-1])),
        "form_delete": lambda: _without_form(show),
        "paths": lambda: _with_paths(show, number),
        "paths_delete": lambda: show.replace(transitions=()),
        "sheet": lambda: show.replace(sheet=show.sheet.replace(name=f"field {number}")),
        "timeline": lambda: _retimed(show, number),
    }
    return edits[kind]()


def _lazy(docs: dict[str, bytes]) -> LazyBlobs:
    blobs = {blob_digest(raw): raw for raw in docs.values()}
    return LazyBlobs({path: blob_digest(raw) for path, raw in docs.items()}, blobs.__getitem__)


def _parts(
    before: dict[str, bytes], after: dict[str, bytes], cache: DecodeCache | None = None
) -> tuple[TreeParts, TreeParts]:
    shared = DecodeCache() if cache is None else cache
    return TreeParts(_lazy(before), shared), TreeParts(_lazy(after), shared)


@settings(max_examples=MAX_EXAMPLES, deadline=None)
@example(["form_add", "form_edit", "form_delete"])
@example(["paths", "counts", "paths_delete"])
@example(["move", "face", "note", "label", "sheet", "timeline"])
@given(st.lists(st.sampled_from(FAST_KINDS), min_size=1, max_size=MAX_EDITS))
def test_tree_changes_equal_whole_show_changes_after_random_edits(kinds: list[str]) -> None:
    _, agent, designer = open_session(_four_set_show(), AT)
    for number, kind in enumerate(kinds):
        save_show(designer, _edit(load_show(designer), kind, number), AT + (number + 1) * STEP)
    cache = DecodeCache()
    for revision in agent.repo.revisions(agent.show_id)[1:]:
        old, new = agent.tree_at(revision.parent), agent.tree_at(revision.id)
        paths = changed_paths(old, new)
        before, after = TreeParts(old, cache), TreeParts(new, cache)
        assert reads_alone(before, after, paths)
        was, now = agent.show_at(revision.parent), agent.show_at(revision.id)
        assert was is not None
        assert now is not None
        assert tree_changes(before, after, paths) == changes(was, now)


def test_tree_changes_decode_only_the_changed_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    show = _with_paths(_four_set_show(), 1)
    edited = apply_change(
        _patched(show, "3", "T1", PlacementPatch(x=-4.0)), [CountsOp(set_id=SetId("3"), counts=7.0)]
    )
    old, new = encode_show(show), encode_show(edited)
    cache = DecodeCache()
    before, after = _parts(old, new, cache)
    for key in before.set_keys():
        before.set(key)
    before.stored_paths(("2", "3"))
    misses = cache.misses
    validated: list[object] = []
    original = Show.model_validate

    def recording(data: object, **kwargs: Any) -> Show:
        validated.append(data)
        return original(data, **kwargs)

    monkeypatch.setattr(Show, "model_validate", recording)
    paths = changed_paths(old, new)
    assert paths == [role_docs("3")[0], set_doc("3"), paths_doc(("2", "3"))]
    assert reads_alone(before, after, paths)
    found = tree_changes(before, after, paths)
    assert {(c.key.kind, c.key.role) for c in found.changes} == {
        ("placement", "drill"),
        ("set", "timing"),
        ("transition", None),
    }
    assert cache.misses - misses == len(paths) - 1
    assert validated == []


def test_tree_changes_retime_the_stored_paths_into_a_set_whose_counts_changed() -> None:
    show = _with_paths(_four_set_show(), 1)
    old = encode_show(show)
    new = _with_json(set_doc("3"), lambda body: {**body, "counts": 9.0})(old)
    paths = changed_paths(old, new)
    assert paths == [set_doc("3")]
    before, after = _parts(old, new)
    assert reads_alone(before, after, paths)
    found = tree_changes(before, after, paths)
    assert found == changes(decode_show(old), decode_show(new))
    assert {c.key.kind for c in found.changes} == {"set", "transition"}


def _renumbered(show: Show) -> Show:
    return renumber(show)[0]


@pytest.mark.parametrize(
    "edit",
    [
        lambda show: pin(show, "entity", "T4", "hold"),
        lambda show: add_set(show, "5", 4.0),
        lambda show: show.replace(
            cast=show.cast.replace(
                spots=(*show.cast.spots[:3], show.cast.spots[3].replace(color="#ff0000"))
            )
        ),
        _renumbered,
    ],
    ids=["pin", "new set", "cast", "renumber"],
)
def test_reads_alone_refuses_show_cast_and_set_id_changes(edit: Callable[[Show], Show]) -> None:
    base = insert_subset(_four_set_show(), "2", 2.0)[0]
    old, new = encode_show(base), encode_show(edit(base))
    paths = changed_paths(old, new)
    assert paths
    assert not reads_alone(*_parts(old, new), paths)


def test_reads_alone_refuses_older_and_layout1_trees() -> None:
    show = _four_set_show()
    moved = _patched(show, "3", "T1", PlacementPatch(x=-4.0))
    layout1, layout1_moved = layout1_documents(show), layout1_documents(moved)
    assert changed_paths(layout1, layout1_moved) == [set_doc("3")]
    assert not reads_alone(*_parts(layout1, layout1_moved), [set_doc("3")])
    older = json.loads(encode_show(show)[SHOW_DOC])
    headless = {k: v for k, v in older.items() if k not in ("format", "version")}
    old_docs = {**encode_show(show), SHOW_DOC: dump_json(headless)}
    old_moved = {**encode_show(moved), SHOW_DOC: old_docs[SHOW_DOC]}
    assert not reads_alone(*_parts(old_docs, old_moved), changed_paths(old_docs, old_moved))


def _with_json(path: str, change: Callable[[Any], object]) -> Callable[[dict[str, bytes]], Any]:
    def broken(docs: dict[str, bytes]) -> dict[str, bytes]:
        return {**docs, path: dump_json(change(json.loads(docs[path])))}

    return broken


def _refused_trees() -> dict[str, Callable[[dict[str, bytes]], dict[str, bytes]]]:
    drill = role_docs("2")[0]
    stray_form = FORM.replace(set=SetId("1")).to_json()
    return {
        "bad set": lambda docs: {**docs, set_doc("1"): b'{"id": "1"}'},
        "bad drill row": _with_json(drill, lambda rows: {**rows, "T1": {**rows["T1"], "x": "a"}}),
        "missing role": lambda docs: {p: raw for p, raw in docs.items() if p != drill},
        "set key moved": _with_json(set_doc("2"), lambda body: {**body, "key": "9"}),
        "bad paths": lambda docs: {**docs, paths_doc(("1", "2")): b'{"unexpected": []}'},
        "paths of other sets": lambda docs: {
            **docs,
            paths_doc(("1", "2")): dump_json({"id": ["2", "1"], "counts": 8.0}),
        },
        "form of another set": lambda docs: {**docs, forms_doc("2"): dump_json([stray_form])},
        "bad sheet": lambda docs: {**docs, SHEET_DOC: b'{"unexpected": 1}'},
        "bad timeline": lambda docs: {**docs, TIMELINE_DOC: b"[]"},
        "unlisted role documents": lambda docs: {**docs, role_docs("9")[0]: b"{}"},
        "unknown cast document": lambda docs: {**docs, "/cast/extra.json": b"{}"},
        "bad cast": lambda docs: {**docs, CAST_DOC: b'{"groups": 5}'},
    }


def _read(before: dict[str, bytes], after: dict[str, bytes]) -> None:
    old, new = _parts(before, after)
    paths = changed_paths(before, after)
    if reads_alone(old, new, paths):
        tree_changes(old, new, paths)
    else:
        decode_show(after)


@pytest.mark.parametrize("name", sorted(_refused_trees()))
def test_reading_a_refused_tree_alone_fails_closed(name: str) -> None:
    base = encode_show(two_set_show())
    broken = _refused_trees()[name](base)
    with pytest.raises(E8sError):
        decode_show(broken)
    with pytest.raises(E8sError):
        _read(base, broken)
