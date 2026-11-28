"""
tree_items.py --- lists the items two stored trees differ in, reading only the changed documents

`changes()` compares two whole shows. When two trees differ only in set, role, forms, paths,
sheet or timeline documents, and keep every set's ID, the items they differ in follow from those
documents alone: a set's documents hold its placements and own items, a forms document its forms,
a paths document its transition's rows and routes. The one wider read is a set's new counts,
which the paths into that set take, so those paths are read too.

A changed show.json, cast document or set ID reaches items stored in other documents (the set
order, pins, the transitions and forms that name a set), so such trees are compared whole.

Contains:
    changed_set_keys(): lists the listed sets whose set or role documents changed
    reads_alone(): reports whether the items two trees differ in follow from the changed documents
    tree_changes(): lists the items two trees differ in, decoding only the changed documents
    changes_alone(): lists those items when the changed documents hold them all, else None
"""

import itertools
import operator
from collections.abc import Collection, Iterator

from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError
from e8s.show.change_models import ChangeSet, ItemChange, ItemKey
from e8s.show.document_parts import is_same_float
from e8s.show.document_paths import (
    CAST_DOC,
    SHEET_DOC,
    TIMELINE_DOC,
    forms_doc,
    is_set_document,
    paths_doc,
    set_doc,
    transition_folder,
)
from e8s.show.item_entries import (
    Difference,
    entry_differences,
    form_entries,
    is_same_form,
    placement_entries,
    route_entries,
    row_entries,
    set_own_entries,
    sheet_entries,
    source_differences,
    timeline_entries,
    transition_entries,
    transition_own_entries,
)
from e8s.show.migrate import SHOW_DOC
from e8s.show.tree_parts import TreeParts

_SET_FOLDER_INDEX = 2


def changed_set_keys(parts: TreeParts, paths: Collection[str]) -> list[str]:
    """Lists the sets of `parts` whose set.json or role documents are among `paths`, in show order.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.documents import encode_show
    >>> changed_set_keys(TreeParts(encode_show(two_set_show())), ["/sets/2/drill.json"])
    ['2']
    """
    folders = {p.split("/")[_SET_FOLDER_INDEX] for p in paths if is_set_document(p)}
    return [key for key in parts.set_keys() if key in folders]


def reads_alone(before: TreeParts, after: TreeParts, paths: Collection[str]) -> bool:
    """Reports whether the items two trees differ in follow from the documents in `paths` alone.

    Both trees must be current role trees, neither show.json nor the cast document may change,
    and every set whose set.json changed must keep its ID. The changed sets are decoded to check
    that; a set that fails its checks raises SchemaError.

    Args:
        before: The older tree.
        after: The newer tree.
        paths: The paths whose blobs differ between the two trees.

    Returns:
        is_alone: Whether tree_changes() lists every item the trees differ in.
    """
    if SHOW_DOC in paths or CAST_DOC in paths:
        return False
    if not before.is_role_tree() or not after.is_role_tree():
        return False
    renamed = (
        key
        for key in changed_set_keys(after, paths)
        if set_doc(key) in paths and before.set(key).id != after.set(key).id
    )
    return next(renamed, None) is None


def _set_differences(key: str, was: Set, now: Set) -> Iterator[Difference]:
    """Lists the items of one set that differ, expanding only placements that are not equal."""
    if was is now:
        return
    yield from source_differences(
        {(p.entity,): p for p in was.placements},
        {(p.entity,): p for p in now.placements},
        lambda spot, placement: placement_entries(key, spot[0], placement),
        operator.eq,
    )
    yield from entry_differences(set_own_entries((key,), was), set_own_entries((key,), now))


def _transition_differences(
    key: str, was: TransitionSpec | None, now: TransitionSpec | None
) -> Iterator[Difference]:
    """Lists the items of one transition that differ, expanding only rows and routes not equal."""
    if was is now:
        return
    path = (key,)
    if was is None or now is None:
        yield from entry_differences(
            () if was is None else transition_entries(path, was),
            () if now is None else transition_entries(path, now),
        )
        return
    yield from entry_differences(
        transition_own_entries(path, was), transition_own_entries(path, now)
    )
    yield from source_differences(
        {(entity,): row for entity, row in was.rows.items()},
        {(entity,): row for entity, row in now.rows.items()},
        lambda spot, row: row_entries(key, spot[0], row),
        operator.eq,
    )
    yield from source_differences(
        {(route_id,): route for route_id, route in was.routes.items()},
        {(route_id,): route for route_id, route in now.routes.items()},
        lambda route_path, route: route_entries(key, route_path[0], route),
        operator.eq,
    )


