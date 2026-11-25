"""
item_assembly.py --- rebuilds a show from a base show and new values for some of its items

Values are item values as `items` and `changes` give them; None leaves an item out. Only the
objects those items touch are rebuilt: every other set, placement, form, path spec, spot and pin
is the base's own object. Values name sets by key, so a set whose identity item gives it a new ID
renames the forms, path specs and pins that name it.

The items of one object must agree: an order naming a member with no items, a placement with
some roles removed, or a new object missing an item is refused like a model refusal.

Contains:
    assemble(): applies item values to a base show, or returns the refusal of the first part
"""

from collections.abc import Callable
from functools import partial

from e8s.cast.models import Group, LabelScheme, Spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, Set
from e8s.choreo.path_models import FacingOverride, PathRow, Route, TransitionSpec
from e8s.core.ids import SpotId
from e8s.core.json_types import JsonValue
from e8s.sheet.models import Sheet
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.item_reading import (
    AssemblyContext,
    Entries,
    Fields,
    ItemValues,
    Refusal,
    RefusedError,
    all_removed,
    as_object,
    as_paths,
    as_text,
    as_texts,
    assembly_context,
    by_part,
    guarded,
    in_order,
    incoherent,
    named_values,
    of_kinds,
    sorted_keys,
    with_members,
)
from e8s.show.item_table import (
    ENTITY_FIELD,
    ID_FIELD,
    KEY_FIELD,
    LABEL_SCHEME_FIELD,
    PLACEMENT_ROLES,
    PLACEMENTS,
    SET_FIELD,
    SET_ROLES,
    TARGET_FIELD,
    TRANSITION_TARGET,
    ItemPath,
    pin_path,
    set_keys_by_id,
    transition_key,
)
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key
from e8s.show.storable import require_storable
from e8s.time.models import Timeline

type _Update = dict[str, object]

SET_ORDER = ItemKey(kind="set_order", path=())
SPOT_ORDER = ItemKey(kind="spot_order", path=())
PIN_ORDER = ItemKey(kind="pin_order", path=())
SHOW_ITEM = ItemKey(kind="show", path=())
SET_KINDS: frozenset[ItemKind] = frozenset({"set", "placement"})
TRANSITION_KINDS: frozenset[ItemKind] = frozenset({"transition", "path_row", "route"})
SPOT_KINDS: frozenset[ItemKind] = frozenset({"spot", "spot_order"})
CAST_KINDS: frozenset[ItemKind] = frozenset({"spot", "spot_order", "group", "selection", "show"})
WHOLE_KINDS: frozenset[ItemKind] = frozenset({"facing_override", "sheet", "timeline"})
STRUCTURE_KINDS: frozenset[ItemKind] = frozenset(
    {"set_order", "spot_order", "pin_order", "form", "transition", "spot", "pin", "show"}
)


def _placement(entity: str, current: Placement | None, roles: Entries) -> Placement:
    if any(value is None for value in roles.values()):
        raise incoherent(f"spot {entity} stays placed with roles removed", {"spot": entity})
    fields = {name: v for value in roles.values() for name, v in as_object(value).items()}
    if current is not None:
        return current.replace(**fields)
    missing = sorted({role for role, _ in PLACEMENT_ROLES} - {key.role for key in roles})
    if missing:
        raise incoherent(f"new placement of {entity} lacks roles", {"roles": list(missing)})
    return Placement.from_json({ENTITY_FIELD: entity, **fields})


def _placements(key: str, current: Set | None, entries: Entries) -> tuple[Placement, ...] | None:
    """Rebuilds a set's placements from its placement items and order; None when untouched."""
    order_key = ItemKey(kind="set", path=(key,), role=PLACEMENTS)
    roles = by_part(of_kinds(entries, {"placement"}), 1)
    if not roles and order_key not in entries:
        return None
    placed: dict[str, Placement] = (
        {} if current is None else {p.entity: p for p in current.placements}
    )
    order = as_texts(entries[order_key]) if order_key in entries else list(placed)
    members = dict(placed)
    for entity, found in sorted(roles.items()):
        if all_removed(found):
            members.pop(entity, None)
        else:
            members[entity] = _placement(entity, placed.get(entity), found)
    return in_order(order, members, f"set {key} placement")


