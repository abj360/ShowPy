"""
revalidate.py --- revalidates an edited show, rechecking only what the edit can have changed

`revalidate(previous, before, after)` returns exactly `validate_show(after)` (no sheet store, no
comments), issue for issue and in the same order, when `previous` is `validate_show(before)` run
the same way. The edit is read from `changes(before, after)`; each previous issue is matched to
the stored item it names in `context["item"]`. Issues of items the edit cannot reach are kept.
A check is rerun when any item it reads changed, not only the item it reports: a set's checks
when its placements or the forms it lists or stamps change, a form's member check when its
set's placements change, a pin when the placements, forms or rows it covers change, facing
overrides when set counts or the cast change. Paths are recompiled through the engine's
per-spot compile for the spots whose placements or rows changed; a transition whose counts,
routes or destination counts changed is checked whole.

Where the work cannot be bounded by item, or a kept issue cannot be trusted, validate_show runs
in full: reordering, adding, removing or renumbering sets or spots; a sheet or timeline edit; a
show that drops back within MAX_COUNTS, whose previous report checked no paths; a previous
report holding non-finite numbers, an issue whose item revalidate does not know, or a
transition whose kept issues do not start with that transition's current path checks; and a
pair of shows changes() refuses.

Contains:
    revalidate(): returns validate_show's report for an edited show, keeping the previous
        report's issues for the items the edit did not reach
"""

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from e8s.choreo.models import Set
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError
from e8s.core.ids import FormId, TransitionId
from e8s.core.validation import ValidationIssue, ValidationReport
from e8s.show.change_models import ItemKey
from e8s.show.item_table import FORMS, PLACEMENTS, TIMING, set_keys_by_id, transition_key
from e8s.show.items import changes
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key
from e8s.show.validate import (
    ShowChecks,
    compile_issues,
    path_checks,
    run_checks,
    skips_transition,
    validate_paths,
    validate_set,
    validate_show,
)
from e8s.show.validate_pins import pin_issues, pin_item_path
from e8s.show.validate_placements import far_off_issues, inactive_issues
from e8s.show.validate_refs import form_issues, override_issues, set_form_issues
from e8s.show.validate_values import non_finite_issues, too_long_issues

type _Kept = Mapping[Hashable, Sequence[ValidationIssue]]

WHOLE_SHOW_KINDS = frozenset({"set_order", "spot_order", "sheet", "timeline"})
OVERRIDE_KINDS = frozenset({"facing_override", "spot", "group", "selection", "show"})
TRANSITION_KINDS = frozenset({"path_row", "route", "transition"})
SET_CHECK_CODES = frozenset({ErrorCode.MISSING_SPOT, ErrorCode.UNKNOWN_SPOT, ErrorCode.STALE_FORM})
RERUN_SET_CODES = frozenset({ErrorCode.UNKNOWN_FORM, ErrorCode.TIME_RANGE})
SETS = "sets"
TRANSITIONS = "transitions"
FORM_ISSUES = "forms"
PINS = "pins"
OVERRIDES = "overrides"
INACTIVE = "inactive"
FAR_OFF = "far_off"
PLACEMENT_BUCKETS: Mapping[str, str] = {
    ErrorCode.MEMBER_MISMATCH: FORM_ISSUES,
    ErrorCode.INACTIVE_ON_FIELD: INACTIVE,
    ErrorCode.FAR_OFF_SHEET: FAR_OFF,
    ErrorCode.UNKNOWN_FORM: SETS,
    ErrorCode.NOT_IN_FORM: SETS,
}
FORM_FIELD = "form"


class _UnboundedError(Exception):
    """Signals that the edit or the previous report cannot be bounded by item."""


def _item(issue: ValidationIssue) -> ItemKey | None:
    raw = issue.context.get("item")
    if raw is None:
        return None
    try:
        return ItemKey.from_json(raw)
    except E8sError as error:
        raise _UnboundedError from error


def _placement_slot(issue: ValidationIssue, key: ItemKey) -> tuple[str, Hashable]:
    bucket = PLACEMENT_BUCKETS.get(issue.code)
    if bucket is None:
        raise _UnboundedError
    if bucket != FORM_ISSUES:
        return bucket, key.path[0]
    form = issue.context.get(FORM_FIELD)
    if not isinstance(form, str):
        raise _UnboundedError
    return bucket, form