def _counted_folders(before: TreeParts, after: TreeParts, sets: list[str]) -> set[tuple[str, str]]:
    """Lists the transitions into changed sets whose counts changed, which take the new counts."""
    recounted = {
        key for key in sets if not is_same_float(before.set(key).counts, after.set(key).counts)
    }
    return {pair for pair in itertools.pairwise(after.set_keys()) if pair[1] in recounted}


def _differences(
    before: TreeParts, after: TreeParts, paths: Collection[str]
) -> Iterator[Difference]:
    """Lists the differing items of the sets, transitions, forms, sheet and timeline changed."""
    sets = changed_set_keys(after, paths)
    for key in sets:
        yield from _set_differences(key, before.set(key), after.set(key))
    pairs = itertools.pairwise(after.set_keys())
    folders = {pair for pair in pairs if paths_doc(pair) in paths}
    for folder in sorted(folders | _counted_folders(before, after, sets)):
        yield from _transition_differences(
            transition_folder(folder), before.stored_paths(folder), after.stored_paths(folder)
        )
    if any(forms_doc(key) in paths for key in after.set_keys()):
        yield from source_differences(
            before.form_sources(), after.form_sources(), form_entries, is_same_form
        )
    if SHEET_DOC in paths:
        yield from entry_differences(
            sheet_entries((), before.sheet()), sheet_entries((), after.sheet())
        )
    if TIMELINE_DOC in paths:
        yield from entry_differences(
            timeline_entries((), before.timeline()), timeline_entries((), after.timeline())
        )


def tree_changes(before: TreeParts, after: TreeParts, paths: Collection[str]) -> ChangeSet:
    """Lists the items two trees differ in, decoding only the documents in `paths`.

    Call it only when reads_alone() holds; it then equals changes() of the two trees' shows.
    Each changed document is decoded and checked as a whole-show decode checks it, so one that
    fails raises SchemaError. The after tree's paths get the whole-show decode's path checks.

    Args:
        before: The older tree.
        after: The newer tree.
        paths: The paths whose blobs differ between the two trees.

    Returns:
        change_set: One change per differing item, in key order.

    >>> from e8s.choreo.models import PlacementPatch
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.documents import encode_show
    >>> from e8s.show.integrity import changed_paths
    >>> show = two_set_show()
    >>> moved = show.sets[1].with_placements({"T1": PlacementPatch(x=3.0)})
    >>> old, new = encode_show(show), encode_show(show.replace(sets=(show.sets[0], moved)))
    >>> paths = changed_paths(old, new)
    >>> [c.key.role for c in tree_changes(TreeParts(old), TreeParts(new), paths).changes]
    ['drill']
    """
    after.require_known_paths()
    found = (
        ItemChange(key=ItemKey(kind=kind, path=path, role=role), before=was, after=now)
        for (kind, path, role), was, now in _differences(before, after, paths)
    )
    return ChangeSet(changes=tuple(sorted(found, key=lambda change: change.key.sort_key())))


def changes_alone(before: TreeParts, after: TreeParts, paths: Collection[str]) -> ChangeSet | None:
    """Lists the items two trees differ in from the changed documents, or None when it cannot.

    None means reads_alone() does not hold and the two shows must be compared whole. A changed
    document that fails its checks raises the error a whole-show decode of `after` raises, so
    its code, message, context and hint are read_show's.

    Args:
        before: The older tree.
        after: The newer tree.
        paths: The paths whose blobs differ between the two trees.

    Returns:
        change_set: One change per differing item, in key order, or None.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.documents import encode_show
    >>> docs = encode_show(two_set_show())
    >>> changes_alone(TreeParts(docs), TreeParts(docs), []).changes
    ()
    """
    try:
        if not reads_alone(before, after, paths):
            return None
        return tree_changes(before, after, paths)
    except SchemaError as exc:
        raise after.refusal(exc) from exc