def _new_set(key: str, fields: _Update, placements: tuple[Placement, ...] | None) -> Set:
    needed = {ID_FIELD, *(name for _, names in SET_ROLES for name in names)}
    missing = sorted(needed - fields.keys())
    if missing or placements is None:
        raise incoherent(f"new set {key} lacks items", {"set": key, "fields": list(missing)})
    built = Set.from_json({KEY_FIELD: key, **fields})
    return built.replace(placements=placements) if placements else built


def _set(key: str, current: Set | None, entries: Entries) -> Set | None:
    identity = ItemKey(kind="set", path=(key,))
    if identity in entries and entries[identity] is None:
        if not all_removed(entries):
            raise incoherent(f"set {key} is removed but keeps items", {"set": key})
        return None
    fields: _Update = {}
    for item_key, value in entries.items():
        if item_key.kind == "set" and item_key.role != PLACEMENTS:
            fields.update(as_object(value))
    placements = _placements(key, current, entries)
    if current is None:
        return _new_set(key, fields, placements)
    if placements is not None:
        fields[PLACEMENTS] = placements
    return current.replace(**fields) if fields else current


def _sets(ctx: AssemblyContext) -> _Update:
    touched = by_part(of_kinds(ctx.values, SET_KINDS), 0)
    if not touched and SET_ORDER not in ctx.values:
        return {}
    sets = {set_key(s): s for s in ctx.base.sets}
    default = list(sets)
    for key, entries in sorted(touched.items()):
        rebuilt = guarded(entries, partial(_set, key, sets.get(key), entries))
        if rebuilt is None:
            sets.pop(key, None)
        else:
            sets[key] = rebuilt
    order = as_texts(ctx.values[SET_ORDER]) if SET_ORDER in ctx.values else default
    scope = (k for k in ctx.values if k == SET_ORDER or (k.kind, k.role) == ("set", None))
    return {"sets": guarded(scope, partial(in_order, order, sets, "set"))}


def _form(ctx: AssemblyContext, form_id: str, value: JsonValue) -> Form:
    fields = as_object(value)
    set_id = ctx.set_id(as_text(fields[SET_FIELD]))
    scope = (ItemKey(kind="form", path=(form_id,)),)
    build = partial(Form.from_json, {**fields, ID_FIELD: form_id, SET_FIELD: set_id})
    return guarded(scope, build)


def _forms(ctx: AssemblyContext) -> _Update:
    entries = named_values(ctx.values, "form")
    keys = set_keys_by_id(ctx.base) if ctx.renamed else {}
    moved = {
        form_id: form.replace(set=ctx.set_id(keys.get(form.set, form.set)))
        for form_id, form in ctx.base.forms.items()
        if keys.get(form.set, form.set) in ctx.renamed
    }
    if not entries and not moved:
        return {}
    return {"forms": with_members({**ctx.base.forms, **moved}, entries, partial(_form, ctx))}


def _row(entity: str, value: JsonValue) -> PathRow:
    return PathRow.from_json({**as_object(value), ENTITY_FIELD: entity})


def _route(route_id: str, value: JsonValue) -> Route:
    return Route.from_json({**as_object(value), ID_FIELD: route_id})


