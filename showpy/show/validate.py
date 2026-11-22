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
from collections.abc import Callable, Iterable, Iterator, Mapping
from itertools import islice
from typing import Literal

from pydantic import BaseModel

from showpy.choreo.engine import PathEngine
from showpy.choreo.match_ops import forms_of_set
from showpy.choreo.models import Set
from showpy.choreo.path_models import TransitionSpec
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import ContinuityError, SchemaError, ShowPyError
from showpy.core.ids import check_id, check_sheet_id, format_tid, parse_ref
from showpy.core.validation import ValidationIssue, ValidationReport
from showpy.sheet.templates import SheetStore, get_sheet
from showpy.show.document_paths import is_storable_folder
from showpy.show.models import Show
from showpy.time.validate import validate_timeline

FULL_TURN = 360.0
MAX_NON_FINITE_ISSUES = 100

type Level = Literal["error", "warning"]
ERROR: Level = "error"
WARNING: Level = "warning"


def _issue(level: Level, code: ErrorCode, where: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, where=where, message=message, hint=hint)


def _membership(show: Show, candidate: Set) -> list[ValidationIssue]:
    where = f"set {candidate.id}"
    cast_ids = set(show.cast.ids())
    placed = set(candidate.entity_ids())
    issues = [
        _issue(
            ERROR,
            ErrorCode.MISSING_SPOT,
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
            ErrorCode.UNKNOWN_SPOT,
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
            ErrorCode.PINNED_CHANGED,
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
                    ErrorCode.UNKNOWN_FORM,
                    where,
                    f"form {p.form} does not exist",
                    "rebuild the form or clear the placement's form",
                )
            )
        elif p.entity not in form.members or form.set != candidate.id:
            issues.append(
                _issue(
                    ERROR,
                    ErrorCode.NOT_IN_FORM,
                    where,
                    f"not a member of {p.form} in this set",
                    "add it to the form's members",
                )
            )
    return issues


def _stale_forms(show: Show, candidate: Set) -> list[ValidationIssue]:
    listed = forms_of_set(show, candidate.id)
    return [
        _issue(
            WARNING,
            ErrorCode.STALE_FORM,
            f"set {candidate.id} form {form_id}",
            f"set {candidate.id} no longer lists form {form_id}",
            "re-place the set's forms, or remove the form",
        )
        for form_id, form in show.forms.items()
        if form.set == candidate.id and form_id not in listed
    ]


def _facings(candidate: Set) -> list[ValidationIssue]:
    return [
        _issue(
            WARNING,
            ErrorCode.FACING_RANGE,
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
    issues += _facings(candidate)
    if candidate.id in show.set_ids():
        issues += _stale_forms(show, candidate)
    return ValidationReport(issues=tuple(issues))


def _with_spec(show: Show, spec: TransitionSpec) -> Show:
    if show.transition(spec.id) == spec:
        return show
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show.replace(transitions=(*others, spec))


def _path_rows(show: Show, spec: TransitionSpec, where: str) -> list[ValidationIssue]:
    cast_ids = set(show.cast.ids())
    issues = [
        _issue(
            ERROR,
            ErrorCode.UNKNOWN_SPOT,
            f"{where} {e}",
            f"{e} is not in the cast",
            "remove the row",
        )
        for e in spec.rows
        if e not in cast_ids
    ]
    for route in spec.routes.values():
        unknown = [m for m in route.members if m not in cast_ids]
        if unknown:
            issues.append(
                _issue(
                    ERROR,
                    ErrorCode.UNKNOWN_SPOT,
                    f"{where} route {route.id}",
                    f"unknown members {unknown}",
                    "rebuild the route",
                )
            )
    return issues


def _compile_issue(error: ShowPyError, where: str) -> ValidationIssue:
    code = ErrorCode.CONTINUITY if isinstance(error, ContinuityError) else error.code
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
                ErrorCode.COUNTS_MISMATCH,
                where,
                f"paths span {spec.counts} counts, set {spec.id[1]} has {expected}",
                "retime with set_counts",
            )
        )
    return ValidationReport(issues=tuple(issues + _compile_rows(show, spec, where)))


def validate_dependencies(show: Show, store: SheetStore | None = None) -> ValidationReport:
    """Checks that the sheet reference resolves and its version is pinned in dependencies.

    >>> from showpy.examples import two_set_show
    >>> [str(i.code) for i in validate_dependencies(two_set_show()).issues]
    ['UNKNOWN_SHEET']
    """
    ref = show.sheet.ref
    kind, ref_id, version = parse_ref(ref)
    issues = []
    if show.dependencies.get(f"{kind}:{ref_id}") != version:
        issues.append(
            _issue(
                ERROR,
                ErrorCode.UNPINNED_SHEET,
                "dependencies",
                f"{ref} is not pinned",
                "pin it with upgrade_dependency",
            )
        )
    try:
        get_sheet(ref, store)
    except ShowPyError as error:
        issues.append(_issue(ERROR, ErrorCode.UNKNOWN_SHEET, "sheet", error.message, error.hint))
    return ValidationReport(issues=tuple(issues))


