"""
v1_to_v2_ids.py --- the ids sub-step of the v1 to v2 format migration (lane A)

The model sub-step. It runs on the show validated with any non-empty v1 ID accepted and renames,
once and by rule, every spot, set, form, route, zone, landmark, guide and selection ID that fails
its ID check or repeats, ignoring case, an ID of its kind kept earlier in show order. Valid IDs keep
their spelling, and the first of a case pair is the one kept. The rule, in order: NFC; every
character outside the kind's alphabet (`-`, a space, `*`, a non-ASCII letter) becomes `_`, and each
`..` run becomes `.`; leading and trailing `.` are stripped, the ID is cut to 60 characters, an
empty result becomes `id`, and a reserved word gets a trailing `_` (a device name before an
extension gets it before the dot: `con.5` becomes `con_.5`). Sheet items (zone, landmark and guide
IDs) keep an inner `-`, and an edge `-` becomes `_`; a new landmark ID also avoids every landmark's
name and aliases. A repaired ID that is still taken gets the smallest free suffix `.2`, `.3`, ...
Every v1 ID is repaired; none is refused.

Every reference follows a rename. Spots go last, through rename_spots(); a set's key follows its
renamed ID unless the key was written explicitly, and a coordinate origin follows its renamed
landmark. A stored selector that names spots by label (`T*`, `T1-T8`) is frozen to its ID list
whenever spots are renamed, and the report records it as a repair. Route IDs are unique per
transition and sheet items per sheet or show, so those namespaces are checked apart. Each rename is
recorded with `report.rename()`, per kind in show order, so a host can follow it in what it keeps
outside ShowPy; the notes sub-step that follows renames note IDs by the same rule and points note
anchors at the renamed sets and spots.

Contains:
    KEEPS_LEGACY_IDS: tells whether validation after this step still accepts any non-empty v1 ID
    ID_CUT_LENGTH: bounds a repaired ID, leaving room for a collision suffix
    renames_of(): maps each ID of one kind that must change to its new ID, by the rename rule
    rename_ids(): renames stored IDs that break the ID rules, recording each rename
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from typing import TYPE_CHECKING, Final, NamedTuple

from pydantic import BaseModel

from showpy.cast.select import KIND_SEPARATOR, TOKEN_PATTERN
from showpy.cast.spots import rename_spots
from showpy.choreo.models import Set
from showpy.choreo.path_models import TransitionSpec
from showpy.core.errors import SchemaError
from showpy.core.ids import (
    ID_BODY_CHARS,
    ID_MAX_LENGTH,
    LEGACY_IDS_KEY,
    RESERVED_IDS,
    SHEET_ID_BODY_CHARS,
    SetId,
    TransitionId,
    check_id,
    check_sheet_id,
    format_tid,
    is_device_name,
    parse_tid,
)
from showpy.core.selection import Selection, SelectorField
from showpy.sheet.models import CoordinateSystem, Landmark, Sheet, Zone, normalized_name
from showpy.show.models import Pin, Show

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

KEEPS_LEGACY_IDS: Final = False
ID_CUT_LENGTH: Final = 60
EMPTY_ID: Final = "id"
FIRST_SUFFIX: Final = 2
FILL: Final = "_"
DOUBLE_DOTS: Final = re.compile(r"\.{2,}")
LEGACY_CONTEXT: Final = {LEGACY_IDS_KEY: True}
FORM_TOKEN: Final = "form"
SELECTION_TOKEN: Final = "sel"

type IdCheck = Callable[[str], str]
type Renames = dict[str, str]


class _Rule(NamedTuple):
    """Holds how one ID kind is checked and which characters its IDs may hold.

    Attributes:
        check: Returns an ID's checked text or raises SchemaError.
        alphabet: Characters the kind's IDs may hold; the rule turns any other into `_`.
    """

    check: IdCheck
    alphabet: frozenset[str]


class _Plan(NamedTuple):
    """Holds every rename of one show, per kind, each in show order.

    Attributes:
        sets: Old set ID to new set ID.
        forms: Old form ID to new form ID.
        routes: Per transition (by old set IDs), old route ID to new route ID.
        sheet_zones: Old sheet zone ID to new zone ID.
        show_zones: Old show zone ID to new zone ID.
        landmarks: Old sheet landmark ID to new landmark ID.
        guides: Old sheet guide ID to new guide ID.
        selections: Old selection name to new selection name.
        spots: Old spot ID to new spot ID.
    """

    sets: Renames
    forms: Renames
    routes: dict[TransitionId, Renames]
    sheet_zones: Renames
    show_zones: Renames
    landmarks: Renames
    guides: Renames
    selections: Renames
    spots: Renames


def _legacy[M: BaseModel](model: M, **changes: object) -> M:
    """Returns a copy with `changes`, validated with any non-empty v1 ID accepted.

    `replace` validates without that context, and the show still holds other kinds' v1 IDs
    until their turn comes.
    """
    fields = {name: getattr(model, name) for name in type(model).model_fields}
    return type(model).model_validate(fields | changes, context=LEGACY_CONTEXT)


def _passing(text: str, check: IdCheck) -> str | None:
    """Returns the checked ID, or None when it fails its check."""
    try:
        return check(text)
    except SchemaError:
        return None


def _without_reserved(text: str) -> str:
    """Gives a reserved word, or a device name before an extension, a trailing `_`."""
    if text.casefold() in RESERVED_IDS:
        return f"{text}_"
    if is_device_name(text):
        head, dot, rest = text.partition(".")
        return f"{head}_{dot}{rest}"
    return text


def _repaired(text: str, rule: _Rule) -> str:
    """Applies the rename rule's repairs to an NFC-normalized ID that fails its check.

    Every character outside the kind's alphabet becomes `_` and each `..` run becomes `.`;
    then edge dots are stripped, the ID is cut to 60 characters, an empty result becomes `id`,
    an edge `-` becomes `_` and a reserved word gets its `_`.
    """
    mapped = "".join(char if char in rule.alphabet else FILL for char in text)
    cut = DOUBLE_DOTS.sub(".", mapped).strip(".")[:ID_CUT_LENGTH].rstrip(".") or EMPTY_ID
    if cut.startswith("-"):
        cut = FILL + cut[1:]
    if cut.endswith("-"):
        cut = cut[:-1] + FILL
    return _without_reserved(cut)


def _suffixed(base: str, taken: set[str]) -> str:
    """Returns `base`, or `base.<n>` with the smallest n from 2 that is free ignoring case."""
    if base.casefold() not in taken:
        return base
    suffixes = (f".{n}" for n in range(FIRST_SUFFIX, FIRST_SUFFIX + len(taken) + 1))
    candidates = (base[: ID_MAX_LENGTH - len(s)].rstrip(".") + s for s in suffixes)
    # Pigeonhole: one of len(taken) + 1 distinct suffixed candidates is always free.
    return next(c for c in candidates if c.casefold() not in taken)


def _renames(ids: Sequence[str], rule: _Rule, held: Iterable[str] = ()) -> Renames:
    """Maps each ID of one kind that must change to its new ID, in show order.

    Args:
        ids: The kind's IDs in show order.
        rule: The kind's ID check and alphabet.
        held: Names a new ID must also avoid, ignoring case, such as other sets' explicit keys.

    Returns:
        renames: Old ID to new ID, only for IDs that change.
    """
    kept: Renames = {}
    taken: set[str] = set()
    for old in ids:
        passed = _passing(old, rule.check)
        if passed is not None and passed.casefold() not in taken:
            kept[old] = passed
            taken.add(passed.casefold())
    taken |= {name.casefold() for name in held}
    renames: Renames = {}
    for old in ids:
        if old in kept:
            renames[old] = kept[old]
            continue
        normalized = unicodedata.normalize("NFC", old)
        new = _suffixed(_passing(normalized, rule.check) or _repaired(normalized, rule), taken)
        taken.add(new.casefold())
        renames[old] = new
    return {old: new for old, new in renames.items() if new != old}


def renames_of(ids: Sequence[str], kind: str, held: Iterable[str] = ()) -> Renames:
    """Maps each ID of one kind that fails its check or repeats ignoring case to its new ID.

    Args:
        ids: The kind's IDs in show order.
        kind: The ID kind, such as `note`; its check and alphabet are the plain ID rule's.
        held: Names a new ID must also avoid, ignoring case.

    Returns:
        renames: Old ID to new ID, only for IDs that change, in show order.

    >>> renames_of(["n-1", "n1", "N1"], "note")
    {'n-1': 'n_1', 'N1': 'N1.2'}
    """
    return _renames(ids, _Rule(partial(check_id, kind=kind), ID_BODY_CHARS), held)


def _sheet_renames(ids: Sequence[str], kind: str, held: Iterable[str] = ()) -> Renames:
    return _renames(ids, _Rule(partial(check_sheet_id, kind=kind), SHEET_ID_BODY_CHARS), held)


def _landmark_renames(landmarks: Sequence[Landmark]) -> Renames:
    """Renames landmark IDs, a new one also avoiding every landmark's name and aliases."""
    names = [normalized_name(n) for mark in landmarks for n in (mark.name, *mark.aliases)]
    return _sheet_renames([mark.id for mark in landmarks], "landmark", names)