def _spec(
    ctx: AssemblyContext, folder: str, current: TransitionSpec | None, entries: Entries
) -> TransitionSpec | None:
    """Rebuilds one transition's path spec from its items; None when the spec is removed."""
    own_key = ItemKey(kind="transition", path=(folder,))
    if own_key in entries and entries[own_key] is None:
        if not all_removed(entries):
            raise incoherent(f"transition {folder} is removed but keeps items", {"key": folder})
        return None
    rows = with_members(
        {} if current is None else current.rows, named_values(entries, "path_row"), _row
    )
    routes = with_members(
        {} if current is None else current.routes, named_values(entries, "route"), _route
    )
    own = as_object(entries[own_key]) if own_key in entries else {}
    tid = ctx.tid(folder)
    if current is not None:
        return current.replace(id=tid, rows=rows, routes=routes, **own)
    if own_key not in entries:
        raise incoherent(f"new transition {folder} lacks its item", {"key": folder})
    spec = TransitionSpec.from_json({ID_FIELD: [str(tid.from_set), str(tid.to_set)], **own})
    return spec.replace(rows=rows, routes=routes)


def _transitions(ctx: AssemblyContext) -> _Update:
    touched = by_part(of_kinds(ctx.values, TRANSITION_KINDS), 0)
    if not touched and not ctx.renamed:
        return {}
    keys = set_keys_by_id(ctx.base)
    specs = {transition_key(keys, spec.id): spec for spec in ctx.base.transitions}
    renamed = [f for f in specs if f not in touched and ctx.is_renamed((TRANSITION_TARGET, f))]
    if not touched and not renamed:
        return {}
    rebuilt: dict[str, TransitionSpec | None] = {
        f: specs[f].replace(id=ctx.tid(f)) for f in renamed
    }
    for folder, entries in sorted(touched.items()):
        rebuilt[folder] = guarded(entries, partial(_spec, ctx, folder, specs.get(folder), entries))
    kept = [rebuilt.get(f, spec) for f, spec in specs.items()]
    added = [rebuilt[f] for f in sorted(rebuilt) if f not in specs]
    return {"transitions": tuple(spec for spec in (*kept, *added) if spec is not None)}


def _spot(spot_id: str, value: JsonValue) -> Spot:
    return Spot.from_json({**as_object(value), ID_FIELD: spot_id})


def _group(key: str, value: JsonValue) -> Group:
    return Group.from_json({**as_object(value), KEY_FIELD: key})


def _selection(_name: str, value: JsonValue) -> tuple[SpotId, ...]:
    return tuple(map(SpotId, as_texts(value)))


def _show_fields(ctx: AssemblyContext) -> Fields | None:
    if SHOW_ITEM not in ctx.values:
        return None
    if ctx.values[SHOW_ITEM] is None:
        raise incoherent("the show item is removed", {})
    return as_object(ctx.values[SHOW_ITEM])


def _cast(ctx: AssemblyContext) -> _Update:
    cast, changed = ctx.base.cast, of_kinds(ctx.values, CAST_KINDS)
    if not changed:
        return {}
    update: _Update = {}
    if any(key.kind in SPOT_KINDS for key in changed):
        spots = with_members({s.id: s for s in cast.spots}, named_values(changed, "spot"), _spot)
        default = [spot.id for spot in cast.spots]
        order = as_texts(ctx.values[SPOT_ORDER]) if SPOT_ORDER in ctx.values else default
        update["spots"] = in_order(order, spots, "spot")
    if any(key.kind == "group" for key in changed):
        update["groups"] = with_members(cast.groups, named_values(changed, "group"), _group)
    if any(key.kind == "selection" for key in changed):
        update["selections"] = with_members(
            cast.selections, named_values(changed, "selection"), _selection
        )
    fields = _show_fields(ctx)
    if fields is not None:
        update[LABEL_SCHEME_FIELD] = LabelScheme.from_json(fields[LABEL_SCHEME_FIELD])
    return {"cast": cast.replace(**update)}


def _pin(ctx: AssemblyContext, path: ItemPath, value: JsonValue) -> Pin:
    return Pin.from_json({**as_object(value), TARGET_FIELD: path[0], ID_FIELD: ctx.pin_id(path)})


