#!/usr/bin/env python3

"""
test_events_parity.py --- tests that events read from changed documents equal whole-show events

The reference below is the derivation events_since used while it compared two whole shows per
revision, kept here unchanged as the oracle: reading a revision from its changed documents must
give exactly the events it gave.

Contains:
    test_events_since_equals_the_whole_show_derivation_after_random_edits: gives the same events,
        in the same order, as the whole-show derivation over random edits of every kind
    test_events_since_reads_comment_only_and_reverted_revisions_like_whole_shows: gives the same
        events for comment-only revisions and a revert
"""

import itertools
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta

from hypothesis import example, given, settings
from hypothesis import strategies as st

from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.choreo.recipe_models import LineRecipe
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.geo.shapes import line
from e8s.show.anchors import Anchor
from e8s.show.change_models import ItemChange
from e8s.show.changes import apply_change
from e8s.show.collab_models import Comment, CountsOp, FormOp, PathsOp, PlacementOp
from e8s.show.documents import dump_json, is_show_doc
from e8s.show.events import events_since
from e8s.show.history import revert
from e8s.show.integrity import changed_paths
from e8s.show.item_table import TIMING, set_keys_by_id, transition_key
from e8s.show.items import changes
from e8s.show.manage import add_set, insert_subset, remove_set
from e8s.show.models import Show
from e8s.show.pinning import pin, unpin
from e8s.show.renumber import renumber
from e8s.show.set_keys import set_key
from e8s.show.show_io import load_show, save_show
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import COMMENTS_DOC
from e8s.show.workspace import Workspace, open_workspace
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
MAX_EXAMPLES = 30
MAX_EDITS = 7
KINDS = (
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
    "pin",
    "unpin",
    "insert",
    "renumber",
    "remove",
    "cast",
)
MIN_SETS_TO_REMOVE = 4

type _Found = tuple[str, object, str]


def _reference_pins(before: Show, after: Show) -> list[tuple[str, object]]:
    removed = [p for p in before.pins if p not in after.pins]
    added = [p for p in after.pins if p not in before.pins]
    return [*(("unpin", p) for p in removed), *(("pin", p) for p in added)]


def _reference_sets(
    delta: Iterable[ItemChange], before: Show, after: Show
) -> list[tuple[str, object]]:
    old = {set_key(s) for s in before.sets}
    timing: set[str] = set()
    moved: dict[str, set[str]] = {}
    for change in delta:
        key, path = change.key, change.key.path
        if key.kind not in ("set", "placement"):
            continue
        if change.before is None or change.after is None or path[0] not in old:
            continue
        if key.kind == "set" and key.role == TIMING:
            timing.add(path[0])
        elif key.kind == "placement":
            moved.setdefault(path[0], set()).add(path[1])
    counts: list[tuple[str, object]] = []
    placements: list[tuple[str, object]] = []
    for current in after.sets:
        name = set_key(current)
        if name in timing:
            counts.append(("set_counts", CountsOp(set_id=current.id, counts=current.counts)))
        entities = moved.get(name, set())
        placements += [
            ("move", PlacementOp(set_id=current.id, placement=p))
            for p in current.placements
            if p.entity in entities
        ]
    return [*counts, *placements]


