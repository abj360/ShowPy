"""
diff_render.py --- renders the item changes between two shows as the JSON-safe `diff_show` dict

Every item kind lands under one key of the result, so no stored edit goes unreported. Set IDs in
the result are display IDs; item paths hold set keys, which this module maps back through the two
shows.

Contains:
    SetDiffer: the callable type that lists what differs between two sets
    PathDiffer: the callable type that lists what differs between two transitions
    render_changes(): renders a change set as the `diff_show` dict, leaving out empty keys
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import parse_tid
from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import thaw_json
from e8s.core.types import FrozenModel
from e8s.core.util import natural_sort
from e8s.show.change_models import ChangeSet
from e8s.show.document_paths import transition_folder
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key

type SetDiffer = Callable[[Set, Set], FrozenModel]
type PathDiffer = Callable[[TransitionSpec, TransitionSpec], FrozenModel]
type _Obj = dict[str, JsonValue]
type _Path = tuple[str, ...]

ORDER_CHANGED = "order_changed"
TRANSITION_FIELDS = ("counts", "notes", "source_hash")
WHOLE_FIELD_FLAGS = ("sheet", "timeline", "facing_overrides")
SET_TARGET = "set"
TRANSITION_TARGET = "transition"


@dataclass(frozen=True)
class _Delta:
    """Holds one item change with its values read as plain JSON.

    Attributes:
        path: The item's path of stable IDs.
        role: The item's role, or None for the whole item.
        before: The item's value in the older show; None when added.
        after: The item's value in the newer show; None when removed.
    """

    path: _Path
    role: str | None
    before: JsonValue
    after: JsonValue

    @property
    def is_edit(self) -> bool:
        """Reports whether the item exists in both shows."""
        return self.before is not None and self.after is not None


def _thawed(value: object) -> JsonValue:
    return thaw_json(cast("FrozenJsonValue", value))


def _fields(value: JsonValue) -> _Obj:
    return value if isinstance(value, dict) else {}


def _names(value: JsonValue) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _sorted(names: Iterable[str]) -> list[JsonValue]:
    ordered: list[JsonValue] = [*natural_sort(names)]
    return ordered


def _compact(found: _Obj) -> _Obj:
    return {key: value for key, value in found.items() if value}


def _dumped(model: FrozenModel) -> _Obj:
    return cast("_Obj", model._dumped(exclude_defaults=True))


def _pair(delta: _Delta, field: str) -> list[JsonValue]:
    return [_fields(delta.before).get(field), _fields(delta.after).get(field)]


def _differing(delta: _Delta, fields: Iterable[str]) -> _Obj:
    pairs = {field: _pair(delta, field) for field in fields}
    return {field: pair for field, pair in pairs.items() if pair[0] != pair[1]}


def _keyed(deltas: Iterable[_Delta], names: dict[_Path, str] | None = None) -> _Obj:
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []
    for delta in deltas:
        name = names[delta.path] if names else delta.path[0]
        bucket = added if delta.before is None else removed if delta.after is None else changed
        bucket.append(name)
    return _compact(
        {
            "added": _sorted(added),
            "removed": _sorted(removed),
            "changed": _sorted(changed),
        }
    )


def _is_reordered(deltas: Iterable[_Delta]) -> bool:
    for delta in deltas:
        before, after = _names(delta.before), _names(delta.after)
        kept = set(before) & set(after)
        if [item for item in before if item in kept] != [item for item in after if item in kept]:
            return True
    return False


def _transition_keys(show: Show) -> dict[str, tuple[str, str]]:
    keys = {current.id: set_key(current) for current in show.sets}
    return {
        transition_folder((keys[tid.from_set], keys[tid.to_set])): tid
        for tid in show.transition_ids()
    }


def _pin_path(keys: dict[str, str], record: Pin) -> _Path:
    if record.target == SET_TARGET:
        return record.target, keys.get(record.id, record.id)
    if record.target == TRANSITION_TARGET:
        tid = parse_tid(record.id)
        pair = (keys.get(tid.from_set, tid.from_set), keys.get(tid.to_set, tid.to_set))
        return record.target, transition_folder(pair)
    return record.target, record.id


def _pin_names(show: Show) -> dict[_Path, str]:
    keys = {str(current.id): set_key(current) for current in show.sets}
    return {_pin_path(keys, record): f"{record.target}:{record.id}" for record in show.pins}


class _Render:
    """Renders one change set between two shows, one method per key of the result.

    Attributes:
        before: The older show.
        after: The newer show.
    """

    def __init__(
        self,
        before: Show,
        after: Show,
        found: ChangeSet,
        set_differ: SetDiffer,
        path_differ: PathDiffer,
    ) -> None:
        self.before, self.after = before, after
        self._set_differ, self._path_differ = set_differ, path_differ
        self._old_sets = {set_key(s): s for s in before.sets}
        self._new_sets = {set_key(s): s for s in after.sets}
        self._grouped: dict[str, list[_Delta]] = {}
        for change in found.changes:
            key = change.key
            delta = _Delta(key.path, key.role, _thawed(change.before), _thawed(change.after))
            self._grouped.setdefault(key.kind, []).append(delta)

    def deltas(self, *kinds: str) -> list[_Delta]:
        """Lists the item changes of the given kinds."""
        return [delta for kind in kinds for delta in self._grouped.get(kind, [])]

    def sets(self) -> _Obj:
        """Renders added, removed, renamed and changed sets, counts and order."""
        touched = {delta.path[0] for delta in self.deltas("set", "placement")}
        both = natural_sort(k for k in touched if k in self._old_sets and k in self._new_sets)
        changed = {self._new_sets[k].id: self._set_change(k) for k in both}
        timing = [d for d in self.deltas("set") if d.role == "timing" and d.is_edit]
        renames: list[JsonValue] = [
            [self._old_sets[k].id, self._new_sets[k].id]
            for k in both
            if self._old_sets[k].id != self._new_sets[k].id
        ]
        return _compact(
            {
                "added": _sorted(self._new_sets[k].id for k in touched - self._old_sets.keys()),
                "removed": _sorted(self._old_sets[k].id for k in touched - self._new_sets.keys()),
                "renamed": renames,
                "changed": {set_id: change for set_id, change in changed.items() if change},
                "counts": {self._new_sets[d.path[0]].id: _pair(d, "counts") for d in timing},
                ORDER_CHANGED: _is_reordered(self.deltas("set_order")),
            }
        )

    def _set_change(self, key: str) -> _Obj:
        found = _dumped(self._set_differ(self._old_sets[key], self._new_sets[key]))
        pinned: list[JsonValue] = []
        for delta in self.deltas("set", "placement"):
            if delta.path[0] != key or not delta.is_edit:
                continue
            if delta.role == "build":
                found["build"] = True
            elif delta.role == "pin":
                pinned.append(delta.path[1])
            elif delta.role == "placements" and _is_reordered([delta]):
                found["reordered"] = True
        return _compact({**found, "pinned": pinned})

    def labels(self) -> _Obj:
        """Renders changed set labels and cues and changed placement notes."""
        sets: _Obj = {}
        notes: dict[str, _Obj] = {}
        for delta in self.deltas("set"):
            if delta.role == "labels" and delta.is_edit:
                found = _differing(delta, ("label", "cue"))
                sets[self._new_sets[delta.path[0]].id] = found
        for delta in self.deltas("placement"):
            if delta.role == "labels" and delta.is_edit:
                per_set = notes.setdefault(self._new_sets[delta.path[0]].id, {})
                per_set[delta.path[1]] = _pair(delta, "note")
        return _compact({"sets": _compact(sets), "notes": dict(notes.items())})

    def forms(self) -> _Obj:
        """Renders added, removed and changed forms."""
        return _keyed(self.deltas("form"))

    def paths(self) -> _Obj:
        """Renders transitions whose rows, routes, counts, notes or build hash differ."""
        old_ids, new_ids = _transition_keys(self.before), _transition_keys(self.after)
        keys = {d.path[0] for d in self.deltas("transition", "path_row", "route")}
        found: _Obj = {}
        for key in natural_sort(keys & old_ids.keys() & new_ids.keys()):
            was = self.before.transition(old_ids[key])
            now = self.after.transition(new_ids[key])
            change = _dumped(self._path_differ(was, now))
            for field in TRANSITION_FIELDS:
                if getattr(was, field) != getattr(now, field):
                    change[field] = [getattr(was, field), getattr(now, field)]
            if change:
                found[str(self.after.transition_id(new_ids[key]))] = change
        return found

    def cast(self) -> _Obj:
        """Renders added, removed and changed spots, their order and the label scheme."""
        found = _keyed(self.deltas("spot"))
        scheme = any(
            _fields(d.before).get("label_scheme") != _fields(d.after).get("label_scheme")
            for d in self.deltas("show")
        )
        return _compact(
            {
                **found,
                ORDER_CHANGED: _is_reordered(self.deltas("spot_order")),
                "label_scheme": scheme,
            }
        )

    def pins(self) -> _Obj:
        """Renders added, removed and changed pins and their order."""
        names = {**_pin_names(self.before), **_pin_names(self.after)}
        found = _keyed(self.deltas("pin"), names)
        order = [
            _Delta(
                (),
                None,
                [":".join(p) for p in _paths(d.before)],
                [":".join(p) for p in _paths(d.after)],
            )
            for d in self.deltas("pin_order")
        ]
        return _compact({**found, ORDER_CHANGED: _is_reordered(order)})

    def show(self) -> _Obj:
        """Renders changed title, pack, ID, dependencies and extensions."""
        found: _Obj = {}
        for delta in self.deltas("show"):
            differing = _differing(delta, ("id", "title", "pack", "dependencies", "extensions"))
            for field, pair in differing.items():
                is_flag = field in ("dependencies", "extensions")
                found[field] = True if is_flag else pair
        return found

    def flags(self) -> _Obj:
        """Renders one flag for each whole stored field that changed."""
        return {
            "sheet": bool(self.deltas("sheet")),
            "timeline": bool(self.deltas("timeline")),
            "facing_overrides": bool(self.deltas("facing_override")),
        }


def _paths(value: JsonValue) -> list[tuple[str, ...]]:
    items = value if isinstance(value, list) else []
    return [tuple(str(part) for part in item) for item in items if isinstance(item, list)]


def render_changes(
    before: Show, after: Show, found: ChangeSet, set_differ: SetDiffer, path_differ: PathDiffer
) -> _Obj:
    """Renders the item changes between two shows as the `diff_show` dict.

    Args:
        before: The older show.
        after: The newer show.
        found: The item changes between them.
        set_differ: Lists what differs between two sets, as `diff_sets` does.
        path_differ: Lists what differs between two transitions, as `diff_paths` does.

    Returns:
        summary: One key per kind of difference; keys with no difference are left out.
    """
    render = _Render(before, after, found, set_differ, path_differ)
    summary = {
        "sets": render.sets(),
        "labels": render.labels(),
        "forms": render.forms(),
        "paths": render.paths(),
        "cast": render.cast(),
        "pins": render.pins(),
        "groups": _keyed(render.deltas("group")),
        "selections": _keyed(render.deltas("selection")),
        **render.show(),
        **render.flags(),
    }
    return _compact(summary)
