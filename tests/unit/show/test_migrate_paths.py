#!/usr/bin/env python3

"""
test_migrate_paths.py --- tests the paths sub-step of the v1 to v2 format migration

Contains:
    test_regression_easing_strength_below_one_is_raised_with_a_repair(): stores the strength 1 that
        v1 used in place of a lower one
    test_regression_steps_over_the_cap_are_refused(): refuses a step count v2 cannot hold
    test_other_documents_pass_through(): leaves every non-paths document unchanged
"""

import copy

import pytest

from showpy.choreo.path_models import MAX_EASING_STEPS, TransitionSpec
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue
from showpy.show.documents import SHEET_DOC, paths_doc
from showpy.show.migrate import ReportBuilder
from showpy.show.migrations.v1_to_v2_paths import migrate_paths

PATHS = paths_doc(("1", "2"))
LOW_STRENGTH = 0.5


def _v1_docs(row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    spec: dict[str, JsonValue] = {"id": ["1", "2"], "counts": 8.0, "rows": {"T1": row}}
    return {PATHS: spec, SHEET_DOC: {"easing": {"kind": "steps", "strength": 1e9}}}


def _migrated(docs: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], ReportBuilder]:
    before = copy.deepcopy(docs)
    report = ReportBuilder(1)
    out = migrate_paths(docs, report)
    assert docs == before
    return out, report


def test_regression_easing_strength_below_one_is_raised_with_a_repair() -> None:
    """Stores the strength 1 that v1 used in place of a lower one, and records the repair."""
    row: dict[str, JsonValue] = {
        "entity": "T1",
        "easing": {"kind": "ease_in", "strength": LOW_STRENGTH},
    }
    out, report = _migrated(_v1_docs(row))
    assert TransitionSpec.model_validate(out[PATHS]).row("T1").easing.strength == 1.0
    (repair,) = report.build().repairs
    assert "rows.T1.easing.strength" in repair


def test_regression_steps_over_the_cap_are_refused() -> None:
    """Refuses a step count v2 cannot hold, naming the document, the field and the value."""
    over = float(MAX_EASING_STEPS + 1)
    row: dict[str, JsonValue] = {"entity": "T1", "easing": {"kind": "steps", "strength": over}}
    with pytest.raises(SchemaError) as info:
        _migrated(_v1_docs(row))
    field = "rows.T1.easing.strength"
    assert info.value.context == {"path": PATHS, "field": field, "value": repr(over)}


def test_other_documents_pass_through() -> None:
    """Leaves every non-paths document unchanged, even one holding a path-like easing."""
    docs = _v1_docs({"entity": "T1"})
    out, report = _migrated(docs)
    assert out == docs
    assert report.build().repairs == ()
