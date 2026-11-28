"""
validate.py --- structural validation of sets, paths, dependencies and whole shows

Validators never raise on bad data: every problem becomes a ValidationIssue with a code,
a location and a hint. Safety rules (collisions, speed, bounds) belong to a consumer checker.

Contains:
    validate_set(): checks a candidate set against the show
    compile_issues(): compiles chosen spots' paths through the engine and reports failures
    path_checks(): checks the spots a transition's paths name or face, and its counts
    validate_paths(): checks a transition's paths, including continuity through the engine
    validate_dependencies(): checks that the sheet reference resolves and is pinned
    skips_transition(): tells whether validate_show leaves a transition's paths unchecked
    transition_issues(): checks one consecutive transition's paths as validate_show does
    ShowChecks: supplies the per-set, per-transition and per-area checks of a whole-show run
    whole_show_checks(): returns the checks that read the whole show
    run_checks(): runs validate_show's checks in its issue order from the checks given
    validate_show(): checks everything plus set sequence, transitions, references and the timeline
"""

import math
from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple

from e8s.choreo.engine import PathEngine
from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ContinuityError, E8sError
from e8s.core.ids import SetId, TransitionId, format_tid, parse_ref
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue, ValidationReport
from e8s.sheet.templates import SheetStore, get_sheet
from e8s.show.change_models import ItemKey
from e8s.show.collab_models import Comment
from e8s.show.item_table import DRILL, FORMS, PIN, PLACEMENTS, transition_key
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.validate_comments import comment_issues
from e8s.show.validate_issue import ERROR, WARNING, make_issue
from e8s.show.validate_path_refs import path_spot_issues
from e8s.show.validate_placements import placement_issues
from e8s.show.validate_refs import reference_issues
from e8s.show.validate_values import (
    first_set_issues,
    form_key_issues,
    has_non_finite,
    id_issues,
    negative_count_issues,
    non_finite_issues,
    selection_issues,
    sequence_issues,
    too_long_issues,
)
from e8s.time.validate import validate_timeline


def _set_item(candidate: Set, role: str, **extra: JsonValue) -> dict[str, JsonValue]:
    return {"item": ItemKey(kind="set", path=(set_key(candidate),), role=role).to_json(), **extra}


def _placement_item(
    candidate: Set, entity: str, role: str, **extra: JsonValue
) -> dict[str, JsonValue]:
    item = ItemKey(kind="placement", path=(set_key(candidate), entity), role=role)
    return {"item": item.to_json(), **extra}


def _membership(show: Show, candidate: Set) -> list[ValidationIssue]:
    where = f"set {candidate.id}"
    cast_ids = set(show.cast.ids())
    placed = set(candidate.entity_ids())
    issues = [
        make_issue(
            ERROR,
            ErrorCode.MISSING_SPOT,
            where,
            f"{e} has no placement",
            "every spot is placed in every set",
            _set_item(candidate, PLACEMENTS, spot=e),
        )
        for e in show.cast.ids()
        if e not in placed
    ]
    issues += [
        make_issue(
            ERROR,
            ErrorCode.UNKNOWN_SPOT,
            where,
            f"{e} is not in the cast",
            "add the spot or remove the row",
            _set_item(candidate, PLACEMENTS, spot=e),
        )
        for e in candidate.entity_ids()
        if e not in cast_ids
    ]
    return issues


def _pins(show: Show, candidate: Set) -> list[ValidationIssue]:
    if candidate.id not in show.set_ids():
        return []
    stored = show.set(candidate.id)
    return [
        make_issue(
            ERROR,
            ErrorCode.PINNED_CHANGED,
            f"set {candidate.id} {p.entity}",
            "a pinned placement changed",
            "unpin it before editing",
            _placement_item(candidate, p.entity, PIN),
        )
        for p in stored.placements
        if p.pinned and p.entity in candidate.entity_ids() and candidate.get(p.entity) != p
    ]


def _forms(show: Show, candidate: Set) -> list[ValidationIssue]:
    issues = []
    for p in candidate.placements:
        if p.form is None:
            continue
        where = f"set {candidate.id} {p.entity}"
        form = show.forms.get(p.form)
        if form is None:
            issues.append(
                make_issue(
                    ERROR,
                    ErrorCode.UNKNOWN_FORM,
                    where,
                    f"form {p.form} does not exist",
                    "rebuild the form or clear the placement's form",
                    _placement_item(candidate, p.entity, DRILL, form=p.form),
                )
            )
        elif p.entity not in form.members or form.set != candidate.id:
            issues.append(
                make_issue(
                    ERROR,
                    ErrorCode.NOT_IN_FORM,
                    where,
                    f"not a member of {p.form} in this set",
                    "add it to the form's members",
                    _placement_item(candidate, p.entity, DRILL, form=p.form),
                )
            )
    return issues


def _stale_forms(show: Show, candidate: Set) -> list[ValidationIssue]:
    listed = forms_of_set(show, candidate.id)
    return [
        make_issue(
            WARNING,
            ErrorCode.STALE_FORM,
            f"set {candidate.id} form {form_id}",
            f"set {candidate.id} no longer lists form {form_id}",
            "re-place the set's forms, or remove the form",
            _set_item(candidate, FORMS, form=form_id),
        )
        for form_id, form in show.forms.items()
        if form.set == candidate.id and form_id not in listed
    ]


