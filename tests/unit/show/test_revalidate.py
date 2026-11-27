#!/usr/bin/env python3

"""
test_revalidate.py --- tests incremental revalidation of an edited show

Contains:
    test_one_spot_revalidation_runs_no_whole_transition_check: recompiles only the moved spot,
        once per transition that touches its set
    test_revalidate_keeps_issues_of_unchanged_items: returns the previous report's own issue
        objects for items the edit did not reach
    test_revalidate_falls_back_on_a_previous_report_it_cannot_read: reruns validate_show when a
        previous issue names an item, code or context revalidate does not know
    test_revalidate_falls_back_when_the_edit_cannot_be_bounded: rechecks in full a show that
        drops back under MAX_COUNTS, a previous report missing a kept transition issue and pins
        repeated in both shows
    test_one_spot_revalidation_under_50_ms_at_300_by_60: revalidates a one-spot move of a 300
        spot, 60 set show within 50 ms
"""

import time
from collections.abc import Callable

import pytest

from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_models import MotionPlan
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathRow, PathStyle
from e8s.core.caps import MAX_COUNTS
from e8s.core.error_codes import ErrorCode
from e8s.core.ids import SpotId
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue, ValidationReport
from e8s.show import revalidate as revalidate_module
from e8s.show.change_models import ItemKey
from e8s.show.models import Pin, Show
from e8s.show.revalidate import revalidate
from e8s.show.validate import validate_show
from tests.fixtures.generators import seeded_show

SEED = 3
SPOTS = 8
SETS = 4
MOVED_SPOT = "G1"
EDITED_SET = 2
EDITED_KEY = "3"
BUDGET_SPOTS = 300
BUDGET_SETS = 60
BUDGET_SEED = 11
BUDGET_S = 0.05
BUDGET_RUNS = 5


def _moved(show: Show, position: int, spot: str, dx: float) -> Show:
    owner = show.sets[position]
    edited = owner.with_placements({spot: PlacementPatch(x=owner.get(spot).x + dx)}, force=True)
    return show.replace(sets=tuple(edited if s is owner else s for s in show.sets))


def test_one_spot_revalidation_runs_no_whole_transition_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recompiles only the moved spot, once per transition that touches its set."""
    before = seeded_show(SEED, SPOTS, SETS)
    after = _moved(before, EDITED_SET, MOVED_SPOT, 0.5)
    previous, expected = validate_show(before), validate_show(after)
    compiled: list[tuple[str, tuple[str, str]]] = []
    original = PathEngine.compile_entity

    def counted(engine: PathEngine, entity: str, tid: tuple[str, str]) -> MotionPlan:
        compiled.append((entity, (tid[0], tid[1])))
        return original(engine, entity, tid)

    monkeypatch.setattr(PathEngine, "compile_entity", counted)
    assert revalidate(previous, before, after) == expected
    assert sorted(compiled) == [(MOVED_SPOT, ("2", "3")), (MOVED_SPOT, ("3", "4"))]


def _with_issues(show: Show) -> Show:
    spots = tuple(s.replace(active=False) if s.id == "P2" else s for s in show.cast.spots)
    spec = show.transition(("1", "2"))
    held = PathRow(entity=SpotId("W1"), style=PathStyle.HOLD)
    others = tuple(t for t in show.transitions if t.id != spec.id)
    return show._copied(
        update={
            "cast": show.cast.replace(spots=spots),
            "pins": (Pin(target="entity", id="B1"),),
            "transitions": (spec.replace(rows={**spec.rows, SpotId("W1"): held}), *others),
        }
    )


def _untouched(issue: ValidationIssue) -> bool:
    key = ItemKey.model_validate(issue.context["item"])
    if issue.code == "INACTIVE_ON_FIELD":
        return key.path[0] != EDITED_KEY
    return issue.code == "PIN_MISMATCH" or (issue.code == "CONTINUITY" and key.path[0] == "1-2")


def test_revalidate_keeps_issues_of_unchanged_items() -> None:
    """Returns the previous report's own issue objects for items the edit did not reach."""
    before = _with_issues(seeded_show(SEED, SPOTS, SETS))
    after = _moved(before, EDITED_SET, "G2", 0.5)
    previous = validate_show(before)
    report = revalidate(previous, before, after)
    assert report == validate_show(after)
    untouched = [issue for issue in previous.issues if _untouched(issue)]
    assert {str(issue.code) for issue in untouched} == {
        "INACTIVE_ON_FIELD",
        "PIN_MISMATCH",
        "CONTINUITY",
    }
    assert len([i for i in untouched if i.code == "INACTIVE_ON_FIELD"]) == SETS - 1
    assert all(any(kept is issue for kept in report.issues) for issue in untouched)


def _count_full_runs(monkeypatch: pytest.MonkeyPatch) -> list[Show]:
    runs: list[Show] = []

    def counted(show: Show) -> ValidationReport:
        runs.append(show)
        return validate_show(show)

    monkeypatch.setattr(revalidate_module, "validate_show", counted)
    return runs