def _sequence(show: Show) -> list[ValidationIssue]:
    zero_counts = [
        _issue(
            WARNING,
            ErrorCode.ZERO_COUNTS,
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
            ErrorCode.STALE_TRANSITION,
            format_tid(spec.id),
            "paths for sets that are not consecutive",
            "delete or re-create these paths",
        )
        for spec in show.transitions
        if spec.id not in valid
    ]
    return zero_counts + stale


def _form_keys(show: Show) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.KEY_MISMATCH,
            where=f"form {key}",
            message=f"form stored under {key!r} has id {form.id!r}",
            hint="store every form under its own id",
            context={"key": key, "id": form.id},
        )
        for key, form in show.forms.items()
        if key != form.id
    ]


def _selections(show: Show) -> list[ValidationIssue]:
    cast_ids = set(show.cast.ids())
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.UNKNOWN_SPOT,
            where=f"selection {name}",
            message=f"{spot} is not in the cast",
            hint="remove the spot from the selection or add it to the cast",
            context={"path": f"cast.selections.{name}", "spot": spot},
        )
        for name, members in show.cast.selections.items()
        for spot in sorted(members)
        if spot not in cast_ids
    ]


def _bad_id(kind: str, path: str, value: str, error: ShowPyError) -> ValidationIssue:
    return ValidationIssue(
        level=ERROR,
        code=ErrorCode.BAD_ID,
        where=f"{kind} {value!r}",
        message=f"{kind} ID {value!r} cannot be stored",
        hint=error.hint,
        context={"path": path, "id": value},
    )


def _checked(
    kind: str, path: str, value: str, check: Callable[[str], str]
) -> list[ValidationIssue]:
    try:
        check(value)
    except SchemaError as error:
        return [_bad_id(kind, path, value, error)]
    return []


def _set_id_issue(index: int, set_id: str) -> list[ValidationIssue]:
    if is_storable_folder(set_id):
        return []
    return _checked("set", f"sets[{index}].id", set_id, lambda text: check_id(text, kind="set"))


def _ids(show: Show) -> list[ValidationIssue]:
    issues = _checked("show", "id", show.id, lambda text: check_sheet_id(text, kind="show"))
    for index, candidate in enumerate(show.sets):
        issues += _set_id_issue(index, candidate.id)
    for index, note in enumerate(show.notes):
        issues += _checked(
            "note", f"notes[{index}].id", note.id, lambda t: check_sheet_id(t, kind="note")
        )
    for key, form in show.forms.items():
        issues += _checked(
            "form", f"forms.{key}.id", form.id, lambda t: check_sheet_id(t, kind="form")
        )
        if key != form.id:
            issues += _checked(
                "form", f"forms.{key}", key, lambda t: check_sheet_id(t, kind="form")
            )
    return issues


def _non_finite_values(value: object, path: str) -> Iterator[tuple[str, float]]:
    if isinstance(value, float):
        if not math.isfinite(value):
            yield path, value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _non_finite_values(getattr(value, name), f"{path}.{name}" if path else name)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _non_finite_values(item, f"{path}.{key}")
    elif isinstance(value, tuple | list):
        for index, item in enumerate(value):
            yield from _non_finite_values(item, f"{path}[{index}]")


def _has_non_finite(value: object) -> bool:
    return next(_non_finite_values(value, ""), None) is not None


def _non_finite(show: Show) -> list[ValidationIssue]:
    found = islice(_non_finite_values(show, ""), MAX_NON_FINITE_ISSUES)
    return [
        ValidationIssue(
            level=ERROR,
            code=ErrorCode.NOT_FINITE,
            where=path,
            message=f"{path} is {value}",
            hint="use a finite number",
            context={"path": path, "value": str(value)},
        )
        for path, value in found
    ]


def _path_reports(show: Show) -> list[ValidationReport]:
    reports = []
    for tid in show.transition_ids():
        stored = show.lookup.stored.get(tid)
        parts = (show.set(tid.from_set), show.set(tid.to_set), stored)
        if any(part is not None and _has_non_finite(part) for part in parts):
            continue
        reports.append(validate_paths(show, show.transition(tid)))
    return reports


def _all(reports: Iterable[ValidationReport]) -> ValidationReport:
    return ValidationReport.merge(*reports)


def validate_show(show: Show, store: SheetStore | None = None) -> ValidationReport:
    """Checks sets, paths, dependencies (when a store is given), sequence and timeline.

    >>> from showpy.examples import two_set_show
    >>> validate_show(two_set_show()).ok
    True
    """
    reports = [validate_set(show, s) for s in show.sets]
    reports += _path_reports(show)
    extra = _sequence(show) + _form_keys(show) + _selections(show) + _ids(show)
    extra += _non_finite(show)
    reports.append(ValidationReport(issues=tuple(extra)))
    reports.append(validate_timeline(show.timeline))
    if store is not None:
        reports.append(validate_dependencies(show, store))
    return _all(reports)
