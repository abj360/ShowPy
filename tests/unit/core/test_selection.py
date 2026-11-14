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
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import ShowPyError
from showpy.core.selection import Selection


def test_selection_dedupes_and_keeps_order() -> None:
    assert Selection(ids=("T2", "T1", "T2")).ids == ("T2", "T1")


def test_union_intersection_difference() -> None:
    a = Selection(ids=("T1", "T2", "T3"))
    b = Selection(ids=("T3", "T4"))
    assert (a | b).ids == ("T1", "T2", "T3", "T4")
    assert (a & b).ids == ("T3",)
    assert (a - b).ids == ("T1", "T2")


def test_len_iter_contains() -> None:
    sel = Selection(ids=("T1", "T2"))
    assert len(sel) == 2
    assert list(sel) == ["T1", "T2"]
    assert "T1" in sel
    assert "T9" not in sel


@pytest.mark.defect("UP-9.3-33")
@pytest.mark.xfail(strict=True, reason="UP-9.3-33")
def test_regression_selection_rejects_a_bare_string() -> None:
    """Rejects `ids="T12"` instead of splitting it into characters (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Selection(ids="T12")  # pyright: ignore[reportArgumentType] - deliberately invalid input


@pytest.mark.defect("UP-9.3-34")
@pytest.mark.xfail(strict=True, reason="UP-9.3-34")
def test_regression_selection_model_copy_keeps_ids_unique() -> None:
    """Deduplicates IDs on model_copy as the constructor does (audit 9.3)."""
    duplicated = Selection(ids=("a", "b")).model_copy(update={"ids": ("a", "a")})
    assert duplicated.ids == ("a",)


@pytest.mark.defect("UP-9.3-35")
@pytest.mark.xfail(strict=True, reason="UP-9.3-35")
def test_regression_selection_equality_ignores_the_description() -> None:
    """Compares selections by IDs, not by how they were described (audit 9.3)."""
    left = Selection(ids=("a",), description="first")
    assert left == Selection(ids=("a",), description="second")