def validate_set(show: Show, candidate: Set) -> ValidationReport:
    """Checks a candidate set: every spot once, known IDs, forms, pins unchanged.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> validate_set(show, show.set("1")).ok
    True
    """
    issues = _membership(show, candidate) + _pins(show, candidate) + _forms(show, candidate)
    if candidate.id in show.set_ids():
        issues += _stale_forms(show, candidate)
    return ValidationReport(issues=tuple(issues))


def _with_spec(show: Show, spec: TransitionSpec) -> Show:
    if show.transition(spec.id) == spec:
        return show
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show.replace(transitions=(*others, spec))


def _folder(show: Show, tid: tuple[str, str]) -> str:
    return transition_key({set_id: set_key(show.set(set_id)) for set_id in tid}, tid)


def _compile_issue(error: E8sError, where: str, item: ItemKey) -> ValidationIssue:
    code = ErrorCode.CONTINUITY if isinstance(error, ContinuityError) else error.code
    return make_issue(ERROR, code, where, error.message, error.hint, {"item": item.to_json()})


def compile_issues(
    show: Show, spec: TransitionSpec, entities: Iterable[str]
) -> list[ValidationIssue]:
    """Compiles each given spot's path through the engine and reports the ones that fail.

    A spot not placed in both sets of the transition is skipped.

    Args:
        show: The show the transition belongs to.
        spec: The transition's paths, stored or candidate.
        entities: The spots to compile, in report order.

    Returns:
        issues: One issue per spot whose path does not compile, in the order given.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> compile_issues(show, show.transition(("1", "2")), show.cast.ids())
    []
    """
    trial = _with_spec(show, spec)
    where = format_tid(spec.id)
    issues = []
    a, b = trial.set(spec.id[0]), trial.set(spec.id[1])
    engine = PathEngine(trial, cache=False)
    folder = _folder(show, spec.id)
    for entity in entities:
        if entity not in a.entity_ids() or entity not in b.entity_ids():
            continue
        try:
            engine.compile_entity(entity, spec.id)
        except E8sError as error:
            item = ItemKey(kind="path_row", path=(folder, entity))
            issues.append(_compile_issue(error, f"{where} {entity}", item))
    return issues


def path_checks(show: Show, spec: TransitionSpec) -> list[ValidationIssue]:
    """Checks a transition's paths without the engine: the spots they name or face, and counts.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> path_checks(show, show.transition(("1", "2")))
    []

    Args:
        show: The show the transition belongs to.
        spec: The transition's paths, stored or candidate.

    Returns:
        issues: Unknown or faced spots outside the cast, then a counts mismatch, if any.
    """
    issues = path_spot_issues(show, [spec])
    expected = show.set(spec.id[1]).counts
    if not math.isclose(spec.counts, expected):
        issues.append(
            make_issue(
                ERROR,
                ErrorCode.COUNTS_MISMATCH,
                format_tid(spec.id),
                f"paths span {spec.counts} counts, set {spec.id[1]} has {expected}",
                "retime with set_counts",
                {
                    "item": ItemKey(kind="transition", path=(_folder(show, spec.id),)).to_json(),
                    "counts": spec.counts,
                    "expected": expected,
                },
            )
        )
    return issues


def validate_paths(show: Show, spec: TransitionSpec) -> ValidationReport:
    """Checks a transition's paths: known spots, counts, windows, style params, continuity.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> validate_paths(show, show.transition(("1", "2"))).ok
    True
    """
    issues = path_checks(show, spec) + compile_issues(show, spec, show.cast.ids())
    return ValidationReport(issues=tuple(issues))


def validate_dependencies(show: Show, store: SheetStore | None = None) -> ValidationReport:
    """Checks that the sheet reference resolves and its version is pinned in dependencies.

    >>> from e8s.examples import two_set_show
    >>> [str(i.code) for i in validate_dependencies(two_set_show()).issues]
    ['UNKNOWN_SHEET']
    """
    ref = show.sheet.ref
    kind, ref_id, version = parse_ref(ref)
    issues = []
    if show.dependencies.get(f"{kind}:{ref_id}") != version:
        issues.append(
            make_issue(
                ERROR,
                ErrorCode.UNPINNED_SHEET,
                "dependencies",
                f"{ref} is not pinned",
                "pin it with upgrade_dependency",
            )
        )
    try:
        get_sheet(ref, store)
    except E8sError as error:
        issues.append(
            make_issue(ERROR, ErrorCode.UNKNOWN_SHEET, "sheet", error.message, error.hint)
        )
    return ValidationReport(issues=tuple(issues))


def _counterpart(known: Show, set_id: SetId) -> Set | None:
    position = known.lookup.positions.get(set_id)
    return None if position is None else known.sets[position]


