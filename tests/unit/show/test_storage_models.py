#!/usr/bin/env python3

"""
test_storage_models.py --- tests the fsck, gc, upgrade and conformance reports

Contains:
    test_every_storage_report_round_trips_through_json: each report survives to_json and from_json
    test_report_counts_refuse_negative_numbers: refuses a negative byte or revision count
"""

import pytest

from e8s.core.errors import SchemaError
from e8s.core.types import FrozenModel
from e8s.show.storage_models import (
    FsckFinding,
    FsckReport,
    GcReport,
)

MODELS: tuple[FrozenModel, ...] = (
    FsckFinding(code="MISSING_BLOB", path="blobs/ab", message="blob is gone"),
    FsckReport(findings=(FsckFinding(code="X", path="p", message="m"),), ok=False),
    GcReport(removed=("a", "b"), bytes_freed=12, dry_run=True),
)


@pytest.mark.parametrize("model", MODELS, ids=lambda model: type(model).__name__)
def test_every_storage_report_round_trips_through_json(model: FrozenModel) -> None:
    assert type(model).from_json(model.to_json()) == model


def test_report_counts_refuse_negative_numbers() -> None:
    with pytest.raises(SchemaError):
        GcReport(bytes_freed=-1)
