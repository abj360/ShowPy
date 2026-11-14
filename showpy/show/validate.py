"""
validate.py --- structural validation of sets, paths, dependencies and whole shows

Validators never raise on bad data: every problem becomes a ValidationIssue with a code,
a location and a hint. Safety rules (collisions, speed, bounds) belong to a consumer checker.

Contains:
    validate_set(): checks a candidate set against the show
    validate_paths(): checks a transition's paths, including continuity through the engine
    validate_dependencies(): checks that the sheet reference resolves and is pinned
    validate_show(): checks everything plus set sequence, transitions and the timeline
"""

import math
from collections.abc import Iterable
from typing import Literal

from showpy.choreo.engine import PathEngine
from showpy.choreo.models import Set, TransitionSpec
from showpy.core.errors import ContinuityError, ShowPyError
from showpy.core.ids import format_tid, parse_ref
from showpy.core.validation import ValidationIssue, ValidationReport
from showpy.sheet.templates import SheetStore, get_sheet
from showpy.show.models import Show
from showpy.time.validate import validate_timeline

FULL_TURN = 360.0

type Level = Literal["error", "warning"]
ERROR: Level = "error"
WARNING: Level = "warning"


def _issue(level: Level, code: str, where: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, where=where, message=message, hint=hint)


def _membership(show: Show, candidate: Set) -> list[ValidationIssue]:
    where = f"set {candidate.id}"
    cast_ids = set(show.cast.ids())
    placed = set(candidate.entity_ids())
    issues = [
        _issue(
            ERROR,
            "MISSING_SPOT",
            where,
            f"{e} has no placement",
            "every spot is placed in every set",
        )
        for e in show.cast.ids()
        if e not in placed
    ]
    issues += [
        _issue(
            ERROR,
            "UNKNOWN_SPOT",
            where,
            f"{e} is not in the cast",
            "add the spot or remove the row",
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
        _issue(
            ERROR,
            "PINNED_CHANGED",
            f"set {candidate.id} {p.entity}",
            "a pinned placement changed",
            "unpin it before editing",
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
                _issue(
                    ERROR,
                    "UNKNOWN_FORM",
                    where,
                    f"form {p.form} does not exist",
                    "rebuild the form or clear the placement's form",
                )
            )
        elif p.entity not in form.members or form.set != candidate.id:
            issues.append(
                _issue(
                    ERROR,
                    "NOT_IN_FORM",
                    where,
                    f"not a member of {p.form} in this set",
                    "add it to the form's members",
                )
            )
    return issues


def _facings(candidate: Set) -> list[ValidationIssue]:
    return [
        _issue(
            WARNING,
            "FACING_RANGE",
            f"set {candidate.id} {p.entity}",
            f"facing {p.facing} is outside [0, 360)",
            "normalize the facing",
        )
        for p in candidate.placements
        if not 0.0 <= p.facing < FULL_TURN
    ]


def validate_set(show: Show, candidate: Set) -> ValidationReport:
    """Checks a candidate set: every spot once, known IDs, facing range, forms, pins unchanged.

    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> validate_set(show, show.set("1")).ok
    True
    """
    issues = _membership(show, candidate) + _pins(show, candidate) + _forms(show, candidate)
    return ValidationReport(issues=tuple(issues + _facings(candidate)))


def _with_spec(show: Show, spec: TransitionSpec) -> Show:
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show.model_copy(update={"transitions": (*others, spec)})


def _path_rows(show: Show, spec: TransitionSpec, where: str) -> list[ValidationIssue]:
    cast_ids = set(show.cast.ids())
    issues = [
        _issue(ERROR, "UNKNOWN_SPOT", f"{where} {e}", f"{e} is not in the cast", "remove the row")
        for e in spec.rows
        if e not in cast_ids
    ]
    for route in spec.routes.values():
        unknown = [m for m in route.members if m not in cast_ids]
        if unknown:
            issues.append(
                _issue(
                    ERROR,
                    "UNKNOWN_SPOT",
                    f"{where} route {route.id}",
                    f"unknown members {unknown}",
                    "rebuild the route",
                )
            )
    return issues


def _compile_issue(error: ShowPyError, where: str) -> ValidationIssue:
    code = "CONTINUITY" if isinstance(error, ContinuityError) else error.code
    return _issue(ERROR, code, where, error.message, error.hint)


def _compile_rows(show: Show, spec: TransitionSpec, where: str) -> list[ValidationIssue]:
    trial = _with_spec(show, spec)
    issues = []
    a, b = trial.set(spec.id[0]), trial.set(spec.id[1])
    engine = PathEngine(trial, cache=False)
    for entity in show.cast.ids():
        if entity not in a.entity_ids() or entity not in b.entity_ids():
            continue
        try:
            engine.compile_entity(entity, spec.id)
        except ShowPyError as error:
            issues.append(_compile_issue(error, f"{where} {entity}"))
    return issues


def validate_paths(show: Show, spec: TransitionSpec) -> ValidationReport:
    """Checks a transition's paths: known spots, counts, windows, style params, continuity.

    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> validate_paths(show, show.transition(("1", "2"))).ok
    True
    """
    where = format_tid(spec.id)
    issues = _path_rows(show, spec, where)
    expected = show.set(spec.id[1]).counts
    if not math.isclose(spec.counts, expected):
        issues.append(
            _issue(
                ERROR,
                "COUNTS_MISMATCH",
                where,
                f"paths span {spec.counts} counts, set {spec.id[1]} has {expected}",
                "retime with set_counts",
            )
        )
    return ValidationReport(issues=tuple(issues + _compile_rows(show, spec, where)))


def validate_dependencies(show: Show, store: SheetStore | None = None) -> ValidationReport:
    """Checks that the sheet reference resolves and its version is pinned in dependencies.

    >>> from showpy.examples import two_set_show
    >>> [i.code for i in validate_dependencies(two_set_show()).issues]
    ['UNKNOWN_SHEET']
    """
    ref = show.sheet.ref
    kind, ref_id, version = parse_ref(ref)
    issues = []
    if show.dependencies.get(f"{kind}:{ref_id}") != version:
        issues.append(
            _issue(
                ERROR,
                "UNPINNED_SHEET",
                "dependencies",
                f"{ref} is not pinned",
                "pin it with upgrade_dependency",
            )
        )
    try:
        get_sheet(ref, store)
    except ShowPyError as error:
        issues.append(_issue(ERROR, "UNKNOWN_SHEET", "sheet", error.message, error.hint))
    return ValidationReport(issues=tuple(issues))


def _sequence(show: Show) -> list[ValidationIssue]:
    zero_counts = [
        _issue(
            WARNING,
            "ZERO_COUNTS",
            f"set {s.id}",
            "set arrives in 0 counts",
            "give the set counts > 0",
        )
        for s in show.sets[1:]
        if s.counts == 0
    ]
    valid = set(show.transition_ids())
    stale = [
        _issue(
            ERROR,
            "STALE_TRANSITION",
            format_tid(spec.id),
            "paths for sets that are not consecutive",
            "delete or re-create these paths",
        )
        for spec in show.transitions
        if spec.id not in valid
    ]
    return zero_counts + stale


def _all(reports: Iterable[ValidationReport]) -> ValidationReport:
    return ValidationReport.merge(*reports)


def validate_show(show: Show, store: SheetStore | None = None) -> ValidationReport:
    """Checks sets, paths, dependencies (when a store is given), sequence and timeline.

    >>> from showpy.examples import two_set_show
    >>> validate_show(two_set_show()).ok
    True
    """
    reports = [validate_set(show, s) for s in show.sets]
    reports += [validate_paths(show, show.transition(tid)) for tid in show.transition_ids()]
    reports.append(ValidationReport(issues=tuple(_sequence(show))))
    reports.append(validate_timeline(show.timeline))
    if store is not None:
        reports.append(validate_dependencies(show, store))
    return _all(reports)