def _plan(show: Show) -> _Plan:
    """Works out every rename of a show from its stored IDs, before any is applied."""
    explicit_keys = [s.key for s in show.sets if s.key is not None and s.key != s.id]
    return _Plan(
        sets=renames_of(show.set_ids(), "set", explicit_keys),
        forms=renames_of(list(show.forms), "form"),
        routes={spec.id: renames_of(list(spec.routes), "route") for spec in show.transitions},
        sheet_zones=_sheet_renames([z.id for z in show.sheet.zones], "zone"),
        show_zones=_sheet_renames([z.id for z in show.zones], "zone"),
        landmarks=_landmark_renames(show.sheet.landmarks),
        guides=_sheet_renames([g.id for g in show.sheet.guides], "guide"),
        selections=renames_of(list(show.cast.selections), "selection"),
        spots=renames_of(list(show.cast.ids()), "spot"),
    )


def _renames_more_than_spots(plan: _Plan) -> bool:
    others = (plan.sets, plan.forms, plan.sheet_zones, plan.show_zones, plan.landmarks)
    rest = (plan.guides, plan.selections)
    return any(others) or any(rest) or any(plan.routes.values())


def _report(plan: _Plan, report: "ReportBuilder") -> None:
    """Records every planned rename, per kind in show order."""
    by_kind: list[tuple[str, Iterable[tuple[str, str]]]] = [
        ("spot", plan.spots.items()),
        ("set", plan.sets.items()),
        ("form", plan.forms.items()),
        ("route", [pair for routes in plan.routes.values() for pair in routes.items()]),
        ("zone", [*plan.sheet_zones.items(), *plan.show_zones.items()]),
        ("landmark", plan.landmarks.items()),
        ("guide", plan.guides.items()),
        ("selection", plan.selections.items()),
    ]
    for kind, pairs in by_kind:
        for old, new in pairs:
            report.rename(kind, old, new)