def _without_counts(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    return {name: field for name, field in value.items() if name != "counts"}


def _reference_paths(
    delta: Iterable[ItemChange], before: Show, after: Show
) -> list[tuple[str, object]]:
    names: set[str] = set()
    for change in delta:
        if change.key.kind not in ("transition", "path_row", "route"):
            continue
        if change.key.kind == "transition" and _without_counts(change.before) == _without_counts(
            change.after
        ):
            continue
        names.add(change.key.path[0])
    keys = set_keys_by_id(after)
    shared = set(before.transition_ids())
    return [
        ("edit_paths", PathsOp(spec=after.transition(tid)))
        for tid in after.transition_ids()
        if tid in shared and transition_key(keys, tid) in names
    ]


def _reference_forms(
    delta: Iterable[ItemChange], before: Show, after: Show
) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    for change in sorted(delta, key=lambda c: c.key.path):
        if change.key.kind != "form":
            continue
        form_id = FormId(change.key.path[0])
        if change.after is None:
            found.append(("delete_form", FormOp(form=before.forms[form_id])))
        else:
            found.append(("edit_form", FormOp(form=after.forms[form_id])))
    return found


def _reference_show_events(before: Show, after: Show) -> list[tuple[str, object]]:
    delta = changes(before, after).changes
    return [
        *_reference_pins(before, after),
        *_reference_paths(delta, before, after),
        *_reference_sets(delta, before, after),
        *_reference_forms(delta, before, after),
    ]


def _reference(ws: Workspace, revision: str) -> list[_Found]:
    """Derives show events as events_since did by comparing whole shows, comments left out."""
    log = ws.repo.revisions(ws.show_id)
    if revision != ROOT_REVISION:
        log = log[[r.id for r in log].index(revision) + 1 :]
    current = None if revision == ROOT_REVISION else ws.show_at(revision)
    found: list[_Found] = []
    for r in log:
        if not any(is_show_doc(p) for p in changed_paths(ws.tree_at(r.parent), ws.tree_at(r.id))):
            continue
        after = ws.show_at(r.id)
        if current is not None and after is not None:
            found += [
                (kind, payload, r.id) for kind, payload in _reference_show_events(current, after)
            ]
        current = after
    return found


def _derived(ws: Workspace, revision: str) -> list[_Found]:
    return [
        (e.kind, e.payload, e.revision) for e in events_since(ws, revision) if e.kind != "comment"
    ]


def _base() -> Show:
    return add_set(add_set(two_set_show(), "3", 4.0), "4", 6.0)


def _patched(show: Show, number: int, patch: PlacementPatch) -> Show:
    position = 1 + number % (len(show.sets) - 1)
    entity = show.cast.ids()[number % len(show.cast.spots)]
    edited = show.sets[position].with_placements({entity: patch}, force=True)
    return show.replace(sets=(*show.sets[:position], edited, *show.sets[position + 1 :]))


def _with_paths(show: Show, number: int) -> Show:
    first, second = show.set_ids()[1], show.set_ids()[2]
    row = PathRow(entity=SpotId("T2"), start=number % 3)
    spec = TransitionSpec(
        id=make_tid(first, second), counts=show.set(second).counts, rows={SpotId("T2"): row}
    )
    return apply_change(show, [PathsOp(spec=spec)], force=True)


def _without_form(show: Show) -> Show:
    kept = {k: f for k, f in show.forms.items() if k != "f1"}
    sets = tuple(s.replace(forms=tuple(f for f in s.forms if f != "f1")) for s in show.sets)
    return show.replace(forms=kept, sets=sets)


def _relabelled(show: Show, number: int) -> Show:
    edited = show.sets[2].replace(label=f"hit {number}")
    return show.replace(sets=(*show.sets[:2], edited, *show.sets[3:]))


def _recoloured(show: Show, number: int) -> Show:
    spots = show.cast.spots
    spot = spots[number % len(spots)].replace(color=f"#00{number % 10}000")
    edited = tuple(spot if s.id == spot.id else s for s in spots)
    return show.replace(cast=show.cast.replace(spots=edited))


def _removed(show: Show) -> Show:
    if len(show.sets) < MIN_SETS_TO_REMOVE:
        return show
    return remove_set(show, show.set_ids()[-1], force=True)


def _edit(show: Show, kind: str, number: int) -> Show:
    retimed = show.set_ids()[2]
    edits: dict[str, Callable[[], Show]] = {
        "move": lambda: _patched(show, number, PlacementPatch(x=-1.0 - number)),
        "face": lambda: _patched(show, number, PlacementPatch(facing=10.0 * number)),
        "note": lambda: _patched(show, number, PlacementPatch(note=f"note {number}")),
        "counts": lambda: apply_change(show, [CountsOp(set_id=retimed, counts=4.0 + number)]),
        "label": lambda: _relabelled(show, number),
        "form_add": lambda: apply_change(show, [FormOp(form=FORM)], force=True),
        "form_edit": lambda: apply_change(
            show, [FormOp(form=FORM.replace(members=FORM.members[::-1]))], force=True
        ),
        "form_delete": lambda: _without_form(show),
        "paths": lambda: _with_paths(show, number),
        "paths_delete": lambda: show.replace(transitions=()),
        "sheet": lambda: show.replace(sheet=show.sheet.replace(name=f"field {number}")),
        "pin": lambda: pin(show, "entity", "T4", f"hold {number}"),
        "unpin": lambda: unpin(show, "entity", "T4") if show.pins else show,
        "insert": lambda: insert_subset(show, "2", 2.0, force=True)[0],
        "renumber": lambda: renumber(show)[0],
        "remove": lambda: _removed(show),
        "cast": lambda: _recoloured(show, number),
    }
    return edits[kind]()


@settings(max_examples=MAX_EXAMPLES, deadline=None)
@example(["insert", "renumber", "move", "paths", "counts"])
@example(["form_add", "pin", "form_edit", "unpin", "form_delete"])
@example(["cast", "remove", "face", "note", "label", "sheet", "paths_delete"])
@given(st.lists(st.sampled_from(KINDS), min_size=1, max_size=MAX_EDITS))
def test_events_since_equals_the_whole_show_derivation_after_random_edits(kinds: list[str]) -> None:
    repo, agent, designer = open_session(_base(), AT)
    base = designer.revision
    for number, kind in enumerate(kinds):
        save_show(designer, _edit(load_show(designer), kind, number), AT + (number + 1) * STEP)
    expected = _reference(open_workspace(repo, agent.show_id), base)
    assert _derived(agent, base) == expected
    assert _derived(open_workspace(repo, agent.show_id), ROOT_REVISION) == _reference(
        open_workspace(repo, agent.show_id), ROOT_REVISION
    )


def test_events_since_reads_comment_only_and_reverted_revisions_like_whole_shows() -> None:
    repo, agent, designer = open_session(_base(), AT)
    base = designer.revision
    save_show(designer, _edit(load_show(designer), "move", 1), AT + STEP)
    comment = Comment(id="c1", anchor=Anchor.on_set("2"), text="tighten the line")
    designer.write(COMMENTS_DOC, dump_json([comment._dumped()]), at=AT + 2 * STEP)
    save_show(designer, _edit(load_show(designer), "counts", 2), AT + 3 * STEP)
    revert(designer, base, AT + 4 * STEP)
    found = events_since(agent, base)
    assert [e.kind for e in found] == ["move", "comment", "set_counts", "set_counts", "move"]
    assert _derived(agent, base) == _reference(open_workspace(repo, agent.show_id), base)
    pairs = itertools.pairwise(agent.repo.revisions(agent.show_id))
    assert all(later.parent == earlier.id for earlier, later in pairs)