def _issue(code: str, item: JsonValue, **extra: JsonValue) -> ValidationIssue:
    return ValidationIssue(
        level="error",
        code=ErrorCode(code),
        where="x",
        message="m",
        hint="h",
        context={"item": item, **extra},
    )


def _key(kind: str, *path: str, role: str | None = None) -> JsonValue:
    return ItemKey.model_validate({"kind": kind, "path": path, "role": role}).to_json()


@pytest.mark.parametrize(
    "unknown",
    [
        _issue("UNKNOWN_SPOT", {"kind": "no such kind", "path": []}),
        _issue("UNKNOWN_SHEET", _key("sheet")),
        _issue("PATH", _key("placement", "2", "B1", role="drill")),
        _issue("MEMBER_MISMATCH", _key("placement", "2", "B1", role="drill")),
        _issue("ZERO_COUNTS", _key("set", "2", role="timing")),
        ValidationIssue(
            level="error",
            code=ErrorCode.NOT_FINITE,
            where="sets[1].counts",
            message="sets[1].counts is inf",
            hint="use a finite number",
            context={"path": "sets[1].counts", "value": "inf"},
        ),
        _issue("PATH", _key("transition", "2-3")),
    ],
    ids=["bad_item", "sheet", "placement_code", "member_form", "set_code", "non_finite", "row"],
)
def test_revalidate_falls_back_on_a_previous_report_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, unknown: ValidationIssue
) -> None:
    """Reruns validate_show when a previous issue names something revalidate does not know."""
    before = seeded_show(SEED, SPOTS, SETS)
    after = _moved(before, EDITED_SET, MOVED_SPOT, 0.5)
    previous = ValidationReport.merge(validate_show(before), ValidationReport(issues=(unknown,)))
    expected = validate_show(after)
    full_runs = _count_full_runs(monkeypatch)
    assert revalidate(previous, before, after) == expected
    assert full_runs == [after]


type Case = tuple[ValidationReport, Show, Show]


def _leaving_max_counts() -> Case:
    show = seeded_show(SEED, SPOTS, SETS)
    longest = show.sets[1]._copied(update={"counts": float(MAX_COUNTS)})
    before = show._copied(update={"sets": (show.sets[0], longest, *show.sets[2:])})
    return validate_show(before), before, _moved(show, EDITED_SET, MOVED_SPOT, 0.5)


def _missing_a_kept_counts_issue() -> Case:
    show = seeded_show(SEED, SPOTS, SETS)
    spec = show.transition(("2", "3"))
    others = tuple(t for t in show.transitions if t.id != spec.id)
    retimed = spec.replace(counts=spec.counts + 1)
    before = show._copied(update={"transitions": (retimed, *others)})
    return ValidationReport(), before, _moved(before, EDITED_SET, MOVED_SPOT, 0.5)


def _pins_repeated_in_both() -> Case:
    show = seeded_show(SEED, SPOTS, SETS)
    before = show._copied(update={"pins": (Pin(target="entity", id="B1"),) * 2})
    moved = _moved(before, EDITED_SET, MOVED_SPOT, 0.5)
    return validate_show(before), before, before._copied(update={"sets": moved.sets})


@pytest.mark.parametrize(
    "case",
    [_leaving_max_counts, _missing_a_kept_counts_issue, _pins_repeated_in_both],
    ids=["leaves_max_counts", "stale_previous", "pins_repeat"],
)
def test_revalidate_falls_back_when_the_edit_cannot_be_bounded(
    monkeypatch: pytest.MonkeyPatch, case: Callable[[], Case]
) -> None:
    """Rechecks in full where kept issues cannot be trusted, and still matches validate_show."""
    previous, before, after = case()
    expected = validate_show(after)
    full_runs = _count_full_runs(monkeypatch)
    assert revalidate(previous, before, after) == expected
    assert full_runs == [after]


@pytest.fixture(scope="module")
def budget_show() -> Show:
    """Builds the 300 spot, 60 set seeded show once for the timing test."""
    return seeded_show(BUDGET_SEED, BUDGET_SPOTS, BUDGET_SETS)


@pytest.mark.slow
@pytest.mark.timing
def test_one_spot_revalidation_under_50_ms_at_300_by_60(budget_show: Show) -> None:
    """Revalidates a one-spot move of a 300 spot, 60 set show within 50 ms (best of 5)."""
    before = budget_show
    after = _moved(before, BUDGET_SETS // 2, before.cast.ids()[0], 0.25)
    previous = validate_show(before)
    report = revalidate(previous, before, after)
    assert report == validate_show(after)
    times = []
    for _ in range(BUDGET_RUNS):
        start = time.perf_counter()
        revalidate(previous, before, after)
        times.append(time.perf_counter() - start)
    assert min(times) < BUDGET_S, f"best of {BUDGET_RUNS}: {min(times) * 1e3:.1f} ms"