def _renamed_tid(tid: tuple[str, str], sets: Renames) -> TransitionId:
    return TransitionId(SetId(sets.get(tid[0], tid[0])), SetId(sets.get(tid[1], tid[1])))


def _names_renamed_set(tid: tuple[str, str], sets: Renames) -> bool:
    return tid[0] in sets or tid[1] in sets


def _renamed_pin(pin: Pin, sets: Renames, forms: Renames) -> Pin:
    if pin.target == "set" and pin.id in sets:
        return _legacy(pin, id=sets[pin.id])
    if pin.target == "transition" and _names_renamed_set(parse_tid(pin.id), sets):
        return _legacy(pin, id=format_tid(_renamed_tid(parse_tid(pin.id), sets)))
    if pin.target == "form" and pin.id in forms:
        return _legacy(pin, id=forms[pin.id])
    return pin


def _renamed_set(stored: Set, sets: Renames, forms: Renames) -> Set:
    """Renames a set's ID, its defaulted key, its form list and its placements' forms."""
    placements = tuple(
        _legacy(p, form=forms[p.form]) if p.form is not None and p.form in forms else p
        for p in stored.placements
    )
    listed = tuple(forms.get(f, f) for f in stored.forms)
    if stored.id not in sets and listed == stored.forms and placements == stored.placements:
        return stored
    changes: dict[str, object] = {"forms": listed, "placements": placements}
    if stored.id in sets:
        changes["id"] = sets[stored.id]
        if stored.key == stored.id:
            changes["key"] = sets[stored.id]
    return _legacy(stored, **changes)


def _renamed_routes(spec: TransitionSpec, sets: Renames, routes: Renames) -> TransitionSpec:
    """Renames a transition's set pair and its route IDs, keys and row references."""
    if not routes and not _names_renamed_set(spec.id, sets):
        return spec
    rows = {
        key: _legacy(row, route=routes[row.route])
        if row.route is not None and row.route in routes
        else row
        for key, row in spec.rows.items()
    }
    renamed = {
        routes.get(key, key): _legacy(route, id=routes.get(route.id, route.id))
        for key, route in spec.routes.items()
    }
    return _legacy(spec, id=_renamed_tid(spec.id, sets), rows=rows, routes=renamed)


def _renamed_selector(selector: SelectorField, plan: _Plan) -> SelectorField:
    """Rewrites the `form:` and `sel:` names inside selector text; other selectors pass."""
    if not isinstance(selector, str):
        return selector
    names = {FORM_TOKEN: plan.forms, SELECTION_TOKEN: plan.selections}

    def renamed_token(match: re.Match[str]) -> str:
        kind, separator, name = match[0].partition(KIND_SEPARATOR)
        renames = names.get(kind, {}) if separator else {}
        return f"{kind}{separator}{renames[name]}" if name in renames else match[0]

    return TOKEN_PATTERN.sub(renamed_token, selector)