def _set_slot(issue: ValidationIssue, key: ItemKey) -> tuple[str, Hashable] | None:
    if issue.code in SET_CHECK_CODES:
        return SETS, key.path[0]
    if issue.code in RERUN_SET_CODES:
        return None
    raise _UnboundedError


def _slot(issue: ValidationIssue) -> tuple[str, Hashable] | None:
    if issue.code == ErrorCode.NOT_FINITE:
        raise _UnboundedError
    key = _item(issue)
    if key is None:
        return None
    if key.kind in TRANSITION_KINDS:
        return TRANSITIONS, key.path[0]
    slots: Mapping[str, Callable[[], tuple[str, Hashable] | None]] = {
        "placement": lambda: _placement_slot(issue, key),
        "set": lambda: _set_slot(issue, key),
        "form": lambda: (FORM_ISSUES, key.path[0]),
        "pin": lambda: (PINS, key.path),
        "facing_override": lambda: (OVERRIDES, ()),
    }
    if key.kind not in slots:
        raise _UnboundedError
    return slots[key.kind]()


def _kept(previous: ValidationReport) -> dict[str, _Kept]:
    kept: dict[str, dict[Hashable, list[ValidationIssue]]] = {}
    for issue in previous.issues:
        slot = _slot(issue)
        if slot is not None:
            kept.setdefault(slot[0], {}).setdefault(slot[1], []).append(issue)
    return dict(kept)