def skips_transition(show: Show, tid: TransitionId, known: Show | None = None) -> bool:
    """Tells whether validate_show leaves a transition's paths unchecked.

    It does when either set or the stored paths hold a non-finite number, which NOT_FINITE
    reports, or when the destination set has negative counts, which TIME_RANGE reports.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> skips_transition(show, show.transition_ids()[0])
    False

    Args:
        show: The show the transition belongs to.
        tid: One of the show's consecutive transitions.
        known: An earlier show that holds no non-finite number; parts of `show` that are the
            very objects found at the same place in it are not searched.

    Returns:
        skipped: Whether the transition's paths go unchecked.
    """
    parts = (show.set(tid.from_set), show.set(tid.to_set), show.lookup.stored.get(tid))
    counterparts: tuple[object, ...] = (None, None, None)
    if known is not None:
        counterparts = (
            _counterpart(known, tid.from_set),
            _counterpart(known, tid.to_set),
            known.lookup.stored.get(tid),
        )
    if any(
        part is not None and has_non_finite(part, counterpart)
        for part, counterpart in zip(parts, counterparts, strict=True)
    ):
        return True
    return show.set(tid.to_set).counts < 0


def transition_issues(show: Show, tid: TransitionId) -> list[ValidationIssue]:
    """Checks one consecutive transition's paths as validate_show does.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> transition_issues(show, show.transition_ids()[0])
    []

    Args:
        show: The show the transition belongs to.
        tid: One of the show's consecutive transitions.

    Returns:
        issues: The paths' issues; none when skips_transition says the paths go unchecked.
    """
    if skips_transition(show, tid):
        return []
    return list(validate_paths(show, show.transition(tid)).issues)


class ShowChecks(NamedTuple):
    """Supplies the checks validate_show runs per set, per transition and over whole areas.

    An incremental run passes checks that reuse earlier results; validate_show passes checks
    that read the whole show. Either way run_checks keeps one issue order.

    Attributes:
        set_issues: Issues of one set, as validate_set reports them.
        transition_issues: Issues of one consecutive transition, as transition_issues reports.
        non_finite: Non-finite number issues, as non_finite_issues reports.
        references: Reference issues, as reference_issues reports.
        placements: Placement issues, as placement_issues reports.
    """

    set_issues: Callable[[Set], Sequence[ValidationIssue]]
    transition_issues: Callable[[TransitionId], Sequence[ValidationIssue]]
    non_finite: Callable[[], Sequence[ValidationIssue]]
    references: Callable[[], Sequence[ValidationIssue]]
    placements: Callable[[], Sequence[ValidationIssue]]


def whole_show_checks(show: Show) -> ShowChecks:
    """Returns the checks that read the whole show, as validate_show runs them.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> list(whole_show_checks(show).set_issues(show.sets[0]))
    []
    """
    return ShowChecks(
        set_issues=lambda candidate: validate_set(show, candidate).issues,
        transition_issues=lambda tid: transition_issues(show, tid),
        non_finite=lambda: non_finite_issues(show),
        references=lambda: reference_issues(show),
        placements=lambda: placement_issues(show),
    )


def run_checks(
    show: Show, checks: ShowChecks, *, comments: Sequence[Comment] = ()
) -> ValidationReport:
    """Runs validate_show's checks in its issue order, without the sheet store's check.

    Sets come first, then transitions (unless the show is too long), then counts, sequence,
    form keys, selections, IDs, non-finite numbers, references, placements, comments and the
    timeline. Checks of counts, sequence, keys, selections, IDs, comments and the timeline
    always read the whole show.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> run_checks(show, whole_show_checks(show)).ok
    True

    Args:
        show: The show to check.
        checks: Where the per-set, per-transition and per-area issues come from.
        comments: Review comments whose anchors must resolve in the show.

    Returns:
        report: Every issue found, in validate_show's order.
    """
    issues = [issue for candidate in show.sets for issue in checks.set_issues(candidate)]
    too_long = too_long_issues(show)
    if not too_long:
        issues += [i for tid in show.transition_ids() for i in checks.transition_issues(tid)]
    issues += too_long + sequence_issues(show) + first_set_issues(show)
    issues += negative_count_issues(show)
    issues += form_key_issues(show) + selection_issues(show) + id_issues(show)
    issues += [*checks.non_finite(), *checks.references(), *checks.placements()]
    issues += comment_issues(show, comments)
    issues += validate_timeline(show.timeline).issues
    return ValidationReport(issues=tuple(issues))


def validate_show(
    show: Show, store: SheetStore | None = None, *, comments: Sequence[Comment] = ()
) -> ValidationReport:
    """Checks sets, paths, dependencies (when a store is given), sequence and timeline.

    Comments live outside the show, so their anchors are checked only for the comments given.

    >>> from e8s.examples import two_set_show
    >>> validate_show(two_set_show()).ok
    True

    Args:
        show: The show to check.
        store: Sheet store that resolves the sheet reference; None skips that check.
        comments: Review comments whose anchors must resolve in the show.

    Returns:
        report: Every issue found; validation never raises on bad data.
    """
    report = run_checks(show, whole_show_checks(show), comments=comments)
    if store is None:
        return report
    return ValidationReport.merge(report, validate_dependencies(show, store))