def _pins(ctx: AssemblyContext) -> _Update:
    entries = {key.path: value for key, value in of_kinds(ctx.values, {"pin"}).items()}
    if not entries and PIN_ORDER not in ctx.values and not ctx.renamed:
        return {}
    current = {pin_path(set_keys_by_id(ctx.base), p): p for p in ctx.base.pins}
    renamed = {
        path: record.replace(id=ctx.pin_id(path))
        for path, record in current.items()
        if path not in entries and ctx.is_renamed(path)
    }
    if not entries and PIN_ORDER not in ctx.values and not renamed:
        return {}
    pins = {**current, **renamed}
    for path, value in sorted(entries.items()):
        if value is None:
            pins.pop(path, None)
        else:
            pins[path] = _pin(ctx, path, value)
    order = as_paths(ctx.values[PIN_ORDER]) if PIN_ORDER in ctx.values else list(current)
    return {"pins": in_order(order, pins, "pin")}


def _whole(ctx: AssemblyContext) -> _Update:
    update: _Update = {}
    for key in sorted_keys(of_kinds(ctx.values, WHOLE_KINDS)):
        value = ctx.values[key]
        if value is None:
            raise incoherent(f"the {key.kind} item is removed", {})
        if key.kind == "sheet":
            update["sheet"] = Sheet.from_json(value)
        elif key.kind == "timeline":
            update["timeline"] = Timeline.from_json(value)
        elif isinstance(value, list):
            update["facing_overrides"] = tuple(map(FacingOverride.from_json, value))
        else:
            raise incoherent("the facing overrides item is not a list", {"value": value})
    return update


def _show(ctx: AssemblyContext) -> _Update:
    fields = _show_fields(ctx)
    if fields is None:
        return {}
    return {name: value for name, value in fields.items() if name != LABEL_SCHEME_FIELD}


def _is_set_identity(key: ItemKey) -> bool:
    return key.kind == "set" and key.role is None


def _is_structural(key: ItemKey) -> bool:
    return key.kind in STRUCTURE_KINDS or _is_set_identity(key)


PARTS: tuple[tuple[Callable[[AssemblyContext], _Update], Callable[[ItemKey], bool]], ...] = (
    (_sets, lambda key: key.kind in SET_KINDS or key == SET_ORDER),
    (_forms, lambda key: key.kind == "form" or _is_set_identity(key)),
    (_transitions, lambda key: key.kind in TRANSITION_KINDS or _is_set_identity(key)),
    (_cast, lambda key: key.kind in CAST_KINDS),
    (_pins, lambda key: key.kind in {"pin", "pin_order"} or _is_set_identity(key)),
    (_whole, lambda key: key.kind in WHOLE_KINDS),
    (_show, lambda key: key == SHOW_ITEM),
)


def _finished(base: Show, update: _Update) -> Show:
    show = base.replace(**update) if update else base
    require_storable(show)
    return show


def assemble(base: Show, values: ItemValues) -> Show | Refusal:
    """Applies item values to a base show, rebuilding only the objects the items touch.

    Args:
        base: The show the values apply to; it is never changed.
        values: New JSON values by item key, as `changes` gives them; None leaves an item out.

    Returns:
        result: The rebuilt show, storable and sharing every untouched object with `base`; or
            the refusal of the first part whose items disagree or that the models refuse, naming
            that part's given items.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.change_models import ItemKey
    >>> drill = ItemKey(kind="placement", path=("2", "T1"), role="drill")
    >>> values = {drill: {"x": 3.0, "y": 0.0, "on_field": True, "form": None, "order": None}}
    >>> assemble(two_set_show(), values).set("2").get("T1").x
    3.0
    """
    try:
        identities = (key for key in values if _is_set_identity(key))
        ctx = guarded(identities, partial(assembly_context, base, values))
        update: _Update = {}
        for build, covers in PARTS:
            update |= guarded((key for key in values if covers(key)), partial(build, ctx))
        structure = (key for key in values if _is_structural(key))
        return guarded(structure, partial(_finished, base, update))
    except RefusedError as refused:
        return refused.refusal