def _grouped(pairs: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for outer, inner in pairs:
        grouped[outer].add(inner)
    return {outer: frozenset(inner) for outer, inner in grouped.items()}


@dataclass(frozen=True)
class _Touched:
    """Holds the items an edit changed, grouped the way the checks read them.

    Attributes:
        placements: Set key to the spots whose placements changed in it.
        set_roles: Set role to the keys of the sets whose role changed.
        forms: IDs of forms added, removed or changed.
        transitions: Transition keys whose counts, notes or routes changed.
        rows: Transition key to the spots whose path rows changed.
        pins: Item paths of pins added, removed or changed.
        spots: Whether any spot's stored fields changed.
        overrides: Whether anything facing overrides read changed.
    """

    placements: Mapping[str, frozenset[str]]
    set_roles: Mapping[str, frozenset[str]]
    forms: frozenset[str]
    transitions: frozenset[str]
    rows: Mapping[str, frozenset[str]]
    pins: frozenset[tuple[str, ...]]
    spots: bool
    overrides: bool

    def sets_with(self, *roles: str) -> frozenset[str]:
        """Returns the keys of sets whose placements, or any of the given roles, changed."""
        found = set(self.placements)
        for role in roles:
            found |= self.set_roles.get(role, frozenset())
        return frozenset(found)


def _touched(keys: Sequence[ItemKey]) -> _Touched:
    if any(k.kind in WHOLE_SHOW_KINDS or (k.kind == "set" and k.role is None) for k in keys):
        raise _UnboundedError
    return _Touched(
        placements=_grouped((k.path[0], k.path[1]) for k in keys if k.kind == "placement"),
        set_roles=_grouped((k.role, k.path[0]) for k in keys if k.kind == "set" and k.role),
        forms=frozenset(k.path[0] for k in keys if k.kind == "form"),
        transitions=frozenset(k.path[0] for k in keys if k.kind in ("transition", "route")),
        rows=_grouped((k.path[0], k.path[1]) for k in keys if k.kind == "path_row"),
        pins=frozenset(k.path for k in keys if k.kind == "pin"),
        spots=any(k.kind == "spot" for k in keys),
        overrides=any(k.kind in OVERRIDE_KINDS for k in keys),
    )


@dataclass(frozen=True)
class _Run:
    """Holds one revalidation: both shows, the kept issues and the units to recheck.

    Attributes:
        before: The show the previous report describes.
        after: The edited show.
        kept: Previous issues by bucket, then by the unit they belong to.
        touched: What the edit changed.
        keys: Set ID to storage key; renumbers fall back, so both shows share it.
        sets: Keys of sets whose set checks rerun.
        forms: Keys of forms whose reference checks rerun.
        pinned_sets: Keys of sets whose set pins rerun.
        moved: Spots with a changed placement in any set.
    """

    before: Show
    after: Show
    kept: Mapping[str, _Kept]
    touched: _Touched
    keys: Mapping[str, str]
    sets: frozenset[str]
    forms: frozenset[str]
    pinned_sets: frozenset[str]
    moved: frozenset[str]

    def previous(self, bucket: str, unit: Hashable) -> Sequence[ValidationIssue]:
        """Returns the previous issues of one unit."""
        return self.kept.get(bucket, {}).get(unit, ())


def _form_owners(
    before: Show, after: Show, forms: frozenset[str], keys: Mapping[str, str]
) -> set[str]:
    found = (show.forms.get(FormId(f)) for show in (before, after) for f in forms)
    owners = {form.set for form in found if form is not None}
    return {keys[owner] for owner in owners if owner in keys}


def _referring_sets(kept: Mapping[str, _Kept], forms: frozenset[str]) -> set[str]:
    return {
        str(unit)
        for unit, issues in kept.get(SETS, {}).items()
        for issue in issues
        if issue.context.get(FORM_FIELD) in forms
    }


def _run(previous: ValidationReport, before: Show, after: Show) -> _Run:
    try:
        change_set = changes(before, after)
    except E8sError as error:
        raise _UnboundedError from error
    touched = _touched([change.key for change in change_set.changes])
    kept = _kept(previous)
    keys = set_keys_by_id(after)
    owners = _form_owners(before, after, touched.forms, keys)
    edited = touched.sets_with(PLACEMENTS, FORMS)
    edited_ids = {set_id for set_id, key in keys.items() if key in edited}
    return _Run(
        before=before,
        after=after,
        kept=kept,
        touched=touched,
        keys=keys,
        sets=frozenset(edited | owners | _referring_sets(kept, touched.forms)),
        forms=touched.forms | {k for k, form in after.forms.items() if form.set in edited_ids},
        pinned_sets=frozenset(edited | owners),
        moved=frozenset(spot for spots in touched.placements.values() for spot in spots),
    )


def _set_issues(run: _Run, candidate: Set) -> Sequence[ValidationIssue]:
    key = set_key(candidate)
    if key in run.sets:
        return validate_set(run.after, candidate).issues
    return run.previous(SETS, key)


def _by_spot(issues: Iterable[ValidationIssue]) -> dict[str, list[ValidationIssue]]:
    grouped: dict[str, list[ValidationIssue]] = defaultdict(list)
    for issue in issues:
        key = _item(issue)
        if key is None or key.kind != "path_row":
            raise _UnboundedError
        grouped[key.path[1]].append(issue)
    return grouped


def _recompiled(run: _Run, tid: TransitionId, spots: frozenset[str]) -> list[ValidationIssue]:
    kept = list(run.previous(TRANSITIONS, transition_key(run.keys, tid)))
    head = path_checks(run.before, run.before.transition(tid))
    if kept[: len(head)] != head:
        raise _UnboundedError
    old = _by_spot(kept[len(head) :])
    spec = run.after.transition(tid)
    cast = run.after.cast.ids()
    new = _by_spot(compile_issues(run.after, spec, [spot for spot in cast if spot in spots]))
    compiled = [i for spot in cast for i in (new if spot in spots else old).get(spot, ())]
    return path_checks(run.after, spec) + compiled


def _transition_issues(run: _Run, tid: TransitionId) -> Sequence[ValidationIssue]:
    folder = transition_key(run.keys, tid)
    from_key, to_key = run.keys[tid.from_set], run.keys[tid.to_set]
    touched = run.touched
    whole = folder in touched.transitions or to_key in touched.set_roles.get(TIMING, ())
    spots = (
        touched.placements.get(from_key, frozenset())
        | touched.placements.get(to_key, frozenset())
        | touched.rows.get(folder, frozenset())
    )
    if not whole and not spots:
        return run.previous(TRANSITIONS, folder)
    if skips_transition(run.after, tid, run.before):
        return []
    if whole:
        return validate_paths(run.after, run.after.transition(tid)).issues
    return _recompiled(run, tid, spots)


def _form_issues(run: _Run) -> list[ValidationIssue]:
    return [
        issue
        for key, form in run.after.forms.items()
        for issue in (
            form_issues(run.after, key, form)
            if key in run.forms
            else run.previous(FORM_ISSUES, key)
        )
    ]


def _pin_touched(run: _Run, record: Pin, path: tuple[str, ...]) -> bool:
    if path in run.touched.pins:
        return True
    covered: Mapping[str, Callable[[], bool]] = {
        "entity": lambda: record.id in run.moved,
        "set": lambda: path[1] in run.pinned_sets,
        "form": lambda: record.id in run.touched.forms,
        "transition": lambda: path[1] in run.touched.transitions or path[1] in run.touched.rows,
    }
    return covered[record.target]()


def _pin_issues(run: _Run) -> list[ValidationIssue]:
    pins = run.after.pins
    paths = [pin_item_path(run.keys, record) for record in pins]
    earlier = [pin_item_path(run.keys, record) for record in run.before.pins]
    if len(set(paths)) < len(paths) or len(set(earlier)) < len(earlier):
        return pin_issues(run.after, pins)
    redo = {
        path for record, path in zip(pins, paths, strict=True) if _pin_touched(run, record, path)
    }
    fresh: dict[Hashable, list[ValidationIssue]] = defaultdict(list)
    checked = [record for record, path in zip(pins, paths, strict=True) if path in redo]
    for issue in pin_issues(run.after, checked, scoped=True):
        key = _item(issue)
        if key is None:
            raise _UnboundedError
        fresh[key.path].append(issue)
    return [
        i for path in paths for i in (fresh[path] if path in redo else run.previous(PINS, path))
    ]


def _references(run: _Run) -> list[ValidationIssue]:
    issues = _form_issues(run) + set_form_issues(run.after)
    if run.after.pins:
        issues += _pin_issues(run)
    touched = run.touched
    if touched.overrides or touched.set_roles.get(TIMING):
        return issues + override_issues(run.after)
    return issues + list(run.previous(OVERRIDES, ()))


def _per_set(
    run: _Run, bucket: str, check: Callable[[Show, Iterable[Set]], list[ValidationIssue]]
) -> list[ValidationIssue]:
    edited = run.touched.sets_with(PLACEMENTS)
    return [
        issue
        for owner in run.after.sets
        for issue in (
            check(run.after, [owner])
            if set_key(owner) in edited
            else run.previous(bucket, set_key(owner))
        )
    ]


def _placements(run: _Run) -> list[ValidationIssue]:
    inactive = (
        inactive_issues(run.after, run.after.sets)
        if run.touched.spots
        else _per_set(run, INACTIVE, inactive_issues)
    )
    return inactive + _per_set(run, FAR_OFF, far_off_issues)


def _checks(previous: ValidationReport, before: Show, after: Show) -> ShowChecks:
    if too_long_issues(before) and not too_long_issues(after):
        raise _UnboundedError
    run = _run(previous, before, after)
    folders = [transition_key(run.keys, tid) for tid in after.transition_ids()]
    if len(set(folders)) < len(folders):
        raise _UnboundedError
    return ShowChecks(
        set_issues=lambda candidate: _set_issues(run, candidate),
        transition_issues=lambda tid: _transition_issues(run, tid),
        non_finite=lambda: non_finite_issues(after, known=before),
        references=lambda: _references(run),
        placements=lambda: _placements(run),
    )


def revalidate(previous: ValidationReport, before: Show, after: Show) -> ValidationReport:
    """Returns validate_show's report for an edited show, rechecking only what the edit reached.

    The result equals `validate_show(after)`, order included. Issues of items the edit did not
    reach are taken from `previous`; edits that cannot be bounded by item rerun the whole check.

    >>> from e8s.choreo.models import PlacementPatch
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.validate import validate_show
    >>> before = two_set_show()
    >>> moved = before.sets[1].with_placements({"T1": PlacementPatch(x=3.0)})
    >>> after = before.replace(sets=(before.sets[0], moved))
    >>> revalidate(validate_show(before), before, after) == validate_show(after)
    True

    Args:
        previous: `validate_show(before)`, run with no sheet store and no comments.
        before: The show before the edit.
        after: The show after the edit.

    Returns:
        report: The same report `validate_show(after)` returns.
    """
    try:
        return run_checks(after, _checks(previous, before, after))
    except _UnboundedError:
        return validate_show(after)
