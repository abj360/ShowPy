#!/usr/bin/env python3

"""
test_selection.py --- tests the Selection set algebra

Contains:
    test_*(): one behavior of showpy.core.selection each
    test_regression_selection_rejects_a_bare_string: rejects `ids="T12"` instead of splitting it
        into characters
    test_regression_selection_model_copy_keeps_ids_unique: deduplicates IDs on model_copy as the
        constructor does
    test_regression_selection_equality_ignores_the_description: compares selections by IDs, not by
        how they were described
    test_regression_malformed_ids_are_a_schema_error_not_a_type_error: rejects non-list ids with
        SchemaError, never TypeError
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import SchemaError, ShowPyError
from showpy.core.ids import SpotId
from showpy.core.selection import Selection


def test_selection_dedupes_and_keeps_order() -> None:
    assert Selection(ids=(SpotId("T2"), SpotId("T1"), SpotId("T2"))).ids == ("T2", "T1")


def test_union_intersection_difference() -> None:
    a = Selection(ids=(SpotId("T1"), SpotId("T2"), SpotId("T3")))
    b = Selection(ids=(SpotId("T3"), SpotId("T4")))
    assert (a | b).ids == ("T1", "T2", "T3", "T4")
    assert (a & b).ids == ("T3",)
    assert (a - b).ids == ("T1", "T2")


def test_len_iter_contains() -> None:
    sel = Selection(ids=(SpotId("T1"), SpotId("T2")))
    assert len(sel) == 2
    assert list(sel) == ["T1", "T2"]
    assert "T1" in sel
    assert "T9" not in sel


def test_regression_selection_rejects_a_bare_string() -> None:
    """Rejects `ids="T12"` instead of splitting it into characters (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Selection(ids="T12")  # pyright: ignore[reportArgumentType] - deliberately invalid input


@pytest.mark.defect("UP-9.3-34")
@pytest.mark.xfail(strict=True, reason="UP-9.3-34")
def test_regression_selection_model_copy_keeps_ids_unique() -> None:
    """Deduplicates IDs on model_copy as the constructor does (audit 9.3)."""
    duplicated = Selection(ids=(SpotId("a"), SpotId("b"))).model_copy(update={"ids": ("a", "a")})
    assert duplicated.ids == ("a",)


@pytest.mark.defect("UP-9.3-35")
@pytest.mark.xfail(strict=True, reason="UP-9.3-35")
def test_regression_selection_equality_ignores_the_description() -> None:
    """Compares selections by IDs, not by how they were described (audit 9.3)."""
    left = Selection(ids=(SpotId("a"),), description="first")
    assert left == Selection(ids=(SpotId("a"),), description="second")


@pytest.mark.parametrize("ids", [3, None, [[1]], [{"a": 1}], "T1", [1, 1]])
def test_regression_malformed_ids_are_a_schema_error_not_a_type_error(ids: object) -> None:
    """Rejects ids that are not a list of strings with SchemaError, never TypeError."""
    with pytest.raises(SchemaError):
        Selection.model_validate({"ids": ids})