def _renamed_zones(zones: tuple[Zone, ...], renames: Renames, plan: _Plan) -> tuple[Zone, ...]:
    return tuple(
        _legacy(
            zone,
            id=renames.get(zone.id, zone.id),
            applies_to=_renamed_selector(zone.applies_to, plan),
        )
        for zone in zones
    )


def _renamed_origin(coordinates: CoordinateSystem, landmarks: Renames) -> CoordinateSystem:
    origin = coordinates.origin
    if isinstance(origin, str) and origin in landmarks:
        return _legacy(coordinates, origin=landmarks[origin])
    return coordinates


def _renamed_sheet(sheet: Sheet, plan: _Plan) -> Sheet:
    """Renames a sheet's zones, landmarks and guides, and a landmark its coordinates start at."""
    return _legacy(
        sheet,
        zones=_renamed_zones(sheet.zones, plan.sheet_zones, plan),
        landmarks=tuple(
            _legacy(mark, id=plan.landmarks[mark.id]) if mark.id in plan.landmarks else mark
            for mark in sheet.landmarks
        ),
        guides=tuple(
            _legacy(guide, id=plan.guides[guide.id]) if guide.id in plan.guides else guide
            for guide in sheet.guides
        ),
        default_coordinates=_renamed_origin(sheet.default_coordinates, plan.landmarks),
    )


def _apply(show: Show, plan: _Plan) -> Show:
    """Applies every non-spot rename of the plan to the show."""
    selections = {plan.selections.get(k, k): ids for k, ids in show.cast.selections.items()}
    return _legacy(
        show,
        sets=tuple(_renamed_set(s, plan.sets, plan.forms) for s in show.sets),
        forms={
            plan.forms.get(key, key): _legacy(
                form, id=plan.forms.get(form.id, form.id), set=plan.sets.get(form.set, form.set)
            )
            for key, form in show.forms.items()
        },
        transitions=tuple(
            _renamed_routes(spec, plan.sets, plan.routes.get(spec.id, {}))
            for spec in show.transitions
        ),
        pins=tuple(_renamed_pin(p, plan.sets, plan.forms) for p in show.pins),
        sheet=_renamed_sheet(show.sheet, plan),
        coordinates=_renamed_origin(show.coordinates, plan.landmarks),
        zones=_renamed_zones(show.zones, plan.show_zones, plan),
        facing_overrides=tuple(
            _legacy(o, entities=_renamed_selector(o.entities, plan)) for o in show.facing_overrides
        ),
        cast=_legacy(show.cast, selections=selections),
    )


def _stored_selectors(show: Show) -> list[tuple[str, SelectorField]]:
    """Lists each stored selector with the field path it is stored at."""
    return [
        *((f"sheet.zones[{i}].applies_to", z.applies_to) for i, z in enumerate(show.sheet.zones)),
        *((f"zones[{i}].applies_to", z.applies_to) for i, z in enumerate(show.zones)),
        *(
            (f"facing_overrides[{i}].entities", o.entities)
            for i, o in enumerate(show.facing_overrides)
        ),
    ]


def _report_frozen_selectors(before: Show, after: Show, report: "ReportBuilder") -> None:
    """Records each pattern selector that the spot renames turned into a list of IDs."""
    pairs = zip(_stored_selectors(before), _stored_selectors(after), strict=True)
    for (path, old), (_, new) in pairs:
        if isinstance(old, str) and not isinstance(new, str):
            ids = list(new.ids if isinstance(new, Selection) else new)
            report.repair(
                f"{path}: selector {old!r} named spots by label; stored as {ids} after the spot"
                " renames"
            )


def rename_ids(show: Show, report: "ReportBuilder") -> Show:
    """Renames stored IDs that break the ID rules, once and by rule, recording each rename.

    Args:
        show: Show validated with any non-empty v1 ID accepted.
        report: Collects each rename, per ID kind, in show order.

    Returns:
        show: The show with every ID valid and unique ignoring case within its kind. An ID the
            rule cannot repair raises SchemaError BAD_ID naming its kind and the stored ID.

    >>> from showpy.examples import two_set_show
    >>> from showpy.show.migrate import ReportBuilder
    >>> report = ReportBuilder(1)
    >>> rename_ids(two_set_show(), report) == two_set_show(), report.build().renamed
    (True, ReadOnlyMap({}))
    """
    plan = _plan(show)
    _report(plan, report)
    renamed = _apply(show, plan) if _renames_more_than_spots(plan) else show
    respotted = rename_spots(renamed, plan.spots)[0]
    _report_frozen_selectors(renamed, respotted, report)
    return respotted
